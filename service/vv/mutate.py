"""Selected source mutants, executed in isolated copies. Only assertion failures count as kills."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MUTANTS=[
 ('M01','contracts.py','type(value["sequence_number"]) is not int','not isinstance(value["sequence_number"], int)','test_boundaries.py::test_sequence_boundaries_reject_before_storage'),
 ('M02','contracts.py','< 2**53','<= 2**53','test_boundaries.py::test_sequence_boundaries_reject_before_storage'),
 ('M03','contracts.py','len(value["payload"].encode()) > max_payload','False','test_boundaries.py::test_utf8_payload_byte_boundary_and_no_surrogate'),
 ('M04','contracts.py','if depth > 32:','if depth > 33:','test_boundaries.py::test_json_exact_size_depth_and_quoted_braces'),
 ('M05','contracts.py','if key in result:','if False:','test_boundaries.py::test_json_invalid_cases'),
 ('M06','contracts.py','if len(raw) > limit:','if len(raw) >= limit:','test_boundaries.py::test_json_exact_size_depth_and_quoted_braces'),
 ('M07','records.py','or row["payload_hash"] != expected["payload_hash"]','or False','test_boundaries.py::test_authenticated_cache_metadata_mismatch_falls_back_to_postgres'),
 ('M08','authority.py','role not in row["roles"]','False','test_ledger.py::test_ungranted_route_and_missing_sender_role_are_denied'),
 ('M09','effects.py','if actor["agent_id"] != session["target_agent"]:','if False:','test_effects.py::test_forged_external_witness_cannot_commit_success'),
 ('M10','runner.py','or witness.get("block_hash") != operation["block_hash"]','or False','test_boundaries.py::test_bound_external_witness_contract'),
]

def run():
    directory=ROOT/'evidence/mutations';directory.mkdir(exist_ok=True)
    rows=[]
    tests=sorted({m[4] for m in MUTANTS})
    env=os.environ.copy();env['PYTHONPATH']=str(ROOT)+os.pathsep+str(ROOT/'src')
    baseline=subprocess.run([sys.executable,'-m','pytest','-q',*[str(ROOT/'tests'/x) for x in tests],
        '--junitxml='+str(directory/'positive-control.xml')],cwd=ROOT,env=env,capture_output=True,text=True,timeout=60)
    if baseline.returncode!=0:
        raise RuntimeError('Mutation positive control failed; no scores generated')
    for identifier,file,before,after,test in MUTANTS:
        with tempfile.TemporaryDirectory() as temporary:
            source=Path(temporary)/'hmt_governance';shutil.copytree(ROOT/'src/hmt_governance',source,ignore=shutil.ignore_patterns('__pycache__'))
            path=source/file;text=path.read_text()
            if text.count(before)!=1:raise RuntimeError(identifier+' source anchor drift')
            path.write_text(text.replace(before,after))
            env['PYTHONPATH']=temporary+os.pathsep+str(ROOT)
            output=directory/(identifier+'.xml')
            result=subprocess.run([sys.executable,'-m','pytest','-q',str(ROOT/'tests'/test),'--junitxml='+str(output)],
                cwd=ROOT,env=env,capture_output=True,text=True,timeout=60)
            tree=ET.parse(output)
            failures=tree.findall('.//failure');errors=tree.findall('.//error')
            killed=result.returncode==1 and failures and not errors and all('AssertionError' in (x.text or '') or 'Failed: DID NOT RAISE' in (x.text or '') for x in failures)
            rows.append(dict(id=identifier,file=file,mutation=f'{before} -> {after}',test='service/tests/'+test,
                result='KILLED' if killed else 'SURVIVED_OR_INVALID',failure_count=len(failures),error_count=len(errors),exit_code=result.returncode))
    report=dict(scope='Ten selected Python semantic mutants; not exhaustive mutation coverage; SQL guards and upstream dependencies are not mutated',
        positive_control='PASS',mutants=rows,killed=sum(x['result']=='KILLED' for x in rows),total=len(rows))
    (directory/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    assert report['killed']==report['total'],report
    print(json.dumps(dict(killed=report['killed'],total=report['total'])))
if __name__=='__main__':run()
