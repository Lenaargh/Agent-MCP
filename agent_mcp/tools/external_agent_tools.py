"""Registration for API-backed agents that do not run in local tmux sessions."""

import datetime
import json
import os
from typing import Any, Dict, List

import mcp.types as mcp_types

from .registry import register_tool
from ..core.config import AGENT_COLORS, logger
from ..core import globals as g
from ..core.auth import generate_token, verify_token
from ..db.connection import get_db_connection
from ..db.actions.agent_actions_db import log_agent_action_to_db
from ..utils.audit_utils import log_audit


async def register_external_agent_tool_impl(
    arguments: Dict[str, Any],
) -> List[mcp_types.TextContent]:
    """Register or refresh an API-backed provider without assigning a task."""
    admin_token = arguments.get("token")
    agent_id = arguments.get("agent_id")
    provider = arguments.get("provider", "external")
    capabilities = arguments.get("capabilities", [])

    if not verify_token(admin_token, "admin"):
        return [
            mcp_types.TextContent(
                type="text", text="Unauthorized: Admin token required"
            )
        ]

    if not agent_id or not isinstance(agent_id, str):
        return [
            mcp_types.TextContent(
                type="text",
                text="Error: agent_id is required and must be a string.",
            )
        ]

    if not isinstance(provider, str) or not provider.strip():
        return [
            mcp_types.TextContent(
                type="text",
                text="Error: provider is required and must be a string.",
            )
        ]

    if not isinstance(capabilities, list) or not all(
        isinstance(item, str) for item in capabilities
    ):
        return [
            mcp_types.TextContent(
                type="text",
                text="Error: capabilities must be a list of strings.",
            )
        ]

    normalized_capabilities = list(
        dict.fromkeys([provider.strip(), *capabilities])
    )
    capabilities_json = json.dumps(normalized_capabilities)
    updated_at = datetime.datetime.now().isoformat()
    working_directory = os.path.abspath(
        os.environ.get("MCP_PROJECT_DIR", "/app")
    )

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT token, created_at, color FROM agents WHERE agent_id = ?",
            (agent_id,),
        )
        existing = cursor.fetchone()

        if existing:
            existing_data = dict(existing)
            agent_token = existing_data.get("token")
            created_at = existing_data.get("created_at") or updated_at
            color = existing_data.get("color")
            cursor.execute(
                """
                UPDATE agents
                SET capabilities = ?, status = ?, working_directory = ?,
                    current_task = NULL, updated_at = ?
                WHERE agent_id = ?
                """,
                (
                    capabilities_json,
                    "external",
                    working_directory,
                    updated_at,
                    agent_id,
                ),
            )
            outcome = "already registered and was refreshed"
        else:
            agent_token = generate_token()
            created_at = updated_at
            color = AGENT_COLORS[g.agent_color_index % len(AGENT_COLORS)]
            g.agent_color_index += 1
            cursor.execute(
                """
                INSERT INTO agents
                    (token, agent_id, capabilities, created_at, status,
                     current_task, working_directory, color, updated_at)
                VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    agent_token,
                    agent_id,
                    capabilities_json,
                    created_at,
                    "external",
                    working_directory,
                    color,
                    updated_at,
                ),
            )
            outcome = "registered successfully"

        log_agent_action_to_db(
            cursor,
            "admin",
            "registered_external_agent",
            details={
                "agent_id": agent_id,
                "provider": provider.strip(),
                "capabilities": normalized_capabilities,
            },
        )
        conn.commit()

        if agent_token:
            g.active_agents[agent_token] = {
                "agent_id": agent_id,
                "capabilities": normalized_capabilities,
                "created_at": created_at,
                "status": "external",
                "current_task": None,
                "color": color,
            }
        g.agent_working_dirs[agent_id] = working_directory

        log_audit(
            "admin",
            "register_external_agent",
            {
                "agent_id": agent_id,
                "provider": provider.strip(),
                "capabilities": normalized_capabilities,
            },
        )
        logger.info(
            "External agent '%s' registered for provider '%s'.",
            agent_id,
            provider.strip(),
        )
        return [
            mcp_types.TextContent(
                type="text",
                text=f"External agent '{agent_id}' {outcome}.",
            )
        ]
    except Exception as exc:
        if conn:
            conn.rollback()
        logger.error(
            "Failed to register external agent '%s': %s",
            agent_id,
            exc,
            exc_info=True,
        )
        return [
            mcp_types.TextContent(
                type="text",
                text=f"Error registering external agent: {exc}",
            )
        ]
    finally:
        if conn:
            conn.close()


register_tool(
    name="register_external_agent",
    description=(
        "Register or refresh an API-backed agent without launching a local "
        "tmux process or assigning a task."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "token": {
                "type": "string",
                "description": "Admin authentication token",
            },
            "agent_id": {
                "type": "string",
                "description": "Stable identifier for the external agent",
            },
            "provider": {
                "type": "string",
                "description": "Provider name, for example openai or anthropic",
            },
            "capabilities": {
                "type": "array",
                "description": "Capabilities advertised by the agent",
                "items": {"type": "string"},
                "default": [],
            },
        },
        "required": ["token", "agent_id", "provider"],
        "additionalProperties": False,
    },
    implementation=register_external_agent_tool_impl,
)
