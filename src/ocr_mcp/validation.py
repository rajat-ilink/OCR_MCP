"""Input validation: exactly-one-source, base64 decoding, size limits, file-type sniffing."""

from __future__ import annotations

import base64
import binascii
import io
import re
import zipfile

from .errors import InvalidInputError

_OFFICE_MARKERS = {
    "word/document.xml": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xl/workbook.xml": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt/presentation.xml": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

_PAGES_RE = re.compile(r"^\d{1,4}(-\d{1,4})?(,\d{1,4}(-\d{1,4})?)*$")
_DATA_URI_RE = re.compile(r"^data:[^;,]*(;[^;,]*)*;base64,", re.IGNORECASE)
MAX_URL_LENGTH = 2048


def validate_sources(document_url: str | None, document_base64: str | None) -> None:
    if bool(document_url) == bool(document_base64):
        raise InvalidInputError("Provide exactly one of 'document_url' or 'document_base64'.")
    if document_url and len(document_url) > MAX_URL_LENGTH:
        raise InvalidInputError(f"'document_url' exceeds {MAX_URL_LENGTH} characters.")


def validate_pages(pages: str | None) -> str | None:
    """Validate a 1-based page selector like ``1-3,5``."""
    if pages is None or not pages.strip():
        return None
    pages = pages.replace(" ", "")
    if not _PAGES_RE.match(pages):
        raise InvalidInputError("'pages' must look like '1-3,5' (1-based page numbers).")
    for part in pages.split(","):
        bounds = [int(n) for n in part.split("-")]
        if bounds[0] < 1 or (len(bounds) == 2 and bounds[1] < bounds[0]):
            raise InvalidInputError("'pages' contains an invalid page range.")
    return pages


def decode_base64(data: str, max_bytes: int) -> bytes:
    """Decode (optionally data-URI-prefixed) base64, enforcing ``max_bytes`` before decoding."""
    data = _DATA_URI_RE.sub("", data.strip(), count=1)
    data = "".join(data.split())  # tolerate wrapped lines
    # Reject on encoded length first so oversized payloads are never decoded.
    if len(data) > ((max_bytes + 2) // 3) * 4:
        raise InvalidInputError(f"Document exceeds the maximum size of {max_bytes} bytes.")
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise InvalidInputError("'document_base64' is not valid base64.") from None
    ensure_size(raw, max_bytes)
    return raw


def ensure_size(raw: bytes, max_bytes: int) -> None:
    if not raw:
        raise InvalidInputError("Document is empty.")
    if len(raw) > max_bytes:
        raise InvalidInputError(f"Document exceeds the maximum size of {max_bytes} bytes.")


def sniff_content_type(raw: bytes) -> str:
    """Identify supported types from magic bytes (never trust client-declared types)."""
    head = raw[:16]
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"BM"):
        return "image/bmp"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"heif"):
        return "image/heif"
    if head.startswith(b"PK\x03\x04"):
        office = _sniff_office(raw)
        if office:
            return office
    raise InvalidInputError(
        "Unsupported file type. Supported: PDF, JPEG, PNG, BMP, TIFF, HEIF, DOCX, XLSX, PPTX."
    )


def _sniff_office(raw: bytes) -> str | None:
    """Identify OOXML files by their package layout (only the zip directory is read)."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = set(zf.namelist())
    except zipfile.BadZipFile:
        return None
    for marker, mime in _OFFICE_MARKERS.items():
        if marker in names:
            return mime
    return None
