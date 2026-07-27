"""Agents and tasks must survive a simulated application restart: the
in-memory caches (g.active_agents, g.tasks) are cleared and re-read straight
from the database, and re-running init_database() must not touch existing
rows (no silent recreate/erase on startup)."""

import datetime

import pytest


@pytest.mark.asyncio
async def test_agent_survives_restart(db_backend, unique_id):
    from agent_mcp.core import globals as g
    from agent_mcp.db.actions.agent_db import get_agent_by_id
    from agent_mcp.tools.external_agent_tools import register_external_agent_tool_impl

    g.admin_token = "admin-test-token"
    agent_id = f"{unique_id}_alpha"

    result = await register_external_agent_tool_impl(
        {
            "token": g.admin_token,
            "agent_id": agent_id,
            "provider": "test-provider",
            "capabilities": ["testing"],
        }
    )
    assert "registered successfully" in result[0].text

    # Simulate a process restart: drop the in-memory caches entirely.
    g.active_agents.clear()
    g.agent_working_dirs.clear()

    # Data must still be readable straight from the database.
    agent_row = get_agent_by_id(agent_id)
    assert agent_row is not None
    assert agent_row["agent_id"] == agent_id
    assert agent_row["status"] == "external"
    assert "testing" in agent_row["capabilities"]


@pytest.mark.asyncio
async def test_task_survives_restart(db_backend, unique_id):
    from agent_mcp.db.connection import get_db_connection

    task_id = f"{unique_id}_task"
    now = datetime.datetime.now().isoformat()

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO tasks
                (task_id, title, description, assigned_to, created_by, status,
                 priority, created_at, updated_at, parent_task, child_tasks,
                 depends_on_tasks, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                "Persist across restarts",
                "Verify tasks survive a redeploy",
                None,
                "admin",
                "pending",
                "medium",
                now,
                now,
                None,
                "[]",
                "[]",
                "[]",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # Re-open a fresh connection (as a new request/process would) and confirm
    # the row is there.
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        row = cursor.fetchone()
        assert row is not None
        assert row["title"] == "Persist across restarts"
        assert row["status"] == "pending"
    finally:
        conn.close()


def test_init_database_does_not_erase_existing_rows(db_backend, unique_id):
    """Re-running init_database() (as happens on every app startup) must be
    additive/idempotent, never dropping or recreating tables that already
    hold data."""
    from agent_mcp.db.connection import get_db_connection
    from agent_mcp.db.schema import init_database

    agent_token = f"{unique_id}_token"
    agent_id = f"{unique_id}_restart_marker"
    now = datetime.datetime.now().isoformat()

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO agents
                (token, agent_id, capabilities, created_at, status,
                 current_task, working_directory, color, updated_at)
            VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)
            """,
            (agent_token, agent_id, "[]", now, "external", "/tmp", "#ffffff", now),
        )
        conn.commit()
    finally:
        conn.close()

    # This is exactly what happens on every server startup.
    init_database()

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT agent_id FROM agents WHERE agent_id = ?", (agent_id,))
        row = cursor.fetchone()
        assert row is not None, "init_database() must not erase existing rows"
    finally:
        conn.close()
