# Agent-MCP/agent_mcp/db/portable.py
"""Portable database layer: SQLite for local dev, PostgreSQL for persistent deployments.

The backend is selected automatically from the DATABASE_URL environment
variable. Every existing call site in the codebase keeps working unchanged
against either backend:

- `conn.cursor()` / `cursor.execute(sql, params)` using SQLite's `?`
  positional placeholders
- `row["column_name"]` dict-style row access
- `conn.commit()` / `conn.rollback()` / `conn.close()`
- `except sqlite3.Error as e:` error handling

This means the ~40 existing call sites that catch `sqlite3.Error` do not need
to be touched: PostgreSQL errors raised through this layer are wrapped in a
`sqlite3.Error` subclass so those handlers keep working. The handful of
`INSERT OR REPLACE` upserts (which are not valid Postgres syntax) are the
only statements that need a dialect-specific rewrite; use `upsert_sql()` for
those instead of hand-writing the SQL twice.
"""

import os
import re
import sqlite3
import threading
from typing import Any, Optional, Sequence

from ..core.config import logger

POSTGRES_DIALECT = "postgres"
SQLITE_DIALECT = "sqlite"


def is_postgres_configured() -> bool:
    """Whether DATABASE_URL is set, i.e. the app should use PostgreSQL."""
    return bool(os.environ.get("DATABASE_URL", "").strip())


_PLACEHOLDER_RE = re.compile(r"\?")


def _translate_placeholders(sql: str) -> str:
    """Translate SQLite '?' positional placeholders to psycopg2 '%s'.

    The codebase's SQL strings never contain a literal '?' character inside
    a string literal, so a direct substitution is safe.
    """
    return _PLACEHOLDER_RE.sub("%s", sql)


class PortablePostgresError(sqlite3.Error):
    """A PostgreSQL error, re-raised as a sqlite3.Error subclass.

    Existing code throughout the codebase catches `sqlite3.Error` as its
    generic "something went wrong talking to the database" handler. Rather
    than touch every one of those call sites, PostgreSQL errors are wrapped
    so the same handlers work for both backends.
    """

    def __init__(self, original: BaseException):
        super().__init__(str(original))
        self.original = original


class PortableCursor:
    def __init__(self, raw_cursor):
        self._cursor = raw_cursor

    def execute(self, sql: str, params: Sequence[Any] = ()) -> "PortableCursor":
        try:
            # psycopg2's RealDictCursor mishandles an explicit empty tuple
            # for a parameter-less statement (raises IndexError); passing
            # None for "no params" works on both sqlite3 and psycopg2.
            self._cursor.execute(_translate_placeholders(sql), tuple(params) or None)
        except sqlite3.Error:
            raise
        except Exception as exc:  # psycopg2.Error and friends
            raise PortablePostgresError(exc) from exc
        return self

    def executemany(self, sql: str, seq_of_params) -> "PortableCursor":
        try:
            self._cursor.executemany(
                _translate_placeholders(sql), [tuple(p) for p in seq_of_params]
            )
        except sqlite3.Error:
            raise
        except Exception as exc:
            raise PortablePostgresError(exc) from exc
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def fetchmany(self, size: Optional[int] = None):
        return (
            self._cursor.fetchmany(size)
            if size is not None
            else self._cursor.fetchmany()
        )

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    @property
    def lastrowid(self):
        return getattr(self._cursor, "lastrowid", None)

    def close(self) -> None:
        self._cursor.close()

    def __iter__(self):
        return iter(self._cursor)


class _PostgresConnectionPool:
    """Thin wrapper around psycopg2's ThreadedConnectionPool, created lazily
    on first use so importing this module never requires psycopg2 or a
    reachable database unless PostgreSQL is actually configured."""

    _pool = None
    _lock = threading.Lock()

    @classmethod
    def get(cls):
        if cls._pool is None:
            with cls._lock:
                if cls._pool is None:
                    import psycopg2.pool

                    min_size = int(os.environ.get("DB_POOL_MIN_SIZE", "1"))
                    max_size = int(os.environ.get("DB_POOL_MAX_SIZE", "10"))
                    database_url = os.environ["DATABASE_URL"]
                    cls._pool = psycopg2.pool.ThreadedConnectionPool(
                        min_size, max_size, dsn=database_url, sslmode="require"
                    )
                    logger.info(
                        "PostgreSQL connection pool initialised (min=%s, max=%s).",
                        min_size,
                        max_size,
                    )
        return cls._pool


class PortableConnection:
    """Wraps a pooled psycopg2 connection so it exposes the same surface the
    codebase already uses against sqlite3.Connection: `.cursor()`,
    `.commit()`, `.rollback()`, `.close()`.

    `close()` returns the connection to the pool rather than tearing down
    the socket, so the many existing `finally: conn.close()` blocks in the
    codebase keep working as connection-pool checkin, not a real close.
    """

    dialect = POSTGRES_DIALECT

    def __init__(self, raw_conn):
        self._conn = raw_conn
        self._closed = False

    def cursor(self) -> PortableCursor:
        from psycopg2.extras import RealDictCursor

        return PortableCursor(self._conn.cursor(cursor_factory=RealDictCursor))

    def execute(self, sql: str, params: Sequence[Any] = ()) -> PortableCursor:
        return self.cursor().execute(sql, params)

    def commit(self) -> None:
        try:
            self._conn.commit()
        except sqlite3.Error:
            raise
        except Exception as exc:
            raise PortablePostgresError(exc) from exc

    def rollback(self) -> None:
        try:
            self._conn.rollback()
        except sqlite3.Error:
            raise
        except Exception as exc:
            raise PortablePostgresError(exc) from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _PostgresConnectionPool.get().putconn(self._conn)


def get_postgres_connection() -> PortableConnection:
    """Check out a pooled PostgreSQL connection wrapped for drop-in use
    wherever the codebase currently expects a sqlite3.Connection."""
    raw_conn = _PostgresConnectionPool.get().getconn()
    return PortableConnection(raw_conn)


def dialect_of(conn) -> str:
    """Returns POSTGRES_DIALECT or SQLITE_DIALECT for a connection object
    returned by db.connection.get_db_connection()/get_rag_db_connection()."""
    return getattr(conn, "dialect", SQLITE_DIALECT)


def upsert_sql(
    dialect: str, table: str, key_columns: Sequence[str], all_columns: Sequence[str]
) -> str:
    """Build an upsert statement portable across the sqlite/postgres dialects.

    Mirrors `INSERT OR REPLACE`'s "overwrite the whole row" semantics: every
    non-key column is overwritten on conflict. `all_columns` must include
    `key_columns`. Placeholders are '?' regardless of dialect; the portable
    cursor translates them for PostgreSQL.
    """
    columns_sql = ", ".join(all_columns)
    placeholders_sql = ", ".join("?" for _ in all_columns)
    if dialect == POSTGRES_DIALECT:
        conflict_sql = ", ".join(key_columns)
        update_cols = [c for c in all_columns if c not in key_columns]
        if update_cols:
            set_sql = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
            return (
                f"INSERT INTO {table} ({columns_sql}) VALUES ({placeholders_sql}) "
                f"ON CONFLICT ({conflict_sql}) DO UPDATE SET {set_sql}"
            )
        return (
            f"INSERT INTO {table} ({columns_sql}) VALUES ({placeholders_sql}) "
            f"ON CONFLICT ({conflict_sql}) DO NOTHING"
        )
    return f"INSERT OR REPLACE INTO {table} ({columns_sql}) VALUES ({placeholders_sql})"
