"""SSRF-hardened document download.

Defences: https only, no credentials in URL, port 443 only, optional domain allowlist,
every resolved address must be globally routable, the connection is pinned to the
validated IP (defeats DNS rebinding), redirects are followed manually and re-validated,
proxies/env config are ignored, and the body is streamed with a hard size cap.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlsplit

import httpx

from .errors import DocumentFetchError, UnsafeUrlError

logger = logging.getLogger(__name__)

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


def _host_allowed(host: str, allowed_domains: list[str]) -> bool:
    if not allowed_domains:
        return True
    host = host.lower()
    for domain in allowed_domains:
        domain = domain.lower().lstrip(".")
        if host == domain or host.endswith("." + domain):
            return True
    return False


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def resolve_public_ip(host: str, port: int) -> str:
    """Resolve ``host`` and return one address, failing unless *all* addresses are public."""
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host, port, type=socket.SOCK_STREAM
            )
        except socket.gaierror:
            raise DocumentFetchError("Could not resolve the document URL host.") from None
        addresses = [ipaddress.ip_address(info[4][0]) for info in infos]
    if not addresses:
        raise DocumentFetchError("Could not resolve the document URL host.")
    if not all(_is_public(ip) for ip in addresses):
        raise UnsafeUrlError(
            "The document URL resolves to a non-public address, which is not allowed."
        )
    return str(addresses[0])


def check_url(url: str, allowed_domains: list[str]) -> tuple[str, str]:
    """Static URL checks. Returns (hostname, normalized_url)."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        raise UnsafeUrlError("The document URL is malformed.") from None
    if parts.scheme.lower() != "https":
        raise UnsafeUrlError("Only https:// document URLs are allowed.")
    if not parts.hostname:
        raise UnsafeUrlError("The document URL has no host.")
    if parts.username or parts.password:
        raise UnsafeUrlError("Credentials in the document URL are not allowed.")
    if port not in (None, 443):
        raise UnsafeUrlError("Only the default HTTPS port (443) is allowed.")
    host = parts.hostname.rstrip(".").lower()
    if not _host_allowed(host, allowed_domains):
        raise UnsafeUrlError("The document URL host is not in the allowed domain list.")
    return host, parts.geturl()


async def fetch_document(
    url: str,
    *,
    max_bytes: int,
    timeout: float,
    max_redirects: int,
    allowed_domains: list[str],
) -> bytes:
    current = url
    too_big = f"Document exceeds the maximum size of {max_bytes} bytes."
    for _hop in range(max_redirects + 1):
        host, current = check_url(current, allowed_domains)
        ip = await resolve_public_ip(host, 443)
        parts = urlsplit(current)
        pinned_host = f"[{ip}]" if ":" in ip else ip
        target = parts._replace(netloc=pinned_host).geturl()
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout), follow_redirects=False, trust_env=False
            ) as client:
                async with client.stream(
                    "GET",
                    target,
                    headers={"Host": host, "Accept": "*/*", "User-Agent": "ocr-mcp/0.1"},
                    extensions={"sni_hostname": host},
                ) as response:
                    if response.status_code in _REDIRECT_STATUSES:
                        location = response.headers.get("location")
                        if not location:
                            raise DocumentFetchError("Document host returned an invalid redirect.")
                        current = urljoin(current, location)
                        continue
                    if response.status_code != 200:
                        raise DocumentFetchError(
                            f"Document host responded with HTTP {response.status_code}."
                        )
                    declared = response.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > max_bytes:
                        raise DocumentFetchError(too_big)
                    buf = bytearray()
                    async for chunk in response.aiter_bytes():
                        buf.extend(chunk)
                        if len(buf) > max_bytes:
                            raise DocumentFetchError(too_big)
                    return bytes(buf)
        except httpx.TimeoutException:
            raise DocumentFetchError("Timed out downloading the document.") from None
        except httpx.HTTPError as exc:
            logger.info("Document download failed: %s", type(exc).__name__)
            raise DocumentFetchError("Could not download the document.") from None
    raise DocumentFetchError("Too many redirects while downloading the document.")
