# Alpic migration

Status: deployment configuration prepared; live migration is not complete.

Deploy the Python server from the repository root with runtime `python3.13`.
`alpic.json` installs the existing requirements and package, compiles the
Python source and starts the existing HTTP server without its terminal UI.
`xmcp.config.ts` declares Streamable HTTP for Alpic's transport detector;
the actual application remains Python and exposes `/mcp` and `/sse`.

## Preserve existing state and authentication

Configure the following in Alpic's encrypted environment settings. Do not
commit values or put them in deployment commands or chat:

| Variable | Migration value |
| --- | --- |
| `DATABASE_URL` | Existing PostgreSQL database connection, after confirming the live service uses it and taking a backup. |
| `HERMES_MCP_TOKEN` | Existing private Hub bearer token. |
| `AUTH0_ISSUER` | Existing Auth0 issuer. |
| `AUTH0_AUDIENCE` | Auth0 API identifier for the new public `/mcp` URL, registered in Auth0 before switching clients. |
| `AUTH0_REQUIRED_SCOPE` | Existing scope, normally `hermes:access`. |
| `AUTH0_CLIENT_AGENT_MAP` | Copy the existing mapping if set, to preserve client-to-agent assignments. |
| `HERMES_OWNER_SLUG` | Copy the current owner slug if set. |
| `MCP_ALLOWED_ORIGINS` | Existing dashboard origins plus any explicitly required new origin. |
| `OPENAI_API_KEY` | Existing key if RAG indexing is required. Its usage is billed separately from hosting. |
| `DB_POOL_MIN_SIZE`, `DB_POOL_MAX_SIZE` | Copy existing database pool limits if set. |

The Alpic entry point refuses to start without durable database and explicit
authentication settings. If the existing service uses SQLite, export and
migrate its state first; pointing at an empty PostgreSQL database is not a
data migration. Do not upload local `.agent` state or secrets as source.

`AUTH0_AUDIENCE` currently also controls the public OAuth discovery URL in
the application. Leaving its DigitalOcean default would leave discovery
dependent on the old host. Register the new Alpic resource and scope in
Auth0, grant access to the intended clients and set the new audience.
Preserve the issuer and client IDs where possible so existing OAuth identity
bindings are reused. Reconnect clients to request tokens for the new resource.

## Deploy and cut over

1. Create/link the Alpic project in the intended team, select Python 3.13
   and configure the environment variables above before starting production.
2. Deploy this branch from GitHub or use the Alpic CLI with explicit team,
   project and runtime selection. Link the GitHub repository for future deploys
   only once the migration branch is ready to become production.
3. Verify `/api/status`, OAuth discovery and unauthenticated rejection at
   `/mcp` and `/api/agents`. Perform an authenticated MCP initialise and tool
   listing, then check existing agents and message history.
4. Update the private Hub's MCP base URL and reconnect interactive clients
   to the new `/mcp` endpoint. Test an explicitly authorised A2A exchange.
5. Confirm writes survive a restart and are visible to both participating
   agents. Avoid running two writers during cutover: application state is
   also cached in each process. Drain the old service before switching writes.
6. Retire the old server only after these checks. Keep its database unless
   a separate, verified database migration has been completed.

The free Alpic plan currently advertises one project and 10,000 requests per
month, capped. It does not establish that an existing external database or
model API usage is free. Review the account plan before deploying.

References: https://docs.alpic.ai/build-deploy/builds,
https://docs.alpic.ai/cli/deploy and https://alpic.ai/pricing.
