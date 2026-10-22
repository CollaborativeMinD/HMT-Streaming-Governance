"""Export every SQL row, actual outcomes, mutation links and historical supersession."""
import csv
import json
import sqlite3

from vv_common import DB, ROOT, source_seal


def link_history(db):
    from vv_build import REGRESSIONS
    for index, (_, requirement, explanation) in enumerate(REGRESSIONS, 1):
        aid = f'REG-{index:02d}'
        old = db.execute('''SELECT t.run_id FROM test_results t JOIN test_runs r ON r.id=t.run_id
          WHERE t.assertion_id=? AND t.status='FAIL' AND r.historical=1''', (aid,)).fetchone()
        new = db.execute('''SELECT t.run_id FROM test_results t JOIN test_runs r ON r.id=t.run_id
          WHERE t.assertion_id=? AND t.status='PASS' AND r.historical=0 AND r.source_seal=?''', (aid, source_seal())).fetchone()
        if old and new:
            db.execute('INSERT OR REPLACE INTO supersessions VALUES (?,?,?,?,?,?)',
                       (old[0], aid, new[0], aid, requirement, explanation +
                        ' The same assertion is retained unchanged; the passing run supersedes the failing run, not the requirement.'))


def link_mutations(db):
    path = ROOT / 'vv_evidence/mutation_results.json'
    if not path.exists():
        return
    report = json.loads(path.read_text())
    if report['source_seal'] != source_seal():
        raise ValueError('Mutation evidence is stale')
    for row in report['results']:
        test = row['test']
        if '[C' in test:
            aid = 'PY-' + test.split('[')[1].rstrip(']')
        else:
            node = test.replace('.py::', '.').replace('::', '.')
            matches = db.execute('SELECT id,requirement_id FROM assertions WHERE test_selector=?', (node,)).fetchall()
            if not matches:
                raise ValueError('No assertion for mutant: ' + test)
            aid = matches[0][0]
        requirement = db.execute('SELECT requirement_id FROM assertions WHERE id=?', (aid,)).fetchone()[0]
        db.execute('INSERT OR REPLACE INTO mutations VALUES (?,?,?,?,?,?,?)',
                   (row['id'], requirement, aid, json.dumps({k: row[k] for k in ('file', 'old', 'new', 'count')}),
                    row['outcome'], row['evidence'], row['mutated_source_sha256']))
    initial_path = ROOT / 'vv_evidence/mutation_initial_results.json'
    if initial_path.exists():
        initial = json.loads(initial_path.read_text())
        for old in initial['results']:
            new = next(row for row in report['results'] if row['id'] == old['id'])
            if old['outcome'] != 'SURVIVED' or new['outcome'] != 'KILLED':
                continue
            if old['mutated_source_sha256'] != new['mutated_source_sha256']:
                raise ValueError('Superseding assertion must challenge the same mutant')
            old_node = old['test'].replace('.py::', '.').replace('::', '.')
            old_aid, requirement = db.execute('SELECT id,requirement_id FROM assertions WHERE test_selector=?', (old_node,)).fetchone()
            new_aid = 'PY-' + new['test'].split('[')[1].rstrip(']')
            db.execute('INSERT OR REPLACE INTO mutation_supersessions VALUES (?,?,?,?,?,?,?,?)',
                       (old['id'], old_aid, new_aid, requirement, 'SURVIVED', 'KILLED',
                        'vv_evidence/mutations_initial/' + old['id'] + '.txt',
                        'Prior assertion checked expiry and no commit but omitted quarantine count. '
                        'The superseding deadline contract checks zero quarantine, zero pending bytes and HOLD; '
                        'it kills the identical typed-disposition mutant. The prior test remains in the suite.'))


