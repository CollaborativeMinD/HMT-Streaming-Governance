"""Native crypto/network witnesses. Requires the owned lab and a built bridge, no simulated identity."""
import asyncio
import json
import os
import socket
import tempfile
import shutil
from pathlib import Path

import pytest
from hmt_governance.contracts import canonical
from hmt_governance.server import serve

from .support import Host, LAB


def native_environment():
    env = os.environ.copy()
    env['SSL_CERT_FILE'] = str(LAB / 'tls/ca.crt')
    extra = ',127.0.0.1,172.30.245.0/24,172.30.246.0/24,172.30.247.0/24'
    exclusions = env.get('NO_PROXY', '') + ',' + env.get('no_proxy', '') + extra
    env['NO_PROXY'] = env['no_proxy'] = exclusions
    env['HMT_PRIVATE_DIAGNOSTIC'] = str(LAB/'tailcat-private.log')
    env['TS_PANIC_IF_HIT_MAIN_CONTROL'] = 'true'
    return env


def bridge():
    value = os.environ.get('HMT_BRIDGE')
    if not value or not Path(value).is_file():
        pytest.fail('HMT_BRIDGE must point to the compiled native bridge; no qualification skip')
    return value


def private(path, value):
    path.write_text(canonical(value) if isinstance(value, dict) else value)
    path.chmod(0o600)


async def command(*args, body=None, timeout=18):
    proc = await asyncio.create_subprocess_exec(bridge(), *map(str,args),
        env=native_environment(), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(body), timeout)
        if err:
            with (LAB/"native-errors.log").open("ab") as log: log.write(err)
        return proc.returncode, out
    except BaseException:
        proc.kill()
        await proc.wait()
        raise


async def test_headscale_native_acl_allow_and_deny():
    # Preauth keys are single-use and ephemeral nodes log out on close: issue a fresh set per replay.
    import sys
    sys.path.insert(0, str(LAB.parent))
    from lab import docker
    manifest = json.loads((LAB/'manifest.json').read_text())
    cfg = json.loads((LAB/'headscale-runtime.json').read_text())
    name = manifest['prefix']+'-headscale'
    users = json.loads(docker('exec',name,'headscale','--config','/config/config.yaml','users','list','--output','json'))
    for purpose in ('service','agent','admin'):
        key = json.loads(docker('exec',name,'headscale','--config','/config/config.yaml',
            'preauthkeys','create','--user',str(users[0]['id']),'--tags','tag:gov-'+purpose,'--expiration','1h','--output','json'))
        cfg['auth_keys'][purpose] = key['key']
    cfg['state_dir'] = str(LAB/('tsnet-'+os.urandom(4).hex()))
    path = LAB/'headscale-test.json'
    private(path,cfg)
    code, out = await command('network-smoke', path, timeout=70)
    assert code == 0, 'Native Headscale witness failed; inspect private diagnostics'
    value = json.loads(out)
    assert value['headscale_enrollments'] == 3
    assert value['agent_message_port'] is True
    assert value['agent_management_port_denied'] is True
    assert value['admin_management_port'] is True
    assert value['tailscale_saas_control'] is False
    (LAB.parent/'evidence/headscale.json').write_text(canonical(value)+'\n')


