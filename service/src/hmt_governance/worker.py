"""Claim only a session's oldest pending receipt; atomic durable verification and lineage."""

import asyncio

import psycopg
from psycopg.types.json import Jsonb

from .authority import principal, session_authority
from .contracts import Rejected, block, digest


async def process_one(database):
    async with database.connection() as connection:
        row = await (
            await connection.execute(
                "SELECT r.* FROM governance.receipts r WHERE r.status='PENDING' AND NOT EXISTS "
                "(SELECT 1 FROM governance.receipts p WHERE p.session_id=r.session_id AND p.status='PENDING' AND p.admission_index<r.admission_index) "
                "ORDER BY r.admission_index LIMIT 1 FOR UPDATE SKIP LOCKED"
            )
        ).fetchone()
        if row is None:
            return False
        session = None
        try:
            actor = await principal(connection, row["peer_key"], "send")
            await connection.execute(
                "SELECT singleton FROM governance.budget WHERE singleton FOR UPDATE"
            )
            session = await (
                await connection.execute(
                    "SELECT * FROM governance.sessions WHERE session_id=%s FOR UPDATE",
                    (row["session_id"],),
                )
            ).fetchone()
            await session_authority(connection, session, actor)
            budget = await (
                await connection.execute(
                    "SELECT records FROM governance.budget WHERE singleton"
                )
            ).fetchone()
            if budget["records"] >= database.settings.max_records:
                raise Rejected("LEDGER_CAPACITY", "HELD")
            tx = row["frame"]
            ph = (
                await asyncio.to_thread(digest, tx["payload"])
                if len(tx["payload"]) >= 65536
                else digest(tx["payload"])
            )
            bh = block(tx, ph)
            async with connection.transaction():
                record = await (
                    await connection.execute(
                        "INSERT INTO governance.records(session_id,sequence_number,receipt_id,payload,source_agent,target_agent,previous_hash,payload_hash,block_hash) "
                        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                        (
                            tx["session_id"],
                            tx["sequence_number"],
                            row["receipt_id"],
                            tx["payload"],
                            tx["source_agent"],
                            tx["target_entity"],
                            tx["previous_hash"],
                            ph,
                            bh,
                        ),
                    )
                ).fetchone()
            result = dict(
                transaction=tx,
                payload_hash=record["payload_hash"],
                ledger_hash=record["block_hash"],
            )
            await finish(connection, row, "VERIFIED", result)
        except Rejected as exc:
            await failed(
                connection,
                row,
                session,
                exc.disposition
                if exc.disposition in ("HELD", "QUARANTINED")
                else "HELD",
                exc.code,
            )
        except psycopg.Error as exc:
            # The insert savepoint rolled back; keep the outer session lock until the failure is durable.
            if exc.sqlstate not in ("P0002", "P0003"):
                raise
            state = "QUARANTINED" if exc.sqlstate == "P0003" else "HELD"
            await failed(
                connection,
                row,
                session,
                state,
                "INTEGRITY_MISMATCH"
                if state == "QUARANTINED"
                else "AUTHORITY_OR_DEADLINE",
            )
        return True


async def finish(connection, row, status, result):
    await connection.execute(
        "UPDATE governance.receipts SET status=%s,result=%s WHERE receipt_id=%s",
        (status, Jsonb(result), row["receipt_id"]),
    )
    await connection.execute(
        "INSERT INTO governance.events(session_id,kind,detail) VALUES(%s,%s,%s)",
        (row["session_id"], status, str(row["receipt_id"])),
    )


async def failed(connection, row, session, state, reason):
    if session:
        await connection.execute(
            "UPDATE governance.sessions SET state=%s,reason=%s WHERE session_id=%s",
            (state, reason, row["session_id"]),
        )
    await finish(connection, row, state, dict(code=reason))
