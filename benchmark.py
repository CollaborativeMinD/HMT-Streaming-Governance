"""Reproducible baseline/candidate timings; no claimed portable throughput guarantees."""
import argparse
import asyncio
import gc
import hashlib
import importlib.util
import json
import platform
import random
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).parent

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module
    spec.loader.exec_module(module)
    return module


def scanner_run(module,data,packet):
    scanner=module.RabinCDCScanner(block_size_target=512)
    digest=hashlib.sha256()
    cuts=[]
    view=memoryview(data)
    for offset in range(0,len(data),packet):
        for chunk in scanner.feed(view[offset:offset+packet]):
            digest.update(chunk.data)
            cuts.append((chunk.start,chunk.end,chunk.fingerprint,chunk.reason))
    for chunk in scanner.finish():
        digest.update(chunk.data)
        cuts.append((chunk.start,chunk.end,chunk.fingerprint,chunk.reason))
    assert digest.digest()==hashlib.sha256(data).digest()
    return cuts


async def ledger_run(module,count,payload_size):
    engine=await module.GovernanceLedgerEngine().start()
    sender=module.AgentGateway(engine,'bench','sender')
    receiver=module.AgentGateway(engine,'bench','receiver')
    payload='x'*payload_size
    started=time.perf_counter()
    try:
        for _ in range(count):
            envelope=await sender.wrap_output(payload,'receiver')
            await receiver.execute_verified(envelope,lambda _: None)
        return time.perf_counter()-started,len(engine.ledger),engine.head('bench')[1]
    finally:
        await engine.close()


async def retained_memory(module,count):
    gc.collect()
    tracemalloc.start()
    engine=await module.GovernanceLedgerEngine().start()
    sender=module.AgentGateway(engine,'memory','sender')
    try:
        for _ in range(count):
            await sender.wrap_output('small payload','receiver')
        current,peak=tracemalloc.get_traced_memory()
        return dict(retained_bytes=current,peak_bytes=peak,records=len(engine.ledger),events=len(engine.events))
    finally:
        await engine.close()
        tracemalloc.stop()


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--baseline-only',action='store_true')
    parser.add_argument('--output',default='benchmark_results.json')
    args=parser.parse_args()
    modules={'baseline':load('governance_baseline',ROOT/'baseline/governance_core.py')}
    if not args.baseline_only:
        modules['candidate']=load('governance_candidate',ROOT/'governance_core.py')
    source_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in ROOT.glob('governance_*.py') if p.name != 'governance_lab.py'}
    result=dict(environment=dict(python=sys.version,platform=platform.platform()),
                candidate_source_hashes=source_hashes,
                baseline_source_sha256=hashlib.sha256((ROOT/'baseline/governance_core.py').read_bytes()).hexdigest(),
                configuration=dict(scanner_bytes=262144,repetitions=5,ledger_frames=600,memory_frames=3000),results={})
    datasets={'random':random.Random(42).randbytes(262144),'structured':(b'{"agent":"alpha","step":1,"payload":"same log"}\n'*6000)[:262144]}
    for name,module in modules.items():
        output={}
        for kind,data in datasets.items():
            samples=[]
            scanner_run(module,data,73)
            for _ in range(5):
                started=time.perf_counter()
                cuts=scanner_run(module,data,73)
                samples.append(time.perf_counter()-started)
            output['scanner_'+kind]=dict(seconds=samples,median_seconds=statistics.median(samples),chunks=len(cuts),
                                        signature=hashlib.sha256(repr(cuts).encode()).hexdigest())
        for payload_size in (256,16384,131072):
            await ledger_run(module,30,payload_size)
            samples=[]
            for _ in range(5):
                elapsed,n,head=await ledger_run(module,600,payload_size)
                assert n==600
                samples.append(elapsed)
            output['ledger_'+str(payload_size)]=dict(seconds=samples,median_seconds=statistics.median(samples),head=head)
        output['memory']=await retained_memory(module,3000)
        result['results'][name]=output
        print(name,json.dumps(output),flush=True)
    if len(modules)==2:
        for kind in datasets:
            assert result['results']['baseline']['scanner_'+kind]['signature']==result['results']['candidate']['scanner_'+kind]['signature']
        for size in (256,16384,131072):
            assert result['results']['baseline']['ledger_'+str(size)]['head']==result['results']['candidate']['ledger_'+str(size)]['head']
    (ROOT/args.output).write_text(json.dumps(result,indent=2)+'\n')

if __name__=='__main__':
    asyncio.run(main())
