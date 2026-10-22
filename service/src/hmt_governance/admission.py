"""Durable bounded inbox. A pending receipt survives process loss and grants no trust."""

import uuid

from psycopg.types.json import Jsonb

from .authority import principal, session_authority
from .contracts import GENESIS, Rejected, canonical, frame, identity


async def enqueue(database, peer, value, request_key):
    value = frame(value)
    identity(request_key)
    async with database.connection() as connection:
        actor = await principal(connection, peer, "send")
        if value["source_agent"] != actor["agent_id"]:
            raise Rejected("SOURCE_SPOOFING")
        await connection.execute(
            "SELECT singleton FROM governance.budget WHERE singleton FOR UPDATE"
        )
        session = await (
            await connection.execute(
                "SELECT * FROM governance.sessions WHERE session_id=%s FOR UPDATE",
                (value["session_id"],),
            )
        ).fetchone()
        if session is None:
            session = await create_session(connection, database.settings, actor, value)
        if (
            session["source_agent"] != actor["agent_id"]
            or session["target_agent"] != value["target_entity"]
        ):
            raise Rejected("SESSION_FORBIDDEN")
        await session_authority(connection, session, actor)
        existing = await (
            await connection.execute(
                "SELECT * FROM governance.receipts WHERE session_id=%s AND request_key=%s",
                (value["session_id"], request_key),
            )
        ).fetchone()
        if existing:
            if canonical(existing["frame"]) != canonical(value):
                raise Rejected("IDEMPOTENCY_CONFLICT")
            return dict(
                receipt_id=str(existing["receipt_id"]),
                status=existing["status"],
                replayed=True,
            )
        count = await (
            await connection.execute(
                "SELECT count(*) AS n FROM governance.receipts WHERE status='PENDING'"
            )
        ).fetchone()
        if count["n"] >= database.settings.max_pending:
            raise Rejected("INBOX_CAPACITY", "HELD")
        receipt_id = uuid.uuid4()
        await connection.execute(
            "INSERT INTO governance.receipts(receipt_id,session_id,request_key,peer_key,frame,expires_at,status) "
            "VALUES(%s,%s,%s,%s,%s,clock_timestamp()+(%s*interval '1 second'),'PENDING')",
            (
                receipt_id,
                value["session_id"],
                request_key,
                peer,
                Jsonb(value),
                database.settings.transaction_seconds,
            ),
        )
        return dict(receipt_id=str(receipt_id), status="PENDING", replayed=False)


async def create_session(connection, settings, actor, value):
    target = await (
        await connection.execute(
            "SELECT * FROM governance.lock_agents(%s)",
            ([value["target_entity"]],),
        )
    ).fetchone()
    budget = await (
        await connection.execute(
            "SELECT sessions FROM governance.budget WHERE singleton"
        )
    ).fetchone()
    if target is None:
        raise Rejected("UNKNOWN_RECIPIENT")
    if budget["sessions"] >= settings.max_sessions:
        raise Rejected("SESSION_CAPACITY", "HELD")
    session = dict(
        session_id=value["session_id"],
        source_agent=actor["agent_id"],
        target_agent=target["agent_id"],
        source_epoch=actor["epoch"],
        target_epoch=target["epoch"],
        state="ACTIVE",
        reason=None,
        sequence_number=-1,
        head_hash=GENESIS,
    )
    await session_authority(connection, session, actor)
    await connection.execute(
        "INSERT INTO governance.sessions(session_id,source_agent,target_agent,source_epoch,target_epoch,state) "
        "VALUES(%s,%s,%s,%s,%s,'ACTIVE')",
        tuple(
            session[k]
            for k in (
                "session_id",
                "source_agent",
                "target_agent",
                "source_epoch",
                "target_epoch",
            )
        ),
    )
    await connection.execute(
        "UPDATE governance.budget SET sessions=sessions+1 WHERE singleton"
    )
    return session


async def receipt(database, peer, receipt_id):
    try:
        receipt_id = uuid.UUID(receipt_id)
    except (ValueError, TypeError, AttributeError):
        raise Rejected("INVALID_RECEIPT") from None
    async with database.connection() as connection:
        actor = await principal(connection, peer, "read")
        row = await (
            await connection.execute(
                "SELECT * FROM governance.receipts WHERE receipt_id=%s", (receipt_id,)
            )
        ).fetchone()
        if row is None:
            raise Rejected("RECEIPT_UNKNOWN")
        session = await (
            await connection.execute(
                "SELECT * FROM governance.sessions WHERE session_id=%s",
                (row["session_id"],),
            )
        ).fetchone()
        if actor["agent_id"] not in (session["source_agent"], session["target_agent"]):
            raise Rejected("SESSION_FORBIDDEN")
        # Receipt history remains observable; authorization to use it is rechecked on verify/dispatch.
        return dict(
            receipt_id=str(row["receipt_id"]),
            status=row["status"],
            result=row["result"],
        )


async def head(database, peer, session_id):
    identity(session_id)
    async with database.connection() as connection:
        actor = await principal(connection, peer, "read")
        row = await (
            await connection.execute(
                "SELECT * FROM governance.sessions WHERE session_id=%s", (session_id,)
            )
        ).fetchone()
        if row is None:
            return dict(sequence_number=-1, head_hash=GENESIS, state="NEW")
        await session_authority(connection, row, actor)
        return {key: row[key] for key in ("sequence_number", "head_hash", "state")}
