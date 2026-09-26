"""Deterministic extraction of requirements from an offer text.

Pure: no database, no network, no LLM.

What this module promises:

- **Only what the text says.** A requirement is created for a skill of the versioned taxonomy that
  is WRITTEN in the text, or for an explicit duration of experience ("2 ans d'experience").
- **Exact excerpt.** `excerpt` is a contiguous slice of the source text (the sentence or line where
  the requirement is written), never reformulated.
- **Importance only from explicit markers**, read where they are written (`required`, `requis`,
  `plus`, `souhaite`...): in the same sentence; when one sentence holds contradictory markers, in
  the same comma-separated clause; or in the heading of a SHORT block ("Nice to have:" followed
  by a few lines). A general sentence never makes a skill required. No marker, a negated marker
  ("not required") or a contradiction -> `unspecified`. When a heading gives the importance, the
  excerpt runs from that heading to the item, so it always contains the words that justify it.
- **Strict short terms.** `C`, `R`, `Go`, `Excel`... are detected only with their exact spelling
  AND an unambiguous context (a "language" word nearby, or a list next to a recognised skill).
- **No claim about the candidate**: this module never sees the Candidate Brain.
"""

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from app.core.normalize import normalize_text
from app.models.enums import RequirementImportance, RequirementKind
from app.services.skill_taxonomy import SkillTaxonomy, TaxonomyEntry, fold, load_taxonomy

# Bump when extraction rules change (stored on each extracted requirement).
EXTRACTOR_VERSION = "extract-1"

MAX_EXCERPT_CHARS = 500
MAX_QUALIFIER_CHARS = 120
MAX_BLOCK_LINES = 8  # a heading only governs the few lines that follow it
EXPERIENCE_KEY_PREFIX = "experience_years"

# --- Importance markers (explicit only) -------------------------------------------------

_REQUIRED = re.compile(
    r"\b(?:required|mandatory|must|requis|requise|requises|obligatoires?|indispensables?)\b"
)
_NICE = re.compile(
    r"\bnice[ -]to[ -]have\b|\bpreferred\b|\bdesirable\b|\bd[ée]sirables?\b"
    r"|\bsouhait[ée]e?s?\b|\bsouhaitables?\b|\bappr[ée]ci[ée]e?s?\b|\bbonus\b"
    r"|\b(?:a|un)\s+plus\b|\(\s*plus\s*\)|\bplus\s*[.:!]?\s*$"
)
_NEGATIONS = frozenset(
    {
        "not",
        "no",
        "non",
        "pas",
        "sans",
        "without",
        "never",
        "jamais",
        "n",
        "ne",
        "isn",
        "aren",
        "nor",
        "ni",
    }
)

# --- Experience ("2 years", "2+ ans", "2 a 3 ans") -----------------------------------------

_NUMBER = r"(?<![\d.,/-])(?P<num>\d{1,2})\s*(?P<plus>\+)?"
_RANGE = r"(?:\s*(?:-|–|to|à|a)\s*(?P<hi>\d{1,2}))?"
_EXPERIENCE_FORWARD = re.compile(
    _NUMBER + _RANGE + r"\s*(?P<unit>years?|yrs?|ans?|années?|annees?)\b['’]?"
    r"(?:\s+(?:of|minimum|min|de|d['’]))*\s*(?:[\w-]+\s+){0,2}?(?:d['’])?\s*"
    r"(?:experience|expérience)",
    re.IGNORECASE,
)
_EXPERIENCE_BACKWARD = re.compile(
    r"(?:experience|expérience)\s*(?:of|de|d['’]|:)?\s*(?:at least|minimum|min|au moins)?\s*"
    + _NUMBER
    + _RANGE
    + r"\s*(?P<unit>years?|yrs?|ans?|années?|annees?)\b",
    re.IGNORECASE,
)
_SCOPE = re.compile(
    r"\s*(?:in|with|using|on|as|en|dans|sur|avec|comme|en tant que)\b", re.IGNORECASE
)
_QUALIFIER_END = re.compile(r"[,;.()\n]")

_LIST_SEPARATOR = re.compile(
    r"\s*(?:,\s*(?:and\s+|et\s+|or\s+|ou\s+)?|/|&|;|\band\b|\bet\b|\bor\b|\bou\b)\s*",
    re.IGNORECASE,
)
_WORD = re.compile(r"[\w'’]+")
# A marker written in parentheses right after a term qualifies THAT term only: "Python (required)".
_BOUND = re.compile(r"\s*\(\s*([^()]{1,40}?)\s*\)")
_SEGMENT_BREAK = re.compile(r"(?<=[.!?;])\s+")


