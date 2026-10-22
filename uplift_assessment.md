# Uplift assessment

The original notebook is retained as an environment-appropriate lab. The uplift preserves its CDC and ledger wire identities while improving the reusable engine's speed, bounded memory, asynchronous scheduling, and failure behavior. The notebook embeds the same runtime sources, while the downloadable implementation separates responsibilities into small modules.

## Comparison with your existing work

The five supplied archives were hashed and inspected. Exact identities and inspected members are recorded in `reference_review.json`. These are source comparisons; upstream PASS labels and historical qualification campaigns are not new runtime evidence for this composition. No attached package was modified or silently activated.

| Reference | Close or adjacent behavior | Stronger than the first notebook | Adopted here / boundary |
|---|---|---|---|
| Watchdog 1.2, inside Unified Mission Assurance | Payload/code identity, hash-linked custody and replay | HMAC-authenticated custody, fsync journal, SQLite, replay expiry, admitted callable identity | Immutable input snapshot and fail-closed release. Signed custody remains a host integration seam; no new competing durable ledger was created. |
| Fallback Chains 1.0.0 | Ordered execution paths, validation, bounded cache, deadlines | Explicit retry ownership, terminal vs transient reasons, output validation, durable path receipts | Availability HOLD is distinct from integrity quarantine. No automatic tool fallback: this gateway can protect effects, while the supplied fallback partition admits pure/read-only work. |
| HMT Continuity SoS 0.1.0 | Global budgets, scoped containment and composition | Persistent attempt reservations, policy/deadline binding, abandoned operations become UNKNOWN, authenticated reconciliation and idempotent memory outbox | Pending bytes and verification slots share engine limits; queue age counts; uncertain/unavailable work does not grant trust. Tenant/mission breakers and restart reconciliation remain upstream responsibilities. |
| Bounded Performance Orchestration 1.0.0, inside optimization engines | Structured async work and resource admission | Shared capacity registry, TaskGroup cleanup, global deadlines, vector/chunk/scalar strategy selection and parity receipts | TaskGroup streaming with bounded staging, adaptive hash scheduling, sibling cancellation and explicit cleanup. NumPy vectorization does not fit this sequential rolling recurrence; no algorithm substitution was needed. |
| Timing & Abortability / Information Airlock field books | Freshness-at-use, exposure windows, bounded temporal state | Clearly separates capture/use/effect time and diagnostic metrics from authority | Recheck transaction deadlines after hashing. Bounded event rings are diagnostic observations; never treat cache presence, throughput, entropy, or process health as execution authority. |
| Policy bundle 2.0.0 and protected optimization lane | Immutable sources, explicit evidence, bounded complexity | Source/policy admission and evidence authority exceed this local notebook's scope | Preserve the baseline, hash the candidate sources, hard-gate semantic parity, keep runtime modules <=180 lines and functions <=60. No claim of admission by the frozen HMT optimization gate or hosted CI. |

Your reference stack is more complete for durable custody, authenticated identity, scoped recovery and deployment admission. This uplift is smaller and directly suited to a byte-stream lab and portable Python engine. No cross-stack throughput ranking is claimed. Watchdog's journal verification/storage path, subprocess fallback providers, and this in-memory engine perform different work, so timing them as interchangeable implementations would be misleading.

For production composition, prefer the existing Watchdog/SoS host adapters for custody and authority. Supply external keys, authenticated scope, admitted policy and restart reconciliation there. The CDC and envelope encoding can remain a data-plane component. Do not wrap an arbitrary effectful agent as a read-only fallback provider, duplicate retry ownership, or treat an in-memory block hash as signed custody.

## Implemented changes

The rolling scanner uses a precomputed 256-entry removal table, local loop bindings, one modular reduction per byte, a mask for power-of-two boundary moduli, and byte-range copies only at cuts or packet end. It retains exact base-256 / `2**61-1` fingerprints and byte boundaries, including non-power-of-two boundary intervals. Frozen slotted records reduce object overhead.

Small hashes stay inline; larger payload and block hashes share one thread job. This avoids scheduling two tiny worker jobs for every chunk. The 64 KiB cutoff is configurable. Periodic cooperative yields and bounded verification admission constrain event-loop and executor pressure. An encoded payload is reused for admission and worker hashing.

