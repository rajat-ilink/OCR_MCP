"""MCP server definition: the ``extract_document_text`` tool and the HTTP app factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from pydantic import Field
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from . import __version__
from .analyzer import Analyzer, AzureAnalyzer
from .auth import BearerAuthMiddleware
from .config import Settings
from .errors import OcrError
from .models import ExtractionResult
from .url_fetcher import fetch_document
from .validation import (
    decode_base64,
    ensure_size,
    sniff_content_type,
    validate_pages,
    validate_sources,
)

logger = logging.getLogger("ocr_mcp")

# JSON body = base64 (4/3 of the file) plus a little envelope for the other arguments.
_BODY_OVERHEAD = 64 * 1024


def create_server(settings: Settings, analyzer: Analyzer | None = None) -> MCPServer:
    """Build the MCP server. ``analyzer`` can be injected (tests); otherwise Azure is used."""
    # Dependency injection: tests pass a fake analyzer so no Azure calls are made.
    service: Analyzer = analyzer or AzureAnalyzer(settings)
    max_bytes = settings.max_file_bytes

    # Lifespan runs once per server process: nothing to set up on start,
    # but on shutdown we close the Azure client's HTTP connections cleanly.
    @asynccontextmanager
    async def lifespan(_server: MCPServer):
        try:
            yield None
        finally:
            await service.aclose()

    # The server object. `instructions` is sent to MCP clients during connection
    # so an AI assistant knows when and how to use the tool.
    mcp = MCPServer(
        "ocr-mcp",
        title="OCR (Azure AI Document Intelligence)",
        instructions=(
            "Use extract_document_text to OCR a PDF or image. Pass either a public https "
            "document_url or a base64-encoded file in document_base64."
        ),
        version=__version__,
        lifespan=lifespan,
        log_level=settings.log_level,  # type: ignore[arg-type]
    )

    # Plain (non-MCP) endpoint for load balancers / uptime checks. The auth middleware
    # exempts this path, and it reveals nothing about the service.
    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    # Registers the tool. Annotations are hints to clients: it only reads data (read-only,
    # not destructive), repeating a call is harmless (idempotent), and it reaches
    # outside systems (open-world: downloads URLs, calls Azure).
    @mcp.tool(
        title="Extract document text",
        annotations=ToolAnnotations(
            read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
        ),
    )
    # The type hints and Field descriptions below become the tool's JSON input schema
    # automatically; the return type (ExtractionResult) becomes its output schema.
    async def extract_document_text(
        document_url: Annotated[
            str | None,
            Field(description="Public https URL of a PDF/image. Provide this OR document_base64."),
        ] = None,
        document_base64: Annotated[
            str | None,
            Field(description="Base64-encoded PDF/image bytes (data: URI prefix allowed)."),
        ] = None,
        pages: Annotated[
            str | None, Field(description="Optional 1-based page selector, e.g. '1-3,5'.")
        ] = None,
        include_polygons: Annotated[
            bool, Field(description="Include per-line bounding polygons.")
        ] = False,
    ) -> ExtractionResult:
        """Extract text from a document with Azure AI Document Intelligence.

        Returns the full text plus per-page, per-line results (page numbers are 1-based).
        Supported types: PDF, JPEG, PNG, BMP, TIFF, HEIF, DOCX, XLSX, PPTX.
        """
        # This docstring is what MCP clients show as the tool's description.
        try:
            # Step 1 - validate arguments before doing any work or network I/O.
            validate_sources(document_url, document_base64)
            page_selector = validate_pages(pages)

            # Step 2 - get the file bytes from whichever source was supplied.
            if document_url:
                source = "url"
                # Downloaded by us (not by Azure) so we can block internal addresses
                # (SSRF) and enforce the size limit while streaming.
                raw = await fetch_document(
                    document_url,
                    max_bytes=max_bytes,
                    timeout=settings.url_fetch_timeout_seconds,
                    max_redirects=settings.url_max_redirects,
                    allowed_domains=settings.url_allowed_domains,
                )
                ensure_size(raw, max_bytes)  # also rejects empty downloads
            else:
                source = "base64"
                # Length is checked before decoding, so oversized payloads are never decoded.
                raw = decode_base64(document_base64 or "", max_bytes)

            # Step 3 - identify the file type from its bytes (never trust the client's claim);
            # raises for unsupported formats so we don't pay for an Azure call.
            content_type = sniff_content_type(raw)

            # Step 4 - OCR via Azure (concurrency-limited, with a timeout).
            text, result_pages = await service.analyze(
                raw, pages=page_selector, include_polygons=include_polygons
            )
        except OcrError as exc:
            # Anticipated failures carry client-safe messages. ToolError makes the SDK return
            # a normal result with isError=true so the caller (and an LLM) can read the reason.
            # Anything else is unexpected: the SDK hides its details and logs a traceback.
            logger.info("extract_document_text failed: %s", exc)
            raise ToolError(str(exc)) from None

        # Step 5 - shape the response; the SDK serialises it as structured JSON.
        return ExtractionResult(
            text=text,
            page_count=len(result_pages),
            pages=result_pages,
            model_id=service.model_id,
            source=source,
            content_type=content_type,
            size_bytes=len(raw),
        )

    return mcp


def create_app(settings: Settings, analyzer: Analyzer | None = None) -> ASGIApp:
    """ASGI app serving MCP over Streamable HTTP (default) or legacy SSE, per ``MCP_TRANSPORT``.

    Streamable HTTP is stateless and scales horizontally. SSE keeps per-client sessions in
    process memory, so run a single instance (or use sticky routing) when ``MCP_TRANSPORT=sse``.
    """
    mcp = create_server(settings, analyzer)

    # DNS-rebinding protection: reject requests whose Host/Origin header isn't expected,
    # so a malicious web page can't talk to a locally running server via a rebound domain.
    if settings.mcp_allowed_hosts:
        security =TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.mcp_allowed_hosts,
            allowed_origins=settings.mcp_allowed_origins,
        )
    else:
        security = None  # SDK applies loopback-only defaults for 127.0.0.1/localhost binds

    # Reject oversized HTTP bodies up front: base64 is ~4/3 the file size.
    max_body = int(settings.max_file_bytes * 4 / 3) + _BODY_OVERHEAD
    app: Starlette
    if settings.mcp_transport == "sse":
        app = mcp.sse_app(
            sse_path=settings.mcp_sse_path,  # GET: long-lived event stream
            message_path="/messages/",  # POST: client -> server messages
            max_request_body_size=max_body,
            transport_security=security,
            host=settings.mcp_host,
        )
    else:
        app = mcp.streamable_http_app(
            streamable_http_path=settings.mcp_path,  # the endpoint clients connect to (/mcp)
            stateless_http=True,  # no per-client sessions -> any replica can serve any request
            json_response=True,  # reply with plain JSON instead of an SSE stream
            max_request_body_size=max_body,
            transport_security=security,
            host=settings.mcp_host,
        )
    # Optional bearer-token gate wrapped around the whole app (required when exposed).
    if settings.mcp_auth_token is not None:
        return BearerAuthMiddleware(app, settings.mcp_auth_token.get_secret_value())
    return app
