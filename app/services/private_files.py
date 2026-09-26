"""Safe, read-only access to documents stored in the private data directory."""

from pathlib import Path

from app.core.config import Settings
from app.core.errors import NotFoundError, UnprocessableError
from app.services.document_text import MAX_DOCUMENT_BYTES

SUPPORTED_SUFFIXES = {".docx"}


def resolve_private_file(
    settings: Settings, relative: str, suffixes: frozenset[str] | set[str] = SUPPORTED_SUFFIXES
) -> Path:
    """Resolve inside the private data directory only (no traversal, no symlink escape)."""
    root = settings.private_data_path.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise UnprocessableError("The path must stay inside the private data directory")
    if path.suffix.lower() not in suffixes:
        raise UnprocessableError(
            "Unsupported document type (only " + ", ".join(sorted(suffixes)) + " is supported)"
        )
    if not path.is_file():
        raise NotFoundError("Document not found in the private data directory")
    return path


def read_document_bytes(path: Path, max_bytes: int = MAX_DOCUMENT_BYTES) -> bytes:
    """Read a document once, read-only, refusing oversized files."""
    if path.stat().st_size > max_bytes:
        raise UnprocessableError("The document is too large")
    with path.open("rb") as handle:
        data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise UnprocessableError("The document is too large")
    return data
