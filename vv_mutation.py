"""Isolated, count-checked semantic mutants with a passing control and raw evidence."""
import argparse
import ast
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from vv_common import DB, ROOT, source_seal


def run_campaign(only=None):
    evidence = ROOT / 'vv_evidence' / 'mutations'
    evidence.mkdir(exist_ok=True)
    items = json.loads((ROOT / 'vv_mutation_catalog.json').read_text())
    chosen = [item for item in items if only is None or item['id'] == only]
    if not chosen:
        raise ValueError('Unknown mutation ID')
    targets = sorted({item['test'] for item in chosen})
    control = subprocess.run([sys.executable, '-m', 'pytest', '-q', *targets],
                             cwd=ROOT, capture_output=True, text=True, timeout=45)
    (evidence / ('control_' + (only or 'all') + '.txt')).write_text(control.stdout + control.stderr)
    if control.returncode:
        raise RuntimeError('Mutation control failed; refusing to count invalid kills')
    previous = ROOT / 'vv_evidence' / 'mutation_results.json'
    results = ([r for r in json.loads(previous.read_text())['results'] if r['id'] != only]
               if only and previous.exists() else [])
    for item in chosen:
        with tempfile.TemporaryDirectory(prefix='governance-mutant-') as temporary:
            work = __import__('pathlib').Path(temporary)
            for path in ROOT.glob('*.py'):
                if path.name != 'governance_lab.py':
                    shutil.copy2(path, work / path.name)
            for path in ROOT.glob('vv_*.json'):
                shutil.copy2(path, work / path.name)
            (work / 'vv_evidence').mkdir()
            shutil.copy2(DB, work / 'vv_evidence' / DB.name)
            target = work / item['file']
            source = target.read_text()
            if source.count(item['old']) != item['count']:
                raise ValueError('Mutation location drift: ' + item['id'])
            mutated = source.replace(item['old'], item['new'])
            ast.parse(mutated)  # Syntax errors are never mutation kills.
            target.write_text(mutated)
            output_path = evidence / (item['id'] + '.xml')
            command = [sys.executable, '-m', 'pytest', item['test'], '-q', f'--junitxml={output_path}']
            try:
                result = subprocess.run(command, cwd=work, capture_output=True, text=True, timeout=20)
                text = result.stdout + result.stderr
                cases = list(ET.parse(output_path).iter('testcase')) if output_path.exists() else []
                # Require an actual assertion failure. Import/collection errors and crashes are invalid.
                killed = any(case.find('failure') is not None and 'AssertionError' in (case.find('failure').text or '')
                             for case in cases)
                outcome = 'KILLED' if killed else 'SURVIVED' if result.returncode == 0 else 'INVALID'
            except subprocess.TimeoutExpired as exc:
                text = str(exc)
                outcome = 'TIMEOUT'
            (evidence / (item['id'] + '.txt')).write_text(text)
            row = dict(item, outcome=outcome, mutated_source_sha256=hashlib.sha256(mutated.encode()).hexdigest(),
                       evidence=f'vv_evidence/mutations/{item["id"]}.txt')
            results.append(row)
            print(item['id'], outcome, flush=True)
    results.sort(key=lambda row: row['id'])
    report = dict(source_seal=source_seal(), control='PASS', scope='26 hand-selected semantic faults; not exhaustive mutation proof.',
                  results=results, killed=sum(r['outcome'] == 'KILLED' for r in results), total=len(results))
    (ROOT / 'vv_evidence' / 'mutation_results.json').write_text(json.dumps(report, indent=2) + '\n')
    return all(row['outcome'] == 'KILLED' for row in results)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--only')
    raise SystemExit(not run_campaign(parser.parse_args().only))
