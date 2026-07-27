# Agent-MCP/agent_mcp/db/pg_bootstrap.py
"""Applies versioned Alembic migrations to the PostgreSQL core database.

Called from db/schema.py's init_database() instead of the SQLite
CREATE-TABLE-IF-NOT-EXISTS path when DATABASE_URL is set. Never drops or
recreates tables on its own; it only ever runs `alembic upgrade head`,
which applies whichever migrations haven't been applied yet and is a no-op
if the schema is already current.
"""

import os
from pathlib import Path

from alembic import command
from alembic.config import Config

from ..core.config import logger

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"


def run_postgres_migrations() -> None:
    """Run `alembic upgrade head` against DATABASE_URL."""
    database_url = os.environ["DATABASE_URL"]
    alembic_cfg = Config(str(_ALEMBIC_INI))
    alembic_cfg.set_main_option("sqlalchemy.url", database_url)
    logger.info("Applying PostgreSQL migrations (alembic upgrade head)...")
    command.upgrade(alembic_cfg, "head")
    logger.info("PostgreSQL migrations applied.")
