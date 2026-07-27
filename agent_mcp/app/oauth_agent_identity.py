"""Bind an authenticated OAuth connector to a persistent Agent-MCP identity."""

import datetime
import hashlib
import json
import os
import re
import sqlite3
from typing import Any
from urllib.parse import urlparse

from mcp.server.auth.provider import AccessToken

from ..core import globals as g
from ..core.auth import generate_token
from ..core.config import AGENT_COLORS, logger
from ..db.connection import get_db_connection


def _safe_slug(value: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug[:48] or fallback


def _configured_agent_id(client_id: str) -> str | None:
    raw_mapping = os.environ.get("AUTH0_CLIENT_AGENT_MAP", "").strip()
    if not raw_mapping:
        return None
    try:
        mapping = json.loads(raw_mapping)
    except json.JSONDecodeError:
        logger.error("AUTH0_CLIENT_AGENT_MAP is not valid JSON; ignoring it.")
        return None
    if not isinstance(mapping, dict):
        logger.error("AUTH0_CLIENT_AGENT_MAP must be a JSON object; ignoring it.")
        return None
    value = mapping.get(client_id)
    if isinstance(value, str) and value.strip():
        return _safe_slug(value, "oauth_agent")
    return None


def _identity_defaults(client_id: str) -> tuple[str, str]:
    """Return a stable friendly agent ID and provider for a known connector."""
    configured = _configured_agent_id(client_id)
    owner = _safe_slug(os.environ.get("HERMES_OWNER_SLUG", "lena"), "owner")
    parsed = urlparse(client_id)
    client_hint = " ".join(
        part for part in (parsed.hostname or "", parsed.path, client_id) if part
    ).lower()

    if "chatgpt" in client_hint or "openai" in client_hint:
        return configured or f"{owner}_chatgpt", "openai"
    if "claude" in client_hint or "anthropic" in client_hint:
        return configured or f"{owner}_claude", "anthropic"
    if "gemini" in client_hint or "google" in client_hint:
        return configured or f"{owner}_gemini", "google"
    if "manus" in client_hint:
        return configured or f"{owner}_manus", "manus"

    digest = hashlib.sha256(client_id.encode("utf-8")).hexdigest()[:10]
    return configured or f"{owner}_oauth_{digest}", "external"


def _principal(access_token: AccessToken) -> tuple[str, str, str]:
    claims: dict[str, Any] = access_token.claims or {}
    issuer = str(claims.get("iss") or "unknown-issuer")
    subject = str(access_token.subject or claims.get("sub") or "")
    client_id = str(access_token.client_id or "")
    if not subject or not client_id:
        raise ValueError("OAuth token is missing its subject or client ID.")
    return issuer, subject, client_id


def _load_binding(
    cursor: sqlite3.Cursor, issuer: str, subject: str, client_id: str
) -> sqlite3.Row | None:
    cursor.execute(
        """
        SELECT a.token, a.agent_id, a.capabilities, a.created_at, a.status,
               a.current_task, a.working_directory, a.color
        FROM oauth_agent_bindings AS b
        JOIN agents AS a ON a.agent_id = b.agent_id
        WHERE b.issuer = ? AND b.subject = ? AND b.client_id = ?
        """,
        (issuer, subject, client_id),
    )
    return cursor.fetchone()


def _activate_agent(row: sqlite3.Row) -> str:
    agent_token = str(row["token"])
    agent_id = str(row["agent_id"])
    try:
        capabilities = json.loads(row["capabilities"] or "[]")
    except json.JSONDecodeError:
        capabilities = []
    g.active_agents[agent_token] = {
        "agent_id": agent_id,
        "capabilities": capabilities,
        "created_at": row["created_at"],
        "status": row["status"],
        "current_task": row["current_task"],
        "color": row["color"],
    }
    g.agent_working_dirs[agent_id] = row["working_directory"]
    return agent_token


def ensure_oauth_agent(access_token: AccessToken) -> str:
    """Return the private agent token bound to this signed OAuth principal."""
    issuer, subject, client_id = _principal(access_token)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = get_db_connection()

    try:
        cursor = conn.cursor()
        binding = _load_binding(cursor, issuer, subject, client_id)
        if binding:
            cursor.execute(
                """
                UPDATE oauth_agent_bindings
                SET updated_at = ?
                WHERE issuer = ? AND subject = ? AND client_id = ?
                """,
                (now, issuer, subject, client_id),
            )
            conn.commit()
            return _activate_agent(binding)

        agent_id, provider = _identity_defaults(client_id)
        cursor.execute(
            """
            SELECT token, agent_id, capabilities, created_at, status,
                   current_task, working_directory, color
            FROM agents
            WHERE agent_id = ?
            """,
            (agent_id,),
        )
        agent = cursor.fetchone()

        if not agent:
            agent_token = generate_token()
            capabilities = [provider, "oauth-mcp", "interactive"]
            working_directory = os.path.abspath(
                os.environ.get("MCP_PROJECT_DIR", "/app")
            )
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
                    json.dumps(capabilities),
                    now,
                    "external",
                    working_directory,
                    color,
                    now,
                ),
            )

        cursor.execute(
            """
            INSERT INTO oauth_agent_bindings
                (issuer, subject, client_id, agent_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (issuer, subject, client_id, agent_id, now, now),
        )
        conn.commit()

        binding = _load_binding(cursor, issuer, subject, client_id)
        if not binding:
            raise RuntimeError("OAuth agent binding was not persisted.")
        logger.info(
            "Bound OAuth client to external agent '%s' for provider '%s'.",
            agent_id,
            provider,
        )
        return _activate_agent(binding)
    except sqlite3.IntegrityError:
        conn.rollback()
        cursor = conn.cursor()
        binding = _load_binding(cursor, issuer, subject, client_id)
        if not binding:
            raise
        return _activate_agent(binding)
    finally:
        conn.close()
