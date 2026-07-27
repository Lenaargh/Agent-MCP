"""OAuth-authenticated connectors must bind to the same stable agent
identity every time they reconnect - including across a simulated restart -
per the OAuth/identity commits this migration must not disturb."""

import pytest
from mcp.server.auth.provider import AccessToken


def _access_token(issuer, subject, client_id):
    return AccessToken(
        token="fake-jwt-for-test",
        client_id=client_id,
        scopes=["hermes:access"],
        expires_at=9999999999,
        resource="https://agent-mcp-ghg5h.ondigitalocean.app/mcp",
        subject=subject,
        claims={"iss": issuer, "sub": subject},
    )


@pytest.mark.asyncio
async def test_same_principal_reuses_same_agent_identity(db_backend, unique_id, monkeypatch):
    from agent_mcp.app.oauth_agent_identity import ensure_oauth_agent
    from agent_mcp.core import globals as g

    issuer = f"https://{unique_id}.example.auth0.com/"
    subject = f"auth0|{unique_id}"
    client_id = f"claude-desktop-{unique_id}"
    monkeypatch.setenv("HERMES_OWNER_SLUG", unique_id)

    token = _access_token(issuer, subject, client_id)

    first_agent_token = ensure_oauth_agent(token)
    first_agent_id = g.active_agents[first_agent_token]["agent_id"]

    second_agent_token = ensure_oauth_agent(token)
    second_agent_id = g.active_agents[second_agent_token]["agent_id"]

    assert first_agent_token == second_agent_token
    assert first_agent_id == second_agent_id


@pytest.mark.asyncio
async def test_identity_persists_across_restart(db_backend, unique_id, monkeypatch):
    from agent_mcp.app.oauth_agent_identity import ensure_oauth_agent
    from agent_mcp.core import globals as g

    issuer = f"https://{unique_id}.example.auth0.com/"
    subject = f"auth0|{unique_id}-restart"
    client_id = f"chatgpt-{unique_id}"
    monkeypatch.setenv("HERMES_OWNER_SLUG", unique_id)

    token = _access_token(issuer, subject, client_id)
    agent_token_before = ensure_oauth_agent(token)
    agent_id_before = g.active_agents[agent_token_before]["agent_id"]

    # Simulate a restart: the in-memory bindings cache is gone, only the
    # database rows (agents + oauth_agent_bindings) remain.
    g.active_agents.clear()
    g.agent_working_dirs.clear()

    agent_token_after = ensure_oauth_agent(token)
    agent_id_after = g.active_agents[agent_token_after]["agent_id"]

    assert agent_token_after == agent_token_before
    assert agent_id_after == agent_id_before