Receipt waits now await the requested transaction, independently of unrelated queued sessions. Waiter cancellation is shielded from receipt completion. Session locks serialize concurrent outbound writers before reading the predecessor. Shutdown, worker failure, hash timeout, transaction deadline, receipt deadline, record/session capacity, and verifier saturation have explicit non-trusting outcomes.

The gateway snapshots transaction metadata before any await and invokes the tool with the immutable payload from that snapshot. The old mutable-envelope race was reproduced: changing the caller-owned envelope while verification paused made the original gateway's fixture tool receive unverified replacement bytes. The regression now proves that the tool receives only the verified snapshot.

Receipt caches reference existing frozen records rather than duplicating record dictionaries. Receipt, event, session, pending-byte, verification-byte and working-context state all have limits. Old diagnostic observations can expire; verified ledger records cannot be silently discarded. The last processed payload is released before the worker idles.

`stream_to_ledger` adds concurrent incremental scanning and sequential commits without retaining the full input. Staging backpressure, packet limits, a whole-stream deadline, source closure and TaskGroup cancellation are explicit. A sink failure is propagated; it never replays a tool callback. The UI keeps its small chunk list for inspection.

## Measurement and evidence

| Workload | Baseline median | Uplift median | Observed ratio |
|---|---:|---:|---:|
| CDC, random 256 KiB | 103.19 ms | 72.28 ms | 1.43× |
| CDC, structured 256 KiB | 104.14 ms | 70.71 ms | 1.47× |
| 600 complete gateway iterations, 256 B | 140.92 ms | 22.29 ms | 6.32× |
| 600 complete gateway iterations, 16 KiB | 201.43 ms | 34.21 ms | 5.89× |
| 600 complete gateway iterations, 128 KiB | 266.09 ms | 248.96 ms | 1.07× |

Retained Python allocations: **4.80 MB → 1.95 MB (59.4% lower)**. These include the final V&V shutdown and verification fixes. The 128 KiB timing ranges overlap; the small median difference is not strong evidence of a large-payload throughput gain.

`benchmark.py` runs baseline and candidate on the same interpreter, with warm-up and five samples per case. The scanner processes 256 KiB in 73-byte packets using deterministic random bytes and structured repeated logs. The gateway workload runs 600 complete commit/verify/tool-gate iterations at 256 B, 16 KiB and 128 KiB. Memory uses `tracemalloc` after 3,000 committed records. It measures Python allocations, not process RSS or allocator fragmentation.

See `benchmark_results.json` for exact samples, environment, source hashes and ratios. Both scanner cases must have equal chunk/fingerprint signatures, and every ledger workload must end at the same block hash. Timings never determine correctness. Sequential measurement order, interpreter version, operating-system scheduling and a shared runner limit generalization. These are local workspace results; they are not claims about remote notebook throughput.

Memory comparisons preserve all 3,000 verified records. Part of the reduction comes from an intentional retention change: the candidate keeps 1,024 recent diagnostic events and 1,024 receipt tokens, while the baseline retained all of them. An evicted receipt is explicitly unknown. This is not lossless audit-history compression, and the event ring is not a replacement for your durable custody stack.

**The expanded V&V campaign supersedes the initial 39-test snapshot.** See `VV_report.md`, `vv_evidence/summary.json` and `vv_evidence/coverage.json` for current executed assertions, branch coverage, negative evidence, semantic mutants and requirement links. The supplied reference bundles were inspected, not requalified.

## Remaining limits

The engine is single-host, single-event-loop, and in-memory. It has no authenticated writer identity, durable restart continuity, exactly-once tool effects, signed roots, hostile-host isolation or automatic authorized recovery. The enclosing Python process and callbacks remain trusted. A deadline can deny commit/release after a late computation; it cannot forcibly terminate an already running thread or undo a started effect. Standalone content verification proves registered bytes only; complete handoff verification binds routing and lineage.

The engine does not route control decisions through the policy bundle's protected admission interface. That integration would require a concrete host policy and externally managed credentials. This phase improves and measures the authorized local implementation without claiming deployment promotion.


## Final V&V corrections

Six unchanged regression witnesses exposed verification after shutdown, a HOLD arriving during hashing, concurrent shutdown ownership, unexpected verifier failure, cancellation retaining receiver context, and MCP sequence-number coercion. The implementation now fails closed on those paths and keeps shutdown cleanup under one shielded task. Original failing sources/XML and passing runs are linked in SQL. A surviving disposition mutant also exposed an insufficient expiry assertion; the stronger deadline contract kills that same mutant and its prior test remains retained.
