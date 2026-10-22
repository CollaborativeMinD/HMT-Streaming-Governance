"""Execute restricted SysML calculations and compare all effect-state pairs to real SQL guards."""
import ast
import itertools
import json
import re
import uuid
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb
from .support import Host,LAB
from vv.cross import CATALOG

MODEL=Path(__file__).parents[1]/'model/durable-governance.sysml'


def calculation(name,**arguments):
    source=MODEL.read_text()
    body=re.search(r'calc def '+name+r' \{(.*?)\n    \}',source,re.S)[1]
    expression=re.search(r'return : Boolean =(.+?);',body,re.S)[1]
    tree=ast.parse(' '.join(expression.split()),mode='eval')
    allowed=(ast.Expression,ast.BoolOp,ast.And,ast.Or,ast.Compare,ast.Eq,ast.Name,ast.Load,ast.Constant)
    assert all(isinstance(n,allowed) for n in ast.walk(tree))
    return eval(compile(tree,str(MODEL),'eval'),{'__builtins__':{}},arguments)


def test_sysml_catalog_and_release_truth_vectors():
    source=MODEL.read_text()
    assert set(re.findall(r"requirement <'([^']+)'>",source))=={r['requirement'] for r in CATALOG}
    names=('postgresAvailable','peerCurrent','routeCurrent','sessionActive','recipientMatches','recordRegistered','hashesMatch')
    rows=[]
    for vector in itertools.product((False,True),repeat=7):
        result=calculation('ReleasePermitted',**dict(zip(names,vector)))
        assert result==all(vector)
        rows.append(dict(zip(names,vector))|dict(release=result))
    (LAB.parent/'evidence/model-release-vectors.json').write_text(json.dumps(rows,indent=2)+'\n')


async def test_sysml_effect_state_pairs_agree_with_postgres_transition_guards():
    states=('PREPARED','DISPATCHING','UNKNOWN','COMPLETED','FAILED_NO_EFFECT');rows=[]
    async with Host() as host:
        envelope=await host.verified()
        async with await psycopg.AsyncConnection.connect(host.settings.dsn) as connection:
            for old,new in itertools.product(states,repeat=2):
                await connection.execute('SAVEPOINT scenario')
                identifier=uuid.uuid4();claim=uuid.uuid4();block=envelope['ledger_hash']
                def witness(state):return Jsonb(dict(effect_id=str(identifier),block_hash=block,state=state))
                await connection.execute("INSERT INTO governance.effects(effect_id,session_id,sequence_number,effect_key,state,block_hash) VALUES(%s,%s,0,'model-test','PREPARED',%s)",
                    (identifier,host.frame()['session_id'],block))
                if old!='PREPARED':
                    await connection.execute("UPDATE governance.effects SET state='DISPATCHING',claim=%s WHERE effect_id=%s",(claim,identifier))
                if old not in ('PREPARED','DISPATCHING'):
                    await connection.execute('UPDATE governance.effects SET state=%s,result=%s WHERE effect_id=%s',(old,witness(old),identifier))
                await connection.execute('SAVEPOINT transition')
                try:
                    await connection.execute('UPDATE governance.effects SET state=%s,claim=%s,result=%s WHERE effect_id=%s',
                        (new,claim if new!='PREPARED' or old!='PREPARED' else None,
                         witness(new) if new in ('COMPLETED','FAILED_NO_EFFECT','UNKNOWN') else None,identifier))
                    permitted=True
                except psycopg.Error:
                    permitted=False;await connection.execute('ROLLBACK TO SAVEPOINT transition')
                expected=calculation('EffectTransition',oldState=old,newState=new)
                assert permitted==expected,(old,new,permitted,expected)
                rows.append(dict(old=old,new=new,model=expected,postgres=permitted))
                await connection.execute('ROLLBACK TO SAVEPOINT scenario')
    (LAB.parent/'evidence/model-effect-vectors.json').write_text(json.dumps(rows,indent=2)+'\n')