async def test_tailcat_native_peer_handoff_revocation_and_fencing():
    directory = LAB / ('native-' + os.urandom(4).hex())
    directory.mkdir(mode=0o700)
    peers = {}
    for name in ('gateway','sender','receiver','stranger'):
        path = directory/(name+'.key')
        assert (await command('new-key',path))[0] == 0
        peers[name] = json.loads(path.read_text())['public']
    from .containers import Containers
    containers=Containers()
    port=8443
    relay_address=json.loads((LAB/'endpoints.json').read_text())['addresses']['relay']
    socket_directory=Path(tempfile.mkdtemp(prefix='hmt-native-uds-'))
    assert len(str(socket_directory/'broker.sock').encode()) < 108
    (directory/'endpoint-output').mkdir(mode=0o700)
    endpoint_path=directory/'endpoint-output/private' 
    private(directory/'relay.json', dict(listen=f'0.0.0.0:{port}',
        cert=str(LAB/'tls/relay.crt'),key=str(LAB/'tls/relay.key'),allowed=list(peers.values())[:3]))
    relay = await containers.start('relay',['relay',directory/'relay.json'],
        files=[directory/'relay.json',LAB/'tls/relay.crt',LAB/'tls/relay.key'],address=relay_address)
    gateway = None
    try:
        import httpx,ssl
        async with httpx.AsyncClient(verify=ssl.create_default_context(cafile=LAB/'tls/ca.crt'),trust_env=False,timeout=.5) as probe:
            for _ in range(100):
                assert relay.returncode is None, 'Private relay failed before readiness'
                try:
                    if (await probe.get(f'https://{relay_address}:{port}/derp/probe')).status_code==200:break
                except httpx.HTTPError:pass
                await asyncio.sleep(.05)
            else:assert False,'Private relay did not become ready'

        async with Host() as host:
            for name, agent in [('sender',host.sender),('receiver',host.receiver)]:
                await host.sql('UPDATE governance.agents SET peer_key=%s WHERE agent_id=%s',(peers[name],agent))
                host.peers[agent] = peers[name]
            server = await serve(host.engine,socket_directory/'broker.sock')
            async with server:
                private(directory/'gateway.json',dict(key_file=str(directory/'gateway.key'),
                    socket=str(socket_directory/'broker.sock'),endpoint_file=str(endpoint_path),
                    allowed=[peers['sender'],peers['receiver']],
                    region=dict(RegionID=997,RegionCode='private',RegionName='Private Tailcat',
                        Nodes=[dict(Name='private',RegionID=997,HostName=relay_address,IPv4=relay_address,DERPPort=port,STUNPort=-1)])))
                gateway=await containers.start('gateway',['serve',directory/'gateway.json'],
                    files=[directory/'gateway.json',directory/'gateway.key'],
                    directories=[(socket_directory,True),(directory/'endpoint-output',False)])
                for _ in range(100):
                    if endpoint_path.exists(): break
                    assert gateway.returncode is None, 'Gateway failed before private endpoint creation'
                    await asyncio.sleep(.05)
                assert endpoint_path.exists()
                clients = {}
                async def send(who, operation, **arguments):
                    if who not in clients:
                        clients[who] = await containers.start(who,['session',directory/(who+'.key'),endpoint_path],
                            files=[directory/(who+'.key'),endpoint_path],interactive=True)
                    client = clients[who]
                    client.stdin.write((canonical(dict(operation=operation,arguments=arguments))+'\n').encode())
                    await client.stdin.drain()
                    out = await asyncio.wait_for(client.stdout.readline(),15)
                    assert out, 'Native Tailcat session failed for '+operation
                    return json.loads(out)
                response=await send('sender','commit',value=host.frame(),request_key='native-commit')
                assert response['ok'] is True
                receipt=response['result']['receipt_id']
                for _ in range(100):
                    current=await send('sender','status',receipt_id=receipt)
                    if current['result']['status']!='PENDING': break
                    await asyncio.sleep(.02)
                envelope=current['result']['result']
                assert (await send('receiver','verify',envelope=envelope))['result']['verified'] is True
                # Even a permitted peer cannot claim a different source in the message.
                forged=await send('receiver','commit',value=host.frame(),request_key='spoof')
                assert forged['ok'] is False and forged['code']=='SOURCE_SPOOFING'
                stranger=await containers.start('stranger',['send',directory/'stranger.key',endpoint_path],
                    files=[directory/'stranger.key',endpoint_path],interactive=True)
                await asyncio.wait_for(stranger.communicate(b'{"operation":"peer_check","arguments":{}}\n'),18)
                assert stranger.returncode != 0, 'Unapproved private key reached the service'
                for client in clients.values():
                    client.stdin.close()
                    await asyncio.wait_for(client.wait(),3)
                await host.revoke()
                await asyncio.wait_for(gateway.wait(),8)
                assert gateway.returncode != 0, 'Revocation must close the transport'
                report=dict(status='PASS',transport='Tailcat v0.7.0',private_derp=True,
                    native_isolated_containers=True,authenticated_handoff=True,source_spoof_denied=True,unlisted_peer_denied=True,
                    revoked_transport_closed=True,identity_source='PeerEnv authenticated node key')
                (LAB.parent/'evidence/tailcat.json').write_text(canonical(report)+'\n')
    finally:
        await containers.close()
        for process in (*locals().get("clients",{}).values(),gateway,relay):
            if process and process.returncode is None:
                process.terminate()
                await process.wait()
        shutil.rmtree(socket_directory,ignore_errors=True)
