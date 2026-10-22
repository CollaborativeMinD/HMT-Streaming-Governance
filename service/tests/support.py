import asyncio
import json
import os
import secrets
from pathlib import Path

import psycopg
from hmt_governance.contracts import GENESIS, Settings
from hmt_governance.engine import Engine
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / ".lab"


def configuration():
    path = Path(os.environ.get("HMT_TEST_CONFIG", LAB / "runtime.json"))
    runtime = json.loads(path.read_text())
    admin_path = Path(os.environ.get("HMT_TEST_ADMIN", LAB / "admin.json"))
    return runtime, json.loads(admin_path.read_text())


class Host:
    def __init__(self, **changes):
        runtime, admin = configuration()
        self.admin = admin["dsn"]
        self.settings = Settings(
            **runtime, cache_key=secrets.token_bytes(32), **changes
        )
        self.suffix = secrets.token_hex(6)
        self.sender = "sender-" + self.suffix
        self.receiver = "receiver-" + self.suffix
        self.intruder = "intruder-" + self.suffix
        self.peers = {
            name: "nodekey:" + secrets.token_hex(32)
            for name in (self.sender, self.receiver, self.intruder)
        }
        self.engine = None

    async def __aenter__(self):
        async with await psycopg.AsyncConnection.connect(self.admin) as connection:
            for name, peer in self.peers.items():
                await connection.execute(
                    "INSERT INTO governance.agents VALUES(%s,%s,1,true,clock_timestamp()+interval '1 hour',%s)",
                    (
                        name,
                        peer,
                        ["connect", "send", "read", "receive", "execute", "reconcile"],
                    ),
                )
            await connection.execute(
                "INSERT INTO governance.routes VALUES(%s,%s,true)",
                (self.sender, self.receiver),
            )
        self.engine = await Engine(self.settings).start()
        return self

    async def __aexit__(self, *args):
        await self.engine.close()
        for adapter in self.engine.adapters.values():
            if hasattr(adapter, "close"):
                await adapter.close()

    def frame(self, **changes):
        return (
            dict(
                session_id="session-" + self.suffix,
                sequence_number=0,
                source_agent=self.sender,
                target_entity=self.receiver,
                payload="agent trace · 東京",
                previous_hash=GENESIS,
            )
            | changes
        )

    async def sql(self, statement, params=()):
        async with await psycopg.AsyncConnection.connect(
            self.admin, row_factory=dict_row
        ) as connection:
            cursor = await connection.execute(statement, params)
            return await cursor.fetchall() if cursor.description else []

    async def request(self, operation, arguments, who=None):
        return await self.engine.request(
            self.peers[who or self.sender], operation, arguments
        )

    async def commit(self, value=None, key="request-0001"):
        return await self.request(
            "commit", dict(value=value or self.frame(), request_key=key)
        )

    async def settled(self, pending, who=None):
        for _ in range(200):
            result = await self.request(
                "status", dict(receipt_id=pending["receipt_id"]), who=who
            )
            if result["status"] != "PENDING":
                return result
            await asyncio.sleep(0.01)
        raise AssertionError("Durable worker did not settle")

    async def verified(self):
        result = await self.settled(await self.commit())
        assert result["status"] == "VERIFIED", (result, self.engine.database.last_error)
        return result["result"]

    async def verify(self, envelope, who=None):
        return await self.request(
            "verify", dict(envelope=envelope), who=who or self.receiver
        )

    async def revoke(self, who=None):
        await self.sql(
            "UPDATE governance.agents SET active=false,epoch=epoch+1 WHERE agent_id=%s",
            (who or self.sender,),
        )
