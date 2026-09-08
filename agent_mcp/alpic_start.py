"""Start the existing Python server on Alpic with durable state configured."""

import os
from pathlib import Path


def main() -> None:
    # Never silently create an empty, temporary SQLite database during a move.
    # Keep these checks specific to Alpic; local development remains unchanged.
    required = ("DATABASE_URL", "HERMES_MCP_TOKEN", "AUTH0_ISSUER", "AUTH0_AUDIENCE")
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    if missing:
        raise SystemExit("Missing required Alpic environment variables: " + ", ".join(missing))

    # Alpic's application image is read-only. Only regenerable cache and log
    # files live here; DATABASE_URL remains mandatory for durable core state.
    project_dir = Path(os.environ.get("MCP_PROJECT_DIR", "/tmp/agent-mcp"))
    project_dir.mkdir(parents=True, exist_ok=True)
    os.environ["MCP_PROJECT_DIR"] = str(project_dir)

    from agent_mcp.cli import main_cli

    # The existing 'sse' CLI mode serves both /sse and OAuth-protected /mcp.
    # PORT is read by the CLI from Alpic's environment.
    main_cli(args=["--transport", "sse", "--no-tui", "--project-dir", str(project_dir)])


if __name__ == "__main__":
    main()
