"""Replay the durable campaign from the verified archive using a new owned lab."""
import os
import subprocess
import tempfile
import zipfile
import shutil
from pathlib import Path
from ci.package_release import ROOT,PREFIX,verify_archive


def main():
    (archive,)= (ROOT/'dist').glob('*.zip');verify_archive(archive)
    with tempfile.TemporaryDirectory(prefix='hmt-') as folder:
        with zipfile.ZipFile(archive) as source:source.extractall(folder)
        checkout=Path(folder)/PREFIX
        output=ROOT/'.ci-output/durable-clean-replay.log'
        env=os.environ.copy();env['PYTHONPATH']=str(checkout/'service')
        bridge=Path(folder)/'bridge';env['HMT_BRIDGE']=str(bridge)
        def run(*args):
            with output.open('a') as log:
                subprocess.run(args,cwd=checkout,env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=300)
        try:
            run('go','-C','tailcat-gateway','test','./...')
            build_env=env|{'CGO_ENABLED':'0'}
            subprocess.run(['go','-C','tailcat-gateway','build','-trimpath','-o',str(bridge),'.'],cwd=checkout,env=build_env,check=True,timeout=180)
            run(str(ROOT/'node_modules/.bin/sysml-validate'),'service/model/durable-governance.sysml','--strict','--format','json','--out','service/evidence/sysml-validation.json')
            run('uv','run','--locked','--project','service','python','service/lab.py','start')
            run('uv','run','--locked','--project','service','python','service/headscale_lab.py')
            run('uv','run','--locked','--project','service','python','service/run_vv.py')
            run('uv','run','--locked','--project','service','python','-m','vv.verify')
            run('uv','run','--locked','--project','service','python','service/vv/secret_inventory.py')
            shutil.copytree(checkout/'service/evidence',ROOT/'service/evidence',dirs_exist_ok=True)
        finally:
            if (checkout/'service/.lab/manifest.json').exists():
                run('uv','run','--locked','--project','service','python','service/lab.py','stop')
        (ROOT/'.ci-output/durable-clean-replay.json').write_text('{"status":"PASS","scope":"Complete durable local campaign after manifest-verified clean extraction; four HOLDs retained"}\n')
        print('Durable archive clean replay PASS')
if __name__=='__main__':main()
