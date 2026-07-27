# Agent-MCP/mcp_template/mcp_server_src/app/main_app.py
import uuid
import datetime # For SSE connection logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional # Added List and Optional
import os # Added os import
from urllib.parse import urlparse

from starlette.applications import Starlette
from starlette.routing import Mount, Route # Added Route
from starlette.middleware import Middleware # If any middleware is needed
from starlette.middleware.cors import CORSMiddleware # Example if CORS is needed
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

# MCP Server specific imports
from mcp.server.lowlevel import Server as MCPLowLevelServer # Renamed to avoid conflict
from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.auth.middleware.auth_context import (
    AuthContextMiddleware,
    get_access_token,
)
from mcp.server.auth.middleware.bearer_auth import (
    BearerAuthBackend,
    RequireAuthMiddleware,
)
from mcp.server.auth.routes import build_resource_metadata_url
from pydantic import AnyHttpUrl
import mcp.types as mcp_types # For MCP tool types

# Project-specific imports
from ..core.config import logger
from ..core import globals as g # For g.connections (if still used for SSE tracking)
from .routes import routes as http_routes # Import defined HTTP routes
from .auth_middleware import BearerTokenAuthMiddleware
from .auth0_token_verifier import (
    Auth0TokenVerifier,
    auth0_audience,
    auth0_issuer,
    auth0_required_scope,
)
from .oauth_agent_identity import ensure_oauth_agent
from .server_lifecycle import application_startup, application_shutdown, start_background_tasks
from ..tools.registry import (
    dispatch_tool_call,
    is_oauth_agent_tool,
    list_available_tools,
)

# --- MCP Server Setup (mimicking original main.py:2055) ---
mcp_app_instance = MCPLowLevelServer("mcp-server") # Name from original main.py:2055

# Register MCP tool handlers with the low-level server instance
# Original main.py: lines 1636-1938 (@app.list_tools, @app.call_tool)
@mcp_app_instance.list_tools()
async def mcp_list_tools_handler() -> List[mcp_types.Tool]:
    """MCP endpoint to list available tools."""
    return await list_available_tools(
        oauth_agent_only=get_access_token() is not None
    )

@mcp_app_instance.call_tool()
async def mcp_call_tool_handler(name: str, arguments: dict) -> List[mcp_types.TextContent]:
    """MCP endpoint to call a specific tool."""
    trusted_arguments = dict(arguments or {})
    access_token = get_access_token()
    if access_token is not None:
        if not is_oauth_agent_tool(name):
            return [
                mcp_types.TextContent(
                    type="text",
                    text=(
                        "Forbidden: this tool is not available to interactive "
                        "OAuth agents."
                    ),
                )
            ]
        trusted_arguments["token"] = ensure_oauth_agent(access_token)
    else:
        # The private Hub transport authenticates at the server boundary and
        # retains its server-to-server administrative role.
        trusted_arguments["token"] = g.admin_token
    return await dispatch_tool_call(name, trusted_arguments)


# --- SSE Transport Setup (mimicking original main.py:1943-1969 for SSE part) ---
# The SseServerTransport handles /messages/ (POST for tool calls) and /sse (GET for connections)
sse_transport = SseServerTransport("/messages/") # Path from original main.py:1943
streamable_http_manager = StreamableHTTPSessionManager(
    app=mcp_app_instance,
    event_store=None,
    json_response=True,
    stateless=True,
)
auth0_token_verifier = Auth0TokenVerifier()


async def streamable_http_handler(
    scope: Scope, receive: Receive, send: Send
) -> None:
    """Serve the current MCP Streamable HTTP transport at /mcp."""
    await streamable_http_manager.handle_request(scope, receive, send)


async def protected_resource_metadata(request) -> JSONResponse:
    """Advertise Auth0 as the authorization server for Agent-MCP."""
    return JSONResponse(
        {
            "resource": auth0_audience(),
            "authorization_servers": [auth0_issuer()],
            "scopes_supported": [auth0_required_scope()],
            "bearer_methods_supported": ["header"],
            "resource_name": "Hermes Agent MCP",
        }
    )

async def sse_connection_handler(request): # Starlette Request object
    """Handles new SSE client connections."""
    try:
        # Client ID generation (original main.py:1947)
        # While SseServerTransport might manage its own client IDs, logging this is useful.
        client_id_log = str(uuid.uuid4())[:8] # For logging this specific connection attempt
        client_host = request.client.host if request.client else 'unknown'
        logger.info(f"SSE connection request from {client_host} (Log ID: {client_id_log})")
        # The original also printed to console, which logger now handles.
        # print(f"[{datetime.datetime.now().isoformat()}] SSE connection request from {client_host} (ID: {client_id_log})")

        # `connect_sse` is a context manager from SseServerTransport
        # Extract ASGI components from Starlette Request
        async with sse_transport.connect_sse(
            request.scope, request.receive, request._send # ASGI scope, receive, send callables
        ) as streams:
            # streams[0] is input_stream, streams[1] is output_stream
            actual_client_id = streams[2] if len(streams) > 2 else client_id_log # Get actual client ID if provided by transport
            logger.info(f"SSE client connected: {actual_client_id}")
            # print(f"[{datetime.datetime.now().isoformat()}] SSE client connected: {actual_client_id}")
            
            # Store connection if g.connections is still used for tracking (original main.py:147)
            # g.connections[actual_client_id] = {"connected_at": datetime.datetime.now().isoformat()}

            try:
                # Run the MCP low-level server for this connection
                # (Original main.py:1957-1961)
                await mcp_app_instance.run(
                    streams[0], # input_stream
                    streams[1], # output_stream
                    mcp_app_instance.create_initialization_options() # As per original
                )
            finally:
                logger.info(f"SSE client disconnected: {actual_client_id}")
                # print(f"[{datetime.datetime.now().isoformat()}] SSE client disconnected: {actual_client_id}")
                # if actual_client_id in g.connections:
                #     del g.connections[actual_client_id]
    except Exception as e:
        # Log errors during SSE connection handling (original main.py:1964-1966)
        logger.error(f"Error in SSE connection handler: {str(e)}", exc_info=True)
        # print(f"[{datetime.datetime.now().isoformat()}] Error in SSE connection: {str(e)}")
        # Starlette will handle sending an error response if one isn't already sent.
        raise # Re-raise to let Starlette handle it if appropriate


