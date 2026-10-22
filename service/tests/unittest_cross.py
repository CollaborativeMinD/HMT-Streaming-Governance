"""Separate unittest behavioral replay; no conversion of unresolved findings into satisfaction."""
import unittest
from vv.cross import CATALOG,GATES,execute,findings

class CrossFindings(unittest.IsolatedAsyncioTestCase):
    def test_scope_completeness(self):
        rows=findings()
        self.assertEqual(len(rows),len(CATALOG)**2*len(GATES))
        self.assertEqual(sum(bool(r['applicable']) for r in rows),len(CATALOG))
        self.assertEqual(sum(r['disposition']=='HOLD' for r in rows),4)

def case(requirement):
    async def replay(self):await execute(requirement)
    return replay
for row in CATALOG:
    if row['status']=='EXECUTABLE':
        setattr(CrossFindings,'test_'+row['requirement'],case(row['requirement']))
