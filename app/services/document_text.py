"""Local text extraction from .docx files, using only the standard library.

Nothing leaves the machine and no third-party service is involved. Error messages are
deliberately generic: they never contain document content.
"""

import zipfile
from collections.abc import Iterator
from io import BytesIO
from xml.etree import ElementTree

from app.core.errors import UnprocessableError

MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
MAX_XML_BYTES = 20 * 1024 * 1024
MAX_ZIP_ENTRIES = 1000

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
_P = f"{{{_W}}}p"
_T = f"{{{_W}}}t"
_TAB = f"{{{_W}}}tab"
_BR = f"{{{_W}}}br"
_CR = f"{{{_W}}}cr"
_NUM_PR = f"{{{_W}}}numPr"
_P_PR = f"{{{_W}}}pPr"
_TXBX = f"{{{_W}}}txbxContent"
_FALLBACK = f"{{{_MC}}}Fallback"

LIST_MARKER = "• "


def extract_docx_text(data: bytes) -> str:
    """Return the text of a .docx, one paragraph per line, list items prefixed by a bullet.

    Raises `UnprocessableError` for anything that is not a usable .docx.
    """
    if len(data) > MAX_DOCUMENT_BYTES:
        raise UnprocessableError("The document is too large")
    try:
        text = _extract(data)
    except (zipfile.BadZipFile, ElementTree.ParseError, KeyError, OSError, ValueError):
        raise UnprocessableError("The file is not a valid .docx document") from None
    if not text.strip():
        raise UnprocessableError("The document contains no extractable text")
    return text


def _extract(data: bytes) -> str:
    with zipfile.ZipFile(BytesIO(data)) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise ValueError("too many entries")
        info = archive.getinfo("word/document.xml")
        if info.file_size > MAX_XML_BYTES:
            raise ValueError("document part too large")
        with archive.open(info) as part:
            xml = part.read(MAX_XML_BYTES + 1)
    if len(xml) > MAX_XML_BYTES:
        raise ValueError("document part too large")
    upper = xml.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError("DTDs and entities are not allowed")
    root = ElementTree.fromstring(xml)

    lines: list[str] = []
    for paragraph in _paragraphs(root):
        lines.extend(paragraph.split("\n"))
    return "\n".join(_collapse_blank_lines(lines))


def _paragraphs(element: ElementTree.Element) -> Iterator[str]:
    """Paragraph texts in document order (tables and text boxes included)."""
    if element.tag == _FALLBACK:  # duplicate rendering of the same content
        return
    if element.tag == _P:
        boxes = list(_text_boxes(element))
        text = _paragraph_text(element)
        if text.strip() or not boxes:  # a paragraph holding only a text box adds no blank line
            yield text
        for box in boxes:
            for child in box:
                yield from _paragraphs(child)
        return
    for child in element:
        yield from _paragraphs(child)


def _text_boxes(element: ElementTree.Element) -> Iterator[ElementTree.Element]:
    for child in element:
        if child.tag == _TXBX:
            yield child
        elif child.tag != _FALLBACK:
            yield from _text_boxes(child)


def _paragraph_text(paragraph: ElementTree.Element) -> str:
    parts: list[str] = []
    _collect_runs(paragraph, parts)
    text = "".join(parts).replace(" ", " ")
    properties = paragraph.find(_P_PR)
    if properties is not None and properties.find(_NUM_PR) is not None and text.strip():
        return LIST_MARKER + text.lstrip()
    return text


def _collect_runs(element: ElementTree.Element, parts: list[str]) -> None:
    for child in element:
        if child.tag in (_TXBX, _FALLBACK):
            continue
        if child.tag == _T:
            parts.append(child.text or "")
        elif child.tag == _TAB:
            parts.append("\t")
        elif child.tag in (_BR, _CR):
            parts.append("\n")
        elif child.tag != _P_PR:
            _collect_runs(child, parts)


def _collapse_blank_lines(lines: list[str]) -> list[str]:
    result: list[str] = []
    for line in (item.rstrip() for item in lines):
        if not line and (not result or not result[-1]):
            continue
        result.append(line)
    while result and not result[-1]:
        result.pop()
    return result
