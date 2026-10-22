"""Real OpenBao transit, independent signature validation, ACL and fail-closed tests."""
import copy
import json
import ssl

import httpx
import pytest
from hmt_governance.contracts import Rejected
from hmt_governance.keys import OpenBao, verify_checkpoint

from .support import LAB


def root_client():
    cfg=json.loads((LAB/'bao-runtime.json').read_text())
    admin=json.loads((LAB/'bao-admin.json').read_text())
    return httpx.AsyncClient(base_url=cfg['url'],verify=ssl.create_default_context(cafile=cfg['ca']),
        headers={'X-Vault-Token':admin['root_token']},trust_env=False,timeout=3),admin


async def test_transit_independent_verification_context_tamper_and_rotation():
    bao=OpenBao(**json.loads((LAB/'bao-runtime.json').read_text()))
    root,_=root_client()
    try:
        key1=await bao.cache_key();key2=await bao.cache_key()
        assert len(key1)==32 and key1!=key2
        checkpoint=dict(context='hmt-ledger-checkpoint-v1',session_id='example',sequence_number=2,head_hash='a'*64)
        old=await bao.sign(checkpoint)
        trusted=await bao.public_keys()
        assert verify_checkpoint(old,trusted,checkpoint['context'])==checkpoint
        forged=copy.deepcopy(old);forged['checkpoint']['head_hash']='b'*64
        with pytest.raises(Rejected,match='INVALID_CHECKPOINT'):
            verify_checkpoint(forged,trusted,checkpoint['context'])
        with pytest.raises(Rejected,match='INVALID_CHECKPOINT'):
            verify_checkpoint(old,trusted,'another-authority')
        response=await root.post('/v1/transit/keys/ledger-checkpoints/rotate',json={})
        assert response.status_code in (200,204)
        current=await bao.sign(checkpoint);public=await bao.public_keys()
        assert current['signature'].split(':')[1]!=old['signature'].split(':')[1]
        assert verify_checkpoint(old,public,checkpoint['context'])==checkpoint
        assert verify_checkpoint(current,public,checkpoint['context'])==checkpoint
    finally:
        await bao.close();await root.aclose()


async def test_runtime_acl_cannot_read_root_rotate_or_mint_tokens():
    cfg=json.loads((LAB/'bao-runtime.json').read_text())
    async with httpx.AsyncClient(base_url=cfg['url'],verify=ssl.create_default_context(cafile=cfg['ca']),
        headers={'X-Vault-Token':cfg['token']},trust_env=False,timeout=3) as client:
        for method,path in [('GET','sys/policies/acl'),('POST','auth/token/create'),('POST','transit/keys/ledger-checkpoints/rotate')]:
            response=await client.request(method,'/v1/'+path,json={} if method=='POST' else None)
            assert response.status_code==403


async def test_seal_and_revoked_token_never_return_keys_or_signatures():
    cfg=json.loads((LAB/'bao-runtime.json').read_text())
    root,admin=root_client();bao=OpenBao(**cfg)
    try:
        response=await root.post('/v1/auth/token/create',json=dict(policies=['governance-runtime'],no_default_policy=True,ttl='1m'))
        assert response.status_code==200
        token=response.json()['auth']['client_token']
        child=OpenBao(**(cfg|dict(token=token)))
        try:
            assert len(await child.cache_key())==32
            assert (await root.post('/v1/auth/token/revoke',json=dict(token=token))).status_code in (200,204)
            with pytest.raises(Rejected,match='KEY_SERVICE_UNAVAILABLE'):
                await child.cache_key()
        finally:
            await child.close()
        assert (await root.post('/v1/sys/seal',json={})).status_code in (200,204)
        with pytest.raises(Rejected,match='KEY_SERVICE_UNAVAILABLE'):
            await bao.sign(dict(context='sealed-test'))
    finally:
        await root.post('/v1/sys/unseal',json=dict(key=admin['unseal_key']))
        for _ in range(50):
            if (await root.get('/v1/sys/health')).status_code==200: break
            import asyncio
            await asyncio.sleep(.1)
        await bao.close();await root.aclose()
