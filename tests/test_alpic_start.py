"""A cloud deployment must not silently fall back to temporary local state."""

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    "missing", ["DATABASE_URL", "HERMES_MCP_TOKEN", "AUTH0_ISSUER", "AUTH0_AUDIENCE"]
)
def test_alpic_start_requires_explicit_persistence_and_auth(missing):
    env = dict(os.environ)
    values = {
        "DATABASE_URL": "postgresql://test:never-print-this@localhost/test",
        "HERMES_MCP_TOKEN": "test-secret-never-print-this",
        "AUTH0_ISSUER": "https://issuer.example/",
        "AUTH0_AUDIENCE": "https://server.example/mcp",
    }
    env.update(values)
    env[missing] = "   "
    result = subprocess.run(
        [sys.executable, "-m", "agent_mcp.alpic_start"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert result.stderr.strip() == (
        "Missing required Alpic environment variables: " + missing
    )
    assert "never-print-this" not in result.stdout + result.stderr


def test_alpic_start_uses_writable_project_directory(monkeypatch, tmp_path):
    from agent_mcp import alpic_start

    for name in ("DATABASE_URL", "HERMES_MCP_TOKEN", "AUTH0_ISSUER", "AUTH0_AUDIENCE"):
        monkeypatch.setenv(name, "test-value")
    project_dir = tmp_path / "runtime"
    monkeypatch.setenv("MCP_PROJECT_DIR", str(project_dir))
    calls = []
    monkeypatch.setitem(sys.modules, "agent_mcp.cli", SimpleNamespace(main_cli=lambda **kw: calls.append(kw)))
    alpic_start.main()
    assert project_dir.is_dir()
    assert calls == [{"args": ["--transport", "sse", "--no-tui", "--project-dir", str(project_dir)]}]
