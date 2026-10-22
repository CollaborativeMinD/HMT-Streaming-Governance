"""Explicit structural budgets and environment-scoped speed/memory budgets."""
import ast
import gc
import random
import statistics
import time
import tracemalloc
from pathlib import Path

from governance_core import AgentGateway, GovernanceLedgerEngine, RabinCDCScanner

ROOT = Path(__file__).parent


def structure_probe():
    rows = []
    decisions = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Match, ast.IfExp, ast.ExceptHandler)

    def depth(node, current=0):
        current += isinstance(node, decisions)
        return max([current, *(depth(child, current) for child in ast.iter_child_nodes(node))])

    for path in sorted(ROOT.glob('governance_*.py')):
        if path.name == 'governance_lab.py':
            continue
        tree = ast.parse(path.read_text())
        functions = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        complexity = [1 + sum(int(isinstance(node, decisions)) +
                      (len(node.values) - 1 if isinstance(node, ast.BoolOp) else 0)
                      for node in ast.walk(function)) for function in functions]
        rows.append(dict(module=path.name, lines=len(path.read_text().splitlines()),
                         function_lines=max((f.end_lineno - f.lineno + 1 for f in functions), default=0),
                         decision_depth=depth(tree), cyclomatic_proxy=max(complexity, default=1)))
    # Complexity is an explicitly defined AST proxy, not a claim of cognitive complexity.
    return dict(module_budget=all(r['lines'] <= 180 for r in rows),
                function_budget=all(r['function_lines'] <= 60 for r in rows),
                depth_budget=all(r['decision_depth'] <= 5 for r in rows),
                branch_budget=all(r['cyclomatic_proxy'] <= 20 for r in rows), measurements=rows)


async def performance_probe():
    data = random.Random(19).randbytes(524288)

    def timed(size):
        start = time.perf_counter()
        scanner = RabinCDCScanner()
        total = sum(len(c.data) for c in scanner.feed(memoryview(data)[:size]))
        total += sum(len(c.data) for c in scanner.finish())
        return time.perf_counter() - start, total

    small = [timed(262144)[0] for _ in range(3)]
    large = [timed(524288)[0] for _ in range(3)]
    # These are local catastrophic-regression ceilings, not portable throughput SLAs.
    ratio = statistics.median(large) / max(statistics.median(small), 1e-9)
    gc.collect()
    tracemalloc.start()
    engine = await GovernanceLedgerEngine(receipt_capacity=64, event_capacity=64).start()
    start = time.perf_counter()
    try:
        sender = AgentGateway(engine, 'memory-speed')
        for _ in range(1500):
            await sender.wrap_output('small payload')
        retained, peak = tracemalloc.get_traced_memory()
        elapsed = time.perf_counter() - start
        records = len(engine.ledger)
        caches_bounded = engine.health()['receipt_cache'] <= 64 and engine.health()['event_cache'] <= 64
    finally:
        await engine.close()
        tracemalloc.stop()
    return dict(scanner_budget=max(large) < 5.0, scaling_budget=ratio < 4.0,
                ledger_budget=elapsed < 10.0, memory_budget=peak < 6 * 1024 * 1024,
                records=records, caches_bounded=caches_bounded,
                measurements=dict(scanner_small_seconds=small, scanner_large_seconds=large,
                                  doubling_ratio=ratio, ledger_seconds=elapsed,
                                  retained_bytes=retained, peak_bytes=peak))
