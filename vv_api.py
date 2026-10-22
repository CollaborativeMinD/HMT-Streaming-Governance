"""Actual MCP stdio subprocess scenarios, with no embedded assertions."""
import asyncio
import hashlib
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def api_probe():
    server = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).with_name('governance_mcp.py'))])
    async with asyncio.timeout(20):
        async with stdio_client(server) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                names = sorted(tool.name for tool in (await client.list_tools()).tools)

                async def call(name, arguments):
                    result = await client.call_tool(name, arguments)
                    text = next((item.text for item in result.content if hasattr(item, 'text')), '{}')
                    return result.isError, text

                frame = dict(session_id='wire', sequence_number=0, source_agent='sender',
                             target_entity='receiver', payload='MCP · 🧬', previous_hash='0' * 64)
                rejected = []
                for i, value in enumerate((True, '0', 0.0)):
                    error, _ = await call('governance_commit_state', frame | dict(
                        session_id=f'malformed-{i}', sequence_number=value))
                    rejected.append(error)
                missing_error, _ = await call('governance_commit_state', {'payload': 'missing'})
                _, text = await call('governance_commit_state', frame)
                pending = json.loads(text)
                for _ in range(100):
                    _, text = await call('governance_get_status', {'tracking_token': pending['tracking_token']})
                    receipt = json.loads(text)
                    if receipt['status'] != 'pending':
                        break
                    await asyncio.sleep(0.001)
                _, text = await call('governance_verify_payload', dict(
                    payload='unregistered', expected_hash=hashlib.sha256(b'unregistered').hexdigest()))
                unregistered = json.loads(text)
                handoff = dict(transaction=frame, payload_hash=receipt['payload_hash'], ledger_hash=receipt['block_hash'],
                               expected_session_id='wire', expected_target='receiver')
                _, text = await call('governance_verify_handoff', handoff)
                valid = json.loads(text)
                handoff['transaction'] = frame | dict(source_agent='rewritten')
                _, text = await call('governance_verify_handoff', handoff)
                bad = json.loads(text)
                _, text = await call('governance_get_status', {'tracking_token': 'unknown'})
                unknown = json.loads(text)
                return dict(tools=len(names), strict_rejections=rejected, missing_rejected=missing_error,
                            commit=receipt['status'], valid=valid['verified'], bad=bad['verified'],
                            bad_action=bad.get('action'), unknown=unknown['status'],
                            unregistered=unregistered['verified'], payload_oracle=receipt['payload_hash'] ==
                            hashlib.sha256(frame['payload'].encode()).hexdigest())
