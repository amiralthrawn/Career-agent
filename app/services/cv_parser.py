"""Deterministic, local extraction of PROPOSALS from CV text.

Principles (see docs/cv-ingestion.md):
- the output is a list of proposals, never facts;
- nothing is invented: a value that is not clearly stated stays `None` (a year stays a year, a
  month stays a month: dates are never completed), and anything ambiguous
  is reported in `uncertainties` so a human decides;
- each proposal keeps the exact passage of the CV it comes from;
- absence is never negation: a missing section or field produces nothing, and no level, date,
  degree or skill is ever guessed;
- no logging, no network, no external service.

The parser is heuristic and layout dependent by nature; its job is to propose, not to decide.
"""

import hashlib
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.models.enums import EmploymentType, EvidenceTargetType, LanguageLevel
from app.models.partial_date import normalize_partial_date, precision_of

PARSER_VERSION = "cv-docx-local-1"
MAX_PROPOSALS = 300
MAX_EXCERPT_CHARS = 4000
MAX_ENTRY_LINES = 40
MAX_MONTH = 12

# --- Uncertainty messages (constants: they never contain CV text) ---------------------

SINGLE_DATE = "A single date was found and was not assigned to a start or an end date."


def single_date_note(value: str) -> str:
    """Keep the stated date visible to the reviewer, with its real precision."""
    precision = precision_of(value)
    label = precision.value if precision else "unknown"
    return (
        f"{SINGLE_DATE} Stated: {value} ({label}). Provide start_date or end_date when accepting."
    )


INVALID_DATE = "A date-like text could not be interpreted and was ignored."
DATE_ORDER_ASSUMED = (
    "A numeric date could be day/month or month/day; it was read as day/month/year "
    "(French convention). Check it."
)
INSTITUTION_GUESSED = "The institution was taken from an unclassified part of the line."
ORDER_ASSUMED = "Company and job title are assumed from their order in the line."
ISSUER_ASSUMED = "The issuer is assumed to be the second part of the line."
PARENTHESES_IGNORED = "Text in parentheses was not interpreted: no level or detail was inferred."
LEVEL_UNMAPPED = "A level is described but does not map to a CEFR level or 'native'."
MULTIPLE_URLS = "Several URLs of the same kind were found; the first one is used."
UNUSED_TEXT = "Part of the entry could not be interpreted; see the excerpt."


def missing_field(name: str) -> str:
    return f"Required field '{name}' could not be determined; provide it when accepting."


REQUIRED_FIELDS: dict[EvidenceTargetType, tuple[str, ...]] = {
    EvidenceTargetType.EDUCATION: ("institution",),
    EvidenceTargetType.EXPERIENCE: ("company", "title"),
    EvidenceTargetType.PROJECT: ("name",),
    EvidenceTargetType.SKILL: ("name",),
    EvidenceTargetType.CERTIFICATION: ("name",),
    EvidenceTargetType.LANGUAGE: ("language",),
}


@dataclass
class ProposalDraft:
    kind: EvidenceTargetType
    data: dict[str, Any]
    source_excerpt: str
    uncertainties: list[str] = field(default_factory=list)


@dataclass
class ParseResult:
    drafts: list[ProposalDraft]
    stats: dict[str, Any]


# --- Text helpers ---------------------------------------------------------------------


def normalize(text: str) -> str:
    """Lowercase, accent-free, punctuation-free form used for comparisons and hashing."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", stripped.casefold()).strip()


BULLET_RE = re.compile(
    r"^\s*(?:[•·▪▫◦●○■□➢"
    r"➤►▶‣⁃*]|[-–—](?=\s))\s*"
)


def strip_bullet(line: str) -> tuple[str, bool]:
    match = BULLET_RE.match(line)
    if match:
        return line[match.end() :].strip(), True
    return line.strip(), False


def split_top_level(text: str, separators: str) -> list[str]:
    """Split on any of `separators`, ignoring those inside parentheses."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char in separators and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


SEGMENT_SPLIT_RE = re.compile(r"\t|\s+[-–—|]\s+|\s{3,}|\s*\|\s*")


def split_segments(text: str) -> list[str]:
    return [part.strip(" ,;:") for part in SEGMENT_SPLIT_RE.split(text) if part.strip(" ,;:")]


