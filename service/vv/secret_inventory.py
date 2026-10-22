"""Check publication candidates against this disposable lab's actual credential inventory.
Never emit secret values. This complements, and does not replace, a general secret scanner.
"""
import json
import subprocess
from pathlib import Path
from urllib.parse import unquote,urlsplit
from psycopg.conninfo import conninfo_to_dict

ROOT=Path(__file__).parents[1];REPO=ROOT.parent

def main():
    lab=ROOT/'.lab';values=[]
    if (lab/'runtime.json').exists():
        runtime=json.loads((lab/'runtime.json').read_text());values+=[conninfo_to_dict(runtime['dsn'])['password'],unquote(urlsplit(runtime['redis_url']).password)]
        values += [conninfo_to_dict(json.loads((lab/'admin.json').read_text())['dsn'])['password']]
        values += [path.read_text() for path in (lab/'tls').glob('*.key')]
        values += list(json.loads((lab/'bao-admin.json').read_text()).values())
        values += [json.loads((lab/'bao-runtime.json').read_text())['token']]
        values += list(json.loads((lab/'headscale-runtime.json').read_text())['auth_keys'].values())
        for path in lab.glob('native-*/*.key'):
            values.append(json.loads(path.read_text())['private'])
        for path in lab.glob('native-*/endpoint-output/private'):
            values.append(path.read_text())
    if (REPO/'.git').exists():
        paths=subprocess.check_output(['git','ls-files','-z'],cwd=REPO).decode().split('\0')
    else:
        paths=list(json.loads((REPO/'MANIFEST.json').read_text()))
    bad=[]
    for name in paths:
        if not name:continue
        path=REPO/name
        if path.is_file() and any(value.encode() in path.read_bytes() for value in values if value):bad.append(name)
    assert not bad,'Lab credential appears in staged publication candidate: '+repr(bad)
    result=dict(status='PASS',inventoried_credentials=len(values),candidate_files=sum(bool(n) for n in paths),
        scope='Exact known disposable lab secrets versus tracked publication bytes; general Gitleaks scan is separate')
    (ROOT/'evidence/secret-inventory.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))
if __name__=='__main__':main()
