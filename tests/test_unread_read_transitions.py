"""Message unread/read transitions, and that message IDs appear in both the
send response and the retrieval response (the bug called out in the task)."""

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
async def test_message_starts_unread_then_can_be_marked_read(db_backend, unique_id):
    from agent_mcp.core import globals as g
    from agent_mcp.db.connection import get_db_connection
    from agent_mcp.tools.agent_communication_tools import (
        get_agent_messages_tool_impl,
        send_agent_message_tool_impl,
    )

    g.admin_token = "admin-test-token"
    sender_id = f"{unique_id}_sender"
    recipient_id = f"{unique_id}_recipient"
    await _register(sender_id, g.admin_token)
    await _register(recipient_id, g.admin_token)

    sender_token = next(
        tok for tok, data in g.active_agents.items() if data["agent_id"] == sender_id
    )
    recipient_token = next(
        tok
        for tok, data in g.active_agents.items()
        if data["agent_id"] == recipient_id
    )

    send_result = await send_agent_message_tool_impl(
        {
            "token": sender_token,
            "recipient_id": recipient_id,
            "message": "hello from restart test",
            "deliver_method": "store",
        }
    )
    send_text = send_result[0].text
    assert "Message ID:" in send_text
    message_id = send_text.split("Message ID: ")[1].rstrip(")")

    # Directly verify the row starts unread.
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT read FROM agent_messages WHERE message_id = ?", (message_id,)
        )
        row = cursor.fetchone()
        assert row is not None
        assert not row["read"]
    finally:
        conn.close()

    # Retrieve without marking as read: must stay unread, and the message ID
    # must be present in the retrieval output (this was the reported bug).
    retrieve_unread = await get_agent_messages_tool_impl(
        {"token": recipient_token, "mark_as_read": False}
    )
    retrieve_text = retrieve_unread[0].text
    assert message_id in retrieve_text, "message_id must appear in retrieval output"

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT read FROM agent_messages WHERE message_id = ?", (message_id,)
        )
        row = cursor.fetchone()
        assert not row["read"], "mark_as_read=False must not flip the read flag"
    finally:
        conn.close()

    # Now retrieve with mark_as_read (the default) and confirm the transition.
    await get_agent_messages_tool_impl({"token": recipient_token, "mark_as_read": True})

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT read FROM agent_messages WHERE message_id = ?", (message_id,)
        )
        row = cursor.fetchone()
        assert row["read"], "mark_as_read=True must flip the read flag"
    finally:
        conn.close()

    # Failed/retryable semantics: a message that was never successfully
    # processed (mark_as_read=False) must still show up on a later retrieval.
    retrieve_again = await get_agent_messages_tool_impl(
        {"token": recipient_token, "include_received": True, "mark_as_read": False}
    )
    assert message_id in retrieve_again[0].text
