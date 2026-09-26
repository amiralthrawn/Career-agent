"""Safe, read-only access to documents stored in the private data directory."""

from pathlib import Path

from app.core.config import Settings
from app.core.errors import NotFoundError, UnprocessableError
from app.services.document_text import MAX_DOCUMENT_BYTES

SUPPORTED_SUFFIXES = {".docx"}


def resolve_private_file(settings: Settings, relative: str) -> Path:
    """Resolve inside the private data directory only (no traversal, no symlink escape)."""
    root = settings.private_data_path.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise UnprocessableError("The path must stay inside the private data directory")
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise UnprocessableError("Unsupported document type (only .docx is supported)")
    if not path.is_file():
        raise NotFoundError("Document not found in the private data directory")
    return path


def read_document_bytes(path: Path) -> bytes:
    """Read a document once, read-only, refusing oversized files."""
    if path.stat().st_size > MAX_DOCUMENT_BYTES:
        raise UnprocessableError("The document is too large")
    with path.open("rb") as handle:
        data = handle.read(MAX_DOCUMENT_BYTES + 1)
    if len(data) > MAX_DOCUMENT_BYTES:
        raise UnprocessableError("The document is too large")
    return data
