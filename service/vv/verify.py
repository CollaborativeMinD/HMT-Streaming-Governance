"""Fail closed on stale sources, incomplete replays, invalid mutations or unresolved local obligations."""
import hashlib
import json
from .report import ROOT,sources,tests

def main():
    expected=json.loads((ROOT/'evidence/source-manifest.json').read_text())
    assert sources()==expected,'Durable source/evidence binding is stale'
    assert json.loads((ROOT/'evidence/run-start.json').read_text())['source_manifest']==expected
    summary=json.loads((ROOT/'evidence/summary.json').read_text())
    assert summary['source_seal']==hashlib.sha256(json.dumps(expected,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    assert summary['cartesian_rows']==9216 and summary['executable']==28 and summary['holds']==4
    assert sum(r['status']=='SATISFIED_LOCAL' for r in summary['requirements'])==28
    assert sum(r['status']=='HOLD' for r in summary['requirements'])==4
    assert len(tests(ROOT/'evidence/final-pytest.xml'))==summary['pytest_executions']
    assert len(tests(ROOT/'evidence/unittest.xml'))==summary['unittest_executions']
    mutations=json.loads((ROOT/'evidence/mutations/summary.json').read_text())
    assert mutations['positive_control']=='PASS' and len(mutations['mutants'])==10
    assert all(r['result']=='KILLED' and r['error_count']==0 for r in mutations['mutants'])
    print('Durable local qualification evidence is complete and source-bound; four deployment HOLDs retained')
if __name__=='__main__':main()
