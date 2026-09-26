"""Local, read-only review of CV proposals.

Produces a self-contained HTML report inside `data/private/reviews/` (git-ignored) so that
the real proposals can be examined without exposing their content in the terminal, in logs,
in Git, or through any tracked file. Nothing is stored in the database and nothing is accepted:
this module never creates a fact, an evidence or a decision.

Two sources:
- `preview_cv`: parse a private document in memory (no database needed);
- `stored_review`: render the proposals of an existing ingestion (database read only).
"""

import hashlib
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.partial_date import precision_of
from app.services.cv_ingestion import CVIngestionService
from app.services.cv_parser import PARSER_VERSION, parse_cv
from app.services.document_text import extract_docx_text
from app.services.private_files import read_document_bytes, resolve_private_file

REVIEW_DIRECTORY = "reviews"
PREVIEW_STATUS = "pending (preview, not stored)"


@dataclass(frozen=True)
class ReviewItem:
    number: int
    kind: str
    status: str
    data: dict[str, Any]
    uncertainties: list[str]
    excerpt: str


@dataclass(frozen=True)
class ReviewReport:
    mode: str  # "preview" or "stored"
    source: str
    sha256: str
    parser_version: str
    items: list[ReviewItem]


def preview_cv(settings: Settings, source_path: str) -> ReviewReport:
    """Parse a private document in memory. Read-only: no database, no write, no decision."""
    path = resolve_private_file(settings, source_path)
    data = read_document_bytes(path)
    result = parse_cv(extract_docx_text(data))
    items = [
        ReviewItem(
            number=index,
            kind=draft.kind.value,
            status=PREVIEW_STATUS,
            data=draft.data,
            uncertainties=draft.uncertainties,
            excerpt=draft.source_excerpt,
        )
        for index, draft in enumerate(result.drafts, start=1)
    ]
    return ReviewReport(
        mode="preview",
        source=Path(source_path).as_posix(),
        sha256=hashlib.sha256(data).hexdigest(),
        parser_version=PARSER_VERSION,
        items=items,
    )


def stored_review(session: Session, settings: Settings, ingestion_id: int) -> ReviewReport:
    """Render the proposals of an existing ingestion (read only; the session is not committed)."""
    ingestion = CVIngestionService(session, settings).get_ingestion(ingestion_id)
    items = [
        ReviewItem(
            number=proposal.id,
            kind=proposal.kind.value,
            status=proposal.status.value,
            data=proposal.reviewed_data or proposal.data,
            uncertainties=list(proposal.uncertainties),
            excerpt=proposal.source_excerpt,
        )
        for proposal in ingestion.proposals
    ]
    return ReviewReport(
        mode="stored",
        source=ingestion.source_uri,
        sha256=ingestion.sha256,
        parser_version=ingestion.parser_version,
        items=items,
    )


def write_report(settings: Settings, report: ReviewReport) -> Path:
    """Write the HTML report under the private data directory and return its path."""
    directory = settings.private_data_path / REVIEW_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"cv-proposals-{report.mode}-{report.sha256[:12]}.html"
    path.write_text(render_html(report), encoding="utf-8")
    return path


# --- HTML rendering (every value is escaped; no script, no external resource) ---------

