"""Builds tiny synthetic .docx files for tests. All content is fictional."""

import zipfile
from collections.abc import Sequence
from io import BytesIO
from xml.sax.saxutils import escape

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/></Types>'
)


def paragraph(text: str, *, list_item: bool = False) -> str:
    """One <w:p>. Tabs in `text` become <w:tab/>; "\\n" becomes <w:br/>."""
    props = "<w:pPr><w:numPr><w:ilvl w:val='0'/><w:numId w:val='1'/></w:numPr></w:pPr>"
    runs = []
    for i, line in enumerate(text.split("\n")):
        if i:
            runs.append("<w:r><w:br/></w:r>")
        for j, chunk in enumerate(line.split("\t")):
            if j:
                runs.append("<w:r><w:tab/></w:r>")
            if chunk:
                runs.append(f'<w:r><w:t xml:space="preserve">{escape(chunk)}</w:t></w:r>')
    return f"<w:p>{props if list_item else ''}{''.join(runs)}</w:p>"


def document_xml(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<w:document xmlns:w="{W_NS}" xmlns:mc="{MC_NS}"><w:body>{body}</w:body></w:document>'
    )


def docx_from_body(body: str, *, extra_files: dict[str, bytes] | None = None) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("word/document.xml", document_xml(body))
        for name, content in (extra_files or {}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


def simple_docx(lines: Sequence[str]) -> bytes:
    """Lines starting with "* " are list items; "" is an empty paragraph."""
    body = "".join(
        paragraph(line[2:], list_item=True) if line.startswith("* ") else paragraph(line)
        for line in lines
    )
    return docx_from_body(body)


# Fictional CV: nothing here is, or resembles, real candidate data.
SYNTHETIC_CV_LINES = [
    "FIXTURE PERSON",
    "PROFIL",
    "Synthetic profile sentence used only by tests.",
    "",
    "FORMATION",
    "Master Fixture Science - Fixture University\tsept. 2021 – juin 2023",
    "* Synthetic coursework",
    "Licence Fixtures, Test Institute of Fixtures 2018 – 2021",
    "",
    "EXPÉRIENCES PROFESSIONNELLES",
    "Stagiaire Data Fixture – Fixture Corp\t15/06/2022 – 15/09/2022",
    "* Built a synthetic pipeline",
    "",
    "PROJETS",
    "Fixture Dashboard - Python, Fixture-lib",
    "* Repository: https://github.com/example-fixture/fixture-dashboard",
    "",
    "COMPÉTENCES",
    "Langages : Python (avancé), SQL, FixtureLang",
    "Outils : Git",
    "",
    "CERTIFICATIONS",
    "Fixture Certified Practitioner – Fixture Authority – 2023",
    "",
    "LANGUES",
    "Français : natif",
    "Anglais (B2), Espagnol (notions)",
]
