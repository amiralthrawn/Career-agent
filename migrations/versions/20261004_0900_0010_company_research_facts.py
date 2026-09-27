"""company research facts (step 7)

`company_research_facts` holds ACCEPTED, sourced external observations about a company
(Perplexity, step 6), used as extra searchable text by the unchanged deterministic
qualification matcher - never a rewrite of `companies` own fields. Fully immutable (append-
only, as `application_drafts` in 0009): an accepted observation is a historical record of
what research returned and when.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-27 11:14:15.590443
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0010'
down_revision: str | None = '0009'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PG_FUNCTION = (
    "CREATE OR REPLACE FUNCTION forbid_row_update() RETURNS trigger AS $$ "
    "BEGIN RAISE EXCEPTION '% rows are immutable', TG_TABLE_NAME; END; $$ LANGUAGE plpgsql"
)


def _install_trigger() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute(
            "CREATE TRIGGER IF NOT EXISTS company_research_facts_immutable BEFORE UPDATE "
            "ON company_research_facts "
            "BEGIN SELECT RAISE(ABORT, 'company_research_facts rows are immutable'); END"
        )
    elif dialect == "postgresql":
        op.execute(PG_FUNCTION)  # shared with earlier triggers; CREATE OR REPLACE is idempotent
        op.execute(
            "CREATE TRIGGER company_research_facts_immutable BEFORE UPDATE "
            "ON company_research_facts FOR EACH ROW EXECUTE FUNCTION forbid_row_update()"
        )


def _drop_trigger() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS company_research_facts_immutable")
    elif dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS company_research_facts_immutable ON company_research_facts"
        )
    # forbid_row_update() is still used by earlier triggers: it is NOT dropped here.


def upgrade() -> None:
    op.create_table('company_research_facts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('company_id', sa.Integer(), nullable=False),
    sa.Column('claim', sa.Text(), nullable=False),
    sa.Column('source_url', sa.String(length=2048), nullable=True),
    sa.Column('source_title', sa.String(length=500), nullable=True),
    sa.Column('excerpt', sa.Text(), nullable=True),
    sa.Column('published', sa.String(length=100), nullable=True),
    sa.Column('retrieved_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name=op.f('fk_company_research_facts_company_id_companies'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_company_research_facts'))
    )
    op.create_index(op.f('ix_company_research_facts_company_id'), 'company_research_facts', ['company_id'], unique=False)
    _install_trigger()


def downgrade() -> None:
    _drop_trigger()
    op.drop_index(op.f('ix_company_research_facts_company_id'), table_name='company_research_facts')
    op.drop_table('company_research_facts')
