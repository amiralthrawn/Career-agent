"""Review the proposals extracted from a private CV, locally and read-only.

Usage:
    python scripts/preview_cv_proposals.py documents/<cv>.docx     # parse in memory, no database
    python scripts/preview_cv_proposals.py --ingestion-id 1         # proposals already stored

The details (type, structured data, uncertainties, source excerpt, status) are written to an
HTML report in `data/private/reviews/` (git-ignored). The terminal only shows counters and the
report location: no CV content is ever printed. Nothing is stored, accepted or modified.
"""

import argparse
import collections
import sys
from collections.abc import Sequence

from app.core.config import ConfigurationError, get_settings
from app.core.database import get_session_factory
from app.core.errors import DomainError
from app.services.cv_review import (
    REVIEW_DIRECTORY,
    ReviewReport,
    preview_cv,
    stored_review,
    write_report,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("source_path", nargs="?", help="document path relative to data/private/")
    source.add_argument("--ingestion-id", type=int, help="review an already stored ingestion")
    args = parser.parse_args(argv)

    settings = get_settings()
    try:
        if args.ingestion_id is not None:
            with get_session_factory()() as session:
                report: ReviewReport = stored_review(session, settings, args.ingestion_id)
        else:
            report = preview_cv(settings, args.source_path)
        path = write_report(settings, report)
    except (DomainError, ConfigurationError) as error:
        print(f"Error: {error}", file=sys.stderr)  # messages are generic, never CV content
        return 1

    by_kind = collections.Counter(item.kind for item in report.items)
    print(f"Report written: {REVIEW_DIRECTORY}/{path.name} (inside the private data directory)")
    print(f"Proposals: {len(report.items)} {dict(sorted(by_kind.items()))}")
    print(f"With uncertainties: {sum(1 for item in report.items if item.uncertainties)}")
    print("Nothing was stored, accepted or modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
