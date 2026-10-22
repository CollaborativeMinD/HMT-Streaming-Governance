"""Real stdio MCP round-trip check. Run: uv run --with 'mcp[cli]>=1.26,<2' check_mcp.py"""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    server = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).with_name("governance_mcp.py"))])
    async with stdio_client(server) as (reader, writer):
        async with ClientSession(reader, writer) as client:
            await client.initialize()
            tools = (await client.list_tools()).tools
            names = {tool.name for tool in tools}
            assert names == {"governance_commit_state", "governance_verify_payload", "governance_get_status", "governance_verify_handoff", "governance_get_health"}
            Path(__file__).with_name("mcp_tools.json").write_text(json.dumps(
                {"server_name": "Streaming Governance Ledger",
                 "tools": [tool.model_dump(mode="json", exclude_none=True) for tool in tools]},
                indent=2) + "\n")

            async def call(name, arguments):
                result = await client.call_tool(name, arguments)
                assert not result.isError, result
                return json.loads(result.content[0].text)

            frame = dict(session_id="stdio-test", sequence_number=0,
                         source_agent="sender", target_entity="receiver",
                         payload="MCP round trip · 🧬", previous_hash="0" * 64)
            pending = await call("governance_commit_state", frame)
            assert pending["status"] == "pending", pending
            for _ in range(100):
                committed = await call("governance_get_status", {"tracking_token":pending["tracking_token"]})
                if committed["status"] != "pending":
                    break
                await asyncio.sleep(0.01)
            assert committed["status"] == "verified", committed
            verify = await call("governance_verify_payload", dict(payload=frame["payload"], expected_hash=committed["payload_hash"]))
            assert verify["verified"], verify
            envelope = dict(transaction=frame, payload_hash=committed["payload_hash"],
                            ledger_hash=committed["block_hash"],
                            expected_session_id="stdio-test", expected_target="receiver")
            valid = await call("governance_verify_handoff", envelope)
            assert valid["verified"], valid
            frame["source_agent"] = "rewritten"
            invalid = await call("governance_verify_handoff", envelope)
            assert not invalid["verified"] and invalid["action"] == "SYSTEM_QUARANTINE", invalid
            health = await call("governance_get_health", {})
            assert health['quarantined_sessions'] == 1 and health['pending_bytes'] == 0
            print("PASS: initialize, list 5 tools, queue, poll, verify payload, verify envelope, quarantine metadata rewrite, health")


if __name__ == "__main__":
    asyncio.run(main())