# --- Sections -------------------------------------------------------------------------

SECTION_HEADERS: dict[str, set[str]] = {
    "education": {
        "formation",
        "formations",
        "education",
        "etudes",
        "parcours academique",
        "parcours scolaire",
        "diplomes",
        "formation academique",
        "formations et diplomes",
        "formation et diplomes",
        "education and training",
        "academic background",
        "cursus",
        "cursus academique",
        "etudes et formations",
    },
    "experience": {
        "experience",
        "experiences",
        "experience professionnelle",
        "experiences professionnelles",
        "parcours professionnel",
        "work experience",
        "professional experience",
        "employment history",
        "work history",
        "experience pro",
        "experiences pro",
        "stages",
        "experiences et stages",
        "stages et experiences",
        "experiences professionnelles et stages",
        "stages et alternance",
        "alternance",
    },
    "project": {
        "projets",
        "projet",
        "projets personnels",
        "projets academiques",
        "projets realises",
        "realisations",
        "projects",
        "personal projects",
        "academic projects",
        "selected projects",
        "projets et realisations",
        "projets personnels et academiques",
    },
    "skill": {
        "competences",
        "competences techniques",
        "skills",
        "technical skills",
        "competences cles",
        "technologies",
        "outils",
        "competences informatiques",
        "hard skills",
        "soft skills",
    },
    "certification": {
        "certifications",
        "certification",
        "certificats",
        "certificats et formations",
        "certifications et formations",
        "licences et certifications",
        "licenses and certifications",
        "diplomes et certifications",
        "formations complementaires",
        "formations et certifications",
    },
    "language": {
        "langues",
        "langue",
        "languages",
        "language",
        "competences linguistiques",
        "langues etrangeres",
    },
    "ignored": {
        "profil",
        "profil professionnel",
        "resume",
        "summary",
        "about me",
        "a propos",
        "objectif",
        "objectifs",
        "centres d interet",
        "centre d interet",
        "interets",
        "loisirs",
        "hobbies",
        "interests",
        "references",
        "informations complementaires",
        "contact",
        "coordonnees",
        "benevolat",
        "vie associative",
        "engagements",
        "atouts",
        "qualites",
    },
}
HEADER_LOOKUP = {name: section for section, names in SECTION_HEADERS.items() for name in names}
MAX_HEADER_CHARS = 60


def section_of(line: str) -> str | None:
    text, is_bullet = strip_bullet(line)
    if is_bullet or not text or len(text) > MAX_HEADER_CHARS:
        return None
    return HEADER_LOOKUP.get(normalize(text))


LIST_SECTIONS = {"skill", "language", "certification"}
MAX_UNLISTED_HEADER_CHARS = 30


def _looks_like_unlisted_header(lines: list[str], index: int) -> bool:
    """A short isolated line (blank before, content after) with no list punctuation."""
    text, is_bullet = strip_bullet(lines[index])
    if is_bullet or not text or len(text) > MAX_UNLISTED_HEADER_CHARS:
        return False
    if any(char in text for char in ":,;.") or any(char.isdigit() for char in text):
        return False
    after_blank = index == 0 or not lines[index - 1].strip()
    has_content_after = index + 1 < len(lines) and bool(lines[index + 1].strip())
    return after_blank and has_content_after


def split_sections(lines: list[str]) -> list[tuple[str, list[str]]]:
    """Split into (section, lines). An unrecognised title after a list section ends that section.

    Without this, the content of an unknown section (hobbies, interests...) would be read as
    skills, languages or certifications. Unrecognised content is ignored, never guessed.
    """
    sections: list[tuple[str, list[str]]] = []
    current: tuple[str, list[str]] | None = None
    for index, line in enumerate(lines):
        section = section_of(line)
        if section is None and current and current[0] in LIST_SECTIONS:
            if _looks_like_unlisted_header(lines, index):
                section = "ignored"
        if section is not None:
            current = (section, [])
            sections.append(current)
        elif current is not None:
            current[1].append(line)
    return sections


# --- Dates ----------------------------------------------------------------------------

