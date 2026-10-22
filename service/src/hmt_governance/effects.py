"""Durable effect intent and one-shot dispatch. Unknown outcomes never become retryable."""

import datetime
import uuid

from psycopg.types.json import Jsonb

from .authority import principal, session_authority
from .contracts import Rejected, identity
from .records import verified, verify


def view(row):
    return {
        key: str(value) if isinstance(value, (uuid.UUID, datetime.datetime)) else value
        for key, value in row.items()
        if key != "claim"
    }


async def prepare(database, cache, peer, envelope, effect_key):
    identity(effect_key)
    # Persist integrity quarantine before entering the intent transaction.
    await verify(database, cache, peer, envelope, role="execute")
    async with database.connection() as connection:
        _, session, record = await verified(
            connection, cache, peer, envelope, "execute"
        )
        existing = await (
            await connection.execute(
                "SELECT * FROM governance.effects WHERE session_id=%s AND sequence_number=%s",
                (session["session_id"], record["sequence_number"]),
            )
        ).fetchone()
        if existing:
            if existing["effect_key"] != effect_key:
                raise Rejected("EFFECT_IDEMPOTENCY_CONFLICT")
            return view(existing)
        row = await (
            await connection.execute(
                "INSERT INTO governance.effects(effect_id,session_id,sequence_number,effect_key,state,block_hash) "
                "VALUES(%s,%s,%s,%s,'PREPARED',%s) RETURNING *",
                (
                    uuid.uuid4(),
                    session["session_id"],
                    record["sequence_number"],
                    effect_key,
                    record["block_hash"],
                ),
            )
        ).fetchone()
        return view(row)


async def access(connection, peer, effect_id, role):
    actor = await principal(connection, peer, role)
    row = await (
        await connection.execute(
            "SELECT * FROM governance.effects WHERE effect_id=%s FOR UPDATE",
            (uuid.UUID(effect_id),),
        )
    ).fetchone()
    if row is None:
        raise Rejected("EFFECT_UNKNOWN")
    session = await (
        await connection.execute(
            "SELECT * FROM governance.sessions WHERE session_id=%s FOR UPDATE",
            (row["session_id"],),
        )
    ).fetchone()
    if actor["agent_id"] != session["target_agent"]:
        raise Rejected("WRONG_RECIPIENT")
    await session_authority(connection, session, actor)
    return row


async def claim(database, peer, effect_id):
    async with database.connection() as connection:
        row = await access(connection, peer, effect_id, "execute")
        if row["state"] != "PREPARED":
            return view(row) | dict(dispatch=False)
        token = uuid.uuid4()
        await connection.execute(
            "UPDATE governance.effects SET state='DISPATCHING',claim=%s,updated_at=clock_timestamp() WHERE effect_id=%s",
            (token, row["effect_id"]),
        )
        return view(row) | dict(state="DISPATCHING", claim=str(token), dispatch=True)


async def outcome(database, peer, effect_id, claim_id, state, witness):
    if state not in ("COMPLETED", "UNKNOWN") or type(witness) is not dict:
        raise Rejected("INVALID_OUTCOME")
    async with database.connection() as connection:
        row = await access(connection, peer, effect_id, "execute")
        if (
            row["state"] not in ("DISPATCHING", "UNKNOWN")
            or str(row["claim"]) != claim_id
        ):
            raise Rejected("STALE_DISPATCH_CLAIM")
        await connection.execute(
            "UPDATE governance.effects SET state=%s,result=%s,updated_at=clock_timestamp() WHERE effect_id=%s",
            (state, Jsonb(witness), row["effect_id"]),
        )
        return view(row) | dict(state=state, result=witness)


async def reconcile(database, peer, effect_id, witness):
    if type(witness) is not dict or witness.get("state") not in (
        "COMPLETED",
        "FAILED_NO_EFFECT",
        "UNKNOWN",
    ):
        raise Rejected("INVALID_RECONCILIATION")
    async with database.connection() as connection:
        row = await access(connection, peer, effect_id, "reconcile")
        if row["state"] not in ("DISPATCHING", "UNKNOWN"):
            raise Rejected("RECONCILIATION_NOT_REQUIRED")
        if (
            witness.get("effect_id") != effect_id
            or witness.get("block_hash") != row["block_hash"]
        ):
            raise Rejected("RECONCILIATION_IDENTITY_MISMATCH")
        await connection.execute(
            "UPDATE governance.effects SET state=%s,result=%s,updated_at=clock_timestamp() WHERE effect_id=%s",
            (witness["state"], Jsonb(witness), row["effect_id"]),
        )
        return view(row) | dict(state=witness["state"], result=witness)
