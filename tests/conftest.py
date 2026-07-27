"""Shared fixtures for the persistence/messaging/identity test suite.

Tests run against SQLite by default (no DATABASE_URL needed) and, when
DATABASE_URL is set in the environment, an equivalent PostgreSQL run is
exercised too via the `db_backend` fixture's parametrization.
"""

import os
import shutil
import tempfile
import uuid

import pytest

BACKENDS = ["sqlite"]
if os.environ.get("DATABASE_URL", "").strip():
    BACKENDS.append("postgres")


def _reset_agent_mcp_state():
    """Clears in-memory module-level state between tests so each test starts
    from a clean slate regardless of import order."""
    from agent_mcp.core import globals as g

    g.active_agents.clear()
    g.tasks.clear()
    g.agent_working_dirs.clear()
    g.agent_tmux_sessions.clear()
    g.admin_token = None
    g.agent_color_index = 0


@pytest.fixture(params=BACKENDS)
def db_backend(request, monkeypatch, tmp_path_factory):
    """Yields 'sqlite' or 'postgres', configuring the environment so
    get_db_connection() routes to the right backend, and initializes the
    schema fresh for each test. PostgreSQL rows created by tests are cleaned
    up afterward via the shared _test_ prefix on generated IDs."""
    backend = request.param

    project_dir = tmp_path_factory.mktemp("agent-mcp-project")
    monkeypatch.setenv("MCP_PROJECT_DIR", str(project_dir))

    if backend == "sqlite":
        monkeypatch.delenv("DATABASE_URL", raising=False)
    else:
        # DATABASE_URL is already set in the real environment (Doppler); we
        # just leave it as-is for the postgres parametrization.
        pass

    _reset_agent_mcp_state()

    from agent_mcp.db.schema import init_database

    init_database()

    yield backend

    if backend == "postgres":
        _cleanup_postgres_test_rows()

    _reset_agent_mcp_state()


def _cleanup_postgres_test_rows():
    """Deletes rows created by this test run (identified by the 'test_'
    prefix used throughout the suite) so the shared dev database doesn't
    accumulate test data across runs."""
    from agent_mcp.db.connection import get_db_connection

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM agent_messages WHERE sender_id LIKE 'test_%' OR recipient_id LIKE 'test_%'")
        cursor.execute("DELETE FROM oauth_agent_bindings WHERE agent_id LIKE 'test_%'")
        cursor.execute("DELETE FROM agent_actions WHERE agent_id LIKE 'test_%'")
        cursor.execute("DELETE FROM tasks WHERE task_id LIKE 'test_%'")
        cursor.execute("DELETE FROM agents WHERE agent_id LIKE 'test_%'")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def unique_id():
    """A short unique suffix, prefixed with test_ so postgres cleanup finds it."""
    return f"test_{uuid.uuid4().hex[:10]}"
