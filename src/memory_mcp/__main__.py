"""Entry point for kandela."""

import argparse
import logging
import os
import sys
from pathlib import Path

from memory_mcp.constants import (
    DEFAULT_DB_PATH,
    DEFAULT_EMBEDDING_MODEL,
    __version__,
)
from memory_mcp.server import create_server


def _resolve_path(raw: str) -> str:
    """Expand ~, $ENV_VARS, and resolve to absolute path.

    Prevents the common pitfall of creating a literal '~' directory
    or CWD-relative data that moves depending on where the command runs.
    """
    return str(Path(os.path.expandvars(raw)).expanduser().resolve())


def _validate_environment() -> None:
    """Pre-flight checks before starting the server."""
    if sys.version_info < (3, 11):
        print(
            f"ERROR: Python >= 3.11 required (found {sys.version_info.major}.{sys.version_info.minor})",
            file=sys.stderr,
        )
        sys.exit(1)


def _print_startup_banner(db_path: str, transport: str, port: int) -> None:
    """Print startup info so users know what's happening."""
    transport_info = transport
    if transport == "http":
        transport_info += f" (port {port})"
    print(f"Kandela v{__version__}", file=sys.stderr)
    print(f"  Data:      {db_path}", file=sys.stderr)
    print(f"  Transport: {transport_info}", file=sys.stderr)


