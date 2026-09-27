"""contact research observations (step 8)

`contact_research_observations` holds a professional contact REPORTED by a `ResearchProvider`
(Perplexity), scoped to one target and one company, awaiting human review. Unlike
`company_research_facts` (0010, append-only, auto-accepted), this table is mutable in place -
same convention as `ingestion_proposals` (0002): a row moves PENDING -> ACCEPTED/REJECTED, and
only then may carry a `resulting_contact_id`. No immutability trigger.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-05 09:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0011'
down_revision: str | None = '0010'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('contact_research_observations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('target_id', sa.Integer(), nullable=False),
    sa.Column('company_id', sa.Integer(), nullable=False),
    sa.Column('requested_role_category', sa.Enum('hr', 'recruiter', 'tech', 'manager', 'founder', 'other', 'unknown', name='rolecategory', native_enum=False, length=32), nullable=False),
    sa.Column('status', sa.Enum('pending', 'accepted', 'rejected', name='proposalstatus', native_enum=False, length=32), nullable=False),
    sa.Column('claim', sa.Text(), nullable=False),
    sa.Column('source_url', sa.String(length=2048), nullable=False),
    sa.Column('source_title', sa.String(length=500), nullable=True),
    sa.Column('excerpt', sa.Text(), nullable=True),
    sa.Column('published', sa.String(length=50), nullable=True),
    sa.Column('retrieved_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('fingerprint', sa.String(length=64), nullable=False),
    sa.Column('reviewed_data', sa.JSON(), nullable=True),
    sa.Column('review_note', sa.Text(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resulting_contact_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['resulting_contact_id', 'company_id'], ['contacts.id', 'contacts.company_id'], name='fk_contact_research_contact_company'),
    sa.ForeignKeyConstraint(['target_id', 'company_id'], ['targets.id', 'targets.company_id'], name='fk_contact_research_target_company', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_contact_research_observations')),
    sa.UniqueConstraint('target_id', 'fingerprint', name=op.f('uq_contact_research_observations_target_id_fingerprint'))
    )
    op.create_index(op.f('ix_contact_research_observations_company_id'), 'contact_research_observations', ['company_id'], unique=False)
    op.create_index(op.f('ix_contact_research_observations_resulting_contact_id'), 'contact_research_observations', ['resulting_contact_id'], unique=False)
    op.create_index(op.f('ix_contact_research_observations_status'), 'contact_research_observations', ['status'], unique=False)
    op.create_index(op.f('ix_contact_research_observations_target_id'), 'contact_research_observations', ['target_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_contact_research_observations_target_id'), table_name='contact_research_observations')
    op.drop_index(op.f('ix_contact_research_observations_status'), table_name='contact_research_observations')
    op.drop_index(op.f('ix_contact_research_observations_resulting_contact_id'), table_name='contact_research_observations')
    op.drop_index(op.f('ix_contact_research_observations_company_id'), table_name='contact_research_observations')
    op.drop_table('contact_research_observations')
