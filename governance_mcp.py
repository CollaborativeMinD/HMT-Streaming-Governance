# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp[cli]>=1.26,<2"]
# ///
"""Run alongside governance_core.py: uv run governance_mcp.py (stdio MCP)."""
from contextlib import asynccontextmanager
from dataclasses import asdict

from mcp.server.fastmcp import FastMCP
from pydantic import StrictInt

from governance_core import GovernanceError, GovernanceLedgerEngine, HoldError

engine = GovernanceLedgerEngine(queue_size=64)


@asynccontextmanager
async def lifespan(server):
    await engine.start()
    try:
        yield {"engine": engine}
    finally:
        await engine.close()


mcp = FastMCP("Streaming Governance Ledger", lifespan=lifespan)


@mcp.tool()
async def governance_commit_state(
    session_id: str, sequence_number: StrictInt, source_agent: str,
    target_entity: str, payload: str, previous_hash: str,
    expected_hash: str | None = None,
) -> dict:
    """Queue a bounded transaction. Pending is NOT verified. Poll its token.

    previous_hash is the prior verified BLOCK hash, or 64 zeros for sequence 0.
    Optional expected_hash asserts the payload's SHA-256 before commit.
    busy means retry with the same sequence; rejected means invalid input.
    held means resource or verification availability prevented admission.
    """
    return await engine.governance_commit_state(
        session_id=session_id, sequence_number=sequence_number,
        source_agent=source_agent, target_entity=target_entity,
        payload=payload, previous_hash=previous_hash, expected_hash=expected_hash,
    )


@mcp.tool()
async def governance_verify_payload(payload: str, expected_hash: str) -> dict:
    """Verify UTF-8 content against a SHA-256 already committed to this ledger.

    An unregistered, self-claimed hash fails. Use governance_verify_handoff
    at a tool/agent boundary to bind session, routing, and predecessor too.
    """
    return await engine.governance_verify_payload(payload, expected_hash)


@mcp.tool()
async def governance_get_status(tracking_token: str) -> dict:
    """Poll a receipt: pending, verified, quarantined, held, or unknown.

    A verified receipt includes payload_hash and block_hash. Only then may
    the next transaction use block_hash as its previous_hash. Completed tokens
    are bounded cached observations; an evicted token is unknown, never trusted.
    """
    return engine.governance_get_status(tracking_token)


@mcp.tool()
async def governance_get_health() -> dict:
    """Read resource use, receipt/event retention, hash jobs, and HOLD state.

    Observability only; a healthy process does not authorize a tool invocation.
    """
    return engine.health()


@mcp.tool()
async def governance_verify_handoff(
    transaction: dict, payload_hash: str, ledger_hash: str,
    expected_session_id: str, expected_target: str,
) -> dict:
    """Verify committed lineage, payload, session and intended recipient.

    On failure the receiving session is quarantined. Caller must halt tool
    execution, clear its working context, and emit System Quarantine.
    """
    try:
        if transaction.get("session_id") != expected_session_id:
            raise GovernanceError("Unexpected session")
        if transaction.get("target_entity") != expected_target:
            raise GovernanceError("Unexpected target")
        record = await engine.verify_envelope(dict(
            transaction=transaction, payload_hash=payload_hash, ledger_hash=ledger_hash))
        return dict(verified=True, record=asdict(record))
    except HoldError as exc:
        return dict(verified=False, action="HOLD", reason=str(exc))
    except (GovernanceError, KeyError, TypeError, ValueError) as exc:
        engine.quarantine(expected_session_id, -1, str(exc))
        return dict(verified=False, action="SYSTEM_QUARANTINE", reason=str(exc))


if __name__ == "__main__":
    mcp.run(transport="stdio")
