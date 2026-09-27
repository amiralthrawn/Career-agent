"""application drafts (step 4)

A draft is a PROPOSED text, never sent: `application_drafts` records what an LLMClient
generated (or what a human later approved/rejected), always tied to the qualification whose
PersonalizationBrief it was built from. Content is immutable, enforced by a database trigger
(as `target_requirements` in 0007); only `status`, `decided_at`, `decided_by` and
`superseded_by_id` may change, when a human decides or a new draft supersedes an undecided one.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-27 08:38:36.753719
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0009'
down_revision: str | None = '0008'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IMMUTABLE_DRAFT_COLUMNS = (
    "candidate_id",
    "target_id",
    "qualification_id",
    "kind",
    "model",
    "subject",
    "body",
    "claims",
    "selected_evidence",
    "warnings",
    "usage_prompt_tokens",
    "usage_completion_tokens",
    "duration_ms",
    "created_at",
)
PG_FUNCTION = (
    "CREATE OR REPLACE FUNCTION forbid_row_update() RETURNS trigger AS $$ "
    "BEGIN RAISE EXCEPTION '% rows are immutable', TG_TABLE_NAME; END; $$ LANGUAGE plpgsql"
)


def _install_trigger() -> None:
    dialect = op.get_bind().dialect.name
    scope = f" OF {', '.join(IMMUTABLE_DRAFT_COLUMNS)}"
    if dialect == "sqlite":
        op.execute(
            "CREATE TRIGGER IF NOT EXISTS application_drafts_immutable BEFORE UPDATE"
            f"{scope} ON application_drafts "
            "BEGIN SELECT RAISE(ABORT, 'application_drafts rows are immutable'); END"
        )
    elif dialect == "postgresql":
        op.execute(PG_FUNCTION)  # shared with 0006/0007; CREATE OR REPLACE is idempotent
        op.execute(
            f"CREATE TRIGGER application_drafts_immutable BEFORE UPDATE{scope} "
            "ON application_drafts FOR EACH ROW EXECUTE FUNCTION forbid_row_update()"
        )


def _drop_trigger() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS application_drafts_immutable")
    elif dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS application_drafts_immutable ON application_drafts"
        )
    # forbid_row_update() is still used by 0006/0007's triggers: it is NOT dropped here.


def upgrade() -> None:
    op.create_table('application_drafts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('target_id', sa.Integer(), nullable=False),
    sa.Column('qualification_id', sa.Integer(), nullable=True),
    sa.Column('kind', sa.Enum('application_email', name='draftkind', native_enum=False, length=32), nullable=False),
    sa.Column('status', sa.Enum('proposed', 'approved', 'rejected', 'superseded', name='draftstatus', native_enum=False, length=32), nullable=False),
    sa.Column('model', sa.String(length=120), nullable=False),
    sa.Column('subject', sa.String(length=200), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('claims', sa.JSON(), nullable=False),
    sa.Column('selected_evidence', sa.JSON(), nullable=False),
    sa.Column('warnings', sa.JSON(), nullable=False),
    sa.Column('usage_prompt_tokens', sa.Integer(), nullable=True),
    sa.Column('usage_completion_tokens', sa.Integer(), nullable=True),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decided_by', sa.String(length=16), nullable=True),
    sa.Column('superseded_by_id', sa.Integer(), nullable=True),
    sa.Column('candidate_id', sa.Integer(), nullable=False),
    sa.CheckConstraint("(status IN ('approved', 'rejected')) = (decided_at IS NOT NULL)", name=op.f('ck_application_drafts_decided_at_iff_decided')),
    sa.CheckConstraint('length(body) > 0', name=op.f('ck_application_drafts_body_not_empty')),
    sa.CheckConstraint('length(subject) > 0', name=op.f('ck_application_drafts_subject_not_empty')),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_application_drafts_candidate_id_candidates'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['qualification_id'], ['qualifications.id'], name=op.f('fk_application_drafts_qualification_id_qualifications'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['superseded_by_id'], ['application_drafts.id'], name=op.f('fk_application_drafts_superseded_by_id_application_drafts')),
    sa.ForeignKeyConstraint(['target_id'], ['targets.id'], name=op.f('fk_application_drafts_target_id_targets'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_application_drafts'))
    )
    op.create_index(op.f('ix_application_drafts_candidate_id'), 'application_drafts', ['candidate_id'], unique=False)
    op.create_index(op.f('ix_application_drafts_target_id'), 'application_drafts', ['target_id'], unique=False)
    op.create_index('uq_application_drafts_pending', 'application_drafts', ['target_id', 'kind'], unique=True, postgresql_where=sa.text("status = 'proposed'"), sqlite_where=sa.text("status = 'proposed'"))
    _install_trigger()


def downgrade() -> None:
    _drop_trigger()
    op.drop_index('uq_application_drafts_pending', table_name='application_drafts', postgresql_where=sa.text("status = 'proposed'"), sqlite_where=sa.text("status = 'proposed'"))
    op.drop_index(op.f('ix_application_drafts_target_id'), table_name='application_drafts')
    op.drop_index(op.f('ix_application_drafts_candidate_id'), table_name='application_drafts')
    op.drop_table('application_drafts')
