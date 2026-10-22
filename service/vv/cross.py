"""Finite SQL/DF scenario expansion. Cartesian enumeration is not exhaustive proof."""
import ast
import csv
import importlib
import inspect
import json
import sqlite3
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CATALOG=json.loads((ROOT/'vv/catalog.json').read_text())
GATES=('DURABILITY','INTEGRITY','IDENTITY','CACHE','EFFECT','NETWORK','KEYS','BOUNDARY','DEPLOYMENT')


def database():
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.executescript('''CREATE TABLE requirements(id TEXT PRIMARY KEY,invariant TEXT,status TEXT);
    CREATE TABLE failure_modes(id TEXT PRIMARY KEY,requirement_id TEXT REFERENCES requirements,description TEXT);
    CREATE TABLE gates(id TEXT PRIMARY KEY);
    CREATE TABLE assertions(id TEXT PRIMARY KEY,requirement_id TEXT,gate_id TEXT,test_node TEXT,source_line INTEGER);
    CREATE TABLE supersessions(id TEXT PRIMARY KEY,failed_evidence TEXT,satisfied_by TEXT,reason TEXT,disposition TEXT);
    ''')
    db.executemany('INSERT INTO gates VALUES(?)',[(g,) for g in GATES])
    for row in CATALOG:
        r=row['requirement'];db.execute('INSERT INTO requirements VALUES(?,?,?)',(r,row['invariant'],row['status']))
        db.execute('INSERT INTO failure_modes VALUES(?,?,?)',(row['failure'],r,row['invariant']))
        line=None;node=None
        if row['test']:
            path=ROOT/'tests'/(row['module']+'.py');tree=ast.parse(path.read_text())
            function=next(n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==row['test'])
            line=min(n.lineno for n in ast.walk(function) if isinstance(n,ast.Assert)) if any(isinstance(n,ast.Assert) for n in ast.walk(function)) else function.lineno
            node='service/tests/'+row['module']+'.py::'+row['test']
        db.execute('INSERT INTO assertions VALUES(?,?,?,?,?)',(r+'-assert',r,row['gate'],node,line))
    db.executemany('INSERT INTO supersessions VALUES(?,?,?,?,?)',[
        ('S01','evidence/initial-ledger.xml','I03','Duplicate quarantine event fixed: insert event only on ACTIVE-to-QUARANTINED transition. Pre-fix source retained.','RUNTIME_FIXED'),
        ('S02','evidence/initial-ledger.xml','A04','Receipt observer used a revoked sender; changed observer to the still-authorized receiver without weakening authorization.','FIXTURE_AMENDED'),
        ('S04','evidence/failures/headscale-readiness.xml','N01','Enrollment completed before the data path was ready; bounded positive echo readiness probes precede the unchanged denial challenge.','FIXTURE_READINESS_FIXED'),
        ('S05','evidence/failures/mutations-initial.json','B04/E03','Added assertion witnesses for exact-size valid JSON and direct external witness binding; SQL defense remains in place.','ASSERTIONS_STRENGTHENED'),
        ('S03','evidence/failures/native-reconnect.xml','H04','Persistent sessions satisfy N02; the original repeated one-shot reconnect availability claim remains on HOLD.','NOT_NULLIFIED'),
    ])
    db.commit()
    return db


QUERY='''SELECT r.id requirement,r.invariant,f.id failure_mode,g.id gate,
 CASE WHEN f.requirement_id=r.id AND a.gate_id=g.id THEN 1 ELSE 0 END applicable,
 CASE WHEN f.requirement_id=r.id AND a.gate_id=g.id THEN r.status ELSE 'OUT_OF_SCOPE' END disposition,
 a.test_node,a.source_line,
 CASE WHEN f.requirement_id<>r.id THEN 'Failure belongs to a different derived requirement'
 WHEN a.gate_id<>g.id THEN 'Gate has no declared authority over this scenario'
 WHEN r.status='HOLD' THEN 'Unresolved deployment or availability obligation; no passing test claimed'
 ELSE 'Execute the linked positive and negative behavioral assertions' END rationale
 FROM requirements r CROSS JOIN failure_modes f CROSS JOIN gates g
 JOIN assertions a ON a.requirement_id=r.id ORDER BY r.id,f.id,g.id'''


def findings():
    with database() as db:return [dict(r) for r in db.execute(QUERY)]


async def execute(requirement):
    row=next(r for r in CATALOG if r['requirement']==requirement)
    assert row['status']=='EXECUTABLE'
    function=getattr(importlib.import_module('tests.'+row['module']),row['test'])
    with tempfile.TemporaryDirectory() as path:
        args={'tmp_path':Path(path)} if 'tmp_path' in inspect.signature(function).parameters else {}
        result=function(**args)
        if inspect.isawaitable(result):await result


def export():
    import pandas as pd
    directory=ROOT/'evidence/cross';directory.mkdir(exist_ok=True)
    db=database()
    target=sqlite3.connect(directory/'assurance.sqlite');db.backup(target);target.close()
    (directory/'schema-and-data.sql').write_text('\n'.join(db.iterdump())+'\n'+QUERY+';\n')
    for table in ('requirements','failure_modes','gates','assertions','supersessions'):
        pd.read_sql_query('SELECT * FROM '+table,db).to_csv(directory/(table+'.csv'),index=False)
    df=pd.read_sql_query(QUERY,db);df.to_csv(directory/'all-possibilities.csv',index=False)
    summary=dict(requirements=len(CATALOG),failure_modes=len(CATALOG),gates=len(GATES),cartesian_rows=len(df),
        executable=int((df.disposition=='EXECUTABLE').sum()),holds=int((df.disposition=='HOLD').sum()),
        out_of_scope=int((df.disposition=='OUT_OF_SCOPE').sum()),scope='Declared finite catalog only; out-of-scope combinations are not nullified threats')
    (directory/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    db.close();return summary

if __name__=='__main__':print(json.dumps(export()))
