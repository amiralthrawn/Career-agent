import zipfile
from io import BytesIO

import pytest

from app.core.errors import UnprocessableError
from app.services.document_text import MAX_DOCUMENT_BYTES, MAX_ZIP_ENTRIES, extract_docx_text
from tests.docx_factory import (
    W_NS,
    document_xml,
    docx_from_body,
    paragraph,
    simple_docx,
)

SECRET = "SYNTHETIC-SECRET-MARKER"


def test_paragraphs_lists_tabs_and_line_breaks_are_extracted() -> None:
    data = simple_docx(["Title", "* item one", "", "", "left\tright", "first\nsecond"])

    assert extract_docx_text(data).split("\n") == [
        "Title",
        "• item one",
        "",
        "left\tright",
        "first",
        "second",
    ]


def test_tables_and_text_boxes_are_read_and_fallback_is_not_duplicated() -> None:
    cells = "".join(f"<w:tc>{paragraph(name)}</w:tc>" for name in ("cell A", "cell B"))
    table = f"<w:tbl><w:tr>{cells}</w:tr></w:tbl>"
    box = (
        "<w:p><w:r><mc:AlternateContent>"
        f"<mc:Choice><w:txbxContent>{paragraph('boxed text')}</w:txbxContent></mc:Choice>"
        f"<mc:Fallback><w:txbxContent>{paragraph('boxed text')}</w:txbxContent></mc:Fallback>"
        "</mc:AlternateContent></w:r></w:p>"
    )

    text = extract_docx_text(docx_from_body(paragraph("before") + table + box))

    assert text.split("\n") == ["before", "cell A", "cell B", "boxed text"]


def test_not_a_zip_is_rejected() -> None:
    with pytest.raises(UnprocessableError, match="not a valid .docx"):
        extract_docx_text(b"this is not a docx")


def test_zip_without_document_part_is_rejected() -> None:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "x")

    with pytest.raises(UnprocessableError, match="not a valid .docx"):
        extract_docx_text(buffer.getvalue())


def test_malformed_xml_is_rejected_without_leaking_content() -> None:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", f"<w:document>{SECRET}<unclosed>")

    with pytest.raises(UnprocessableError) as error:
        extract_docx_text(buffer.getvalue())

    assert SECRET not in str(error.value)
    assert error.value.__cause__ is None and error.value.__suppress_context__


def test_empty_document_is_rejected() -> None:
    with pytest.raises(UnprocessableError, match="no extractable text"):
        extract_docx_text(simple_docx(["", "   ", ""]))


def test_entities_and_dtds_are_rejected() -> None:
    xml = document_xml(paragraph("x")).replace(
        "<w:document", '<!DOCTYPE d [<!ENTITY e "boom">]><w:document', 1
    )
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)

    with pytest.raises(UnprocessableError):
        extract_docx_text(buffer.getvalue())


def test_oversized_document_is_rejected() -> None:
    with pytest.raises(UnprocessableError, match="too large"):
        extract_docx_text(b"0" * (MAX_DOCUMENT_BYTES + 1))


def test_zip_with_too_many_entries_is_rejected() -> None:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document_xml(paragraph("x")))
        for index in range(MAX_ZIP_ENTRIES):
            archive.writestr(f"extra/{index}.txt", "")

    with pytest.raises(UnprocessableError):
        extract_docx_text(buffer.getvalue())


def test_namespace_constant_is_the_wordprocessing_one() -> None:
    assert W_NS.endswith("wordprocessingml/2006/main")
