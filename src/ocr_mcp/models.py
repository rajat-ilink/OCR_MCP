"""Structured output returned by the ``extract_document_text`` tool."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Line(BaseModel):
    index: int = Field(description="0-based line index within the page")
    text: str
    polygon: list[float] | None = Field(
        default=None,
        description="Bounding polygon [x1,y1,x2,y2,...] in the page's unit; only if include_polygons=true",
    )


class Page(BaseModel):
    page_number: int = Field(description="1-based page number")
    width: float | None = None
    height: float | None = None
    unit: str | None = Field(default=None, description="'pixel' for images, 'inch' for PDFs")
    angle: float | None = Field(default=None, description="Clockwise text angle in degrees")
    text: str = Field(description="Page text, one line per row")
    lines: list[Line]


class ExtractionResult(BaseModel):
    text: str = Field(description="Full extracted text of the document")
    page_count: int
    pages: list[Page]
    model_id: str
    source: str = Field(description="'url' or 'base64'")
    content_type: str = Field(description="Detected file type, e.g. application/pdf")
    size_bytes: int
