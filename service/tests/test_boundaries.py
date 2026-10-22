import asyncio
import copy
import json
import secrets
from dataclasses import replace

import pytest
from hmt_governance.contracts import GENESIS,Rejected,Settings,frame,strict_json
from hmt_governance.records import check_record
from hmt_governance.server import serve
from hmt_governance.contracts import canonical

from .support import Host,LAB,configuration


@pytest.mark.parametrize('raw',[b'{"x":1,"x":2}',b'{"x":NaN}',b'[]',b'null',b'{"x":"\\ud800"}',b'{'])
def test_json_invalid_cases(raw):
    with pytest.raises(Rejected): strict_json(raw)


def test_json_exact_size_depth_and_quoted_braces():
    try:
        result = strict_json(b'{}',2)
    except Rejected:
        assert False, 'An exact-size valid JSON body must be accepted'
    assert result == {}
    with pytest.raises(Rejected,match='BODY_CAPACITY'):strict_json(b'{}',1)
    assert strict_json(b'{"x":"'+b'{'*64+b'"}')['x']=='{'*64
    assert strict_json(b'{"x":'+b'['*31+b'0'+b']'*31+b'}')['x']
    with pytest.raises(Rejected,match='JSON_DEPTH_CAPACITY'):
        strict_json(b'{"x":'+b'['*32+b'0'+b']'*32+b'}')


@pytest.mark.parametrize('sequence',[-1,True,1.0,2**53,'0',None])
def test_sequence_boundaries_reject_before_storage(sequence):
    value=dict(session_id='s',sequence_number=sequence,source_agent='a',target_entity='b',payload='',previous_hash=GENESIS)
    with pytest.raises(Rejected,match='INVALID_SEQUENCE'):frame(value)


def test_utf8_payload_byte_boundary_and_no_surrogate():
    value=dict(session_id='s',sequence_number=0,source_agent='a',target_entity='b',payload='é'*3,previous_hash=GENESIS)
    assert frame(value,6)==value
    with pytest.raises(Rejected,match='PAYLOAD_CAPACITY'):frame(value,5)
    with pytest.raises(Rejected,match='INVALID_UNICODE'):frame(value|dict(payload='\ud800'))


@pytest.mark.parametrize('change',[dict(dsn='host=localhost sslmode=disable'),dict(redis_url='redis://localhost'),dict(redis_url='rediss://localhost?ssl_check_hostname=false'),dict(cache_key='x'*32),dict(workers=True),dict(poll_seconds=float('nan'))])
def test_host_settings_fail_closed(change):
    runtime,_=configuration()
    with pytest.raises((ValueError,TypeError)):Settings(**(runtime|change))


async def test_cache_encrypted_poisoned_and_namespace_acl():
    import redis.exceptions
    async with Host() as host:
        envelope=await host.verified()
        await host.verify(envelope)
        cache=host.engine.cache;key=cache.key(envelope['ledger_hash'])
        encoded=await cache.client.get(key)
        assert encoded and host.frame()['payload'].encode() not in encoded
        await cache.client.set(key,b'forged-ciphertext')
        assert (await host.verify(envelope))['verified'] is True and cache.errors>=1
        with pytest.raises(redis.exceptions.ResponseError):await cache.client.set('outside:governance','x')
        with pytest.raises(redis.exceptions.ResponseError):await cache.client.flushall()


async def test_authenticated_cache_metadata_mismatch_falls_back_to_postgres():
    async with Host() as host:
        envelope=await host.verified();await host.verify(envelope)
        cache=host.engine.cache
        value=await cache.get(envelope['ledger_hash']);value['payload_hash']='a'*64
        await cache.put(value)
        assert (await host.verify(envelope))['ledger_hash']==envelope['ledger_hash']
        assert cache.errors>=1


async def test_uds_api_schema_errors_no_remote_identity_override(tmp_path):
    async with Host() as host:
        server=await serve(host.engine,tmp_path/'api.sock')
        async with server:
            async def query(value):
                reader,writer=await asyncio.open_unix_connection(tmp_path/'api.sock')
                writer.write(canonical(value).encode()+b'\n');await writer.drain()
                response=json.loads(await reader.readline());writer.close();await writer.wait_closed()
                return response
            peer=host.peers[host.sender]
            def packet(operation,arguments):return dict(peer_key=peer,body=canonical(dict(operation=operation,arguments=arguments)))
            assert (await query(packet('peer_check',{})))['ok'] is True
            assert (await query(packet('commit',dict(value=host.frame(),request_key='r',peer_key=peer))))['code']=='INVALID_ARGUMENTS'
            assert (await query(packet('arbitrary_shell',{})))['code']=='UNKNOWN_OPERATION'
            assert (await query(packet('verify',[])))['code']=='INVALID_ARGUMENTS'
            assert (await query(dict(peer_key=peer,body='{}',extra='x')))['code']=='INVALID_PACKET'


async def test_request_capacity_and_close_owned_jobs():
    async with Host() as host:
        for _ in range(host.settings.max_jobs):host.engine._jobs.get_nowait()
        with pytest.raises(Rejected,match='REQUEST_CAPACITY'):
            await host.request('peer_check',{})
        for _ in range(host.settings.max_jobs):host.engine._jobs.put_nowait(None)
        await asyncio.gather(host.engine.close(),host.engine.close())
        assert all(t.done() for t in host.engine.tasks)
        with pytest.raises(Rejected,match='ENGINE_UNAVAILABLE'):await host.request('head',dict(session_id='s'))


def test_container_transport_network_cannot_reach_data_or_write_root():
    import sys
    sys.path.insert(0,str(LAB.parent))
    from lab import PINS,docker
    from .test_native import bridge
    manifest=json.loads((LAB/'manifest.json').read_text())
    endpoint=json.loads((LAB/'endpoints.json').read_text())['addresses']
    name=manifest['prefix']+'-boundary-'+secrets.token_hex(3)
    value=docker('run','--rm','--name',name,'--network',manifest['prefix']+'-transport',
        '--user','1000:1000','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
        '--memory','256m','--pids-limit','64','--entrypoint','/bridge',
        '--mount',f'type=bind,src={bridge()},dst=/bridge,readonly',
        PINS['postgres'],'boundary-probe',endpoint['postgres']+':5432',endpoint['redis']+':6379')
    report=json.loads(value)
    assert report['status']=='PASS' and report['postgres_unreachable'] and report['redis_unreachable']
    (LAB.parent/'evidence/container.json').write_text(canonical(report)+'\n')


def test_bound_external_witness_contract():
    from hmt_governance.runner import validate
    operation=dict(effect_id='example',block_hash='a'*64)
    valid=operation|dict(state='COMPLETED')
    validate(valid,operation)
    for witness in (valid|dict(effect_id='another'),valid|dict(block_hash='b'*64),valid|dict(state='PREPARED')):
        with pytest.raises(Rejected,match='EXTERNAL_WITNESS_MISMATCH'):
            validate(witness,operation)
