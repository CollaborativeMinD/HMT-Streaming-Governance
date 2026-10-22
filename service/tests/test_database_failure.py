"""Destructive only to the named disposable lab: real PostgreSQL process loss and WAL recovery."""
import asyncio
import json
import sys
import time

import psycopg
import pytest
from hmt_governance.contracts import Rejected

from .support import Host,LAB

async def test_postgres_crash_disables_warm_cache_then_recovers_durable_record():
    sys.path.insert(0,str(LAB.parent))
    from lab import docker
    name=json.loads((LAB/'manifest.json').read_text())['prefix']+'-postgres'
    async with Host() as host:
        envelope=await host.verified();await host.verify(envelope)
        assert host.engine.cache.hits==0
        assert (await host.verify(envelope))['verified']
        assert host.engine.cache.hits>=1
        await asyncio.to_thread(docker,'kill','--signal','KILL',name)
        try:
            with pytest.raises(Rejected,match='POSTGRES_UNAVAILABLE|OUTCOME_UNKNOWN'):
                await host.verify(envelope)
        finally:
            await asyncio.to_thread(docker,'start',name)
            deadline=time.monotonic()+15
            while True:
                try:
                    async with await psycopg.AsyncConnection.connect(host.admin):break
                except psycopg.Error:
                    assert time.monotonic()<deadline,'Owned PostgreSQL did not recover'
                    await asyncio.sleep(.1)
        assert (await host.verify(envelope))['verified'] is True
        rows=await host.sql('SELECT count(*) AS count FROM governance.records WHERE session_id=%s',(host.frame()['session_id'],))
        assert rows[0]['count']==1