_MONTH_NAMES = {
    "janv": 1, "janvier": 1, "jan": 1, "january": 1,
    "fevr": 2, "fevrier": 2, "fev": 2, "feb": 2, "february": 2,
    "mars": 3, "mar": 3, "march": 3,
    "avr": 4, "avril": 4, "apr": 4, "april": 4,
    "mai": 5, "may": 5,
    "juin": 6, "jun": 6, "june": 6,
    "juil": 7, "juillet": 7, "jul": 7, "july": 7,
    "aout": 8, "aug": 8, "august": 8,
    "sept": 9, "sep": 9, "septembre": 9, "september": 9,
    "oct": 10, "octobre": 10, "october": 10,
    "nov": 11, "novembre": 11, "november": 11,
    "dec": 12, "decembre": 12, "december": 12,
}  # fmt: skip
_ACCENTED_MONTHS = ("février", "févr", "fév", "août", "décembre", "déc")
_MONTH_RE = "|".join(
    sorted((re.escape(m) for m in (*_MONTH_NAMES, *_ACCENTED_MONTHS)), key=len, reverse=True)
)
_YEAR = r"(?:19|20)\d{2}"
_DATE = (
    rf"(?:\d{{1,2}}/\d{{1,2}}/{_YEAR}|\d{{1,2}}/{_YEAR}"
    rf"|(?:{_MONTH_RE})\.?\s+{_YEAR}|{_YEAR})"
)
_PRESENT = r"(?:pr[ée]sent|aujourd'?hui|actuel(?:lement)?|en cours|current|now|ongoing|today)"
_SEP = r"\s*(?:[-–—→]|to|until|à|au)\s*"
PERIOD_RE = re.compile(
    rf"(?<![\d/])(?P<start>{_DATE})(?![\d/])(?:{_SEP}(?P<end>{_DATE}|{_PRESENT})(?![\d/]))?",
    re.IGNORECASE,
)
PRESENT_RE = re.compile(_PRESENT, re.IGNORECASE)


@dataclass
class Period:
    """Dates as partial ISO strings (`YYYY`, `YYYY-MM`, `YYYY-MM-DD`): the CV's precision."""

    start: str | None = None
    end: str | None = None
    ongoing: bool = False
    single: str | None = None
    span: tuple[int, int] = (0, 0)
    flags: list[str] = field(default_factory=list)


def parse_date(text: str, flags: list[str]) -> str | None:
    """Keep exactly what the CV states: a year stays a year, a month stays a month.

    A missing month or day is never completed.
    """
    value = text.strip()
    token = normalize(value).replace(" ", "-")
    try:
        if value.count("/") == 2:
            day, month, year = (int(part) for part in value.split("/"))
            if day <= MAX_MONTH and month <= MAX_MONTH and day != month:
                flags.append(DATE_ORDER_ASSUMED)
            return normalize_partial_date(f"{year:04d}-{month:02d}-{day:02d}")
        if value.count("/") == 1:
            month, year = (int(part) for part in value.split("/"))
            return normalize_partial_date(f"{year:04d}-{month:02d}")
        if re.fullmatch(_YEAR, value):
            return normalize_partial_date(value)
        month_name, year_text = token.rsplit("-", 1)
        return normalize_partial_date(f"{int(year_text):04d}-{_MONTH_NAMES[month_name]:02d}")
    except (ValueError, KeyError):
        flags.append(INVALID_DATE)
        return None


def find_period(line: str) -> Period | None:
    match = PERIOD_RE.search(line)
    if match is None:
        return None
    period = Period(span=match.span())
    start_text, end_text = match.group("start"), match.group("end")
    if end_text is None:
        period.single = parse_date(start_text, period.flags)
    else:
        period.start = parse_date(start_text, period.flags)
        if PRESENT_RE.fullmatch(end_text.strip()):
            period.ongoing = True
        else:
            period.end = parse_date(end_text, period.flags)
    return period


def remove_span(text: str, span: tuple[int, int]) -> str:
    return (text[: span[0]] + " " + text[span[1] :]).strip(" ()-–—|,;:\t")


# --- Entries --------------------------------------------------------------------------


