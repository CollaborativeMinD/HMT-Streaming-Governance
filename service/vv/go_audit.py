"""Govulncheck JSON is a stream and can exit zero with findings. Gate reached symbols explicitly."""
import json
from pathlib import Path
ROOT=Path(__file__).parents[1]

def main():
    raw=(ROOT/'evidence/go-vulnerability.json').read_text();decoder=json.JSONDecoder();objects=[]
    while raw.strip():
        value,end=decoder.raw_decode(raw.lstrip());objects.append(value);raw=raw.lstrip()[end:]
    findings=[o['finding'] for o in objects if 'finding' in o]
    reached=sorted({f['osv'] for f in findings if any(frame.get('function') for frame in f.get('trace',[]))})
    modules=sorted({f['osv'] for f in findings}-set(reached))
    summary=dict(status='PASS' if not reached else 'FAIL',reached_advisories=reached,module_only_advisories=modules,
        scope='Pinned Go 1.27.3 native Linux source/call graph; no promise about other builds or unknown vulnerabilities')
    (ROOT/'evidence/go-audit-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    assert not reached,summary
    print(json.dumps(summary))
if __name__=='__main__':main()
