# Verification and validation report

The SQL CROSS JOIN materializes **7,488 tuples**: 18 invariants × 32 challenges × 13 gates.

Dispositions: `{"OUT_OF_SCOPE": 7456, "SATISFIED": 32}`.

Current campaigns: **126 test executions**, outcomes `{"PASS": 126}`; semantic mutations `{"KILLED": 26}`. All 6 pre-fix regression failures retain requirement-preserving supersession links.

Each applicable obligation is exercised by separate pytest and unittest assertions. The suites share scenario drivers and the declared contract, but use their own assertion mechanisms. They are not independent implementations or external certification.

OUT_OF_SCOPE tuples remain in the report with an explicit reason. They are not passes or evidence of immunity. Every such tuple links to the pytest and unittest catalog-scope audits, which check its classification against the declared threat/owner relation. Those links are explicitly labeled CATALOG_SCOPE_AUDIT, separate from BEHAVIORAL_CONTRACT witnesses. The catalog covers declared local behavior, not all inputs, scheduler interleavings, deployment threats or combinations of simultaneous faults.

## Evidence

- [SQLite tables and views](vv_evidence/assurance.sqlite), [replayable SQL](vv_evidence/assurance.sql).
- [Every cross-joined possibility](vv_evidence/dataframes/cross_report.csv), [finding-to-test evidence](vv_evidence/dataframes/finding_evidence.csv).
- [Requirement satisfaction](vv_evidence/dataframes/requirement_evidence.csv), [field-level assertion expectations](vv_evidence/dataframes/assertion_fields.csv).
- [All supersessions](vv_evidence/dataframes/supersessions.csv), [mutation witnesses](vv_evidence/mutation_results.json).
- [Surviving-mutant assertion supersession](vv_evidence/dataframes/mutation_supersessions.csv), [initial mutation evidence](vv_evidence/mutation_initial_results.json).
- [pytest XML](vv_evidence/crossjoin_pytest_results.xml), [unittest JSON](vv_evidence/crossjoin_unittest_results.json).

## Failures and remediation

| Assertion | Requirement | Failing test → satisfying test | Evidence | Why |
|---|---|---|---|---|
| REG-01 | GOV-05 | [test_cancelled_verification_clears_context_and_holds_session](vv_evidence/pre_fix/test_vv_regressions.py) → [same unchanged test](test_vv_regressions.py) | [pre-boundary](vv_baseline_failures.xml) → [current-unit](vv_evidence/unit_results.xml) | Cancel verification: clear context and hold session before cancellation propagates. The same assertion is retained unchanged; the passing run supersedes the failing run, not the requirement. |
| REG-02 | GOV-05 | [test_closed_engine_denies_previously_verified_handoff](vv_evidence/pre_fix/test_vv_regressions.py) → [same unchanged test](test_vv_regressions.py) | [pre-boundary](vv_baseline_failures.xml) → [current-unit](vv_evidence/unit_results.xml) | Reject verification when the owning engine is closed. The same assertion is retained unchanged; the passing run supersedes the failing run, not the requirement. |
| REG-03 | VV-01 | [test_concurrent_close_has_one_shutdown_owner](vv_evidence/pre_fix/test_vv_regressions.py) → [same unchanged test](test_vv_regressions.py) | [pre-boundary](vv_baseline_failures.xml) → [current-unit](vv_evidence/unit_results.xml) | One shielded shutdown task owns cleanup for all close callers. The same assertion is retained unchanged; the passing run supersedes the failing run, not the requirement. |
| REG-04 | GOV-05 | [test_hold_during_standalone_hash_cannot_return_verified](vv_evidence/pre_fix/test_vv_regressions.py) → [same unchanged test](test_vv_regressions.py) | [pre-boundary](vv_baseline_failures.xml) → [current-unit](vv_evidence/unit_results.xml) | Recheck availability after the hashing await before returning trust. The same assertion is retained unchanged; the passing run supersedes the failing run, not the requirement. |
| REG-05 | GOV-05 | [test_unexpected_verifier_failure_holds_and_clears_context](vv_evidence/pre_fix/test_vv_regressions.py) → [same unchanged test](test_vv_regressions.py) | [pre-boundary](vv_baseline_failures.xml) → [current-unit](vv_evidence/unit_results.xml) | Classify unexpected verifier exceptions as HOLD and clear receiving context. The same assertion is retained unchanged; the passing run supersedes the failing run, not the requirement. |
| REG-06 | VV-03 | [test_strict_schema_and_real_stdio_handoff](vv_evidence/pre_api/test_vv_api.py) → [same unchanged test](test_vv_api.py) | [pre-api](vv_api_baseline_failures.xml) → [current-unit](vv_evidence/unit_results.xml) | StrictInt prevents MCP numeric coercion before the strict core parser. The same assertion is retained unchanged; the passing run supersedes the failing run, not the requirement. |

The original failure XML, full pre-fix source snapshot, source hashes, and unchanged regression assertions are retained. A failed run is superseded by a passing run of the same test after the implementation fix. No failed test is deleted or weakened.

The initial **M023** mutant survived [the prior expiry test](test_uplift.py), which checked HOLD and no commit but omitted quarantine count. [C025 in the dedicated pytest suite](vv_crossjoin_pytest.py) adds zero quarantine and zero pending bytes to the deadline contract. It kills the identical mutant: [initial survivor](vv_evidence/mutations_initial/M023.txt) → [superseding assertion failure on the mutant](vv_evidence/mutations/M023.txt). The prior test remains in the suite; the SQL mutation_supersessions table records the requirement and explanation.

## Qualification limits

Mutation results cover a hand-selected set of semantic faults with count-checked edits and a passing control. A kill requires an executed AssertionError; syntax/import errors and timeouts do not count as kills. This is not exhaustive mutation coverage.

Speed gates are local catastrophic-regression ceilings: each 512 KiB scan <5 seconds, median doubling ratio <4, 1,500 ledger commits <10 seconds, traced peak <6 MiB with 64-entry diagnostic caches. Measurements and platform/source provenance are included. They are not production throughput SLAs.

The ledger is in-memory and does not authenticate an agent identity. Hashes establish integrity and lineage relative to this process. Persistence, host authorization, effect reconciliation, and production key management remain deployment responsibilities. Synchronous callback effects are not retried; an exception or cancellation does not establish whether an external effect occurred.
