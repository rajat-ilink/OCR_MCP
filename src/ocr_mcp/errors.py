"""Domain errors. Messages are safe to show to MCP clients (no secrets, no internals)."""


class OcrError(Exception):
    """Base class for anticipated failures; ``str(exc)`` is client-safe."""


class InvalidInputError(OcrError):
    """The caller supplied bad arguments or an unsupported/oversized document."""


class UnsafeUrlError(InvalidInputError):
    """The URL failed security checks (scheme, host, private address, redirect...)."""


class DocumentFetchError(OcrError):
    """The document URL could not be downloaded."""


class AnalysisError(OcrError):
    """Azure Document Intelligence failed or rejected the document."""
