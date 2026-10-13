"""Entry point: ``python -m ocr_mcp`` or the ``ocr-mcp`` console script."""

from __future__ import annotations

import logging
import sys

import uvicorn
from pydantic import ValidationError

from .config import Settings
from .server import create_app


def main() -> None:
    try:
        settings = Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        # Report which settings are wrong without echoing their values.
        problems = "\n".join(
            f"  - {'.'.join(str(p) for p in e['loc']).upper() or 'settings'}: {e['msg']}"
            for e in exc.errors()
        )
        sys.exit(f"Invalid configuration (see .env.example):\n{problems}")

    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # The Azure SDK logs every request/poll at INFO; keep that out of normal operation.
    logging.getLogger("azure").setLevel(max(logging.WARNING, logging.getLevelName(settings.log_level)))
    if settings.mcp_transport == "stdio":
        # Local subprocess transport: JSON-RPC over stdin/stdout, so nothing may print to stdout
        # (logging already goes to stderr). No network listener, hence no auth token needed.
        from .server import create_server

        create_server(settings).run("stdio")
        return
    if settings.mcp_auth_token is None:
        logging.getLogger("ocr_mcp").warning(
            "MCP_AUTH_TOKEN is not set: the endpoint is unauthenticated (loopback only)."
        )
    uvicorn.run(
        create_app(settings),
        host=settings.mcp_host,
        port=settings.mcp_port,
        log_level=settings.log_level.lower(),
        server_header=False,
    )


if __name__ == "__main__":
    main()