@dataclass(frozen=True)
class ExtractedRequirement:
    """One requirement found in a text. `excerpt` is an exact slice of that text."""

    kind: RequirementKind
    key: str
    label: str
    importance: RequirementImportance
    excerpt: str
    value: str | None = None  # experience: the duration as written ("2+ years")
    qualifier: str | None = None  # experience: what it is about, as written ("in data analysis")
    mentions: int = 1


@dataclass
class _Mention:
    kind: RequirementKind
    key: str
    label: str
    excerpt: str
    marker: str  # "required" | "nice_to_have" | "none" | "doubt"
    value: str | None = None
    qualifier: str | None = None


@dataclass(frozen=True)
class _Block:
    """A heading that states an importance ("Nice to have:") and where it starts in the text."""

    marker: str
    start: int


Resolver = Callable[[int], tuple[str, int | None]]
_CLAUSE_BREAK = re.compile(r",|\bbut\b|\bmais\b", re.IGNORECASE)


@dataclass(frozen=True)
class _Hit:
    start: int
    end: int
    entry: TaxonomyEntry
    strict: bool


@dataclass
class _Pattern:
    entry: TaxonomyEntry
    alias: str
    regex: re.Pattern[str]
    strict: bool


def _segments(text: str) -> Iterator[tuple[int, int]]:
    """Spans (start, end) of the sentences / lines of `text`, whitespace trimmed."""
    offset = 0
    for line in text.split("\n"):
        line_start = offset
        offset += len(line) + 1
        position = 0
        for piece in [*_SEGMENT_BREAK.split(line)]:
            begin = line.find(piece, position)
            position = begin + len(piece)
            stripped = piece.strip()
            if stripped:
                left = begin + (len(piece) - len(piece.lstrip()))
                yield line_start + left, line_start + left + len(stripped)


def _marker_of(segment: str) -> str:
    """`required` / `nice_to_have` / `none` / `doubt` / `mixed` from the markers of a text.

    `doubt`: a negated marker. `mixed`: both classes are present (the caller may look at smaller
    parts, such as clauses; if it cannot, mixed is a doubt).
    """
    lowered = segment.casefold()
    classes: set[str] = set()
    for name, pattern in (("required", _REQUIRED), ("nice_to_have", _NICE)):
        for found in pattern.finditer(lowered):
            before = _WORD.findall(lowered[: found.start()])  # the whole sentence before it
            if any(word.strip("'’") in _NEGATIONS for word in before):
                return "doubt"  # "not required": neither required nor nice to have
            classes.add(name)
    if len(classes) > 1:
        return "mixed"  # contradictory markers: which term do they qualify? Do not guess
    return next(iter(classes), "none")


