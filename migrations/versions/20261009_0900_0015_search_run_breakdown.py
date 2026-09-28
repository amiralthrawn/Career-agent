"""search run breakdown (step 3c: --mode all)

Adds `search_runs.breakdown` (JSON, default `{}`): populated only for a `mode=all` run, splitting
the existing aggregate counters by which flow (`offers` / `companies`) an item became. An
`offers`-only or `companies`-only run leaves it empty - its own aggregate columns already say it
all. Purely additive: no existing column, constraint or row is touched.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-09 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "search_runs",
        sa.Column("breakdown", sa.JSON(), server_default="{}", nullable=False),
    )


def downgrade() -> None:
    with op.batch_alter_table("search_runs") as batch:
        batch.drop_column("breakdown")