def main() -> None:
    _validate_environment()

    parser = argparse.ArgumentParser(
        description="Kandela — Persistent semantic memory for AI agents",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport type (default: stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("KANDELA_PORT", os.environ.get("MEMORY_MCP_PORT", "8321"))),
        help="HTTP port (only used with --transport http, default: 8321)",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=DEFAULT_DB_PATH,
        help=f"ChromaDB persistence path (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--embedding-model",
        type=str,
        default=os.environ.get("KANDELA_EMBEDDING_MODEL", os.environ.get("MEMORY_MCP_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)),
        help=f"Sentence-transformers model name (default: {DEFAULT_EMBEDDING_MODEL})",
    )
    parser.add_argument(
        "--migrate",
        action="store_true",
        help="Re-embed all documents with the current model, then exit",
    )
    parser.add_argument(
        "--migrate-importance",
        action="store_true",
        help="Migrate priority metadata to importance scores (v3), then exit",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"kandela {__version__}",
    )
    args = parser.parse_args()

    # Resolve path: expand ~, $VAR, make absolute
    args.db_path = _resolve_path(args.db_path)

    if args.migrate_importance:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
        from memory_mcp.db.store import MemoryStore

        store = MemoryStore(db_path=args.db_path, embedding_model=args.embedding_model)
        result = store.migrate_metadata_v3()
        print(
            f"Importance migration complete: {result['projects_scanned']} projects scanned, "
            f"{result['updated']} updated, {result['skipped']} skipped, "
            f"{result['errors']} errors"
        )
        sys.exit(0)

    if args.migrate:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
        from memory_mcp.db.store import MemoryStore

        store = MemoryStore(db_path=args.db_path, embedding_model=args.embedding_model)
        result = store.migrate_embeddings()
        print(
            f"Migration complete: {result['projects_migrated']} projects, "
            f"{result['documents_migrated']} documents, {result['errors']} errors"
        )
        sys.exit(0)

    # Configure log level from environment (DEBUG for benchmark analysis)
    log_level_str = os.environ.get("MCP_LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    _print_startup_banner(args.db_path, args.transport, args.port)

    # Security warning for single-user mode without authentication
    _main_logger = logging.getLogger("memory_mcp")
    require_auth = os.environ.get(
        "KANDELA_REQUIRE_AUTH", os.environ.get("MEMORY_MCP_REQUIRE_AUTH", "")
    ).lower() in ("1", "true", "yes")
    has_api_key = bool(os.environ.get("KANDELA_API_KEY", os.environ.get("MEMORY_MCP_API_KEY")))
    if not require_auth and args.transport == "http":
        _main_logger.warning(
            "SECURITY: Running without authentication. "
            "Set KANDELA_API_KEY and KANDELA_REQUIRE_AUTH=true for production."
        )
    if require_auth and not has_api_key:
        _main_logger.error(
            "SECURITY: KANDELA_REQUIRE_AUTH is enabled but KANDELA_API_KEY is not set. "
            "All requests will be rejected. Set KANDELA_API_KEY to fix."
        )

    # Encryption-at-rest reminder for production deployments
    _main_logger.warning(
        "SECURITY: Ensure data directory (%s) is on an encrypted filesystem for production use.",
        args.db_path,
    )

    mcp = create_server(
        db_path=args.db_path,
        embedding_model=args.embedding_model,
        host="0.0.0.0",
        port=args.port,
    )

    if args.transport == "http":
        _run_http(mcp, args.port)
    else:
        mcp.run(transport="stdio")


_MAX_BODY_SIZE = 10 * 1024 * 1024  # 10 MB


class _BodySizeLimitMiddleware:
    """ASGI middleware to reject requests with Content-Length > limit.

    Also handles chunked transfer-encoding where Content-Length is absent
    by wrapping the ASGI receive callable to track accumulated body size.
    """

    def __init__(self, app: object, max_size: int = _MAX_BODY_SIZE) -> None:
        self.app = app
        self.max_size = max_size

    async def _send_413(self, send: object) -> None:
        """Send a 413 Payload Too Large response."""
        await send({  # type: ignore[operator]
            "type": "http.response.start",
            "status": 413,
            "headers": [
                [b"content-type", b"application/json"],
            ],
        })
        await send({  # type: ignore[operator]
            "type": "http.response.body",
            "body": b'{"error": "Request body too large (max 10MB)"}',
        })

    async def __call__(self, scope: dict, receive: object, send: object) -> None:  # type: ignore[type-arg]
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            content_length = headers.get(b"content-length")

            # Case 1: Content-Length header present — reject immediately if too large
            if content_length is not None:
                try:
                    if int(content_length) > self.max_size:
                        await self._send_413(send)
                        return
                except (ValueError, TypeError):
                    pass

            # Case 2: No Content-Length (chunked / streaming) — wrap receive to
            # track accumulated body size and reject mid-stream if exceeded.
            else:
                max_size = self.max_size
                accumulated = 0
                exceeded = False

                async def _limiting_receive() -> dict:  # type: ignore[type-arg]
                    nonlocal accumulated, exceeded
                    message = await receive()  # type: ignore[operator]
                    if message.get("type") == "http.request":
                        body_chunk = message.get("body", b"")
                        accumulated += len(body_chunk)
                        if accumulated > max_size:
                            exceeded = True
                            raise _BodyTooLargeError
                    return message

                try:
                    await self.app(scope, _limiting_receive, send)  # type: ignore[operator]
                except _BodyTooLargeError:
                    await self._send_413(send)
                return

        await self.app(scope, receive, send)  # type: ignore[operator]


class _BodyTooLargeError(Exception):
    """Raised internally when chunked body exceeds size limit."""


def _run_http(mcp: object, port: int) -> None:
    """Run HTTP transport with optional auth middleware."""
    import anyio
    import uvicorn

    async def _serve() -> None:
        app = mcp.streamable_http_app()  # type: ignore[union-attr]

        # Single-user mode: enforce auth if KANDELA_REQUIRE_AUTH is set
        from memory_mcp.auth import is_require_auth, SingleUserAuthMiddleware

        if is_require_auth():
            app = SingleUserAuthMiddleware(app)
            logging.getLogger(__name__).info(
                "Single-user auth middleware enabled (KANDELA_REQUIRE_AUTH=true)"
            )

        # CORS middleware
        from starlette.middleware.cors import CORSMiddleware

        # CORS allowed origins from env, default restrictive for production
        cors_origins_env = os.environ.get("KANDELA_CORS_ORIGINS", os.environ.get("MEMORY_MCP_CORS_ORIGINS", ""))
        if cors_origins_env:
            cors_origins = [o.strip() for o in cors_origins_env.split(",") if o.strip()]
        else:
            # Default: same-origin only (no extra origins)
            cors_origins = []

        app = CORSMiddleware(
            app=app,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["*"],
        )
        if cors_origins:
            logging.getLogger(__name__).info(
                "CORS enabled for origins: %s", cors_origins
            )

        # Request body size limit middleware (outermost layer)
        app = _BodySizeLimitMiddleware(app)

        uvicorn_log_level = os.environ.get("MCP_LOG_LEVEL", "info").lower()
        config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=port,
            log_level=uvicorn_log_level,
        )
        server = uvicorn.Server(config)

        # Start background cron tasks (daily log etc.) — event loop is running here
        from memory_mcp.dashboard import start_cron_tasks
        start_cron_tasks()

        await server.serve()

        # Graceful shutdown: block new MCP connections
        ready_event = getattr(mcp, "_ready_event", None)
        if ready_event is not None:
            ready_event.clear()
            logging.getLogger(__name__).info("Graceful shutdown: MCP readiness cleared")

    anyio.run(_serve)


if __name__ == "__main__":
    main()
