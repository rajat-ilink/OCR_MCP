import pytest

from ocr_mcp.config import Settings
from ocr_mcp.models import Line, Page

PDF = b"%PDF-1.4\n%fake\n"


class FakeAnalyzer:
    model_id = "prebuilt-read"

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.closed = False

    async def analyze(self, raw, *, pages, include_polygons):
        self.calls.append({"size": len(raw), "pages": pages, "polygons": include_polygons})
        line = Line(index=0, text="Hello world", polygon=[0, 0, 1, 1] if include_polygons else None)
        return "Hello world", [Page(page_number=1, text="Hello world", lines=[line])]

    async def aclose(self):
        self.closed = True


@pytest.fixture
def settings(monkeypatch) -> Settings:
    monkeypatch.chdir("/")  # never pick up a developer's real .env
    return Settings(
        _env_file=None,
        azure_docintel_endpoint="https://example.cognitiveservices.azure.com/",
        azure_docintel_key="test-key-not-real",
        max_file_size_mb=1,
    )


@pytest.fixture
def fake() -> FakeAnalyzer:
    return FakeAnalyzer()
