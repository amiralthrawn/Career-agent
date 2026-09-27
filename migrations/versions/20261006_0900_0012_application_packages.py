"""application packages (step 9)

`application_packages` turns a qualified Target into an exploitable, reviewable candidature: a
reference to the original CV (never copied, never modified), the generated `ApplicationDraft`
(step 4, reused unchanged), the accepted `Contact` if any (step 8, never a `pending` observation),
and a clearly-separated `personalization_context` (company facts, contact framing, GitHub
evidence) - never merged into the draft's own Candidate-Brain-only `claims`/`selected_evidence`.

Content is immutable, exactly like `application_drafts` (0009): only `status`, `decided_at`,
`decided_by` and `superseded_by_id` may change, when a human decides or a new `prepare()` call
supersedes an undecided package.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-06 09:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0012'
down_revision: str | None = '0011'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IMMUTABLE_PACKAGE_COLUMNS = (
    "candidate_id",
    "target_id",
    "qualification_id",
    "draft_id",
    "cv_document_id",
    "contact_id",
    "personalization_context",
    "warnings",
    "inputs_fingerprint",
    "created_at",
)
PG_FUNCTION = (
    "CREATE OR REPLACE FUNCTION forbid_row_update() RETURNS trigger AS $$ "
    "BEGIN RAISE EXCEPTION '% rows are immutable', TG_TABLE_NAME; END; $$ LANGUAGE plpgsql"
)


def _install_trigger() -> None:
    dialect = op.get_bind().dialect.name
    scope = f" OF {', '.join(IMMUTABLE_PACKAGE_COLUMNS)}"
    if dialect == "sqlite":
        op.execute(
            "CREATE TRIGGER IF NOT EXISTS application_packages_immutable BEFORE UPDATE"
            f"{scope} ON application_packages "
            "BEGIN SELECT RAISE(ABORT, 'application_packages rows are immutable'); END"
        )
    elif dialect == "postgresql":
        op.execute(PG_FUNCTION)  # shared with earlier triggers; CREATE OR REPLACE is idempotent
        op.execute(
            f"CREATE TRIGGER application_packages_immutable BEFORE UPDATE{scope} "
            "ON application_packages FOR EACH ROW EXECUTE FUNCTION forbid_row_update()"
        )


def _drop_trigger() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS application_packages_immutable")
    elif dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS application_packages_immutable ON application_packages"
        )
    # forbid_row_update() is still used by earlier triggers: it is NOT dropped here.


def upgrade() -> None:
    op.create_table('application_packages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('target_id', sa.Integer(), nullable=False),
    sa.Column('qualification_id', sa.Integer(), nullable=True),
    sa.Column('draft_id', sa.Integer(), nullable=True),
    sa.Column('cv_document_id', sa.Integer(), nullable=True),
    sa.Column('contact_id', sa.Integer(), nullable=True),
    sa.Column('status', sa.Enum('draft', 'pending_validation', 'approved', 'rejected', 'superseded', name='applicationpackagestatus', native_enum=False, length=32), nullable=False),
    sa.Column('personalization_context', sa.JSON(), nullable=False),
    sa.Column('warnings', sa.JSON(), nullable=False),
    sa.Column('inputs_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decided_by', sa.String(length=16), nullable=True),
    sa.Column('superseded_by_id', sa.Integer(), nullable=True),
    sa.Column('candidate_id', sa.Integer(), nullable=False),
    sa.CheckConstraint("(status IN ('approved', 'rejected')) = (decided_at IS NOT NULL)", name=op.f('ck_application_packages_decided_at_iff_decided')),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_application_packages_candidate_id_candidates'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], name=op.f('fk_application_packages_contact_id_contacts'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['cv_document_id'], ['document_ingestions.id'], name=op.f('fk_application_packages_cv_document_id_document_ingestions'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['draft_id'], ['application_drafts.id'], name=op.f('fk_application_packages_draft_id_application_drafts'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['qualification_id'], ['qualifications.id'], name=op.f('fk_application_packages_qualification_id_qualifications'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['superseded_by_id'], ['application_packages.id'], name=op.f('fk_application_packages_superseded_by_id_application_packages')),
    sa.ForeignKeyConstraint(['target_id'], ['targets.id'], name=op.f('fk_application_packages_target_id_targets'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_application_packages'))
    )
    op.create_index(op.f('ix_application_packages_candidate_id'), 'application_packages', ['candidate_id'], unique=False)
    op.create_index(op.f('ix_application_packages_target_id'), 'application_packages', ['target_id'], unique=False)
    op.create_index('uq_application_packages_pending', 'application_packages', ['target_id'], unique=True, postgresql_where=sa.text("status IN ('draft', 'pending_validation')"), sqlite_where=sa.text("status IN ('draft', 'pending_validation')"))
    _install_trigger()


def downgrade() -> None:
    _drop_trigger()
    op.drop_index('uq_application_packages_pending', table_name='application_packages', postgresql_where=sa.text("status IN ('draft', 'pending_validation')"), sqlite_where=sa.text("status IN ('draft', 'pending_validation')"))
    op.drop_index(op.f('ix_application_packages_target_id'), table_name='application_packages')
    op.drop_index(op.f('ix_application_packages_candidate_id'), table_name='application_packages')
    op.drop_table('application_packages')
