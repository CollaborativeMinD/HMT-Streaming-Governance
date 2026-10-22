"""Dispatch declared scenarios; unknown IDs fail rather than silently pass."""
from vv_probe_boundary import boundary_probe
from vv_probe_quality import performance_probe, structure_probe
from vv_probe_resources import resource_probe, scanner_probe, stream_probe


async def probe(case):
    if case == 'evidence':
        from vv_common import connect_readonly, source_seal
        with connect_readonly() as db:
            counts = [db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                      for table in ('invariants', 'failure_modes', 'gates')]
            return dict(complete_cartesian=db.execute('SELECT COUNT(*) FROM cross_findings').fetchone()[0] ==
                        counts[0] * counts[1] * counts[2], no_orphans=not db.execute('PRAGMA foreign_key_check').fetchall(),
                        all_requirements=not db.execute('''SELECT id FROM requirements r WHERE NOT EXISTS
                          (SELECT 1 FROM assertions a WHERE a.requirement_id=r.id)''').fetchall(),
                        provenance=db.execute("SELECT value FROM metadata WHERE key='source_seal'").fetchone()[0] == source_seal())
    if case == 'scanner':
        return scanner_probe()
    if case == 'structure':
        return structure_probe()
    if case == 'performance':
        return await performance_probe()
    if case == 'api':
        from vv_api import api_probe
        return await api_probe()
    if case in ('queue', 'pending_bytes', 'ledger', 'retention', 'deadline', 'schema', 'concurrent_close'):
        return await resource_probe(case)
    if case in ('stream', 'sink_failure'):
        return await stream_probe(case)
    if case in ('nominal', 'parent', 'sequence', 'replay', 'payload_assertion', 'unregistered',
                'closed', 'payload', 'metadata', 'recipient', 'cross_session', 'block_hash',
                'verifier_fault', 'verification_capacity', 'hold_race', 'snapshot', 'cancel_verification'):
        return await boundary_probe(case)
    raise ValueError(f'Undeclared scenario: {case}')
