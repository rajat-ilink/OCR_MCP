"""Azure AI Document Intelligence wrapper."""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from azure.ai.documentintelligence.aio import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest, AnalyzeResult
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError, ServiceRequestError

from .config import Settings
from .errors import AnalysisError
from .models import Line, Page

logger = logging.getLogger(__name__)

AnalysisOutput = tuple[str, list[Page]]


class Analyzer(Protocol):
    model_id: str

    async def analyze(
        self, raw: bytes, *, pages: str | None, include_polygons: bool
    ) -> AnalysisOutput: ...

    async def aclose(self) -> None: ...


def _map_result(result: AnalyzeResult, include_polygons: bool) -> AnalysisOutput:
    pages: list[Page] = []
    for page in result.pages or []:
        lines = [
            Line(
                index=i,
                text=line.content,
                polygon=list(line.polygon) if include_polygons and line.polygon else None,
            )
            for i, line in enumerate(page.lines or [])
        ]
        pages.append(
            Page(
                page_number=page.page_number,
                width=page.width,
                height=page.height,
                unit=getattr(page.unit, "value", page.unit) if page.unit else None,
                angle=page.angle,
                text="\n".join(line.text for line in lines),
                lines=lines,
            )
        )
    return result.content or "", pages


class AzureAnalyzer:
    def __init__(self, settings: Settings) -> None:
        self.model_id = settings.azure_docintel_model_id
        self._timeout = settings.analysis_timeout_seconds
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_analyses)
        self._client = DocumentIntelligenceClient(
            endpoint=settings.azure_docintel_endpoint,
            credential=AzureKeyCredential(settings.azure_docintel_key.get_secret_value()),
        )

    async def analyze(
        self, raw: bytes, *, pages: str | None, include_polygons: bool
    ) -> AnalysisOutput:
        try:
            async with self._semaphore, asyncio.timeout(self._timeout):
                poller = await self._client.begin_analyze_document(
                    self.model_id, AnalyzeDocumentRequest(bytes_source=raw), pages=pages
                )
                result = await poller.result()
        except TimeoutError:
            raise AnalysisError("Document analysis timed out.") from None
        except ClientAuthenticationError:
            logger.error("Azure Document Intelligence rejected the configured credentials")
            raise AnalysisError(
                "OCR service authentication failed (server misconfiguration)."
            ) from None
        except HttpResponseError as exc:
            self._raise_http(exc)
        except ServiceRequestError:
            logger.error("Azure Document Intelligence unreachable")
            raise AnalysisError("The OCR service is currently unreachable. Try again later.") from None
        return _map_result(result, include_polygons)

    @staticmethod
    def _raise_http(exc: HttpResponseError) -> None:
        status = exc.status_code
        code = getattr(getattr(exc, "error", None), "code", None)
        logger.error("Azure Document Intelligence error status=%s code=%s", status, code)
        if status in (401, 403):
            raise AnalysisError(
                "OCR service authentication failed (server misconfiguration)."
            ) from None
        if status == 429:
            raise AnalysisError("The OCR service is rate limiting requests. Retry shortly.") from None
        if status in (400, 413, 415):
            raise AnalysisError(
                f"The OCR service could not process this document ({code or 'invalid content'})."
            ) from None
        raise AnalysisError("The OCR service failed to process the document.") from None

    async def aclose(self) -> None:
        await self._client.close()