def _window(text: str, start: int, end: int, hit_start: int, hit_end: int) -> str:
    """Exact slice of the source: the segment, or a bounded window around the hit."""
    if end - start <= MAX_EXCERPT_CHARS:
        return text[start:end]
    left = max(start, hit_start - MAX_EXCERPT_CHARS // 3)
    right = min(end, left + MAX_EXCERPT_CHARS)
    left = max(start, min(left, right - MAX_EXCERPT_CHARS))
    return text[left:right].strip()


def experience_years(value: str | None) -> int | None:
    """Lower figure of an explicit duration as written ("2+ years" -> 2, "2 a 3 ans" -> 2)."""
    if not value:
        return None
    found = re.match(r"\s*(\d{1,2})", value)
    return int(found.group(1)) if found else None


class RequirementExtractor:
    def __init__(self, taxonomy: SkillTaxonomy | None = None) -> None:
        self.taxonomy = taxonomy or load_taxonomy()
        self._patterns = self._compile()

    # --- compilation --------------------------------------------------------------------

    def _compile(self) -> list[_Pattern]:
        patterns: list[_Pattern] = []
        for entry in self.taxonomy.entries:
            for alias in entry.aliases:
                strict = alias in entry.strict_aliases
                body = re.escape(alias).replace(r"\ ", r"\s+")
                if strict:  # exact spelling, and not glued to symbols ("R&D", "C++", "Go-getter")
                    regex = re.compile(rf"(?<![\w&+#.\-]){body}(?![\w&+#\-])")
                else:
                    regex = re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)
                patterns.append(_Pattern(entry, alias, regex, strict))
        return patterns

    # --- skill detection ----------------------------------------------------------------

    def _hits(self, segment: str) -> list[_Hit]:
        excluded: dict[str, list[tuple[int, int]]] = {}
        candidates: list[_Hit] = []
        for pattern in self._patterns:
            for found in pattern.regex.finditer(segment):
                spans = excluded.setdefault(
                    pattern.entry.key, self._excluded_spans(pattern.entry, segment)
                )
                if any(begin <= found.start() and found.end() <= end for begin, end in spans):
                    continue
                candidates.append(_Hit(found.start(), found.end(), pattern.entry, pattern.strict))
        # Longest written form first: "SQL Server" wins over "SQL", "react.js" over "react".
        candidates.sort(key=lambda hit: (-(hit.end - hit.start), hit.start))
        accepted: list[_Hit] = []
        for hit in candidates:
            if all(hit.end <= other.start or other.end <= hit.start for other in accepted):
                accepted.append(hit)
        return self._filter_strict(segment, sorted(accepted, key=lambda hit: hit.start))

    @staticmethod
    def _excluded_spans(entry: TaxonomyEntry, segment: str) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = []
        for literal in entry.exclude_contexts:
            for found in re.finditer(re.escape(literal), segment, re.IGNORECASE):
                spans.append((found.start(), found.end()))
        return spans

    def _filter_strict(self, segment: str, hits: list[_Hit]) -> list[_Hit]:
        """Keep a strict hit only with an unambiguous context (fixed point: chains of lists)."""
        accepted = [hit for hit in hits if not hit.strict]
        pending = [hit for hit in hits if hit.strict]
        changed = True
        while changed and pending:
            changed = False
            for hit in list(pending):
                if self._has_context(segment, hit, accepted):
                    accepted.append(hit)
                    pending.remove(hit)
                    changed = True
        return sorted(accepted, key=lambda hit: hit.start)

    def _has_context(self, segment: str, hit: _Hit, accepted: list[_Hit]) -> bool:
        words = hit.entry.context_words or tuple(self.taxonomy.programming_context_words)
        context = frozenset(words)
        before = [fold(word) for word in _WORD.findall(segment[: hit.start])[-3:]]
        after = [fold(word) for word in _WORD.findall(segment[hit.end :])[:3]]
        if any(word in context for word in (*before, *after)):
            return True
        for other in accepted:  # a list next to a recognised skill: "Python, R and SQL"
            if other.end <= hit.start and _LIST_SEPARATOR.fullmatch(segment[other.end : hit.start]):
                return True
            if hit.end <= other.start and _LIST_SEPARATOR.fullmatch(segment[hit.end : other.start]):
                return True
        return False

    # --- experience detection -----------------------------------------------------------

    def _experience_mentions(
        self, text: str, start: int, end: int, resolve: Resolver
    ) -> list[_Mention]:
        segment = text[start:end]
        found: list[_Mention] = []
        taken: list[tuple[int, int]] = []
        for pattern in (_EXPERIENCE_FORWARD, _EXPERIENCE_BACKWARD):
            for match in pattern.finditer(segment):
                if any(match.start() < b and a < match.end() for a, b in taken):
                    continue
                taken.append((match.start(), match.end()))
                number = int(match["num"])
                value = segment[match.start("num") : match.end("unit")]
                qualifier = self._qualifier(segment, match.end())
                marker, excerpt_from = resolve(match.start())
                key = f"{EXPERIENCE_KEY_PREFIX}:{number}"
                if qualifier:
                    key += f":{normalize_text(qualifier)[:60]}"
                found.append(
                    _Mention(
                        kind=RequirementKind.EXPERIENCE,
                        key=key,
                        label=segment[match.start() : match.end()],
                        excerpt=(
                            text[excerpt_from:end]
                            if excerpt_from is not None
                            else _window(
                                text, start, end, start + match.start(), start + match.end()
                            )
                        ),
                        marker=marker,
                        value=value,
                        qualifier=qualifier,
                    )
                )
        return found

    @staticmethod
    def _qualifier(segment: str, after: int) -> str | None:
        tail = segment[after:]
        if not _SCOPE.match(tail):
            return None
        stop = _QUALIFIER_END.search(tail)
        piece = tail[: stop.start()] if stop else tail
        return piece.strip()[:MAX_QUALIFIER_CHARS] or None

    # --- public API ---------------------------------------------------------------------

    def mentioned_keys(self, text: str) -> frozenset[str]:
        """Taxonomy keys written in `text` (used to see which facts talk about a skill)."""
        keys: set[str] = set()
        for start, end in _segments(text):
            keys.update(hit.entry.key for hit in self._hits(text[start:end]))
        return frozenset(keys)

    @staticmethod
    def _bound_markers(segment: str, hits: list[_Hit]) -> tuple[dict[int, str], str]:
        """Markers written in parentheses after a term, and the sentence with them masked."""
        bound: dict[int, str] = {}
        masked = segment
        for position, hit in enumerate(hits):
            found = _BOUND.match(segment, hit.end)
            if found is None:
                continue
            marker = _marker_of(found.group(1))
            marker = "doubt" if marker == "mixed" else marker
            if marker != "none":
                bound[position] = marker
                masked = (
                    masked[: found.start()]
                    + " " * (found.end() - found.start())
                    + masked[found.end() :]
                )
        return bound, masked

    def _blocks(self, text: str) -> dict[int, _Block]:
        """Lines governed by a heading that states an importance, keyed by their start offset."""
        blocks: dict[int, _Block] = {}
        current: _Block | None = None
        governed = 0
        offset = 0
        for line in text.split("\n"):
            line_start = offset
            offset += len(line) + 1
            stripped = line.strip()
            if not stripped:
                current = None  # a blank line closes the block
                continue
            if stripped.endswith(":"):  # a new heading: it replaces the previous one
                marker = _marker_of(stripped)
                if marker != "none" and not self._hits(stripped):
                    lead = len(line) - len(line.lstrip())
                    current = _Block("doubt" if marker == "mixed" else marker, line_start + lead)
                    governed = 0
                else:
                    current = None
                continue
            if current is not None:
                if governed >= MAX_BLOCK_LINES:
                    current = None
                    continue
                blocks[line_start] = current
                governed += 1
        return blocks

    @staticmethod
    def _clause_marker(masked: str) -> Callable[[int], str]:
        """Marker in force at a position of a sentence.

        Normally the marker of the whole sentence. When the sentence holds contradictory markers,
        each comma-separated clause speaks for itself ("Python required, SQL a plus"); a clause
        without marker gets none, it never borrows another clause's.
        """
        whole = _marker_of(masked)
        if whole != "mixed":
            return lambda _position: whole
        cuts = [0, *(m.end() for m in _CLAUSE_BREAK.finditer(masked)), len(masked)]
        spans = list(zip(cuts, cuts[1:], strict=False))

        def at(position: int) -> str:
            for begin, end in spans:
                if begin <= position < end:
                    marker = _marker_of(masked[begin:end])
                    return "doubt" if marker == "mixed" else marker
            return "doubt"

        return at

    @staticmethod
    def _inherit(own: str, block: _Block | None, end: int) -> tuple[str, int | None]:
        """Combine a term's own marker with the heading of its block.

        Returns the marker and, when the heading is what states it, where the excerpt must start
        (so that the excerpt contains the heading). A marker of the term itself contradicting a
        different heading is a doubt.
        """
        if block is None:
            return own, None
        if own == "none":
            if block.marker == "doubt":
                return "doubt", None
            if end - block.start > MAX_EXCERPT_CHARS:
                return "none", None  # the heading is too far to be quoted with the item
            return block.marker, block.start
        return (own, None) if own == block.marker else ("doubt", None)

    def extract(self, text: str) -> list[ExtractedRequirement]:
        mentions: list[_Mention] = []
        blocks = self._blocks(text)
        for start, end in _segments(text):
            segment = text[start:end]
            block = blocks.get(text.rfind("\n", 0, start) + 1)
            hits = self._hits(segment)
            bound, masked = self._bound_markers(segment, hits)
            clause_marker = self._clause_marker(masked)

            def resolve(
                offset: int,
                own: str | None = None,
                *,
                _end: int = end,
                _block: _Block | None = block,
                _clause: Callable[[int], str] = clause_marker,
            ) -> tuple[str, int | None]:
                return self._inherit(own or _clause(offset), _block, _end)

            for position, hit in enumerate(hits):
                marker, excerpt_from = resolve(hit.start, bound.get(position))
                mentions.append(
                    _Mention(
                        kind=RequirementKind.SKILL,
                        key=hit.entry.key,
                        label=hit.entry.name,
                        excerpt=(
                            text[excerpt_from:end]
                            if excerpt_from is not None
                            else _window(text, start, end, start + hit.start, start + hit.end)
                        ),
                        marker=marker,
                    )
                )
            mentions.extend(self._experience_mentions(text, start, end, resolve))
        return _merge(mentions)


