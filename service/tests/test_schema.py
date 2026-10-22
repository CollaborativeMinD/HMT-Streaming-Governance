"""Actual startup schema gates and NULL-safe PostgreSQL receipt binding."""
import json

import psycopg
import pytest
from psycopg.types.json import Jsonb
from hmt_governance import admission
from hmt_governance.contracts import Rejected,block,digest
from hmt_governance.database import Database

from .support import Host


async def test_startup_disabled_trigger_holds_and_closes_failed_pool():
    async with Host() as host:
        await host.engine.close()
        database=Database(host.settings)
        await host.sql('ALTER TABLE governance.records DISABLE TRIGGER immutable_records')
        try:
            with pytest.raises(Rejected,match='SCHEMA_TRIGGER_DRIFT'):
                await database.start()
            assert database.pool.closed
        finally:
            await host.sql('ALTER TABLE governance.records ENABLE TRIGGER immutable_records')
            await database.close()


async def test_missing_receipt_hash_cannot_exploit_sql_null_comparison():
    async with Host() as host:
        await host.engine.close()
        database=await Database(host.settings).start()
        try:
            frame=host.frame()
            receipt=await admission.enqueue(database,host.peers[host.sender],frame,'sql-null-test')
            ph=digest(frame['payload']);bh=block(frame,ph)
            async with await psycopg.AsyncConnection.connect(host.settings.dsn) as connection:
                await connection.execute("INSERT INTO governance.records(session_id,sequence_number,receipt_id,payload,source_agent,target_agent,previous_hash,payload_hash,block_hash) VALUES(%s,0,%s,%s,%s,%s,%s,%s,%s)",
                    (frame['session_id'],receipt['receipt_id'],frame['payload'],frame['source_agent'],frame['target_entity'],frame['previous_hash'],ph,bh))
                with pytest.raises(psycopg.Error,match='UNREGISTERED_RECEIPT'):
                    await connection.execute("UPDATE governance.receipts SET status='VERIFIED',result=%s WHERE receipt_id=%s",(Jsonb(dict(transaction=frame)),receipt['receipt_id']))
                await connection.rollback()
            assert not await host.sql('SELECT * FROM governance.records WHERE session_id=%s',(frame['session_id'],))
        finally:
            await database.close()
