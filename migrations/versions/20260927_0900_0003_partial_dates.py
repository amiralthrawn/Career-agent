"""partial dates

Dates keep the precision of their source: 'YYYY', 'YYYY-MM' or 'YYYY-MM-DD' (VARCHAR(10)).
A year is never stored as a full date.

Upgrade converts existing DATE values to 'YYYY-MM-DD' (no information lost).
Downgrade converts back to DATE; partial dates (year or month only) cannot be represented and
are set to NULL rather than completed with an invented day or month.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DATE_COLUMNS: dict[str, tuple[str, ...]] = {
    "education": ("start_date", "end_date"),
    "experiences": ("start_date", "end_date"),
    "projects": ("start_date", "end_date"),
    "certifications": ("issue_date", "expiration_date"),
}


def upgrade() -> None:
    for table, columns in DATE_COLUMNS.items():
        with op.batch_alter_table(table) as batch:
            for column in columns:
                batch.alter_column(
                    column,
                    existing_type=sa.Date(),
                    type_=sa.String(length=10),
                    existing_nullable=True,
                    postgresql_using=f"to_char({column}, 'YYYY-MM-DD')",
                )


def downgrade() -> None:
    is_sqlite = op.get_bind().dialect.name == "sqlite"
    for table, columns in DATE_COLUMNS.items():
        for column in columns:
            # Partial dates cannot be represented as DATE: drop them instead of inventing a day.
            op.execute(f"UPDATE {table} SET {column} = NULL WHERE length({column}) <> 10")
        if is_sqlite:
            # SQLite (dev/tests) already stores DATE as text, so complete dates stay valid, and
            # the CAST that a column type change would apply corrupts them ('2021-09-15' -> 2021).
            continue
        with op.batch_alter_table(table) as batch:
            for column in columns:
                batch.alter_column(
                    column,
                    existing_type=sa.String(length=10),
                    type_=sa.Date(),
                    existing_nullable=True,
                    postgresql_using=f"{column}::date",
                )
