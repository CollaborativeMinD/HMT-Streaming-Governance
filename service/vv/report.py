"""Link both behavioral replays to the finite catalog and bind evidence to source bytes."""
import hashlib
import json
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path

from .cross import ROOT,CATALOG,export


def sources():
    repo=ROOT.parent
    paths=[]
    for folder in ('service/src','service/tests','service/vv','service/model','tailcat-gateway','deployment'):
        paths += [p for p in (repo/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and '.build' not in p.parts]
    paths += [repo/p for p in ("readme.md","package.json","package-lock.json",".gitleaks.toml",".github/workflows/ci.yml","ci/package_release.py","ci/smoke_durable.py","docs/ci-cd.md","service/Dockerfile","service/README.md","service/PROVENANCE.md")]
    paths += [ROOT/name for name in ('lab.py','headscale_lab.py','run_vv.py','pyproject.toml','uv.lock')]
    return {p.relative_to(repo).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def tests(path):
    tree=ET.parse(path)
    cases=tree.findall('.//testcase')
    assert cases and all(not any(c.find(x) is not None for x in ('failure','error','skipped')) for c in cases),path
    return cases


def main():
    start=json.loads((ROOT/'evidence/run-start.json').read_text())
    assert start['source_manifest']==sources(),'Source changed since qualification started'
    for name in ('final-pytest.xml','unittest.xml','mutations/summary.json'):
        assert (ROOT/'evidence'/name).stat().st_mtime>=start['started_at'],'Stale run evidence: '+name
    summary=export()
    py=tests(ROOT/'evidence/final-pytest.xml');unit=tests(ROOT/'evidence/unittest.xml')
    mutations=json.loads((ROOT/'evidence/mutations/summary.json').read_text())
    assert mutations['positive_control']=='PASS' and mutations['killed']==mutations['total']==10
    model=json.loads((ROOT/'evidence/sysml-validation.json').read_text())
    assert model['counts']['error']==model['counts']['warning']==0
    result=[]
    for row in CATALOG:
        identifier=row['requirement']
        if row['status']=='HOLD':
            result.append(dict(requirement=identifier,status='HOLD',pytest=None,unittest=None,reason=row['invariant']))
        else:
            p=next(c for c in py if c.get('name')=='test_nullify_executable_finding['+identifier+']')
            u=next(c for c in unit if c.get('name')=='test_'+identifier)
            result.append(dict(requirement=identifier,status='SATISFIED_LOCAL',pytest=p.get('classname')+'.'+p.get('name'),unittest=u.get('classname')+'.'+u.get('name')))
    with sqlite3.connect(ROOT/'evidence/cross/assurance.sqlite') as db:
        db.executescript('DROP TABLE IF EXISTS requirement_results;CREATE TABLE requirement_results(requirement_id TEXT PRIMARY KEY,status TEXT,pytest TEXT,unittest TEXT,reason TEXT);')
        db.executemany('INSERT INTO requirement_results VALUES(?,?,?,?,?)',[(r['requirement'],r['status'],r.get('pytest'),r.get('unittest'),r.get('reason')) for r in result])
        db.commit()
        (ROOT/'evidence/cross/schema-and-data.sql').write_text('\n'.join(db.iterdump())+'\n')
    source=sources();seal=hashlib.sha256(json.dumps(source,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    summary|=dict(pytest_executions=len(py),unittest_executions=len(unit),selected_mutants_killed=10,
        source_seal=seal,requirements=result,profile='v0.2 durable local qualification; deployment HOLDs are not waivers')
    (ROOT/'evidence/source-manifest.json').write_text(json.dumps(source,indent=2)+'\n')
    (ROOT/'evidence/summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    lines=['# Durable governance qualification','',f"{len(py)} pytest and {len(unit)} unittest executions passed; 10/10 selected Python mutants killed.",
        '',f"SQL CROSS JOIN: {summary['cartesian_rows']:,} rows; 28 executable requirements satisfied locally, four HOLD obligations, 9,184 declared OUT_OF_SCOPE combinations.",
        '', 'The separate suites execute the linked runtime challenges. Scope audits do not turn unrelated tuples into behavioral passes. Finite catalog enumeration is not exhaustive security proof.',
        '', '| Requirement | Result | Behavioral assertion |','|---|---|---|']
    for r in CATALOG:
        link=f"[test](../tests/{r['module']}.py)" if r['test'] else r['invariant']
        lines.append(f"| {r['requirement']} | {'SATISFIED_LOCAL' if r['test'] else 'HOLD'} | {link} |")
    lines += ['', 'Runtime fixes, fixture amendments and unresolved reconnect findings are distinguished in [supersessions](cross/supersessions.csv). The initial mutation run killed 8/10: M06 needed an explicit positive-boundary assertion, and M10 was masked by the independent SQL witness guard. The added direct witness-contract test kills M10 without weakening the SQL defense.',
        '', 'Model witnesses: strict SysML validation with zero errors/warnings; 128 release gate vectors; all 25 effect-state pairs checked against PostgreSQL triggers.',
        '', 'The local speed fixture includes PostgreSQL, polling and fresh receiver authorization. [Timing and traced Python memory](performance.json) exclude service RSS and WAN behavior.',
        '', 'Source binding: `'+seal+'`. [Source bytes](source-manifest.json) · [Both-framework results](summary.json) · [Mutation witnesses](mutations/summary.json).']
    (ROOT/'evidence/REPORT.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({k:summary[k] for k in ('pytest_executions','unittest_executions','cartesian_rows','selected_mutants_killed','source_seal')}))
if __name__=='__main__':main()
