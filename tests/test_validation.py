import base64

import pytest

from ocr_mcp.errors import InvalidInputError
from ocr_mcp.validation import decode_base64, sniff_content_type, validate_pages, validate_sources


def test_exactly_one_source():
    validate_sources("https://x", None)
    validate_sources(None, "abcd")
    for args in [(None, None), ("https://x", "abcd"), ("", "")]:
        with pytest.raises(InvalidInputError):
            validate_sources(*args)


@pytest.mark.parametrize("value", ["1", "1-3,5", "2, 4-6"])
def test_pages_ok(value):
    assert validate_pages(value)


@pytest.mark.parametrize("value", ["0", "3-1", "a", "1;2", "1-", "../x"])
def test_pages_bad(value):
    with pytest.raises(InvalidInputError):
        validate_pages(value)


def test_base64_roundtrip_and_data_uri():
    raw = b"%PDF-1.7 hello"
    enc = base64.b64encode(raw).decode()
    assert decode_base64(enc, 1000) == raw
    assert decode_base64("data:application/pdf;base64," + enc, 1000) == raw


def test_base64_invalid_and_oversize():
    with pytest.raises(InvalidInputError, match="valid base64"):
        decode_base64("not base64!!", 1000)
    with pytest.raises(InvalidInputError, match="maximum size"):
        decode_base64(base64.b64encode(b"x" * 2000).decode(), 1000)
    with pytest.raises(InvalidInputError, match="empty"):
        decode_base64("", 10)


def _zip(*names: str) -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name in names:
            zf.writestr(name, "<x/>")
    return buf.getvalue()


def test_sniff_office():
    assert sniff_content_type(_zip("[Content_Types].xml", "word/document.xml")).endswith("wordprocessingml.document")
    assert sniff_content_type(_zip("xl/workbook.xml")).endswith("spreadsheetml.sheet")
    assert sniff_content_type(_zip("ppt/presentation.xml")).endswith("presentationml.presentation")
    with pytest.raises(InvalidInputError):  # a plain zip is not accepted
        sniff_content_type(_zip("readme.txt"))
    with pytest.raises(InvalidInputError):  # truncated/corrupt zip
        sniff_content_type(b"PK\x03\x04garbage")


def test_sniff():
    assert sniff_content_type(b"%PDF-1.4") == "application/pdf"
    assert sniff_content_type(b"\x89PNG\r\n\x1a\n....") == "image/png"
    assert sniff_content_type(b"\xff\xd8\xff\xe0....") == "image/jpeg"
    with pytest.raises(InvalidInputError):
        sniff_content_type(b"MZ\x90\x00 executable")
