"""campaigns (step 3c: chained, bounded sourcing toward a daily target)

`campaigns` chains as many `SearchRun`s as needed toward a daily target, inside one bounded,
human-started process, and stops itself at the first safety limit (call count, wall-clock
duration, or too many searches in a row with no new target) or provider failure. Adds
`search_runs.campaign_id` (nullable: a manually-triggered run is unaffected).

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-10 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "campaigns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column(
            "mode",
            sa.Enum("offers", "companies", "all", name="sourcingmode", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("max_results_per_call", sa.Integer(), nullable=False),
        sa.Column("daily_target", sa.Integer(), nullable=False),
        sa.Column("max_calls", sa.Integer(), nullable=False),
        sa.Column("max_duration_minutes", sa.Integer(), nullable=False),
        sa.Column("max_consecutive_empty", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "running",
                "completed",
                "stopped_call_limit",
                "stopped_duration_limit",
                "stopped_no_new_results",
                "stopped_provider_error",
                "failed",
                name="campaignstatus",
                native_enum=False,
                length=32,
            ),
            server_default="running",
            nullable=False,
        ),
        sa.Column("stop_reason", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("calls_made", sa.Integer(), server_default="0", nullable=False),
        sa.Column("consecutive_empty_calls", sa.Integer(), server_default="0", nullable=False),
        sa.Column("targets_created", sa.Integer(), server_default="0", nullable=False),
        sa.Column("requirements_extracted", sa.Integer(), server_default="0", nullable=False),
        sa.Column("contacts_proposed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("drafts_generated", sa.Integer(), server_default="0", nullable=False),
        sa.Column("packages_prepared", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["search_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_campaigns_candidate_id", "campaigns", ["candidate_id"])

    op.add_column("search_runs", sa.Column("campaign_id", sa.Integer(), nullable=True))
    op.create_index("ix_search_runs_campaign_id", "search_runs", ["campaign_id"])
    with op.batch_alter_table("search_runs") as batch:
        batch.create_foreign_key(
            "fk_search_runs_campaign_id", "campaigns", ["campaign_id"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    with op.batch_alter_table("search_runs") as batch:
        batch.drop_constraint("fk_search_runs_campaign_id", type_="foreignkey")
        batch.drop_index("ix_search_runs_campaign_id")
        batch.drop_column("campaign_id")
    op.drop_index("ix_campaigns_candidate_id", table_name="campaigns")
    op.drop_table("campaigns")
