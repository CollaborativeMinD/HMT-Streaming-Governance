"""Independent unittest assertions and runner, without pytest dependencies."""
import hashlib
import json
import time
import unittest

from vv_common import ROOT, catalog, connect_readonly, obligations, source_seal
from vv_probes import probe


class CartesianRefutationTests(unittest.IsolatedAsyncioTestCase):
    def test_cartesian_set_and_links(self):
        with connect_readonly() as db:
            missing = db.execute('''SELECT i.id,f.id,g.id FROM invariants i CROSS JOIN failure_modes f
              CROSS JOIN gates g EXCEPT SELECT invariant_id,failure_id,gate_id FROM cross_findings''').fetchall()
            extra = db.execute('''SELECT invariant_id,failure_id,gate_id FROM cross_findings EXCEPT
              SELECT i.id,f.id,g.id FROM invariants i CROSS JOIN failure_modes f CROSS JOIN gates g''').fetchall()
            self.assertEqual(missing, [])
            self.assertEqual(extra, [])
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute("SELECT value FROM metadata WHERE key='source_seal'").fetchone()[0], source_seal())
            self.assertEqual(db.execute("SELECT value FROM metadata WHERE key='catalog_sha256'").fetchone()[0],
                             hashlib.sha256((ROOT / 'vv_catalog.json').read_bytes()).hexdigest())
            declaration = {o['id']: (o['invariant_id'], o['failure_id'], o['gate_id'])
                           for o in catalog()['obligations']}
            uncovered = db.execute('''SELECT r.id FROM requirements r WHERE NOT EXISTS
              (SELECT 1 FROM assertions a WHERE a.requirement_id=r.id)''').fetchall()
            self.assertEqual(uncovered, [])
            for row in db.execute('SELECT * FROM cross_findings'):
                self.assertGreater(len(row['scope_reason']), 0)
                triple = (row['invariant_id'], row['failure_id'], row['gate_id'])
                if row['obligation_id'] is None:
                    self.assertNotIn(triple, declaration.values(), f"scope row {row['id']}")
                else:
                    self.assertIn(row['obligation_id'], declaration)
                    self.assertEqual(triple, declaration[row['obligation_id']], f"owner row {row['id']}")


def make_test(obligation):
    async def test(self):
        observation = await probe(obligation['case_name'])
        for field, expected in json.loads(obligation['expected_json']).items():
            self.assertIn(field, observation, obligation['rationale'])
            self.assertEqual(observation[field], expected,
                             f"{obligation['id']} field {field}; {obligation['rationale']}")
        path = ROOT / 'vv_evidence' / 'observations_unittest'
        path.mkdir(exist_ok=True)
        (path / f"{obligation['id']}.json").write_text(json.dumps(observation, indent=2) + '\n')
    return test


for _obligation in obligations():
    setattr(CartesianRefutationTests, f"test_{_obligation['id']}_{_obligation['case_name']}", make_test(_obligation))


class EvidenceResult(unittest.TextTestResult):
    def startTest(self, test):
        super().startTest(test)
        self.started = time.perf_counter()
        self.records = getattr(self, 'records', [])

    def record(self, test, status, detail=''):
        self.records.append(dict(nodeid=test.id(), status=status, detail=detail,
                                 duration=time.perf_counter() - self.started))

    def addSuccess(self, test):
        super().addSuccess(test)
        self.record(test, 'PASS')

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.record(test, 'FAIL', self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self.record(test, 'ERROR', self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.record(test, 'SKIP', reason)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(CartesianRefutationTests))
    (ROOT / 'vv_evidence' / 'crossjoin_unittest_results.json').write_text(json.dumps(result.records, indent=2) + '\n')
    raise SystemExit(not result.wasSuccessful())
