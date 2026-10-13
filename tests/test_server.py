import base64

import httpx
import pytest
from mcp import Client

from ocr_mcp.config import Settings
from ocr_mcp.server import create_app, create_server

from conftest import PDF


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


async def test_lists_tool(settings, fake):
    async with Client(create_server(settings, fake)) as client:
        tools = (await client.list_tools()).tools
        assert [t.name for t in tools] == ["extract_document_text"]


async def test_extract_base64(settings, fake):
    async with Client(create_server(settings, fake)) as client:
        result = await client.call_tool(
            "extract_document_text",
            {"document_base64": b64(PDF), "pages": "1", "include_polygons": True},
        )
    assert not result.is_error
    data = result.structured_content
    assert data["text"] == "Hello world"
    assert data["page_count"] == 1
    assert data["pages"][0]["page_number"] == 1
    assert data["pages"][0]["lines"][0] == {"index": 0, "text": "Hello world", "polygon": [0.0, 0.0, 1.0, 1.0]}
    assert data["content_type"] == "application/pdf"
    assert fake.calls == [{"size": len(PDF), "pages": "1", "polygons": True}]


@pytest.mark.parametrize(
    "args, message",
    [
        ({}, "exactly one"),
        ({"document_url": "https://a.b/x", "document_base64": "AAAA"}, "exactly one"),
        ({"document_base64": "***"}, "valid base64"),
        ({"document_base64": b64(b"MZ not a doc")}, "Unsupported file type"),
        ({"document_base64": b64(b"%PDF-" + b"x" * (1024 * 1024))}, "maximum size"),
        ({"document_base64": b64(PDF), "pages": "9-1"}, "pages"),
        ({"document_url": "http://169.254.169.254/x"}, "https"),
        ({"document_url": "https://127.0.0.1/x"}, "non-public"),
    ],
)
async def test_errors_are_tool_errors(settings, fake, args, message):
    async with Client(create_server(settings, fake)) as client:
        result = await client.call_tool("extract_document_text", args)
    assert result.is_error
    assert message in result.content[0].text
    assert fake.calls == []


async def test_auth_and_health(settings, fake):
    secured = Settings(
        _env_file=None,
        azure_docintel_endpoint=settings.azure_docintel_endpoint,
        azure_docintel_key="k",
        mcp_auth_token="x" * 32,
    )
    app = create_app(secured, fake)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as http:
        assert (await http.get("/healthz")).status_code == 200
        assert (await http.post("/mcp", json={})).status_code == 401
        bad = await http.post("/mcp", json={}, headers={"Authorization": "Bearer nope"})
        assert bad.status_code == 401


def test_exposed_bind_requires_hardening():
    base = dict(_env_file=None, azure_docintel_endpoint="https://a.b/", azure_docintel_key="k")
    with pytest.raises(ValueError):
        Settings(mcp_host="0.0.0.0", **base)
    Settings(mcp_host="0.0.0.0", mcp_auth_token="x" * 20, mcp_allowed_hosts="a.example.com", **base)


def test_secret_not_in_repr(settings):
    assert "test-key-not-real" not in repr(settings)
