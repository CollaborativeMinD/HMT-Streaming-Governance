"""The transport supplies a proven WireGuard peer; PostgreSQL binds it to an agent."""

from .contracts import KEY, Rejected


async def principal(connection, peer, role):
    if type(peer) is not str or not KEY.fullmatch(peer):
        raise Rejected("UNAUTHENTICATED")
    found = await (
        await connection.execute(
            "SELECT agent_id FROM governance.agents WHERE peer_key=%s", (peer,)
        )
    ).fetchone()
    row = (
        None
        if found is None
        else await (
            await connection.execute(
                "SELECT * FROM governance.lock_agents(%s)", ([found["agent_id"]],)
            )
        ).fetchone()
    )
    if row and row["peer_key"] != peer:
        raise Rejected("AUTHORITY_CHANGED", "HELD")
    if row is None or not row["active"] or role not in row["roles"]:
        raise Rejected("UNAUTHORIZED")
    live = await (
        await connection.execute(
            "SELECT %s>clock_timestamp() AS live", (row["expires_at"],)
        )
    ).fetchone()
    if not live["live"]:
        raise Rejected("IDENTITY_EXPIRED")
    return row


async def session_authority(connection, session, actor=None):
    names = sorted({session["source_agent"], session["target_agent"]})
    rows = await (
        await connection.execute(
            "SELECT * FROM governance.lock_agents(%s)",
            (names,),
        )
    ).fetchall()
    agents = {row["agent_id"]: row for row in rows}
    for name, epoch in (
        (session["source_agent"], session["source_epoch"]),
        (session["target_agent"], session["target_epoch"]),
    ):
        row = agents.get(name)
        if row is None or not row["active"] or row["epoch"] != epoch:
            raise Rejected("AUTHORITY_CHANGED", "HELD")
        if not (
            await (
                await connection.execute(
                    "SELECT %s>clock_timestamp() AS live", (row["expires_at"],)
                )
            ).fetchone()
        )["live"]:
            raise Rejected("IDENTITY_EXPIRED", "HELD")
    route = await (
        await connection.execute(
            "SELECT active FROM governance.lock_route(%s,%s)",
            (session["source_agent"], session["target_agent"]),
        )
    ).fetchone()
    if route is None or not route["active"]:
        raise Rejected("ROUTE_REVOKED", "HELD")
    if actor and actor["agent_id"] not in agents:
        raise Rejected("SESSION_FORBIDDEN")
    if session["state"] != "ACTIVE":
        raise Rejected(
            session.get("reason") or "SESSION_" + session["state"], session["state"]
        )
    return agents
