import pytest

from ocr_mcp.errors import UnsafeUrlError
from ocr_mcp.url_fetcher import check_url, fetch_document, resolve_public_ip


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/a.pdf",
        "file:///etc/passwd",
        "ftp://example.com/a.pdf",
        "https://user:pw@example.com/a.pdf",
        "https://example.com:8443/a.pdf",
        "https:///nohost",
    ],
)
def test_check_url_rejects(url):
    with pytest.raises(UnsafeUrlError):
        check_url(url, [])


def test_allowlist():
    assert check_url("https://acct.blob.core.windows.net/x.pdf", ["blob.core.windows.net"])[0]
    with pytest.raises(UnsafeUrlError):
        check_url("https://evilblob.core.windows.net.attacker.com/x", ["blob.core.windows.net"])
    with pytest.raises(UnsafeUrlError):
        check_url("https://notblob.core.windows.net.evil.io/x", ["blob.core.windows.net"])


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1", "::ffff:127.0.0.1", "0.0.0.0", "fd00::1"],
)
async def test_private_ips_blocked(host):
    with pytest.raises(UnsafeUrlError):
        await resolve_public_ip(host, 443)


async def test_localhost_name_blocked():
    with pytest.raises(UnsafeUrlError):
        await resolve_public_ip("localhost", 443)


async def test_fetch_blocks_metadata_endpoint():
    with pytest.raises(UnsafeUrlError):
        await fetch_document(
            "https://169.254.169.254/latest/meta-data",
            max_bytes=10, timeout=2, max_redirects=1, allowed_domains=[],
        )