def export_report():
    output = ROOT / 'vv_evidence'
    with sqlite3.connect(DB) as db:
        db.execute('PRAGMA foreign_keys=ON')
        link_history(db)
        link_mutations(db)
        db.commit()
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        csv_dir = output / 'dataframes'
        csv_dir.mkdir(exist_ok=True)
        for name in tables:
            cursor = db.execute(f'SELECT * FROM "{name}"')
            with (csv_dir / (name + '.csv')).open('w', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(column[0] for column in cursor.description)
                writer.writerows(cursor)
        (output / 'assurance.sql').write_text('\n'.join(db.iterdump()) + '\n')
        counts = dict(db.execute('SELECT disposition,COUNT(*) FROM cross_report GROUP BY disposition'))
        runs = [dict(zip(('id','framework','source_seal','evidence_path','historical'), r))
                for r in db.execute('SELECT * FROM test_runs')]
        summary = dict(scope='All possibilities within the finite declared catalog, not all possible inputs or interleavings.',
                       counts=counts, total=sum(counts.values()), requirements=db.execute('SELECT COUNT(*) FROM requirements').fetchone()[0],
                       invariants=db.execute('SELECT COUNT(*) FROM invariants').fetchone()[0],
                       failure_modes=db.execute('SELECT COUNT(*) FROM failure_modes').fetchone()[0],
                       gates=db.execute('SELECT COUNT(*) FROM gates').fetchone()[0],
                       obligations=db.execute('SELECT COUNT(*) FROM obligations').fetchone()[0],
                       supersessions=db.execute('SELECT COUNT(*) FROM supersessions').fetchone()[0],
                       mutation_counts=dict(db.execute('SELECT outcome,COUNT(*) FROM mutations GROUP BY outcome')),
                       current_results=dict(db.execute('''SELECT t.status,COUNT(*) FROM test_results t JOIN test_runs r ON r.id=t.run_id
                         WHERE r.historical=0 GROUP BY t.status''')), runs=runs, source_seal=source_seal())
        (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
        render_markdown(db, summary)
        if counts.get('FAILED', 0) or counts.get('OPEN', 0):
            raise RuntimeError('Unresolved applicable findings remain; report exported without suppressing them')


def render_markdown(db, summary):
    lines = ['# Verification and validation report', '',
             f"The SQL CROSS JOIN materializes **{summary['total']:,} tuples**: "
             f"{summary['invariants']} invariants × {summary['failure_modes']} challenges × {summary['gates']} gates.", '',
             f"Dispositions: `{json.dumps(summary['counts'], sort_keys=True)}`.", '',
             f"Current campaigns: **{sum(summary['current_results'].values())} test executions**, "
             f"outcomes `{json.dumps(summary['current_results'])}`; "
             f"semantic mutations `{json.dumps(summary['mutation_counts'])}`. "
             f"All {summary['supersessions']} pre-fix regression failures retain requirement-preserving supersession links.", '',
             'Each applicable obligation is exercised by separate pytest and unittest assertions. The suites share scenario drivers '
             'and the declared contract, but use their own assertion mechanisms. They are not independent implementations or external certification.', '',
             'OUT_OF_SCOPE tuples remain in the report with an explicit reason. They are not passes or evidence of immunity. '
             'Every such tuple links to the pytest and unittest catalog-scope audits, which check its classification '
             'against the declared threat/owner relation. Those links are explicitly labeled CATALOG_SCOPE_AUDIT, '
             'separate from BEHAVIORAL_CONTRACT witnesses. '
             'The catalog covers declared local behavior, not all inputs, scheduler interleavings, deployment threats or combinations of simultaneous faults.', '',
             '## Evidence', '',
             '- [SQLite tables and views](vv_evidence/assurance.sqlite), [replayable SQL](vv_evidence/assurance.sql).',
             '- [Every cross-joined possibility](vv_evidence/dataframes/cross_report.csv), '
             '[finding-to-test evidence](vv_evidence/dataframes/finding_evidence.csv).',
             '- [Requirement satisfaction](vv_evidence/dataframes/requirement_evidence.csv), '
             '[field-level assertion expectations](vv_evidence/dataframes/assertion_fields.csv).',
             '- [All supersessions](vv_evidence/dataframes/supersessions.csv), [mutation witnesses](vv_evidence/mutation_results.json).',
             '- [Surviving-mutant assertion supersession](vv_evidence/dataframes/mutation_supersessions.csv), '
             '[initial mutation evidence](vv_evidence/mutation_initial_results.json).',
             '- [pytest XML](vv_evidence/crossjoin_pytest_results.xml), [unittest JSON](vv_evidence/crossjoin_unittest_results.json).', '',
             '## Failures and remediation', '',
             '| Assertion | Requirement | Failing test → satisfying test | Evidence | Why |', '|---|---|---|---|---|']
    for row in db.execute('SELECT failed_assertion_id,requirement_id,failed_run_id,satisfying_run_id,explanation FROM supersessions ORDER BY failed_assertion_id'):
        aid, req, old, new, why = row
        selector = db.execute('SELECT test_selector FROM assertions WHERE id=?', (aid,)).fetchone()[0]
        module, _, name = selector.rsplit('.', 2)
        folder = 'pre_api' if old == 'pre-api' else 'pre_fix'
        old_path = db.execute('SELECT evidence_path FROM test_runs WHERE id=?', (old,)).fetchone()[0]
        new_path = db.execute('SELECT evidence_path FROM test_runs WHERE id=?', (new,)).fetchone()[0]
        lines.append(f'| {aid} | {req} | [{name}](vv_evidence/{folder}/{module}.py) → '
                     f'[same unchanged test]({module}.py) | [{old}]({old_path}) → [{new}]({new_path}) | {why} |')
    lines += ['', 'The original failure XML, full pre-fix source snapshot, source hashes, and unchanged regression assertions are retained. '
              'A failed run is superseded by a passing run of the same test after the implementation fix. No failed test is deleted or weakened.', '',
              'The initial **M023** mutant survived '
              '[the prior expiry test](test_uplift.py), which checked HOLD and no commit but omitted quarantine count. '
              '[C025 in the dedicated pytest suite](vv_crossjoin_pytest.py) adds zero quarantine and zero pending bytes to the deadline contract. '
              'It kills the identical mutant: [initial survivor](vv_evidence/mutations_initial/M023.txt) → '
              '[superseding assertion failure on the mutant](vv_evidence/mutations/M023.txt). '
              'The prior test remains in the suite; the SQL mutation_supersessions table records the requirement and explanation.', '',
              '## Qualification limits', '',
              'Mutation results cover a hand-selected set of semantic faults with count-checked edits and a passing control. '
              'A kill requires an executed AssertionError; syntax/import errors and timeouts do not count as kills. '
              'This is not exhaustive mutation coverage.', '',
              'Speed gates are local catastrophic-regression ceilings: each 512 KiB scan <5 seconds, median doubling ratio <4, '
              '1,500 ledger commits <10 seconds, traced peak <6 MiB with 64-entry diagnostic caches. '
              'Measurements and platform/source provenance are included. They are not production throughput SLAs.', '',
              'The ledger is in-memory and does not authenticate an agent identity. Hashes establish integrity and lineage relative to this process. '
              'Persistence, host authorization, effect reconciliation, and production key management remain deployment responsibilities. '
              'Synchronous callback effects are not retried; an exception or cancellation does not establish whether an external effect occurred.', '']
    (ROOT / 'VV_report.md').write_text('\n'.join(lines))
