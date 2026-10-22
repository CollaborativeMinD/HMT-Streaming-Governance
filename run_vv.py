# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest>=8,<10", "coverage>=7,<8", "mcp[cli]>=1.26,<2"]
# ///
"""Replay the finite V&V campaign and regenerate every relational evidence artifact."""
import importlib.metadata
import json
import platform
import subprocess
import sys
import time

from vv_build import ingest, initialize
from vv_common import ROOT, source_hashes, source_seal
from vv_report import export_report


def run(command, name, timeout=120):
    start = time.perf_counter()
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=timeout)
    (ROOT / 'vv_evidence' / (name + '.txt')).write_text(result.stdout + result.stderr)
    print(name, 'PASS' if result.returncode == 0 else 'FAIL', flush=True)
    if result.returncode:
        raise RuntimeError(f'{name} failed; retained output in vv_evidence/{name}.txt')
    return dict(command=command, elapsed_seconds=time.perf_counter() - start, exit_code=result.returncode)


def main():
    initialize()
    commands = {}
    sources = ','.join(path[:-3] for path in source_hashes() if path != 'governance_mcp.py')
    commands['unit'] = run([sys.executable, '-m', 'coverage', 'run', '--branch', '--source=' + sources,
                            '-m', 'pytest', '-q', *sorted(p.name for p in ROOT.glob('test_*.py')),
                            '--junitxml=vv_evidence/unit_results.xml'], 'unit')
    commands['coverage'] = run([sys.executable, '-m', 'coverage', 'json', '-o', 'vv_evidence/coverage.json'], 'coverage')
    commands['crossjoin_pytest'] = run([sys.executable, '-m', 'pytest', '-q', 'vv_crossjoin_pytest.py',
                                      '--junitxml=vv_evidence/crossjoin_pytest_results.xml'], 'crossjoin_pytest')
    commands['crossjoin_unittest'] = run([sys.executable, 'vv_crossjoin_unittest.py'], 'crossjoin_unittest')
    commands['mutation'] = run([sys.executable, 'vv_mutation.py'], 'mutation', timeout=120)
    for run_id, path in [('pre-boundary', 'vv_baseline_failures.xml'), ('pre-api', 'vv_api_baseline_failures.xml')]:
        ingest(run_id, 'pytest', ROOT / path, historical=True)
    ingest('current-unit', 'pytest', ROOT / 'vv_evidence/unit_results.xml')
    ingest('current-cross-pytest', 'pytest', ROOT / 'vv_evidence/crossjoin_pytest_results.xml')
    ingest('current-cross-unittest', 'unittest', ROOT / 'vv_evidence/crossjoin_unittest_results.json')
    provenance = dict(python=sys.version, platform=platform.platform(), source_seal=source_seal(),
                      packages={p: importlib.metadata.version(p) for p in ('pytest', 'coverage', 'mcp', 'pydantic')},
                      production_source_hashes=source_hashes(), commands=commands,
                      coverage_scope='In-process runtime modules; MCP subprocess transport is functionally tested, not included in this branch percentage.')
    import hashlib
    provenance['validation_source_hashes'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for pattern in ('test_*.py', 'vv_*.py', 'vv_*.json', 'vv_*.sql', 'run_vv.py') for p in ROOT.glob(pattern)}
    (ROOT / 'vv_evidence/environment.json').write_text(json.dumps(provenance, indent=2) + '\n')
    export_report()
    print((ROOT / 'vv_evidence/summary.json').read_text())


if __name__ == '__main__':
    main()
