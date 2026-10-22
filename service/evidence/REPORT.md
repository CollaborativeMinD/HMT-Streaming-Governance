# Durable governance qualification

86 pytest and 29 unittest executions passed; 10/10 selected Python mutants killed.

SQL CROSS JOIN: 9,216 rows; 28 executable requirements satisfied locally, four HOLD obligations, 9,184 declared OUT_OF_SCOPE combinations.

The separate suites execute the linked runtime challenges. Scope audits do not turn unrelated tuples into behavioral passes. Finite catalog enumeration is not exhaustive security proof.

| Requirement | Result | Behavioral assertion |
|---|---|---|
| D01 | SATISFIED_LOCAL | [test](../tests/test_ledger.py) |
| D02 | SATISFIED_LOCAL | [test](../tests/test_ledger.py) |
| D03 | SATISFIED_LOCAL | [test](../tests/test_ledger.py) |
| D04 | SATISFIED_LOCAL | [test](../tests/test_ledger.py) |
| I01 | SATISFIED_LOCAL | [test](../tests/test_ledger.py) |
| I02 | SATISFIED_LOCAL | [test](../tests/test_ledger.py) |
| I03 | SATISFIED_LOCAL | [test](../tests/test_ledger.py) |
| A01 | SATISFIED_LOCAL | [test](../tests/test_ledger.py) |
| A02 | SATISFIED_LOCAL | [test](../tests/test_ledger.py) |
| A03 | SATISFIED_LOCAL | [test](../tests/test_ledger.py) |
| A04 | SATISFIED_LOCAL | [test](../tests/test_ledger.py) |
| A05 | SATISFIED_LOCAL | [test](../tests/test_ledger.py) |
| D05 | SATISFIED_LOCAL | [test](../tests/test_ledger.py) |
| B01 | SATISFIED_LOCAL | [test](../tests/test_ledger.py) |
| C01 | SATISFIED_LOCAL | [test](../tests/test_boundaries.py) |
| C02 | SATISFIED_LOCAL | [test](../tests/test_boundaries.py) |
| E01 | SATISFIED_LOCAL | [test](../tests/test_effects.py) |
| E02 | SATISFIED_LOCAL | [test](../tests/test_effects.py) |
| E03 | SATISFIED_LOCAL | [test](../tests/test_effects.py) |
| K01 | SATISFIED_LOCAL | [test](../tests/test_keys.py) |
| K02 | SATISFIED_LOCAL | [test](../tests/test_keys.py) |
| K03 | SATISFIED_LOCAL | [test](../tests/test_keys.py) |
| N01 | SATISFIED_LOCAL | [test](../tests/test_native.py) |
| N02 | SATISFIED_LOCAL | [test](../tests/test_native.py) |
| N03 | SATISFIED_LOCAL | [test](../tests/test_boundaries.py) |
| B02 | SATISFIED_LOCAL | [test](../tests/test_boundaries.py) |
| B03 | SATISFIED_LOCAL | [test](../tests/test_boundaries.py) |
| B04 | SATISFIED_LOCAL | [test](../tests/test_boundaries.py) |
| H01 | HOLD | Hostile database-owner rollback requires an independent monotonic checkpoint anchor |
| H02 | HOLD | On-prem WAN/NAT, partitions and reboot fencing need target-host qualification |
| H03 | HOLD | Production PKI, multi-share unseal and workload-token enrollment need operator bootstrap |
| H04 | HOLD | Rapid repeated one-shot Tailcat reconnection is not yet reliable in the local fixture; use persistent sessions |

Runtime fixes, fixture amendments and unresolved reconnect findings are distinguished in [supersessions](cross/supersessions.csv). The initial mutation run killed 8/10: M06 needed an explicit positive-boundary assertion, and M10 was masked by the independent SQL witness guard. The added direct witness-contract test kills M10 without weakening the SQL defense.

Model witnesses: strict SysML validation with zero errors/warnings; 128 release gate vectors; all 25 effect-state pairs checked against PostgreSQL triggers.

The local speed fixture includes PostgreSQL, polling and fresh receiver authorization. [Timing and traced Python memory](performance.json) exclude service RSS and WAN behavior.

Source binding: `933d0e2f32ef9d5d8da9e8d9322300e25ba3c10f21df9b2786f01a41340e6422`. [Source bytes](source-manifest.json) · [Both-framework results](summary.json) · [Mutation witnesses](mutations/summary.json).
