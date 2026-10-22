"""Host-registered effects: commit a one-shot claim, dispatch once, preserve unknown outcomes."""

import asyncio

from . import effects
from .contracts import Rejected
from .records import check_record, portable


async def operation(engine, peer, effect_id, role):
    if engine.closed or engine.held:
        raise Rejected("ENGINE_UNAVAILABLE", "HELD")
    async with engine.database.connection() as connection:
        effect = await effects.access(connection, peer, effect_id, role)
        session = await (
            await connection.execute(
                "SELECT target_agent FROM governance.sessions WHERE session_id=%s",
                (effect["session_id"],),
            )
        ).fetchone()
        row = await (
            await connection.execute(
                "SELECT * FROM governance.records WHERE session_id=%s AND sequence_number=%s",
                (effect["session_id"], effect["sequence_number"]),
            )
        ).fetchone()
        check_record(portable(row), row)
        adapter = engine.adapters.get(session["target_agent"])
        if adapter is None:
            raise Rejected("NO_REGISTERED_EFFECT_ADAPTER", "HELD")
        value = dict(
            effect_id=effect_id, block_hash=effect["block_hash"], payload=row["payload"]
        )
        return effect, adapter, value


async def execute(engine, peer, effect_id):
    _, adapter, value = await operation(engine, peer, effect_id, "execute")
    claim = await effects.claim(engine.database, peer, effect_id)
    if not claim["dispatch"]:
        return claim
    try:
        # Refresh after the claim; the immutable operation itself never comes from caller-provided arguments.
        await operation(engine, peer, effect_id, "execute")
        witness = await adapter.dispatch(value)
        validate(witness, value)
        if witness["state"] != "COMPLETED":
            raise Rejected("EXTERNAL_OUTCOME_UNKNOWN", "HELD")
        return await effects.outcome(
            engine.database, peer, effect_id, claim["claim"], "COMPLETED", witness
        )
    except asyncio.CancelledError:
        task = asyncio.create_task(
            mark_unknown(engine, peer, effect_id, claim["claim"])
        )
        try:
            await asyncio.shield(task)
        except BaseException:
            pass  # The durable DISPATCHING state also forbids a new dispatch.
        raise
    except Exception:
        await mark_unknown(engine, peer, effect_id, claim["claim"])
        return {k: v for k, v in claim.items() if k != "claim"} | dict(
            state="UNKNOWN", dispatch=False
        )


async def mark_unknown(engine, peer, effect_id, claim):
    try:
        await effects.outcome(
            engine.database,
            peer,
            effect_id,
            claim,
            "UNKNOWN",
            dict(reason="ACKNOWLEDGEMENT_UNRESOLVED"),
        )
    except Rejected:
        pass  # An unavailable database leaves DISPATCHING durable, never PREPARED.


def validate(witness, operation):
    if (
        type(witness) is not dict
        or witness.get("effect_id") != operation["effect_id"]
        or witness.get("block_hash") != operation["block_hash"]
        or witness.get("state") not in ("COMPLETED", "FAILED_NO_EFFECT", "UNKNOWN")
    ):
        raise Rejected("EXTERNAL_WITNESS_MISMATCH", "HELD")


async def reconcile(engine, peer, effect_id):
    effect, adapter, value = await operation(engine, peer, effect_id, "reconcile")
    if effect["state"] not in ("DISPATCHING", "UNKNOWN"):
        raise Rejected("RECONCILIATION_NOT_REQUIRED")
    witness = await adapter.lookup(value)
    validate(witness, value)
    return await effects.reconcile(engine.database, peer, effect_id, witness)
