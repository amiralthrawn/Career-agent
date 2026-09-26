"""Dates that keep the precision the source actually gives.

A CV saying `2024` is stored as `"2024"` (precision `year`), never as `2024-01-01`. Values are
ISO 8601 reduced-precision strings: `YYYY`, `YYYY-MM` or `YYYY-MM-DD`. Nothing here ever
completes a missing month or day.
"""

import re
from datetime import date
from enum import StrEnum
from typing import Any

from sqlalchemy import String
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

_PARTIAL_DATE_RE = re.compile(r"^(?P<year>\d{4})(?:-(?P<month>\d{2})(?:-(?P<day>\d{2}))?)?$")
MIN_YEAR, MAX_YEAR = 1900, 2100


class DatePrecision(StrEnum):
    YEAR = "year"
    MONTH = "month"
    DAY = "day"


def normalize_partial_date(value: str) -> str:
    """Validate a `YYYY`, `YYYY-MM` or `YYYY-MM-DD` string and return it unchanged.

    Raises `ValueError` for anything else, including impossible dates.
    """
    text = value.strip()
    match = _PARTIAL_DATE_RE.match(text)
    if match is None:
        raise ValueError("must be YYYY, YYYY-MM or YYYY-MM-DD")
    year = int(match["year"])
    month = int(match["month"]) if match["month"] else None
    day = int(match["day"]) if match["day"] else None
    if not MIN_YEAR <= year <= MAX_YEAR:
        raise ValueError(f"year must be between {MIN_YEAR} and {MAX_YEAR}")
    if month is not None and not 1 <= month <= 12:
        raise ValueError("month must be between 01 and 12")
    if day is not None:
        assert month is not None
        try:
            date(year, month, day)
        except ValueError:
            raise ValueError("not a real calendar date") from None
    return text


def precision_of(value: str | None) -> DatePrecision | None:
    """Precision of a stored partial date; `None` when there is no date (unknown)."""
    if not value:
        return None
    return {4: DatePrecision.YEAR, 7: DatePrecision.MONTH, 10: DatePrecision.DAY}[len(value)]


def is_before(first: str, second: str) -> bool:
    """True only when `first` is certainly earlier than `second`.

    Compared on the precision both dates share: `2024-06` and `2024` overlap, so neither is
    before the other.
    """
    shared = min(len(first), len(second))
    return first[:shared] < second[:shared]


class PartialDateType(TypeDecorator[str]):
    """VARCHAR(10) column holding a partial date; invalid values never reach the database."""

    impl = String(10)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("partial dates are stored as ISO strings (YYYY, YYYY-MM, YYYY-MM-DD)")
        return normalize_partial_date(value)
