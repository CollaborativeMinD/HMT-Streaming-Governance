"""Scenario drivers only; independent suites supply the assertions."""
import asyncio
import hashlib
import json

from governance_core import (
    AgentGateway,
    GovernanceLedgerEngine,
    HoldError,
    QuarantineError,
)


def observe(engine, receiver, effects, outcome, **extra):
    health = engine.health()
    return dict(outcome=outcome, effects=effects, context=len(receiver.context),
                quarantined=health['quarantined_sessions'], held=health['held_sessions'],
                records=len(engine.ledger), pending_bytes=health['pending_bytes'], **extra)


async def boundary_probe(case):
    engine = await GovernanceLedgerEngine().start()
    receiver = AgentGateway(engine, 'case', 'receiver')
    effects = []
    try:
        sender = AgentGateway(engine, 'case', 'sender')
        envelope = await sender.wrap_output('safe', 'receiver')
        receiver.context.append('old context')
        if case in ('parent', 'sequence', 'replay', 'payload_assertion'):
            tx = dict(envelope['transaction'])
            tx.update(sequence_number=1, previous_hash=envelope['ledger_hash'])
            if case == 'parent':
                tx['previous_hash'] = 'f' * 64
            elif case == 'sequence':
                tx['sequence_number'] = 2
            elif case == 'replay':
                tx['sequence_number'] = 0
            else:
                tx['expected_hash'] = hashlib.sha256(b'changed').hexdigest()
            pending = await engine.governance_commit_state(**tx)
            result = await engine.wait(pending['tracking_token'])
            try:
                await receiver.execute_verified(envelope, effects.append)
            except QuarantineError:
                pass
            return observe(engine, receiver, effects, result['status'])
        if case == 'unregistered':
            result = await engine.governance_verify_payload('unknown', hashlib.sha256(b'unknown').hexdigest())
            return dict(verified=result['verified'], registered=result['registered'])
        if case == 'closed':
            await engine.close()
        elif case == 'payload':
            envelope['transaction']['payload'] = 'changed'
        elif case == 'metadata':
            envelope['transaction']['source_agent'] = 'attacker'
        elif case == 'recipient':
            envelope['transaction']['target_entity'] = 'attacker'
        elif case == 'cross_session':
            envelope['transaction']['session_id'] = 'other'
        elif case == 'block_hash':
            envelope['ledger_hash'] = 'f' * 64
        elif case == 'verifier_fault':
            async def broken(*args):
                raise RuntimeError('injected verifier failure')
            engine._compute = broken
        elif case == 'verification_capacity':
            engine.verification_capacity = 1
            # Hold one real verification at the hashing await.
            entered, release = asyncio.Event(), asyncio.Event()
            original = engine._compute

            async def blocked(*args):
                entered.set()
                await release.wait()
                return await original(*args)

            engine._compute = blocked
            first = asyncio.create_task(engine.governance_verify_payload('safe', envelope['payload_hash']))
            await entered.wait()
            try:
                result = await engine.governance_verify_payload('safe', envelope['payload_hash'])
            finally:
                release.set()
                await first
            return dict(verified=result['verified'], outcome=result.get('disposition'),
                        quarantined=engine.health()['quarantined_sessions'],
                        active_verifications=engine.health()['active_verifications'])
        elif case == 'hold_race':
            original = engine._compute

            async def interrupted(*args):
                result = await original(*args)
                engine._hold('INJECTED_HOLD')
                return result

            engine._compute = interrupted
        elif case in ('snapshot', 'cancel_verification'):
            entered, release = asyncio.Event(), asyncio.Event()
            original = engine._compute

            async def delayed(*args):
                entered.set()
                await release.wait()
                return await original(*args)

            engine._compute = delayed
            task = asyncio.create_task(receiver.execute_verified(envelope, effects.append))
            await entered.wait()
            if case == 'snapshot':
                envelope['transaction']['payload'] = 'unverified replacement'
                release.set()
                await task
                return observe(engine, receiver, effects, 'delivered')
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return observe(engine, receiver, effects, 'cancelled',
                           active_verifications=engine.health()['active_verifications'])
        outcome = 'delivered'
        try:
            await receiver.execute_verified(envelope, effects.append)
        except HoldError:
            outcome = 'held'
        except QuarantineError:
            outcome = 'quarantined'
        # Oracle uses the published wire contract, not runtime hash helpers.
        material = dict(version=1, session_id='case', sequence_number=0, source_agent='sender',
                        target_entity='receiver', payload_hash=hashlib.sha256(b'safe').hexdigest(),
                        previous_hash='0' * 64)
        expected_block = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(',', ':'),
                                                   ensure_ascii=False).encode()).hexdigest()
        return observe(engine, receiver, effects, outcome,
                       block_oracle=engine.ledger[('case', 0)].block_hash == expected_block)
    finally:
        await engine.close()
