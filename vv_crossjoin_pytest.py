"""Dedicated pytest refutation suite; failures remain failures in the SQL report."""
import asyncio
import hashlib
import json

import pytest

from vv_common import ROOT, catalog, connect_readonly, obligations, source_seal
from vv_probes import probe

OBLIGATIONS = obligations()


@pytest.mark.parametrize('obligation', OBLIGATIONS, ids=[o['id'] for o in OBLIGATIONS])
def test_refute_finding(obligation):
    observed = asyncio.run(probe(obligation['case_name']))
    expected = json.loads(obligation['expected_json'])
    for field, value in expected.items():
        assert field in observed, f"{obligation['id']}: missing observation {field}"
        assert observed[field] == value, f"{obligation['id']} {field}: {observed}; {obligation['rationale']}"
    # Persist observed measurements separately from the assertion outcome (JUnit).
    from pathlib import Path
    path = Path(__file__).parent / 'vv_evidence' / 'observations_pytest'
    path.mkdir(exist_ok=True)
    (path / f"{obligation['id']}.json").write_text(json.dumps(observed, indent=2) + '\n')


def test_complete_cartesian_and_traceability():
    declared = catalog()
    with connect_readonly() as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert list(db.execute('PRAGMA foreign_key_check')) == []
        expected_count = len(declared['invariants']) * len(declared['failure_modes']) * len(declared['gates'])
        assert db.execute('SELECT COUNT(*) FROM cross_findings').fetchone()[0] == expected_count
        assert db.execute('SELECT COUNT(*) FROM cross_findings WHERE obligation_id IS NOT NULL').fetchone()[0] == len(OBLIGATIONS)
        assert db.execute("SELECT COUNT(*) FROM cross_findings WHERE length(scope_reason)=0").fetchone()[0] == 0
        assert db.execute("SELECT value FROM metadata WHERE key='source_seal'").fetchone()[0] == source_seal()
        assert db.execute("SELECT value FROM metadata WHERE key='catalog_sha256'").fetchone()[0] == hashlib.sha256(
            (ROOT / 'vv_catalog.json').read_bytes()).hexdigest()
        expected_owners = {(o['invariant_id'], o['failure_id'], o['gate_id']): o['id']
                           for o in declared['obligations']}
        actual_owners = {(r['invariant_id'], r['failure_id'], r['gate_id']): r['obligation_id']
                         for r in db.execute('SELECT * FROM cross_findings WHERE obligation_id IS NOT NULL')}
        assert actual_owners == expected_owners, 'SQL applicability differs from the declared threat/owner relation'
        for row in db.execute('SELECT * FROM cross_findings WHERE obligation_id IS NULL'):
            assert (row['invariant_id'], row['failure_id'], row['gate_id']) not in expected_owners, row['id']
        for row in db.execute('SELECT id FROM requirements'):
            assert db.execute('SELECT COUNT(*) FROM assertions WHERE requirement_id=?', (row[0],)).fetchone()[0] > 0
        for obligation in OBLIGATIONS:
            assert db.execute('SELECT COUNT(DISTINCT framework) FROM assertions WHERE obligation_id=?',
                              (obligation['id'],)).fetchone()[0] == 2