def group_entries(lines: list[str]) -> list[list[str]]:
    """Group section lines into entries: header lines followed by a body (bullets/text)."""
    entries: list[list[str]] = []
    current: list[str] = []
    has_body = False
    header_count = 0
    dated_header = False
    for raw in lines:
        if not raw.strip():
            if current:
                entries.append(current)
            current, has_body, header_count, dated_header = [], False, 0, False
            continue
        text, is_bullet = strip_bullet(raw)
        header_like = not is_bullet and len(text) <= 120
        dated = header_like and find_period(text) is not None
        starts_new = (
            current and header_like and (has_body or header_count >= 3 or (dated and dated_header))
        )
        if starts_new:
            entries.append(current)
            current, has_body, header_count, dated_header = [], False, 0, False
        current.append(raw)
        if header_like:
            header_count += 1
            dated_header = dated_header or dated
        else:
            has_body = True
    if current:
        entries.append(current)
    return entries


@dataclass
class Entry:
    headers: list[str]
    body: list[str]
    excerpt: str


def make_entry(lines: list[str]) -> Entry | None:
    headers: list[str] = []
    body: list[str] = []
    for raw in lines[:MAX_ENTRY_LINES]:
        text, is_bullet = strip_bullet(raw)
        if not text:
            continue
        if not is_bullet and len(text) <= 120 and not body:
            headers.append(text)
        else:
            body.append(text)
    if not headers:
        return None
    return Entry(headers, body, "\n".join(lines[:MAX_ENTRY_LINES])[:MAX_EXCERPT_CHARS])


def entry_period(entry: Entry) -> tuple[Period | None, list[str]]:
    """First period in the header lines, and the header lines with that period removed."""
    period: Period | None = None
    cleaned: list[str] = []
    for header in entry.headers:
        found = find_period(header) if period is None else None
        if found is not None:
            period = found
            remainder = remove_span(header, found.span)
            if remainder:
                cleaned.append(remainder)
        else:
            cleaned.append(header)
    return period, cleaned


def apply_period(data: dict[str, Any], period: Period | None, uncertainties: list[str]) -> None:
    if period is None:
        return
    data["start_date"] = period.start
    data["end_date"] = period.end
    if period.single is not None:
        uncertainties.append(single_date_note(period.single))
    uncertainties.extend(period.flags)


# --- Keyword classification -----------------------------------------------------------

