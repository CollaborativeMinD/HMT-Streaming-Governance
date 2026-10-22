import asyncio
import copy
import secrets
from dataclasses import replace

import psycopg
import pytest
from hmt_governance import admission, worker
from hmt_governance.contracts import (
    GENESIS,
    Rejected,
    Settings,
    block,
    digest,
    frame,
    strict_json,
)
from hmt_governance.engine import Engine

from .support import Host


async def test_atomic_receipt_chain_and_unicode_roundtrip():
    async with Host() as host:
        envelope = await host.verified()
        assert envelope["payload_hash"] == digest(host.frame()["payload"])
        assert envelope["ledger_hash"] == block(host.frame(), envelope["payload_hash"])
        assert (await host.verify(envelope))["verified"] is True
        rows = await host.sql(
            "SELECT r.*,s.head_hash FROM governance.records r JOIN governance.sessions s USING(session_id) WHERE session_id=%s",
            (host.frame()["session_id"],),
        )
        assert len(rows) == 1 and rows[0]["head_hash"] == envelope["ledger_hash"]
        assert (
            len(
                await host.sql(
                    "SELECT * FROM governance.events WHERE session_id=%s",
                    (host.frame()["session_id"],),
                )
            )
            == 1
        )


async def test_pending_inbox_survives_restart_before_worker_runs():
    async with Host() as host:
        await host.engine.close()
        from hmt_governance.database import Database

        database = await Database(host.settings).start()
        pending = await admission.enqueue(
            database, host.peers[host.sender], host.frame(), "pending-restart"
        )
        await database.close()
        host.engine = await Engine(host.settings).start()
        assert (await host.settled(pending))["status"] == "VERIFIED"


async def test_verified_state_and_idempotent_ack_survive_new_engine():
    async with Host() as host:
        envelope = await host.verified()
        await host.engine.close()
        host.engine = await Engine(host.settings).start()
        assert (await host.verify(envelope))["verified"] is True
        replay = await host.commit()
        assert replay["status"] == "VERIFIED" and replay["replayed"] is True
        assert (
            await host.request("head", dict(session_id=host.frame()["session_id"]))
        )["head_hash"] == envelope["ledger_hash"]
        assert (
            len(
                await host.sql(
                    "SELECT * FROM governance.records WHERE session_id=%s",
                    (host.frame()["session_id"],),
                )
            )
            == 1
        )


async def test_same_idempotency_key_cannot_change_payload():
    async with Host() as host:
        await host.verified()
        with pytest.raises(Rejected, match="IDEMPOTENCY_CONFLICT"):
            await host.commit(host.frame(payload="changed"))


async def test_parallel_duplicate_submission_creates_one_record():
    async with Host() as host:
        receipts = await asyncio.gather(*(host.commit() for _ in range(8)))
        assert len({r["receipt_id"] for r in receipts}) == 1
        assert (await host.settled(receipts[0]))["status"] == "VERIFIED"
        assert (
            len(
                await host.sql(
                    "SELECT * FROM governance.records WHERE session_id=%s",
                    (host.frame()["session_id"],),
                )
            )
            == 1
        )


async def test_wrong_parent_and_sequence_quarantine_persistently():
    for changes in (
        dict(previous_hash="f" * 64),
        dict(sequence_number=1),
        dict(expected_hash="a" * 64),
    ):
        async with Host() as host:
            receipt = await host.settled(await host.commit(host.frame(**changes)))
            assert receipt["status"] == "QUARANTINED"
            assert not await host.sql(
                "SELECT * FROM governance.records WHERE session_id=%s",
                (host.frame()["session_id"],),
            )
            await host.engine.close()
            host.engine = await Engine(host.settings).start()
            with pytest.raises(Rejected):
                await host.commit(key="different-request")


async def test_sender_spoofing_and_unknown_peer_do_not_create_sessions():
    async with Host() as host:
        with pytest.raises(Rejected, match="SOURCE_SPOOFING"):
            await host.commit(host.frame(source_agent=host.receiver))
        with pytest.raises(Rejected, match="UNAUTHORIZED"):
            await host.engine.request(
                "nodekey:" + secrets.token_hex(32),
                "commit",
                dict(value=host.frame(), request_key="unknown-peer"),
            )
        assert not await host.sql(
            "SELECT * FROM governance.sessions WHERE session_id=%s",
            (host.frame()["session_id"],),
        )


async def test_ungranted_route_and_missing_sender_role_are_denied():
    async with Host() as host:
        with pytest.raises(Rejected, match="ROUTE_REVOKED"):
            await host.commit(host.frame(target_entity=host.intruder))
        await host.sql(
            "UPDATE governance.agents SET roles=ARRAY['read','connect']::text[] WHERE agent_id=%s",
            (host.sender,),
        )
        with pytest.raises(Rejected, match="UNAUTHORIZED"):
            await host.commit()


