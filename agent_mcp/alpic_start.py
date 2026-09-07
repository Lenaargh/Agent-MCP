"""Start the existing Python server on Alpic with durable state configured."""

import os


def main() -> None:
    # Never silently create an empty, temporary SQLite database during a move.
    # Keep these checks specific to Alpic; local development remains unchanged.
    required = ("DATABASE_URL", "HERMES_MCP_TOKEN", "AUTH0_ISSUER", "AUTH0_AUDIENCE")
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    if missing:
        raise SystemExit("Missing required Alpic environment variables: " + ", ".join(missing))

    from agent_mcp.cli import main_cli

    # The existing 'sse' CLI mode serves both /sse and OAuth-protected /mcp.
    # PORT is read by the CLI from Alpic's environment.
    main_cli(args=["--transport", "sse", "--no-tui"])


if __name__ == "__main__":
    main()
