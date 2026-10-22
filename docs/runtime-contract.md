# Streaming Governance Lab

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![marimo](https://img.shields.io/badge/marimo-reactive%20notebook-138B74)](https://marimo.io/)
[![MCP stdio](https://img.shields.io/badge/MCP-stdio-6C56A6)](../governance_mcp.py)
[![V&V: 126 passed](https://img.shields.io/badge/V%26V-126%20passed-208642)](../VV_report.md)
[![Semantic mutants: 26 killed](https://img.shields.io/badge/mutants-26%2F26%20killed-208642)](../vv_evidence/mutation_results.json)
[![SysML v2: validated](https://img.shields.io/badge/SysML%20v2-validated-208642)](../sysml_validation.json)

A runnable marimo lab tracing raw agent logs through byte-preserving content-defined chunking, an asynchronous SHA-256 ledger, and a verification gate before tool execution. Select a tampering scenario to inspect the first rejection, preserved ledger, quarantine events, and cleared receiver context.

The validation badges describe the delivered snapshot. They are static badges, not hosted CI status.

## Run the notebook

Requires [uv](https://docs.astral.sh/uv/). From this directory:

```bash
uvx marimo@0.25.1 edit --sandbox governance_lab.py
```

The engine itself uses only the Python standard library; the notebook also uses marimo. Its PEP 723 metadata captures the supplied hosted Python 3.13 environment and its additional packages; `--sandbox` honors that environment. The reusable engine and V&V runner support Python 3.11+. The supplied notebook snapshot embeds the same engine sources. Change the raw stream, nominal boundary interval, window, transport packet size, or scenario. Empty input produces no chunks or transactions.

The nine scenarios include clean delivery, payload mutation before commit, payload mutation during handoff, broken predecessor, missing sequence, replayed transaction sequence, rewritten routing metadata, and two explicit availability injections: expired transaction deadline and unavailable verifier. An attack targets chunk index 2, or the final available chunk for shorter streams. Already verified calls remain recorded; the attacked call is halted.

## Files

| File | Purpose |
|---|---|
| `governance_lab.py` | Self-contained notebook: byte map, chunk inspector, trace, ledger and executable checks |
| `governance_core.py` | Public facade; sibling `governance_*.py` modules implement the scanner, bounded state, receipt flow, worker, gateway and streaming pipeline |
| `governance_mcp.py` | Runnable FastMCP stdio server |
| `mcp_tools.json` | Tool schemas obtained from the running MCP server |
| `governance.sysml` | Functional SysML v2 calculations, guarded action flow, lifecycle, ports, connected parts and requirements |
| `sysml_validation.json` | Validator version and diagnostic counts |
| `test_*.py` | Unit, negative, behavioral, boundary, integration, API and functional-model tests |
| `run_vv.py`, `run_vv.py.lock`, `VV_report.md` | Reproducible complete V&V campaign and qualification limits |
| `vv_crossjoin_pytest.py`, `vv_crossjoin_unittest.py` | Separate suites refuting applicable finite Cartesian findings |
| `vv_evidence/assurance.sqlite`, `assurance.sql` | Normalized SQL tables, views, actual outcomes and full replayable dump |
| `vv_evidence/dataframes/`, `sql_dataframes.py` | Complete CSV tables/views and a loader for real pandas DataFrames |
| `vv_catalog.json`, `vv_mutation_catalog.json` | Declared invariants/challenges/gates and executable semantic-fault catalog |
| `check_mcp.py` | Real client/server stdio round-trip check |
| `agent_hooks.md` | Agent instruction template and enforcement boundary |
| `uplift_assessment.md` | Source comparisons, reuse decisions, changes and measured tradeoffs |
| `reference_review.json` | Exact supplied archive and inspected-source hashes |
| `benchmark.py`, `benchmark_results.json` | Reproducible before/after measurements with source identities |
| `baseline/governance_core.py` | Preserved original kernel implementation for parity and benchmarks |

The notebook's download bundle contains the engine and model artifacts; use marimo's notebook download for the notebook source. The complete workspace ZIP includes both.

## CDC contract

`RabinCDCScanner.feed(bytes)` yields immutable byte chunks with byte offsets. `finish()` emits any nonempty final tail. Concatenating chunk bytes exactly recovers the input, including Unicode split inside a code point. `chunk_stream(text)` is a convenience API for a fresh scanner and returns byte chunks.

The algorithm is **Rabin–Karp style polynomial hashing**, using base 256 and field modulus `2**61 - 1`. It is not a GF(2) Rabin fingerprint. Removing the outgoing byte and adding the incoming byte takes constant work per byte, with a precomputed removal factor. The rolling window continues across cuts.

A content cut occurs when `fingerprint % nominal_interval == 1`, after a minimum chunk size. A maximum size forces a cut. Defaults: 64-byte window, nominal interval 512 bytes, minimum 128 bytes, maximum 2048 bytes. The nominal interval is approximate; structured or repetitive input can produce a very different observed mean. The final tail may be shorter than the minimum. CDC grants no integrity assurance.

```python
from governance_core import RabinCDCScanner

scanner = RabinCDCScanner(block_size_target=4096)
chunks = [*scanner.feed("agent log · 東京".encode()), *scanner.finish()]
assert b"".join(c.data for c in chunks).decode() == "agent log · 東京"
```

## Ledger and handoff contract

Two addresses have distinct jobs:

```text
payload_hash = SHA256(UTF8(payload))
block_hash   = SHA256(UTF8(canonical_json({
    version, session_id, sequence_number, source_agent,
    target_entity, payload_hash, previous_hash
})))
```

Canonical JSON uses sorted keys, compact separators, and unescaped Unicode. There is no Unicode normalization. Timestamps and the optional expected-payload assertion are excluded from block identity. Scanner bytes travel in base64 inside canonical JSON, so raw-byte chunk digests differ from ledger payload digests.

`previous_hash` names the last **verified block hash**. Genesis uses sequence `0` and 64 zeroes. Every subsequent sequence must be exactly `head + 1`. Missing predecessors, skipped or repeated sequences, wrong parents, and asserted payload mismatches quarantine the session. Records are never overwritten.

Submission uses `put_nowait` and returns a pending token after schema checks. Full queues or pending-byte budgets return `busy`; retry the same unaccepted frame without advancing sequence. Malformed frames return `rejected`. Availability failures and record/session capacity exhaustion return `held`. Integrity mismatches quarantine the session. A pending token never authorizes ingestion. The gateway serializes outbound messages through an engine-owned lock for each session. Direct MCP writers must still coordinate the session sequence.

Verification requires a live, open, unheld engine both before and after hashing. Unexpected verifier faults produce HOLD; cancellation clears receiver context and holds the session. Concurrent close callers share one shielded cleanup task, so cancellation of one caller cannot abandon cleanup. Already admitted queue work may drain during close, while new verification is denied.

Small SHA-256 jobs run inline. Payloads at least 64 KiB use one thread job for payload and block hashing; verification uses the same cutoff. This is a tunable engineering policy, not a real-time guarantee. The worker yields every 32 items. Receipt waits target a single future, not the entire queue, and waiter cancellation cannot cancel the underlying receipt. Queue age counts against an absolute monotonic transaction deadline, checked before and after hashing.

```python
import asyncio
from governance_core import AgentGateway, GovernanceLedgerEngine

async def main():
    engine = await GovernanceLedgerEngine().start()
    try:
        sender = AgentGateway(engine, "mission-1", "planner")
        receiver = AgentGateway(engine, "mission-1", "executor")
        envelope = await sender.wrap_output("review evidence", "executor")
        print(await receiver.execute_verified(envelope, lambda text: {"accepted": text}))
    finally:
        await engine.close()

asyncio.run(main())
```

The UI retains its bounded input/chunk table for inspection. `stream_to_ledger` provides the reusable incremental path: an async packet source feeds a bounded staging queue while a sequencer commits chunks. A TaskGroup cancels sibling tasks on failure, sources close on every exit, and no full stream is retained. Callbacks receive verified outbound envelopes; receiving tools still use `AgentGateway.execute_verified`. No callback retry is automatic. See `test_stream.py` for a complete example. An OS-thread producer needs an event-loop bridge because `asyncio.Queue` is not thread-safe.

### Default resource and time budgets

| Resource | Bound / behavior |
|---|---|
| Ledger queue | 64 waiting items and 4,000,000 pending payload bytes |
| Individual payload | 2,000,000 UTF-8 bytes; reject before oversized allocation when character count already exceeds the bound |
| Verified ledger | 10,000 records; capacity refuses admission without evicting records |
| Sessions | 1,024 total; quarantined/held sessions keep their slot |
| Receipt / diagnostic event caches | 1,024 each; old receipt tokens become `unknown`, never verified by default |
| Concurrent payload verifications | 8 and 4,000,000 encoded bytes; overload returns HOLD |
| Gateway working references | 64 entries |
| Transaction / hash / receipt wait / shutdown | 10 / 5 / 10 / 5 seconds |
| Streaming staging / packet / overall deadline | 8 chunks / 64 KiB / 30 seconds |

No automatic reopening clears a HOLD or quarantine. Preserve evidence and reconcile through a host adapter before resuming. A record/session-capacity admission refusal does not cancel already accepted work. `health()` exposes current use, limits, worker state, evictions and hash-job counts. Deadlines bound acceptance/release; Python cannot forcibly stop an already running hash thread. The scanner remains cooperative between admitted packets. Hard real-time/static-memory qualification is not claimed.

## MCP server

Extract the complete bundle, keeping all `governance_*.py` modules together:

```bash
uv run governance_mcp.py
```

The PEP 723 header installs `mcp[cli]>=1.26,<2`. Sequence numbers use a strict integer schema: boolean values, numeric strings and floats are rejected before admission. Example host configuration:

```json
{
  "mcpServers": {
    "streaming-governance": {
      "command": "uv",
      "args": ["run", "/absolute/path/to/governance_mcp.py"]
    }
  }
}
```

| Tool | Result |
|---|---|
| `governance_commit_state` | Accepts the original six fields plus optional `expected_hash`; returns a tracking token |
| `governance_get_status` | Poll for verified/quarantined; verified includes payload and block digests |
| `governance_verify_payload` | Content must match a hash already committed to this engine |
| `governance_verify_handoff` | Also verifies committed metadata, block hash, expected session and intended recipient |
| `governance_get_health` | Read worker, resource, cache-retention and hash-job observations; no execution authority |

A matching self-claimed, unregistered hash fails. Full handoff verification is required because content verification alone does not establish routing or lineage. On integrity failure the caller must halt, clear working context, and emit System Quarantine. Availability loss returns HOLD and grants no trust. The Python gateway demonstrates enforcement; see [agent_hooks.md](../agent_hooks.md).

## Functional SysML v2 model

Open [governance.sysml](../governance.sysml) in a SysML v2 textual-modeling tool. It uses only the standard `ScalarValues` library and includes typed messages, sequence/boundary/trust calculations, a guarded verify → append → gate → execute action flow, quarantine branches, a transaction state machine, connected scanner/sequencer/queue/worker/boundary parts, and requirements with satisfaction mappings.

SHA-256, scanning, persistence and tool invocation are allocated implementation actions. The model defines their interfaces and sequencing; Python executes the implementations. Importing the model alone does not launch a worker or compute cryptographic hashes.

| Requirement | Model behavior | Executable evidence |
|---|---|---|
| `CDC-01` | `StreamingCDC`, `BoundaryEligible` | Lossless reconstruction and packetization tests |
| `GOV-01` | `SequenceMatches`, worker verification | Missing/wrong parent, duplicate and immutable-record tests |
| `GOV-02` | `VerifyHandoff` before `ExecuteTool` | Gateway, recipient, metadata and MCP checks |
| `GOV-03` | Quarantine action and terminal state | Halted call, cleared context, quarantine-during-hash check |
| `GOV-04` | Bounded queue and pending receipt | Saturation returns busy; queue drains |
| `GOV-05` | HOLD distinct from quarantine | Hash/worker/shutdown and receipt-timeout tests |
| `GOV-06` | Bounded retention, records never evict | Byte/session/record caps, receipt eviction and retained-ledger tests |
| `GOV-07` | Immutable snapshot before await | Baseline race reproduced; changed envelope cannot swap executed payload |
| `GOV-08` | Bounded concurrent stream | Source cleanup, sink failure, packet limit and stream-deadline tests |
| `VV-01` | One shared shutdown cleanup owner | Concurrent/cancelled close regression tests |
| `VV-02` | Functional model and complexity budgets | Boolean/boundary calculation oracles and AST structural checks |
| `VV-03` | Strict MCP transport schema | Actual stdio coercion rejection and handoff tests |
| `VV-04` | Measured resource ceilings | Speed, scaling, traced memory and matched benchmarks |
| `VV-05` | Complete retained evidence | SQL relational negative tests, CROSS JOIN suites and supersession links |

Validate syntax and references (Node.js 20+):

```bash
npx --yes sysml-validate@0.50.0 governance.sysml --strict --format json --out sysml_validation.json
```

Delivered result: **0 errors, 0 warnings**, with four nonblocking hints counted by the tool. This is model validation, not a proof of implementation equivalence or a complete behavior simulation.

## Verification and SQL findings

```bash
uv run --locked run_vv.py
uv run sql_dataframes.py
```

The campaign uses the delivered `run_vv.py.lock` for pytest/coverage/MCP dependencies, runs unit and regression checks, executes separate pytest and unittest Cartesian-refutation suites, runs isolated semantic mutations, ingests real results into SQLite, and exports every table/view to CSV plus a replayable SQL dump. Run it from the extracted project with its historical evidence folders intact.

Delivered checks: **60 unit/API/model/evidence tests, 33 dedicated pytest checks and 33 dedicated unittest checks pass (126 executions)**. All **26 selected semantic mutants are killed by actual assertions**. The initial survivor and the superseding assertion that kills the identical mutant are retained. The functional model has 256 Boolean gate vectors and additional boundary-calculation vectors checked against independent oracles. Coverage is scoped to in-process reusable runtime modules; the real MCP subprocess has separate functional API evidence.

The actual SQL operation is in `vv_cross_join.sql`:

```sql
FROM invariants i CROSS JOIN failure_modes f CROSS JOIN gates g
```

It retains **7,488 tuples** (18 invariants × 32 challenges × 13 gates). **32 applicable obligations are satisfied by both frameworks**; the other 7,456 tuples retain explicit OUT_OF_SCOPE reasons. They are not counted as passing tests or proof of safety. Each out-of-scope row links to both whole-catalog scope audits, which check its declared applicability; `finding_evidence.evidence_kind` distinguishes these audits from behavioral contracts. This finite catalog does not enumerate all inputs, interleavings, threats or simultaneous fault combinations.

[VV_report.md](../VV_report.md) links requirements, field-level assertions, actual failures, superseding passing runs and mutation evidence. Six original regression failures remain visible alongside the fixes. A passing run of the same unchanged regression test supersedes its failing run; no requirement or failing test is discarded. [assurance.sqlite](../vv_evidence/assurance.sqlite) and [cross_report.csv](../vv_evidence/dataframes/cross_report.csv) contain the full report.

The live notebook retains all nine flows and additional control checks, plus an SQL findings browser and evidence downloads. Runtime structural budgets are 180 physical lines per module, 60 per function, decision nesting at most 5 and a documented AST branch proxy at most 20. These checks are not full HMT policy admission.

Run the matched benchmark with `python benchmark.py`. Exact chunk/fingerprint and final ledger-hash equality are hard gates. Timing differences are environment-scoped observations. See `benchmark_results.json` for samples, source hashes and environment, and `uplift_assessment.md` for interpretation.

## Deployment boundary

This is an in-memory reference. Frozen records and a read-only public view prevent accidental API mutation; they are not durable or independently trusted storage. Restart loses records. Diagnostic rings are not custody journals. The authoritative record limit stops growth; sustained workloads need a governed durable-store/archive adapter. A new empty engine is not recovery of a previous ledger or authorization to repeat effects.

SHA-256 establishes integrity against a recorded address, not agent identity or truth. Production use needs authenticated writers, durable append-only storage, signed or externally checkpointed roots, and a real execution gateway. Clearing context here clears a Python list, not an LLM's previously consumed context. Valid handoff redelivery is not exactly-once execution; tools with side effects need idempotency keys. The MCP server is delivered as an artifact and has not been registered in your agent host.

## References

- [marimo documentation](https://docs.marimo.io/)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [SysML v2 release and examples](https://github.com/Systems-Modeling/SysML-v2-Release)
- [sysml-validate](https://www.npmjs.com/package/sysml-validate)
