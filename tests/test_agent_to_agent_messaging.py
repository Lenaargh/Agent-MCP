"""Two registered agents can exchange messages in both directions, and each
only sees messages addressed to (or sent by) itself."""

import pytest


async def _register(agent_id, admin_token):
    from agent_mcp.tools.external_agent_tools import register_external_agent_tool_impl

    await register_external_agent_tool_impl(
        {
            "token": admin_token,
            "agent_id": agent_id,
            "provider": "test-provider",
            "capabilities": [],
        }
    )


@pytest.mark.asyncio
async def test_bidirectional_messaging_between_two_agents(db_backend, unique_id):
    from agent_mcp.core import globals as g
    from agent_mcp.tools.agent_communication_tools import (
        get_agent_messages_tool_impl,
        send_agent_message_tool_impl,
    )

    g.admin_token = "admin-test-token"
    agent_a_id = f"{unique_id}_chatgpt"
    agent_b_id = f"{unique_id}_hermes"
    await _register(agent_a_id, g.admin_token)
    await _register(agent_b_id, g.admin_token)

    token_a = next(
        tok for tok, data in g.active_agents.items() if data["agent_id"] == agent_a_id
    )
    token_b = next(
        tok for tok, data in g.active_agents.items() if data["agent_id"] == agent_b_id
    )

    # A -> B
    result = await send_agent_message_tool_impl(
        {
            "token": token_a,
            "recipient_id": agent_b_id,
            "message": "please pick up task X",
            "deliver_method": "store",
        }
    )
    assert "Message sent" in result[0].text

    b_inbox = await get_agent_messages_tool_impl(
        {"token": token_b, "mark_as_read": False}
    )
    assert "please pick up task X" in b_inbox[0].text
    assert agent_a_id in b_inbox[0].text

    # B's inbox must not show up when A checks its own inbox.
    a_inbox = await get_agent_messages_tool_impl(
        {"token": token_a, "mark_as_read": False}
    )
    assert "please pick up task X" not in a_inbox[0].text

    # B -> A (the reverse route)
    reply = await send_agent_message_tool_impl(
        {
            "token": token_b,
            "recipient_id": agent_a_id,
            "message": "on it",
            "deliver_method": "store",
        }
    )
    assert "Message sent" in reply[0].text

    a_inbox_after_reply = await get_agent_messages_tool_impl(
        {"token": token_a, "mark_as_read": False}
    )
    assert "on it" in a_inbox_after_reply[0].text
    assert agent_b_id in a_inbox_after_reply[0].text


@pytest.mark.asyncio
async def test_unregistered_recipient_is_rejected(db_backend, unique_id):
    from agent_mcp.core import globals as g
    from agent_mcp.tools.agent_communication_tools import send_agent_message_tool_impl

    g.admin_token = "admin-test-token"
    agent_a_id = f"{unique_id}_sender_only"
    await _register(agent_a_id, g.admin_token)
    token_a = next(
        tok for tok, data in g.active_agents.items() if data["agent_id"] == agent_a_id
    )

    result = await send_agent_message_tool_impl(
        {
            "token": token_a,
            "recipient_id": f"{unique_id}_never_registered",
            "message": "hello?",
            "deliver_method": "store",
        }
    )
    assert "Communication denied" in result[0].text