@dataclass
class _Group:
    first: _Mention
    mentions: list[_Mention] = field(default_factory=list)


def _merge(mentions: list[_Mention]) -> list[ExtractedRequirement]:
    """One requirement per key. Markers that disagree (or are in doubt) -> `unspecified`."""
    groups: dict[str, _Group] = {}
    for mention in mentions:
        group = groups.setdefault(mention.key, _Group(first=mention))
        group.mentions.append(mention)
    result: list[ExtractedRequirement] = []
    for group in groups.values():
        markers = {m.marker for m in group.mentions} - {"none"}
        importance = RequirementImportance.UNSPECIFIED
        decisive = group.first
        if markers == {"required"}:
            importance = RequirementImportance.REQUIRED
        elif markers == {"nice_to_have"}:
            importance = RequirementImportance.NICE_TO_HAVE
        if importance is not RequirementImportance.UNSPECIFIED:
            decisive = next(m for m in group.mentions if m.marker == next(iter(markers)))
        result.append(
            ExtractedRequirement(
                kind=group.first.kind,
                key=group.first.key,
                label=group.first.label,
                importance=importance,
                excerpt=decisive.excerpt,
                value=group.first.value,
                qualifier=group.first.qualifier,
                mentions=len(group.mentions),
            )
        )
    return result
