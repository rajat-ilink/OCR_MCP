"""Manual end-to-end check against a running server.

Usage:
    python scripts/try_ocr.py path\\to\\file.pdf
    python scripts/try_ocr.py https://example.com/sample.pdf
Set MCP_AUTH_TOKEN in the environment if the server requires it.
"""

import asyncio
import base64
import json
import os
import sys

import httpx
from mcp import Client

SERVER_URL = os.environ.get("MCP_URL", "http://127.0.0.1:8000/mcp")


async def main(target: str) -> None:
    args = (
        {"document_url": target}
        if target.startswith("https://")
        else {"document_base64": base64.b64encode(open(target, "rb").read()).decode()}
    )
    token = os.environ.get("MCP_AUTH_TOKEN")
    http = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"} if token else {})
    async with Client(SERVER_URL, http_client=http) as client:
        result = await client.call_tool("extract_document_text", args)
        if result.is_error:
            sys.exit(f"Tool error: {result.content[0].text}")
        print(json.dumps(result.structured_content, indent=2))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1]))