INSTITUTION_WORDS = re.compile(
    r"\b(universit\w*|ecole|école|school|institut\w*|iut|lycee|lycée|college|collège|faculte|"
    r"faculté|academy|academie|académie|polytech\w*|business school|campus|epita|epitech)\b",
    re.IGNORECASE,
)
DEGREE_WORDS = re.compile(
    r"\b(master|licence|bachelor|bts|dut|but|mba|msc|bsc|doctorat|phd|bac|baccalaur\w*|"
    r"dipl[oô]me|ing[ée]nieur|cycle|classe pr[ée]paratoire|prépa|prepa|degree|certificat)\b",
    re.IGNORECASE,
)
TITLE_WORDS = re.compile(
    r"\b(stagiaire|stage|intern|internship|alternant|alternance|apprenti|d[ée]veloppeu\w*|"
    r"developer|engineer|ing[ée]nieur|analyste|analyst|data \w+|consultant|chef|manager|"
    r"responsable|assistant|technicien|freelance|charg[ée]|architect\w*|administrat\w*|"
    r"scientist|designer|product|project manager|lead|head)\b",
    re.IGNORECASE,
)
FIELD_LABEL_RE = re.compile(
    r"^(?:sp[ée]cialit[ée]|specialization|specialisation|major|fili[èe]re|option|parcours)"
    r"\s*:\s*(?P<value>.+)$",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://[^\s)>\]]+", re.IGNORECASE)
REPO_HOSTS = ("github.com", "gitlab.com", "bitbucket.org")

EMPLOYMENT_KEYWORDS: list[tuple[re.Pattern[str], EmploymentType]] = [
    (re.compile(r"\b(stage|stagiaire|intern|internship)\b", re.I), EmploymentType.INTERNSHIP),
    (
        re.compile(r"\b(alternance|alternant|apprentissage|apprenti|apprenticeship)\b", re.I),
        EmploymentType.APPRENTICESHIP,
    ),
    (re.compile(r"\b(freelance|ind[ée]pendant)\b", re.I), EmploymentType.FREELANCE),
    (re.compile(r"\bCDD\b"), EmploymentType.FIXED_TERM),
    (re.compile(r"\b(temps partiel|part[- ]time)\b", re.I), EmploymentType.PART_TIME),
    (re.compile(r"\b(b[ée]n[ée]vol\w*|volunteer)\b", re.I), EmploymentType.VOLUNTEER),
]
IN_PROGRESS_RE = re.compile(r"\b(en cours|in progress|expected|pr[ée]vu)\b", re.I)


def join_body(entry: Entry) -> str | None:
    text = "\n".join(entry.body).strip()
    return text or None


# --- Per-kind extraction --------------------------------------------------------------


def _education_segments(header: str) -> list[str]:
    """Split on separators; also on commas when one part mixes a degree and an institution."""
    segments: list[str] = []
    for segment in split_segments(header):
        if INSTITUTION_WORDS.search(segment) and DEGREE_WORDS.search(segment):
            segments.extend(split_top_level(segment, ","))
        else:
            segments.append(segment)
    return segments


def parse_education(entry: Entry) -> ProposalDraft:
    uncertainties: list[str] = []
    period, headers = entry_period(entry)
    institution: str | None = None
    degrees: list[str] = []
    unclassified: list[str] = []
    field_of_study: str | None = None
    for header in headers:
        label = FIELD_LABEL_RE.match(header)
        if label:
            field_of_study = label.group("value").strip()
            continue
        for segment in _education_segments(header):
            if INSTITUTION_WORDS.search(segment) and institution is None:
                institution = segment
            elif DEGREE_WORDS.search(segment):
                degrees.append(segment)
            else:
                unclassified.append(segment)
    if institution is None and unclassified:
        institution = unclassified.pop(0)
        uncertainties.append(INSTITUTION_GUESSED)
    if unclassified:
        uncertainties.append(UNUSED_TEXT)
    in_progress = bool(period and period.ongoing) or any(
        IN_PROGRESS_RE.search(text) for text in entry.headers
    )
    data: dict[str, Any] = {
        "institution": institution,
        "degree": ", ".join(degrees) or None,
        "field_of_study": field_of_study,
        "description": join_body(entry),
        "status": "in_progress" if in_progress else None,
        "start_date": None,
        "end_date": None,
    }
    apply_period(data, period, uncertainties)
    return ProposalDraft(EvidenceTargetType.EDUCATION, data, entry.excerpt, uncertainties)


def parse_experience(entry: Entry) -> ProposalDraft:
    uncertainties: list[str] = []
    period, headers = entry_period(entry)
    segments = [segment for header in headers for segment in split_segments(header)]
    title: str | None = None
    company: str | None = None
    titled = [segment for segment in segments if TITLE_WORDS.search(segment)]
    others = [segment for segment in segments if segment not in titled]
    if len(titled) == 1 and len(others) >= 1:
        title, company = titled[0], others[0]
        if len(others) > 1:
            uncertainties.append(UNUSED_TEXT)
    elif len(segments) >= 2:
        title, company = segments[0], segments[1]
        uncertainties.append(ORDER_ASSUMED)
        if len(segments) > 2:
            uncertainties.append(UNUSED_TEXT)
    elif len(segments) == 1:
        if TITLE_WORDS.search(segments[0]):
            title = segments[0]
        else:
            company = segments[0]
    employment_type = next(
        (kind.value for pattern, kind in EMPLOYMENT_KEYWORDS if any(map(pattern.search, headers))),
        None,
    )
    data: dict[str, Any] = {
        "company": company,
        "title": title,
        "description": join_body(entry),
        "employment_type": employment_type,
        "start_date": None,
        "end_date": None,
    }
    apply_period(data, period, uncertainties)
    return ProposalDraft(EvidenceTargetType.EXPERIENCE, data, entry.excerpt, uncertainties)


def parse_project(entry: Entry) -> ProposalDraft:
    uncertainties: list[str] = []
    period, headers = entry_period(entry)
    urls = URL_RE.findall(" ".join([*entry.headers, *entry.body]))
    repo_urls = [u for u in urls if any(host in u.lower() for host in REPO_HOSTS)]
    site_urls = [u for u in urls if u not in repo_urls]
    if len(repo_urls) > 1 or len(site_urls) > 1:
        uncertainties.append(MULTIPLE_URLS)
    headers = [URL_RE.sub("", header).strip(" -–—|,;:") for header in headers]
    segments = [segment for header in headers for segment in split_segments(header)]
    name = segments[0] if segments else None
    description_parts = [*segments[1:]]
    if description_parts:
        description_parts = [" - ".join(description_parts)]
    body = join_body(entry)
    if body:
        description_parts.append(body)
    data: dict[str, Any] = {
        "name": name,
        "description": "\n".join(description_parts) or None,
        "url": site_urls[0] if site_urls else None,
        "repository_url": repo_urls[0] if repo_urls else None,
        "domain": None,
        "start_date": None,
        "end_date": None,
    }
    apply_period(data, period, uncertainties)
    return ProposalDraft(EvidenceTargetType.PROJECT, data, entry.excerpt, uncertainties)


LABEL_RE = re.compile(r"^(?P<label>[^:\d]{2,40}):\s*(?P<items>.+)$")
PAREN_TAIL_RE = re.compile(r"\s*\([^)]*\)?\s*$")


def parse_skill_line(line: str) -> tuple[list[ProposalDraft], int]:
    """Skills listed on one line; returns drafts and the number of skipped items."""
    text, _ = strip_bullet(line)
    category: str | None = None
    label = LABEL_RE.match(text)
    if label:
        category = label.group("label").strip()
        text = label.group("items")
    drafts: list[ProposalDraft] = []
    skipped = 0
    for item in split_top_level(text, ",;|•·"):
        name = PAREN_TAIL_RE.sub("", item).strip(" .")
        if not name or len(name) > 60 or len(name.split()) > 6:
            skipped += 1
            continue
        uncertainties = [PARENTHESES_IGNORED] if PAREN_TAIL_RE.search(item) else []
        data: dict[str, Any] = {
            "name": name,
            "category": category,
            "level": None,
            "description": None,
        }
        drafts.append(ProposalDraft(EvidenceTargetType.SKILL, data, line.strip(), uncertainties))
    return drafts, skipped


CEFR_RE = re.compile(r"\b([abc][12])\b", re.IGNORECASE)
NATIVE_RE = re.compile(r"\b(native|natif|maternelle|mother tongue)\b", re.IGNORECASE)
LANGUAGE_NAME_RE = re.compile(r"^[A-Za-zÀ-ÿ'’]+(?: [A-Za-zÀ-ÿ'’]+){0,2}")
NOT_LANGUAGES = {
    "toeic",
    "toefl",
    "ielts",
    "delf",
    "dalf",
    "cambridge",
    "bulats",
    "score",
    "niveau",
}


def parse_language_line(line: str) -> tuple[list[ProposalDraft], int]:
    text, _ = strip_bullet(line)
    label = LABEL_RE.match(text)
    if label and normalize(label.group("label")) in SECTION_HEADERS["language"]:
        text = label.group("items")
    drafts: list[ProposalDraft] = []
    skipped = 0
    for item in split_top_level(text, ",;|•·"):
        match = LANGUAGE_NAME_RE.match(item)
        if match is None or normalize(match.group()) in NOT_LANGUAGES:
            skipped += 1
            continue
        rest = item[match.end() :].strip(" :-–—()")
        if len(rest.split()) > 8:
            skipped += 1
            continue
        uncertainties: list[str] = []
        level: str | None = None
        cefr = CEFR_RE.search(rest)
        if cefr:
            level = LanguageLevel(cefr.group(1).lower()).value
        elif NATIVE_RE.search(rest):
            level = LanguageLevel.NATIVE.value
        elif rest:
            uncertainties.append(LEVEL_UNMAPPED)
        data: dict[str, Any] = {"language": match.group().strip(), "level": level}
        drafts.append(ProposalDraft(EvidenceTargetType.LANGUAGE, data, line.strip(), uncertainties))
    return drafts, skipped


def parse_certification_line(line: str) -> ProposalDraft | None:
    text, _ = strip_bullet(line)
    if not text or len(text) > 200:
        return None
    uncertainties: list[str] = []
    period = find_period(text)
    if period is not None:
        text = remove_span(text, period.span)
    segments = split_segments(text)
    if not segments:
        return None
    name, issuer = segments[0], None
    if len(segments) == 2:
        issuer = segments[1]
        uncertainties.append(ISSUER_ASSUMED)
    elif len(segments) > 2:
        name = " - ".join(segments)
    urls = URL_RE.findall(line)
    data: dict[str, Any] = {
        "name": URL_RE.sub("", name).strip(),
        "issuer": issuer,
        "issue_date": None,
        "expiration_date": None,
        "credential_url": urls[0] if urls else None,
        "description": None,
    }
    if period is not None:
        single = period.single or period.start
        data["issue_date"] = single
        if period.end or period.ongoing:
            uncertainties.append(SINGLE_DATE)
        uncertainties.extend(period.flags)
    return ProposalDraft(EvidenceTargetType.CERTIFICATION, data, line.strip(), uncertainties)


# --- Natural keys, fingerprints, orchestration ----------------------------------------


def natural_key(kind: EvidenceTargetType, get: Callable[[str], Any]) -> str:
    """Normalised identity of a fact; shared by proposals and existing facts."""

    def value(name: str) -> str:
        raw = get(name)
        raw = getattr(raw, "value", raw)
        return normalize(str(raw)) if raw else ""

    match kind:
        case EvidenceTargetType.SKILL:
            parts = [value("name")]
        case EvidenceTargetType.LANGUAGE:
            parts = [value("language")]
        case EvidenceTargetType.PROJECT | EvidenceTargetType.CERTIFICATION:
            parts = [value("name")]
        case EvidenceTargetType.EDUCATION:
            parts = [value("institution"), value("degree"), value("start_date")]
        case EvidenceTargetType.EXPERIENCE:
            parts = [value("company"), value("title"), value("start_date")]
    return "|".join(parts)


def fingerprint(kind: EvidenceTargetType, data: dict[str, Any], excerpt: str) -> str:
    key = natural_key(kind, data.get)
    if not key.strip("|"):
        key = normalize(excerpt)
    return hashlib.sha256(f"{kind.value}|{key}".encode()).hexdigest()


def finalize(draft: ProposalDraft) -> ProposalDraft:
    """Flag missing required fields; never silently fix or invent values."""
    for name in REQUIRED_FIELDS[draft.kind]:
        if not draft.data.get(name):
            draft.uncertainties.append(missing_field(name))
    draft.uncertainties = list(dict.fromkeys(draft.uncertainties))
    return draft


def parse_cv(text: str) -> ParseResult:
    lines = text.split("\n")
    drafts: list[ProposalDraft] = []
    skipped = 0
    sections = split_sections(lines)
    for section, section_lines in sections:
        if section == "ignored":
            continue
        if section in ("skill", "language", "certification"):
            for line in section_lines:
                if not line.strip():
                    continue
                if section == "skill":
                    found, missed = parse_skill_line(line)
                    drafts.extend(found)
                    skipped += missed
                elif section == "language":
                    found, missed = parse_language_line(line)
                    drafts.extend(found)
                    skipped += missed
                else:
                    certification = parse_certification_line(line)
                    if certification is None:
                        skipped += 1
                    else:
                        drafts.append(certification)
            continue
        parser = {
            "education": parse_education,
            "experience": parse_experience,
            "project": parse_project,
        }[section]
        for group in group_entries(section_lines):
            entry = make_entry(group)
            if entry is None:
                skipped += 1
            else:
                drafts.append(parser(entry))

    unique: dict[str, ProposalDraft] = {}
    duplicates = 0
    for draft in map(finalize, drafts):
        key = fingerprint(draft.kind, draft.data, draft.source_excerpt)
        if key in unique:
            duplicates += 1
        elif len(unique) < MAX_PROPOSALS:
            unique[key] = draft
    stats = {
        "lines": len([line for line in lines if line.strip()]),
        "sections": sorted({section for section, _ in sections}),
        "proposals": len(unique),
        "duplicates_skipped": duplicates,
        "items_skipped": skipped,
    }
    return ParseResult(list(unique.values()), stats)
