"""Build/replay SQL evidence, ingest executed assertions and export full dataframes."""
import argparse
import hashlib
import json
import re
import sqlite3
import xml.etree.ElementTree as ET

from vv_common import DB, ROOT, catalog, source_hashes, source_seal

REGRESSIONS = [
    ('test_cancelled_verification_clears_context_and_holds_session', 'GOV-05', 'Cancel verification: clear context and hold session before cancellation propagates.'),
    ('test_closed_engine_denies_previously_verified_handoff', 'GOV-05', 'Reject verification when the owning engine is closed.'),
    ('test_concurrent_close_has_one_shutdown_owner', 'VV-01', 'One shielded shutdown task owns cleanup for all close callers.'),
    ('test_hold_during_standalone_hash_cannot_return_verified', 'GOV-05', 'Recheck availability after the hashing await before returning trust.'),
    ('test_unexpected_verifier_failure_holds_and_clears_context', 'GOV-05', 'Classify unexpected verifier exceptions as HOLD and clear receiving context.'),
    ('test_strict_schema_and_real_stdio_handoff', 'VV-03', 'StrictInt prevents MCP numeric coercion before the strict core parser.'),
]


def initialize():
    DB.parent.mkdir(exist_ok=True)
    if DB.exists():
        DB.unlink()
    with sqlite3.connect(DB) as db:
        db.executescript((ROOT / 'vv_schema.sql').read_text())
        c = catalog()
        for table, columns in [('requirements', ['id', 'description']),
                               ('invariants', ['id', 'requirement_id', 'description']),
                               ('failure_modes', ['id', 'description', 'kind']), ('gates', ['id', 'phase', 'description'])]:
            db.executemany(f"INSERT INTO {table} VALUES ({','.join('?' for _ in columns)})",
                           [tuple(row[key] for key in columns) for row in c[table]])
        for o in c['obligations']:
            db.execute('INSERT INTO obligations VALUES (?,?,?,?,?,?,?)',
                       (o['id'], o['case'], o['invariant_id'], o['failure_id'], o['gate_id'], o['rationale'], json.dumps(o['expected'])))
            for framework, prefix in [('pytest', 'PY'), ('unittest', 'UT')]:
                selector = (f"vv_crossjoin_pytest.py::test_refute_finding[{o['id']}]" if prefix == 'PY' else
                            f"vv_crossjoin_unittest.CartesianRefutationTests.test_{o['id']}_{o['case']}")
                aid = prefix + '-' + o['id']
                db.execute('INSERT INTO assertions VALUES (?,?,?,?,?,?)',
                           (aid, framework, selector, o['requirement_id'], o['id'], ','.join(o['categories'])))
                db.executemany('INSERT INTO assertion_fields VALUES (?,?,?)',
                               [(aid, field, json.dumps(value)) for field, value in o['expected'].items()])
        for i, (name, requirement, _) in enumerate(REGRESSIONS, 1):
            module = 'test_vv_api.APIContractTests' if i == 6 else 'test_vv_regressions.BoundaryRegressionTests'
            db.execute('INSERT INTO assertions VALUES (?,?,?,?,?,?)',
                       (f'REG-{i:02d}', 'pytest', f'{module}.{name}', requirement, None, 'negative,regression'))
        for aid, framework, selector in [
            ('SCOPE-PY', 'pytest', 'vv_crossjoin_pytest.test_complete_cartesian_and_traceability'),
            ('SCOPE-UT', 'unittest', '__main__.CartesianRefutationTests.test_cartesian_set_and_links')]:
            db.execute('INSERT INTO assertions VALUES (?,?,?,?,?,?)',
                       (aid, framework, selector, 'VV-05', None, 'structural,semantic'))
        db.executemany('INSERT INTO metadata VALUES (?,?)', [
            ('source_seal', source_seal()), ('source_hashes', json.dumps(source_hashes(), sort_keys=True)),
            ('catalog_sha256', hashlib.sha256((ROOT / 'vv_catalog.json').read_bytes()).hexdigest()),
            ('scope', c['scope'])])
        db.executescript((ROOT / 'vv_cross_join.sql').read_text())


def xml_results(path):
    results = []
    for node in ET.parse(path).iter('testcase'):
        failure, error, skipped = node.find('failure'), node.find('error'), node.find('skipped')
        status = 'FAIL' if failure is not None else 'ERROR' if error is not None else 'SKIP' if skipped is not None else 'PASS'
        detail = next((child.text or child.get('message', '') for child in (failure, error, skipped) if child is not None), '')
        results.append(dict(nodeid=node.get('classname', '') + '.' + node.get('name', ''),
                            status=status, duration=float(node.get('time', 0)), detail=detail))
    return results


def requirement_for(nodeid):
    # Explicit inventory lives in a checked-in file; unknown tests cannot acquire a blanket requirement.
    inventory = json.loads((ROOT / 'vv_test_inventory.json').read_text())
    return inventory[nodeid]['requirement_id']


def ingest(run_id, framework, path, historical=False):
    results = json.loads(path.read_text()) if path.suffix == '.json' else xml_results(path)
    seal = source_seal()
    if historical:
        folder = 'pre_api' if run_id == 'pre-api' else 'pre_fix'
        original = json.loads((ROOT / 'vv_evidence' / folder / 'source_hashes.json').read_text())
        seal = hashlib.sha256(json.dumps(original, sort_keys=True).encode()).hexdigest()
    with sqlite3.connect(DB) as db:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('INSERT INTO test_runs VALUES (?,?,?,?,?)', (run_id, framework, seal, str(path.relative_to(ROOT)), int(historical)))
        for result in results:
            nodeid = result['nodeid']
            match = re.search(r'(?:\[|test_)(C\d{3})(?:\]|_)', nodeid)
            reg = next((i for i, (name, _, _) in enumerate(REGRESSIONS, 1) if nodeid.endswith('.' + name)), None)
            if match:
                aid = ('PY-' if framework == 'pytest' else 'UT-') + match[1]
            elif nodeid.endswith('.test_complete_cartesian_and_traceability'):
                aid = 'SCOPE-PY'
            elif nodeid.endswith('.test_cartesian_set_and_links'):
                aid = 'SCOPE-UT'
            elif reg:
                aid = f'REG-{reg:02d}'
            else:
                aid = f'{framework}:{nodeid}'
                requirement = 'VV-05' if 'cartesian' in nodeid or 'traceability' in nodeid else requirement_for(nodeid)
                db.execute('INSERT OR IGNORE INTO assertions VALUES (?,?,?,?,?,?)',
                           (aid, framework, nodeid, requirement, None, 'inventory'))
            db.execute('INSERT INTO test_results VALUES (?,?,?,?,?)',
                       (run_id, aid, result['status'], result['duration'], result['detail']))


def finalize():
    from vv_report import export_report
    export_report()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['init', 'ingest', 'finalize'])
    parser.add_argument('--run-id')
    parser.add_argument('--framework', choices=['pytest', 'unittest'])
    parser.add_argument('--path')
    parser.add_argument('--historical', action='store_true')
    args = parser.parse_args()
    if args.action == 'init':
        initialize()
    elif args.action == 'ingest':
        ingest(args.run_id, args.framework, ROOT / args.path, args.historical)
    else:
        finalize()
