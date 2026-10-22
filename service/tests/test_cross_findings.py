"""Independent pytest replay of every applicable executable SQL CROSS JOIN finding."""
import pytest
from vv.cross import CATALOG,GATES,execute,findings

@pytest.mark.parametrize('requirement',[r['requirement'] for r in CATALOG if r['status']=='EXECUTABLE'])
async def test_nullify_executable_finding(requirement):
    await execute(requirement)

def test_cartesian_scope_and_unresolved_findings_are_not_counted_as_passes():
    rows=findings()
    assert len(rows)==len(CATALOG)**2*len(GATES)
    applicable=[r for r in rows if r['applicable']]
    assert len(applicable)==len(CATALOG)
    assert all(r['test_node'] and r['source_line'] for r in applicable if r['disposition']=='EXECUTABLE')
    assert {r['requirement'] for r in applicable if r['disposition']=='HOLD'}=={'H01','H02','H03','H04'}
    assert all(r['rationale'] for r in rows if not r['applicable'])
