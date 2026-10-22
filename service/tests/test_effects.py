"""A real TLS/mTLS sink, durable effect claims and read-only reconciliation."""
import asyncio
import datetime
import json
import ssl
from types import MappingProxyType

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from hmt_governance.contracts import Rejected, canonical
from hmt_governance.engine import Engine
from hmt_governance.sink import HTTPSink

from .support import Host


def certificates(path):
    now=datetime.datetime.now(datetime.timezone.utc)
    root=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    issuer=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Temporary sink test CA')])
    def build(key,name):
        return (x509.CertificateBuilder().subject_name(name).issuer_name(issuer)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-datetime.timedelta(minutes=1)).not_valid_after(now+datetime.timedelta(hours=1)))
    ca=build(root,issuer).add_extension(x509.BasicConstraints(ca=True,path_length=0),True).sign(root,hashes.SHA256())
    (path/'ca.crt').write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    for name,usage in [('server',ExtendedKeyUsageOID.SERVER_AUTH),('client',ExtendedKeyUsageOID.CLIENT_AUTH)]:
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        cert=(build(key,x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,name)]))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]),False)
            .add_extension(x509.ExtendedKeyUsage([usage]),False).sign(root,hashes.SHA256()))
        (path/(name+'.crt')).write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (path/(name+'.key')).write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        (path/(name+'.key')).chmod(0o600)


class Sink:
    def __init__(self,path,mode='normal'):
        self.path,self.mode=path,mode
        self.posts=self.gets=0
        self.applied={}
        self.clients=[]
    async def start(self):
        certificates(self.path)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.path/'server.crt',self.path/'server.key')
        context.load_verify_locations(self.path/'ca.crt');context.verify_mode=ssl.CERT_REQUIRED
        self.server=await asyncio.start_server(self.handle,'127.0.0.1',0,ssl=context)
        port=self.server.sockets[0].getsockname()[1]
        return HTTPSink(f'https://localhost:{port}',str(self.path/'ca.crt'),str(self.path/'client.crt'),str(self.path/'client.key'))
    async def handle(self,reader,writer):
        try:
            async with asyncio.timeout(4):
                assert writer.get_extra_info('peercert'), 'Client certificate required'
                method,url,_=(await reader.readline()).decode().split()
                headers={}
                while (line:=await reader.readline())!=b'\r\n':
                    k,v=line.decode().split(':',1);headers[k.lower()]=v.strip()
                size=int(headers.get('content-length','0'))
                body=await reader.readexactly(size)
                identifier=url.rsplit('/',1)[-1]
                status=200
                if method=='POST':
                    self.posts+=1
                    operation=json.loads(body)
                    value={k:operation[k] for k in ('effect_id','block_hash')}|dict(state='COMPLETED')
                    self.applied[identifier]=value
                    if self.mode=='drop-ack': return
                    if self.mode=='forged': value=value|dict(block_hash='f'*64)
                else:
                    self.gets+=1;value=self.applied.get(identifier,{})
                    if not value: status=404
                raw=canonical(value).encode()
                writer.write(f'HTTP/1.1 {status} Result\r\nContent-Length: {len(raw)}\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n'.encode()+raw)
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
    async def close(self):
        self.server.close();await self.server.wait_closed()


async def intent(host):
    envelope=await host.verified()
    return await host.request('prepare',dict(envelope=envelope,effect_key='effect-001'),who=host.receiver),envelope


async def test_mtls_dispatch_concurrent_one_shot_and_no_remote_outcome(tmp_path):
    sink=Sink(tmp_path);adapter=await sink.start()
    try:
        async with Host() as host:
            host.engine.adapters=MappingProxyType({host.receiver:adapter})
            effect,envelope=await intent(host)
            replies=await asyncio.gather(*[host.request('dispatch',dict(effect_id=effect['effect_id']),who=host.receiver) for _ in range(8)])
            assert sink.posts==1 and any(x['state']=='COMPLETED' for x in replies)
            assert (await host.request('prepare',dict(envelope=envelope,effect_key='effect-001'),who=host.receiver))['effect_id']==effect['effect_id']
            with pytest.raises(Rejected,match='EFFECT_IDEMPOTENCY_CONFLICT'):
                await host.request('prepare',dict(envelope=envelope,effect_key='different-key'),who=host.receiver)
            with pytest.raises(Rejected,match='UNKNOWN_OPERATION'):
                await host.request('outcome',dict(effect_id=effect['effect_id'],state='COMPLETED'),who=host.receiver)
    finally:
        await adapter.close();await sink.close()


async def test_lost_ack_restart_never_replays_and_lookup_reconciles(tmp_path):
    sink=Sink(tmp_path,'drop-ack');adapter=await sink.start()
    try:
        async with Host() as host:
            host.engine.adapters=MappingProxyType({host.receiver:adapter})
            effect,_=await intent(host)
            first=await host.request('dispatch',dict(effect_id=effect['effect_id']),who=host.receiver)
            assert first['state']=='UNKNOWN' and sink.posts==1
            await host.engine.close()
            host.engine=await Engine(host.settings,{host.receiver:adapter}).start()
            again=await host.request('dispatch',dict(effect_id=effect['effect_id']),who=host.receiver)
            assert again['dispatch'] is False and again['state']=='UNKNOWN' and sink.posts==1
            done=await host.request('reconcile',dict(effect_id=effect['effect_id']),who=host.receiver)
            assert done['state']=='COMPLETED' and sink.posts==1 and sink.gets==1
    finally:
        await adapter.close();await sink.close()


async def test_forged_external_witness_cannot_commit_success(tmp_path):
    sink=Sink(tmp_path,'forged');adapter=await sink.start()
    try:
        async with Host() as host:
            host.engine.adapters=MappingProxyType({host.receiver:adapter})
            effect,_=await intent(host)
            result=await host.request('dispatch',dict(effect_id=effect['effect_id']),who=host.receiver)
            assert result['state']=='UNKNOWN' and sink.posts==1
            with pytest.raises(Rejected,match='WRONG_RECIPIENT'):
                await host.request('reconcile',dict(effect_id=effect['effect_id']),who=host.sender)
    finally:
        await adapter.close();await sink.close()


async def test_prepare_tampering_persists_quarantine_before_any_effect():
    import copy
    async with Host() as host:
        envelope=await host.verified()
        forged=copy.deepcopy(envelope)
        forged['transaction']['payload']='tampered-before-intent'
        with pytest.raises(Rejected,match='HANDOFF_MISMATCH'):
            await host.request('prepare',dict(envelope=forged,effect_key='blocked'),who=host.receiver)
        rows=await host.sql('SELECT state FROM governance.sessions WHERE session_id=%s',(host.frame()['session_id'],))
        assert rows[0]['state']=='QUARANTINED'
        assert not await host.sql('SELECT * FROM governance.effects WHERE session_id=%s',(host.frame()['session_id'],))
        events=await host.sql("SELECT * FROM governance.events WHERE session_id=%s AND kind='SYSTEM_QUARANTINE'",(host.frame()['session_id'],))
        assert len(events)==1
        with pytest.raises(Rejected):
            await host.request('prepare',dict(envelope=envelope,effect_key='cannot-reopen'),who=host.receiver)
