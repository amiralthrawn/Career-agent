"""Versioned skill taxonomy (`app/data/skill_taxonomy.json`): pure data, no network, no LLM.

It answers three questions and nothing else, with three DIFFERENT relations:

- `aliases`: "which skill does this written form designate?" (postgres = PostgreSQL);
- `implies`: EXPLICIT, one-way coverage. A Skill PostgreSQL covers a requirement SQL because a
  PostgreSQL engine is an SQL database; the reverse does not hold. Only listed pairs count;
- `related`: PROXIMITY only. Tableau is near Power BI, pandas is near Python; knowing one never
  establishes the other.

The taxonomy holds no personal data and no opinion about any candidate.
"""

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.normalize import normalize_text

TAXONOMY_PATH = Path(__file__).resolve().parent.parent / "data" / "skill_taxonomy.json"

_SAFE_ALIAS = re.compile(r"^[a-z0-9 ]+$")


def fold(text: str) -> str:
    """Case-, accent- and spacing-insensitive form used to look a name up."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", stripped.casefold()).strip()


@dataclass(frozen=True)
class TaxonomyEntry:
    key: str
    name: str
    categories: tuple[str, ...]
    aliases: tuple[str, ...]  # written forms recognised in a free text
    weak_aliases: tuple[str, ...]  # recognised when comparing NAMES only (too short for free text)
    strict_aliases: tuple[str, ...]  # aliases that need case + context to be detected in a text
    context_words: tuple[str, ...]  # words that make a strict alias unambiguous
    exclude_contexts: tuple[str, ...]  # literal strings in which this entry must not be detected
    implies: tuple[str, ...]  # keys this entry EXPLICITLY covers (one way)
    related: tuple[str, ...]  # proximity only


class TaxonomyError(ValueError):
    """The taxonomy file is inconsistent (checked at load time and by a test)."""


class SkillTaxonomy:
    def __init__(self, data: dict[str, Any]) -> None:
        self.version: str = str(data["version"])
        self.programming_context_words: frozenset[str] = frozenset(
            fold(word) for word in data.get("programming_context_words", [])
        )
        entries: list[TaxonomyEntry] = []
        for raw in data["skills"]:
            entries.append(
                TaxonomyEntry(
                    key=raw["key"],
                    name=raw["name"],
                    categories=tuple(raw.get("categories", [])),
                    aliases=tuple(raw["aliases"]),
                    weak_aliases=tuple(raw.get("weak_aliases", [])),
                    strict_aliases=tuple(raw.get("strict_aliases", [])),
                    context_words=tuple(fold(w) for w in raw.get("context_words", [])),
                    exclude_contexts=tuple(raw.get("exclude_contexts", [])),
                    implies=tuple(raw.get("implies", [])),
                    related=tuple(raw.get("related", [])),
                )
            )
        self.entries: tuple[TaxonomyEntry, ...] = tuple(entries)
        self._by_key = {entry.key: entry for entry in entries}
        self._by_alias: dict[str, TaxonomyEntry] = {}
        self._by_plain_alias: dict[str, TaxonomyEntry] = {}
        self._validate_and_index()

    def _validate_and_index(self) -> None:
        if len(self._by_key) != len(self.entries):
            raise TaxonomyError("duplicate taxonomy key")
        for entry in self.entries:
            for related in entry.related:
                if related not in self._by_key:
                    raise TaxonomyError(f"{entry.key}: unknown related entry {related}")
                if related == entry.key:
                    raise TaxonomyError(f"{entry.key}: an entry cannot be related to itself")
            for implied in entry.implies:
                if implied not in self._by_key or implied == entry.key:
                    raise TaxonomyError(f"{entry.key}: invalid implied entry {implied}")
                if implied in entry.related:
                    raise TaxonomyError(
                        f"{entry.key}: {implied} cannot be both implied and related"
                    )
                if entry.key in self._by_key[implied].implies:
                    raise TaxonomyError(f"{entry.key}: coverage must be one-way with {implied}")
            if not set(entry.strict_aliases) <= set(entry.aliases):
                raise TaxonomyError(f"{entry.key}: strict_aliases must be aliases")
            for alias in (*entry.aliases, *entry.weak_aliases, entry.name):
                self._index(self._by_alias, fold(alias), entry)
                plain = fold(alias)
                if _SAFE_ALIAS.match(plain):
                    self._index(self._by_plain_alias, plain, entry)
                elif "+" not in alias and "#" not in alias:
                    # punctuation only separates words ("Power-BI"): safe to compare loosely.
                    # `+` and `#` carry meaning (C, C++, C#), so those never get this treatment.
                    collapsed = normalize_text(alias)
                    if collapsed:
                        self._index(self._by_plain_alias, collapsed, entry)

    @staticmethod
    def _index(table: dict[str, TaxonomyEntry], form: str, entry: TaxonomyEntry) -> None:
        owner = table.get(form)
        if owner is not None and owner.key != entry.key:
            raise TaxonomyError(f"alias {form!r} belongs to both {owner.key} and {entry.key}")
        table[form] = entry

    # --- lookups ------------------------------------------------------------------------

    def get(self, key: str) -> TaxonomyEntry | None:
        return self._by_key.get(key)

    def resolve(self, name: str) -> TaxonomyEntry | None:
        """The entry a written NAME designates (Brain skill name, manual label), or None."""
        found = self._by_alias.get(fold(name))
        if found is not None:
            return found
        return self._by_plain_alias.get(normalize_text(name))

    def canonical_key(self, name: str) -> str:
        """Key used to compare two names: the taxonomy key, else the normalised text."""
        entry = self.resolve(name)
        if entry is not None:
            return entry.key
        return normalize_text(name) or fold(name)

    def display_name(self, key: str) -> str | None:
        entry = self._by_key.get(key)
        return entry.name if entry else None

    def covering_keys(self, key: str) -> tuple[str, ...]:
        """Keys of the entries that EXPLICITLY cover `key` (their Skill covers a requirement)."""
        return tuple(entry.key for entry in self.entries if key in entry.implies)

    def related_keys(self, key: str) -> tuple[str, ...]:
        entry = self._by_key.get(key)
        return entry.related if entry else ()


@lru_cache(maxsize=1)
def load_taxonomy() -> SkillTaxonomy:
    with TAXONOMY_PATH.open(encoding="utf-8") as handle:
        return SkillTaxonomy(json.load(handle))
