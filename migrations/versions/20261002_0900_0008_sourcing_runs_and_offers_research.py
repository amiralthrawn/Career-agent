"""sourcing runs and offers research (step 3c)

Adds `search_runs` (an auditable record of one sourcing search: profile, mode, provider, structured
query, status, counters, error codes) and `search_run_items` (what became of each result: target,
qualification, or a rejection/error code). Adds `companies.offers_research` (not_started / found /
not_found, default not_started so existing rows and inserts stay valid) and `offers_research_at`.

No provider payload, header or credential has a column here.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-27 07:54:22.440806
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0008'
down_revision: str | None = '0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('search_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('profile_id', sa.Integer(), nullable=False),
    sa.Column('mode', sa.Enum('offers', 'companies', name='sourcingmode', native_enum=False, length=32), nullable=False),
    sa.Column('provider', sa.String(length=64), nullable=False),
    sa.Column('provider_kind', sa.Enum('web_search', 'offer_source', name='providerkind', native_enum=False, length=32), nullable=False),
    sa.Column('status', sa.Enum('running', 'completed', 'completed_with_errors', 'failed', name='searchrunstatus', native_enum=False, length=32), nullable=False),
    sa.Column('query', sa.JSON(), nullable=False),
    sa.Column('max_results', sa.Integer(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('results_raw', sa.Integer(), server_default='0', nullable=False),
    sa.Column('targets_created', sa.Integer(), server_default='0', nullable=False),
    sa.Column('targets_existing', sa.Integer(), server_default='0', nullable=False),
    sa.Column('companies_created', sa.Integer(), server_default='0', nullable=False),
    sa.Column('opportunities_created', sa.Integer(), server_default='0', nullable=False),
    sa.Column('qualifications_created', sa.Integer(), server_default='0', nullable=False),
    sa.Column('items_rejected', sa.Integer(), server_default='0', nullable=False),
    sa.Column('item_errors', sa.Integer(), server_default='0', nullable=False),
    sa.Column('errors', sa.JSON(), nullable=False),
    sa.Column('sources_consulted', sa.JSON(), nullable=False),
    sa.Column('candidate_id', sa.Integer(), nullable=False),
    sa.CheckConstraint("(status = 'running') = (finished_at IS NULL)", name=op.f('ck_search_runs_running_iff_unfinished')),
    sa.CheckConstraint('max_results > 0', name=op.f('ck_search_runs_max_results_positive')),
    sa.CheckConstraint('results_raw >= 0 AND targets_created >= 0 AND targets_existing >= 0 AND companies_created >= 0 AND opportunities_created >= 0 AND qualifications_created >= 0 AND items_rejected >= 0 AND item_errors >= 0', name=op.f('ck_search_runs_counters_not_negative')),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_search_runs_candidate_id_candidates'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['profile_id'], ['search_profiles.id'], name=op.f('fk_search_runs_profile_id_search_profiles'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_search_runs'))
    )
    op.create_index(op.f('ix_search_runs_candidate_id'), 'search_runs', ['candidate_id'], unique=False)
    op.create_index('ix_search_runs_profile_started', 'search_runs', ['profile_id', 'started_at'], unique=False)
    op.create_index('ix_search_runs_status', 'search_runs', ['status'], unique=False)
    op.create_table('search_run_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('outcome', sa.Enum('target_created', 'target_existing', 'rejected', 'error', name='searchrunitemoutcome', native_enum=False, length=32), nullable=False),
    sa.Column('reason', sa.Enum('missing_company_name', 'missing_offer_title', 'missing_source_url', 'invalid_source_url', 'invalid_field', 'invalid_provenance', 'offer_required', 'ingestion_refused', 'database_error', name='itemreason', native_enum=False, length=32), nullable=True),
    sa.Column('fields', sa.JSON(), nullable=False),
    sa.Column('target_id', sa.Integer(), nullable=True),
    sa.Column('qualification_id', sa.Integer(), nullable=True),
    sa.Column('source_url', sa.String(length=2048), nullable=True),
    sa.Column('excerpt', sa.String(length=500), nullable=True),
    sa.CheckConstraint("(outcome IN ('rejected', 'error')) = (reason IS NOT NULL)", name=op.f('ck_search_run_items_reason_iff_failed')),
    sa.CheckConstraint("outcome NOT IN ('rejected', 'error') OR target_id IS NULL", name=op.f('ck_search_run_items_no_target_when_failed')),
    sa.ForeignKeyConstraint(['qualification_id'], ['qualifications.id'], name=op.f('fk_search_run_items_qualification_id_qualifications'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['run_id'], ['search_runs.id'], name=op.f('fk_search_run_items_run_id_search_runs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['target_id'], ['targets.id'], name=op.f('fk_search_run_items_target_id_targets'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_search_run_items')),
    sa.UniqueConstraint('run_id', 'position', name=op.f('uq_search_run_items_run_id_position'))
    )
    op.create_index(op.f('ix_search_run_items_run_id'), 'search_run_items', ['run_id'], unique=False)
    op.add_column('companies', sa.Column('offers_research', sa.Enum('not_started', 'found', 'not_found', name='offersresearchstatus', native_enum=False, length=32), server_default='not_started', nullable=False))
    op.add_column('companies', sa.Column('offers_research_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('companies') as batch:
        batch.drop_column('offers_research_at')
        batch.drop_column('offers_research')
    op.drop_index(op.f('ix_search_run_items_run_id'), table_name='search_run_items')
    op.drop_table('search_run_items')
    op.drop_index('ix_search_runs_status', table_name='search_runs')
    op.drop_index('ix_search_runs_profile_started', table_name='search_runs')
    op.drop_index(op.f('ix_search_runs_candidate_id'), table_name='search_runs')
    op.drop_table('search_runs')