async def test_recipient_and_metadata_tampering_halt_and_quarantine():
    async with Host() as host:
        envelope = await host.verified()
        with pytest.raises(Rejected, match="WRONG_RECIPIENT"):
            await host.verify(envelope, host.intruder)
        assert (await host.verify(envelope))["verified"] is True
        changed = copy.deepcopy(envelope)
        changed["transaction"]["payload"] = "tampered"
        with pytest.raises(Rejected, match="HANDOFF_MISMATCH"):
            await host.verify(changed)
        with pytest.raises(Rejected):
            await host.verify(envelope)
        rows = await host.sql(
            "SELECT * FROM governance.events WHERE session_id=%s AND kind='SYSTEM_QUARANTINE'",
            (host.frame()["session_id"],),
        )
        assert len(rows) == 1


async def test_fresh_authority_and_route_override_warm_cache():
    for mode in ("identity", "epoch", "route", "expiry"):
        async with Host() as host:
            envelope = await host.verified()
            await host.verify(envelope)
            await host.verify(envelope)
            assert host.engine.cache.hits >= 1
            if mode == "identity":
                await host.revoke()
            elif mode == "epoch":
                await host.sql(
                    "UPDATE governance.agents SET epoch=epoch+1 WHERE agent_id=%s",
                    (host.sender,),
                )
            elif mode == "route":
                await host.sql(
                    "UPDATE governance.routes SET active=false WHERE source_agent=%s",
                    (host.sender,),
                )
            else:
                await host.sql(
                    "UPDATE governance.agents SET expires_at=clock_timestamp()-interval '1 second' WHERE agent_id=%s",
                    (host.sender,),
                )
            with pytest.raises(Rejected):
                await host.verify(envelope)


async def test_revocation_after_enqueue_prevents_commit():
    async with Host() as host:
        await host.engine.close()
        from hmt_governance.database import Database

        db = await Database(host.settings).start()
        pending = await admission.enqueue(
            db, host.peers[host.sender], host.frame(), "revoked-pending"
        )
        await host.revoke()
        await worker.process_one(db)
        await db.close()
        host.engine = await Engine(host.settings).start()
        result = await host.settled(pending, who=host.receiver)
        assert result["status"] == "HELD"
        assert not await host.sql(
            "SELECT * FROM governance.records WHERE session_id=%s",
            (host.frame()["session_id"],),
        )


async def test_expired_pending_receipt_cannot_commit():
    async with Host(transaction_seconds=0.01) as host:
        await host.engine.close()
        from hmt_governance.database import Database

        db = await Database(host.settings).start()
        pending = await admission.enqueue(
            db, host.peers[host.sender], host.frame(), "expired-pending"
        )
        await asyncio.sleep(0.03)
        await worker.process_one(db)
        await db.close()
        host.engine = await Engine(host.settings).start()
        assert (await host.settled(pending))["status"] == "HELD"
        assert not await host.sql(
            "SELECT * FROM governance.records WHERE session_id=%s",
            (host.frame()["session_id"],),
        )


async def test_actual_database_append_only_and_role_boundaries():
    async with Host() as host:
        await host.verified()
        async with await psycopg.AsyncConnection.connect(
            host.settings.dsn
        ) as connection:
            for statement in (
                "DELETE FROM governance.records",
                "UPDATE governance.agents SET active=true",
                "ALTER TABLE governance.records DISABLE TRIGGER ALL",
            ):
                with pytest.raises(psycopg.Error):
                    async with connection.transaction():
                        await connection.execute(statement)
        with pytest.raises(psycopg.Error):
            await host.sql(
                "DELETE FROM governance.records WHERE session_id=%s",
                (host.frame()["session_id"],),
            )
        with pytest.raises(psycopg.Error):
            await host.sql(
                "UPDATE governance.receipts SET status='HELD' WHERE session_id=%s",
                (host.frame()["session_id"],),
            )


async def test_record_capacity_holds_without_eviction():
    async with Host() as host:
        baseline = (
            await host.sql("SELECT records FROM governance.budget WHERE singleton")
        )[0]["records"]
        await host.engine.close()
        host.settings = replace(host.settings, max_records=baseline + 1)
        host.engine = await Engine(host.settings).start()
        envelope = await host.verified()
        result = await host.settled(
            await host.commit(
                host.frame(sequence_number=1, previous_hash=envelope["ledger_hash"]),
                key="next-record",
            )
        )
        assert result["status"] == "HELD"
        assert (
            len(
                await host.sql(
                    "SELECT * FROM governance.records WHERE session_id=%s",
                    (host.frame()["session_id"],),
                )
            )
            == 1
        )


def test_strict_boundaries_and_independent_version_one_identity():
    tx = dict(
        session_id="s",
        sequence_number=0,
        source_agent="a",
        target_entity="b",
        payload="é",
        previous_hash=GENESIS,
    )
    assert frame(tx) == tx
    for changes in (
        dict(sequence_number=True),
        dict(sequence_number=0.0),
        dict(previous_hash="bad"),
        dict(payload="\ud800"),
        dict(extra="client-authority"),
    ):
        with pytest.raises(Rejected):
            frame(tx | changes)
    for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b"[]", b'{"a":"\xff"}'):
        with pytest.raises(Rejected):
            strict_json(raw)
    with pytest.raises(ValueError):
        Settings(dsn="unused", workers=True)
