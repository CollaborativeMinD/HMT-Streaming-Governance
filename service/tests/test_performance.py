"""Local qualification budgets, not a claim of speedup over the in-memory reference."""
import ast
import json
import statistics
import time
import tracemalloc
from pathlib import Path

from .support import Host,LAB


async def test_indexed_roundtrip_latency_and_bounded_python_retention():
    async with Host() as host:
        latencies=[];previous='0'*64
        tracemalloc.start();start,_=tracemalloc.get_traced_memory()
        began=time.perf_counter()
        for sequence in range(64):
            before=time.perf_counter()
            value=host.frame(sequence_number=sequence,previous_hash=previous,payload='x'*4096)
            result=await host.settled(await host.commit(value,key=f'bench-{sequence}'))
            assert result['status']=='VERIFIED'
            envelope=result['result'];previous=envelope['ledger_hash']
            assert (await host.verify(envelope))['verified'] is True
            latencies.append(time.perf_counter()-before)
        elapsed=time.perf_counter()-began
        current,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
        warm=[]
        for _ in range(24):
            before=time.perf_counter();assert (await host.verify(envelope))['verified']
            warm.append(time.perf_counter()-before)
        plans=await host.sql('EXPLAIN (FORMAT JSON) SELECT payload_hash,block_hash FROM governance.records WHERE session_id=%s AND sequence_number=%s',
            (host.frame()['session_id'],63))
        serialized=json.dumps(plans)
        assert 'Index' in serialized, 'Record access must use the primary-key index at this fixture scale'
        p95=sorted(latencies)[60]
        assert p95<1.0, 'Local roundtrip p95 exceeded one-second qualification budget'
        assert current-start<16*1024*1024 and peak-start<32*1024*1024
        report=dict(scope='64 sequential durable 4096-byte commits with fresh-authority receiver verification; same local host',
            records=64,elapsed_seconds=elapsed,records_per_second=64/elapsed,p50_seconds=statistics.median(latencies),
            p95_seconds=p95,warm_verification_p50_seconds=statistics.median(warm),cache_hits=host.engine.cache.hits,
            python_retained_bytes=current-start,python_peak_bytes=peak-start,indexed_lookup=True,
            excludes='database/Redis/container memory and WAN/NAT; no in-memory speedup comparison')
        (LAB.parent/'evidence/performance.json').write_text(json.dumps(report,indent=2)+'\n')


def test_runtime_structural_complexity_and_semantic_authority_boundaries():
    root=Path(__file__).parents[1]/'src/hmt_governance'
    observations=[]
    for path in sorted(root.glob('*.py')):
        tree=ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                branches=sum(isinstance(n,(ast.If,ast.For,ast.While,ast.IfExp,ast.ExceptHandler,ast.AsyncFor)) for n in ast.walk(node))
                branches+=sum(max(0,len(n.values)-1) for n in ast.walk(node) if isinstance(n,ast.BoolOp))
                lines=node.end_lineno-node.lineno+1
                observations.append(dict(file=path.name,function=node.name,lines=lines,structural_branch_complexity=branches+1))
                assert branches+1<=35,(path.name,node.name,branches+1)
                assert lines<=100,(path.name,node.name,lines)
        if path.name not in ('keys.py','sink.py','server.py'):
            assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ('system','Popen','run') for n in ast.walk(tree)), 'No agent-selected subprocess execution'
    # Fresh registry/route checks reside in the DB transaction; no cache module dependency on authority writes.
    assert 'session_authority(connection' in (root/'records.py').read_text()
    assert 'import redis' not in (root/'authority.py').read_text()
    (LAB.parent/'evidence/complexity.json').write_text(json.dumps(observations,indent=2)+'\n')