# --- Starlette Application Creation ---
def create_app(project_dir: str, admin_token_cli: Optional[str] = None) -> Starlette:
    """
    Creates and configures the main Starlette application.
    """
    # Define lifecycle using the lifespan context manager (Starlette 1.x+)
    @asynccontextmanager
    async def lifespan(app):
        # Startup
        await application_startup(project_dir_path_str=project_dir, admin_token_param=admin_token_cli)
        logger.info("Starlette app startup complete. Background tasks should be started by the server runner.")
        async with streamable_http_manager.run():
            yield
        # Shutdown
        await application_shutdown()
        logger.info("Starlette app shutdown complete.")

    # Define middleware (if any)
    # Enable CORS for dashboard integration - comprehensive CORS config
    middleware_stack = [
        Middleware(BearerTokenAuthMiddleware),
        Middleware(
            CORSMiddleware,
            allow_origins=[
                origin.strip()
                for origin in os.environ.get(
                    "MCP_ALLOWED_ORIGINS",
                    "http://localhost:3847,http://127.0.0.1:3847",
                ).split(",")
                if origin.strip()
            ],
            allow_credentials=False,
            allow_methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS', 'HEAD', 'PATCH'],
            allow_headers=['*'],
            expose_headers=['*', 'Mcp-Session-Id'],
            max_age=3600,  # Cache preflight for 1 hour
        ),
    ]

    # Create the Starlette app
    # The original main.py:2100 created `web_app = Starlette()`
    # It then added routes.
    
    # The routes list from app.routes.py already contains most HTTP routes.
    # We need to add the SSE specific routes here.
    all_routes = list(http_routes) # Start with routes from app/routes.py

    # Create ASGI app wrapper for handle_post_message
    # The sse_transport.handle_post_message is already a proper ASGI callable
    # We just need to wrap it as an ASGI app class for Mount to work
    class MessageHandlerApp:
        """ASGI app wrapper for SseServerTransport.handle_post_message"""
        async def __call__(self, scope, receive, send):
            # Directly call the ASGI-compatible handle_post_message method
            await sse_transport.handle_post_message(scope, receive, send)

    # Add SSE routes (Original main.py:2113-2114)
    all_routes.append(Route('/sse', endpoint=sse_connection_handler, name="sse_connect"))
    # Add the SseServerTransport's POST message handler as a Mount with ASGI app wrapper
    all_routes.append(Mount('/messages', app=MessageHandlerApp(), name="mcp_post_message"))

    resource_url = AnyHttpUrl(auth0_audience())
    resource_metadata_url = build_resource_metadata_url(resource_url)
    oauth_mcp_handler = AuthenticationMiddleware(
        AuthContextMiddleware(
            RequireAuthMiddleware(
                streamable_http_handler,
                [auth0_required_scope()],
                resource_metadata_url,
            )
        ),
        backend=BearerAuthBackend(auth0_token_verifier),
    )
    all_routes.append(
        Route(
            "/mcp",
            endpoint=oauth_mcp_handler,
            name="mcp_streamable_http",
            methods=["GET", "POST", "DELETE"],
        )
    )
    # Publish both the RFC 9728 path-specific location and the widely used
    # root fallback. Both documents describe the same canonical /mcp resource.
    metadata_path = urlparse(str(resource_metadata_url)).path
    all_routes.append(
        Route(
            metadata_path,
            endpoint=protected_resource_metadata,
            name="oauth_protected_resource_mcp",
            methods=["GET", "OPTIONS"],
        )
    )
    all_routes.append(
        Route(
            "/.well-known/oauth-protected-resource",
            endpoint=protected_resource_metadata,
            name="oauth_protected_resource_root",
            methods=["GET", "OPTIONS"],
        )
    )

    # Note: Static file serving removed - dashboard is now served separately via npm run dev
    
    # Create the Starlette application instance
    app = Starlette(
        routes=all_routes,
        lifespan=lifespan,
        middleware=middleware_stack,
        debug=os.environ.get("MCP_DEBUG", "false").lower() == "true" # Optional debug mode
    )

    logger.info("Starlette application instance created with routes and lifecycle events.")
    return app

# The actual running of the app (e.g., with uvicorn) will be handled by cli.py