_STYLE = """
:root { color-scheme: light dark; --bg:#fff; --fg:#1d2733; --muted:#5b6b7b; --card:#f5f7fa;
  --line:#d5dce4; --warn:#a15c00; --ok:#1a7f4b; }
@media (prefers-color-scheme: dark) { :root { --bg:#12181f; --fg:#e4e9ee; --muted:#93a1b0;
  --card:#1a222b; --line:#2b3641; --warn:#e8a94a; --ok:#5cc28a; } }
body { font: 15px/1.5 system-ui, sans-serif; background:var(--bg); color:var(--fg);
  margin:0 auto; max-width:60rem; padding:1rem 1rem 3rem; }
h1 { font-size:1.4rem; } h2 { font-size:1.05rem; margin:0; }
.meta, .muted { color:var(--muted); font-size:.9rem; }
.banner { border:1px solid var(--line); border-left:4px solid var(--warn); background:var(--card);
  padding:.6rem .9rem; border-radius:6px; margin:1rem 0; }
table { border-collapse:collapse; width:100%; }
th, td { text-align:left; padding:.25rem .5rem; border-bottom:1px solid var(--line);
  vertical-align:top; word-break:break-word; }
th { width:11rem; color:var(--muted); font-weight:500; }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px;
  padding:.8rem 1rem; margin:1rem 0; }
.head { display:flex; gap:.6rem; flex-wrap:wrap; align-items:baseline; margin-bottom:.5rem; }
.tag { border:1px solid var(--line); border-radius:999px; padding:0 .55rem; font-size:.8rem; }
.warn { color:var(--warn); } .none { color:var(--ok); }
pre { white-space:pre-wrap; word-break:break-word; margin:.3rem 0 0; padding:.5rem;
  border:1px solid var(--line); border-radius:6px; font: 13px/1.45 ui-monospace, monospace; }
"""


def _value(field: str, value: Any) -> str:
    if value is None or value == "":
        return '<span class="muted">not stated</span>'
    text = escape(str(value))
    if field.endswith("_date"):
        precision = precision_of(str(value))
        if precision is not None:
            text += f' <span class="muted">({precision.value})</span>'
    return text


def _render_item(item: ReviewItem) -> str:
    rows = "".join(
        f"<tr><th>{escape(name)}</th><td>{_value(name, value)}</td></tr>"
        for name, value in item.data.items()
    )
    if item.uncertainties:
        notes = "".join(f"<li>{escape(note)}</li>" for note in item.uncertainties)
        uncertainties = f'<ul class="warn">{notes}</ul>'
    else:
        uncertainties = '<p class="none">None reported.</p>'
    return (
        '<section class="card">'
        f'<div class="head"><h2>#{item.number} &middot; {escape(item.kind)}</h2>'
        f'<span class="tag">{escape(item.status)}</span></div>'
        f"<table>{rows}</table>"
        "<h3>Uncertainties</h3>"
        f"{uncertainties}"
        "<h3>Source excerpt</h3>"
        f"<pre>{escape(item.excerpt)}</pre>"
        "</section>"
    )


def render_html(report: ReviewReport) -> str:
    counts: dict[str, int] = {}
    for item in report.items:
        counts[item.kind] = counts.get(item.kind, 0) + 1
    summary = ", ".join(f"{escape(kind)}: {count}" for kind, count in sorted(counts.items()))
    flagged = sum(1 for item in report.items if item.uncertainties)
    if report.mode == "preview":
        banner = (
            "Preview only: these proposals were computed in memory. Nothing was stored, "
            "accepted or added to the Candidate Brain."
        )
    else:
        banner = "Read-only view of stored proposals. Decisions are made through the API only."
    cards = "".join(_render_item(item) for item in report.items) or (
        '<p class="muted">No proposal was extracted. This says nothing about the candidate: '
        "absence of information is not negative information.</p>"
    )
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex, nofollow">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "style-src 'unsafe-inline'\">"
        "<title>CV proposals review</title>"
        f"<style>{_STYLE}</style></head><body>"
        "<h1>CV proposals review</h1>"
        f'<p class="meta">Source: {escape(report.source)} &middot; sha256 '
        f"{escape(report.sha256[:12])}&hellip; &middot; parser {escape(report.parser_version)}"
        f" &middot; {len(report.items)} proposals ({summary or 'none'}); "
        f"{flagged} with uncertainties</p>"
        f'<div class="banner">{escape(banner)} This file contains personal data: it lives in '
        "data/private/ (ignored by Git). Do not share or commit it.</div>"
        f"{cards}</body></html>\n"
    )
