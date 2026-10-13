# OCR MCP Server

MCP server (official Python SDK `mcp` 2.x, **Streamable HTTP**) exposing one tool, `extract_document_text`,
backed by your existing **Azure AI Document Intelligence** resource (`prebuilt-read` model).

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
copy .env.example .env      # then edit .env: set AZURE_DOCINTEL_ENDPOINT and AZURE_DOCINTEL_KEY
.\.venv\Scripts\python -m ocr_mcp
```

Serves `http://127.0.0.1:8000/mcp` (health check: `/healthz`). `.env` is git-ignored; secrets are held as
`SecretStr` and never logged or returned.

## Transports (`MCP_TRANSPORT`)

| Value | Endpoint | Notes |
|---|---|---|
| `streamable-http` (default) | `/mcp` | Stateless; scales horizontally. |
| `sse` (legacy) | `GET /sse` (stream) + `POST /messages/` | Sessions live in process memory: run **one instance** (or sticky routing). Same bearer auth, host checks and body-size cap. |
| `stdio` | stdin/stdout | Local subprocess; no network listener, no token needed. Run `python -m ocr_mcp` with `MCP_TRANSPORT=stdio`. |

SSE client config: `{ "type": "sse", "url": "https://<host>/sse", "headers": { "Authorization": "Bearer <token>" } }`.
`/healthz` is available in both HTTP modes.

## Tool: `extract_document_text`

| Argument | Type | Notes |
|---|---|---|
| `document_url` | string | Public `https` URL. Provide this **or** `document_base64`. |
| `document_base64` | string | Base64 file bytes (`data:...;base64,` prefix allowed). |
| `pages` | string, optional | 1-based selector, e.g. `1-3,5`. |
| `include_polygons` | bool, default false | Add per-line bounding polygons. |

Supported types (detected from file contents, never from client-declared types): PDF, JPEG, PNG, BMP,
TIFF, HEIF, and Office files (`.docx`, `.xlsx`, `.pptx`). Legacy `.doc`/`.xls`/`.ppt` are not supported.

Returns structured JSON:

```json
{
  "text": "full document text",
  "page_count": 1,
  "pages": [{"page_number": 1, "width": 8.5, "height": 11, "unit": "inch", "angle": 0,
             "text": "...", "lines": [{"index": 0, "text": "...", "polygon": null}]}],
  "model_id": "prebuilt-read", "source": "base64", "content_type": "application/pdf", "size_bytes": 12345
}
```

Failures (bad input, unsafe URL, oversize, Azure errors) come back as MCP tool errors (`isError: true`)
with client-safe messages; details go to the server log only.

## Security

- **Size limits**: `MAX_FILE_SIZE_MB` is enforced on encoded base64 length *before* decoding, on URL
  `Content-Length`, and while streaming the download; the HTTP request body is capped to match.
- **URL handling (SSRF)**: https only, port 443, no embedded credentials, optional `URL_ALLOWED_DOMAINS`
  allowlist, every resolved IP must be globally routable (blocks loopback/private/link-local/metadata),
  connection pinned to the validated IP (DNS-rebinding safe), redirects re-validated per hop, proxies ignored.
- **Endpoint protection**: set `MCP_AUTH_TOKEN` for bearer auth. Binding to a non-loopback host *requires*
  `MCP_AUTH_TOKEN` and `MCP_ALLOWED_HOSTS` or the server refuses to start. Terminate TLS in front
  (reverse proxy / Azure Container Apps / App Service).
- Stateless HTTP mode, so it scales horizontally without session affinity; Azure calls are bounded by
  `MAX_CONCURRENT_ANALYSES` and `ANALYSIS_TIMEOUT_SECONDS`.

## Layout

```
src/ocr_mcp/
  config.py       env/.env settings + startup validation
  validation.py   input validation, base64 decode, file-type sniffing
  url_fetcher.py  SSRF-safe download
  analyzer.py     Azure Document Intelligence client + error mapping
  models.py       structured output schema
  auth.py         bearer-token ASGI middleware
  server.py       MCP tool + app factory
  __main__.py     entrypoint (uvicorn)
tests/            pytest suite (fake analyzer; no Azure calls)
```

## Client config example

```json
{ "mcpServers": { "ocr": { "type": "http", "url": "http://127.0.0.1:8000/mcp",
                           "headers": { "Authorization": "Bearer <MCP_AUTH_TOKEN>" } } } }
```

Run tests: `.\.venv\Scripts\python -m pytest`
