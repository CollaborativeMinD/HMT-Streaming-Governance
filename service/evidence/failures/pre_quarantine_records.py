"""Always revalidate durable identity/state; cache data is only a hash-checked acceleration."""

from .authority import principal, session_authority
from .contracts import Rejected, block, canonical, digest, frame


def portable(row):
    return dict(
        session_id=row["session_id"],
        sequence_number=row["sequence_number"],
        source_agent=row["source_agent"],
        target_entity=row["target_agent"],
        payload=row["payload"],
        previous_hash=row["previous_hash"],
        payload_hash=row["payload_hash"],
        block_hash=row["block_hash"],
    )


def check_record(row, expected):
    value = {
        k: row[k]
        for k in (
            "session_id",
            "sequence_number",
            "source_agent",
            "target_entity",
            "payload",
            "previous_hash",
        )
    }
    frame(value)
    actual = digest(value["payload"])
    if (
        actual != expected["payload_hash"]
        or block(value, actual) != expected["block_hash"]
    ):
        raise Rejected("STORED_RECORD_CORRUPTION", "HELD")
    return value


async def verified(connection, cache, peer, envelope, role="receive"):
    actor = await principal(connection, peer, role)
    if type(envelope) is not dict or set(envelope) != {
        "transaction",
        "payload_hash",
        "ledger_hash",
    }:
        raise Rejected("INVALID_ENVELOPE")
    tx = frame(envelope["transaction"])
    session = await (
        await connection.execute(
            "SELECT * FROM governance.sessions WHERE session_id=%s FOR UPDATE",
            (tx["session_id"],),
        )
    ).fetchone()
    if session is None or actor["agent_id"] != session["target_agent"]:
        raise Rejected("WRONG_RECIPIENT")
    await session_authority(connection, session, actor)
    expected = await (
        await connection.execute(
            "SELECT payload_hash,block_hash FROM governance.records WHERE session_id=%s AND sequence_number=%s",
            (tx["session_id"], tx["sequence_number"]),
        )
    ).fetchone()
    if expected is None:
        raise Rejected("UNREGISTERED_RECORD", "QUARANTINED")
    value = await cache.get(expected["block_hash"])
    if value is not None:
        try:
            check_record(value, expected)
        except (Rejected, KeyError, TypeError, ValueError):
            value = None
            cache.errors += 1
    if value is None:
        row = await (
            await connection.execute(
                "SELECT * FROM governance.records WHERE session_id=%s AND sequence_number=%s",
                (tx["session_id"], tx["sequence_number"]),
            )
        ).fetchone()
        value = portable(row)
        check_record(value, expected)
        await cache.put(value)
    registered = {
        k: value[k]
        for k in (
            "session_id",
            "sequence_number",
            "source_agent",
            "target_entity",
            "payload",
            "previous_hash",
        )
    }
    incoming = {k: tx[k] for k in registered}
    if (
        canonical(incoming) != canonical(registered)
        or envelope["payload_hash"] != expected["payload_hash"]
        or envelope["ledger_hash"] != expected["block_hash"]
        or tx["target_entity"] != actor["agent_id"]
        or (
            tx.get("expected_hash") is not None
            and tx["expected_hash"] != expected["payload_hash"]
        )
    ):
        raise Rejected("HANDOFF_MISMATCH", "QUARANTINED")
    return actor, session, value


async def verify(database, cache, peer, envelope):
    failure = None
    async with database.connection() as connection:
        try:
            _, _, value = await verified(connection, cache, peer, envelope)
        except Rejected as exc:
            failure = exc
            if exc.disposition == "QUARANTINED":
                session = envelope["transaction"]["session_id"]
                await connection.execute(
                    "UPDATE governance.sessions SET state='QUARANTINED',reason=%s WHERE session_id=%s",
                    (exc.code, session),
                )
                await connection.execute(
                    "INSERT INTO governance.events(session_id,kind,detail) VALUES(%s,'SYSTEM_QUARANTINE',%s)",
                    (session, exc.code),
                )
        if failure is None:
            result = dict(verified=True, ledger_hash=value["block_hash"])
    if failure:
        raise failure
    return result
