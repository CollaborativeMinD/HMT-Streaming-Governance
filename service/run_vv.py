"""Complete local durable-profile qualification. Requires an owned lab and compiled native bridge."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent

def run(*command):
    subprocess.run(command,cwd=ROOT,env=os.environ,check=True)

def main():
    if not os.environ.get("HMT_BRIDGE") or not Path(os.environ["HMT_BRIDGE"]).is_file():
        raise SystemExit("Set HMT_BRIDGE to the built native bridge before qualification")
    import fcntl
    guard = (ROOT/'.lab/qualification.lock').open('a')
    try:
        fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('Another qualification controller owns this lab')
    os.environ['PYTHONPATH']=str(ROOT)+os.pathsep+str(ROOT/'src')
    sys.path.insert(0,str(ROOT))
    from vv.report import sources
    import time,uuid
    before=sources()
    (ROOT/'evidence/run-start.json').write_text(json.dumps(dict(run_id=str(uuid.uuid4()),started_at=time.time(),source_manifest=before),indent=2)+'\n')
    run(sys.executable,'-m','pytest','tests','-q','--junitxml=evidence/final-pytest.xml')
    run(sys.executable,'-m','vv.unittest_runner')
    run(sys.executable,'-m','vv.mutate')
    run(sys.executable,'-m','vv.cross')
    run(sys.executable,'-m','vv.go_audit')
    if sources()!=before:
        raise SystemExit('Source changed during qualification; evidence cannot be admitted')
    run(sys.executable,'-m','vv.report')
    guard.close()
if __name__=='__main__':main()
