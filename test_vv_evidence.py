"""Negative SQL evidence tests: missing, stale and failing evidence cannot satisfy a gate."""
import sqlite3
import unittest

from vv_common import DB


class EvidenceSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        with sqlite3.connect(DB) as source:
            source.backup(self.db)
        self.db.execute('PRAGMA foreign_keys=ON')
        self.addCleanup(self.db.close)
        self.db.execute('DELETE FROM supersessions')
        self.db.execute('DELETE FROM test_results')
        self.db.execute('DELETE FROM test_runs')

    def add_evidence(self, status='PASS', stale=False, historical=False):
        seal = self.db.execute("SELECT value FROM metadata WHERE key='source_seal'").fetchone()[0]
        self.db.execute('INSERT INTO test_runs VALUES (?,?,?,?,?)',
                        ('probe', 'test', 'stale' if stale else seal, 'synthetic-negative-test-only', int(historical)))
        for aid in ('PY-C001', 'UT-C001'):
            self.db.execute('INSERT INTO test_results VALUES (?,?,?,?,?)', ('probe', aid, status, 0., 'synthetic test fixture'))

    def disposition(self):
        return self.db.execute("SELECT disposition FROM cross_report WHERE obligation_id='C001'").fetchone()[0]

    def test_missing_or_one_framework_evidence_leaves_finding_open(self):
        self.assertEqual(self.disposition(), 'OPEN')
        self.add_evidence()
        self.db.execute("DELETE FROM test_results WHERE assertion_id='UT-C001'")
        self.assertEqual(self.disposition(), 'OPEN')

    def test_failure_has_precedence_over_other_passing_assertions(self):
        self.add_evidence()
        self.db.execute("UPDATE test_results SET status='FAIL' WHERE assertion_id='UT-C001'")
        self.assertEqual(self.disposition(), 'FAILED')

    def test_stale_source_evidence_cannot_satisfy(self):
        self.add_evidence(stale=True)
        self.assertEqual(self.disposition(), 'OPEN')

    def test_historical_pass_cannot_satisfy_current_release(self):
        self.add_evidence(historical=True)
        self.assertEqual(self.disposition(), 'OPEN')

    def test_two_current_independent_frameworks_satisfy(self):
        self.add_evidence()
        self.assertEqual(self.disposition(), 'SATISFIED')

    def test_unknown_requirement_and_dangling_result_are_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute('INSERT INTO invariants VALUES (?,?,?)', ('bad', 'missing', 'must fail'))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute('INSERT INTO test_results VALUES (?,?,?,?,?)', ('missing', 'missing', 'PASS', 0., 'must fail'))
