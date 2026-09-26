"""cv ingestion

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-26 19:52:03.271109
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:

    op.create_table('document_ingestions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_uri', sa.String(length=2048), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('file_size', sa.Integer(), nullable=False),
    sa.Column('parser_version', sa.String(length=50), nullable=False),
    sa.Column('stats', sa.JSON(), nullable=False),
    sa.Column('evidence_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('candidate_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_document_ingestions_candidate_id_candidates'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['evidence_id'], ['evidence.id'], name=op.f('fk_document_ingestions_evidence_id_evidence'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_document_ingestions')),
    sa.UniqueConstraint('candidate_id', 'sha256', name=op.f('uq_document_ingestions_candidate_id_sha256'))
    )
    op.create_index(op.f('ix_document_ingestions_candidate_id'), 'document_ingestions', ['candidate_id'], unique=False)
    op.create_index(op.f('ix_document_ingestions_evidence_id'), 'document_ingestions', ['evidence_id'], unique=False)
    op.create_table('ingestion_proposals',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('ingestion_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.Enum('skill', 'project', 'experience', 'education', 'certification', 'language', name='evidencetargettype', native_enum=False, length=32), nullable=False),
    sa.Column('status', sa.Enum('pending', 'accepted', 'rejected', name='proposalstatus', native_enum=False, length=32), nullable=False),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('reviewed_data', sa.JSON(), nullable=True),
    sa.Column('source_excerpt', sa.Text(), nullable=False),
    sa.Column('uncertainties', sa.JSON(), nullable=False),
    sa.Column('fingerprint', sa.String(length=64), nullable=False),
    sa.Column('review_note', sa.Text(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resulting_fact_id', sa.Integer(), nullable=True),
    sa.Column('linked_existing', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('candidate_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_ingestion_proposals_candidate_id_candidates'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['ingestion_id'], ['document_ingestions.id'], name=op.f('fk_ingestion_proposals_ingestion_id_document_ingestions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ingestion_proposals')),
    sa.UniqueConstraint('ingestion_id', 'fingerprint', name=op.f('uq_ingestion_proposals_ingestion_id_fingerprint'))
    )
    op.create_index(op.f('ix_ingestion_proposals_candidate_id'), 'ingestion_proposals', ['candidate_id'], unique=False)
    op.create_index(op.f('ix_ingestion_proposals_ingestion_id'), 'ingestion_proposals', ['ingestion_id'], unique=False)
    op.create_index(op.f('ix_ingestion_proposals_status'), 'ingestion_proposals', ['status'], unique=False)



def downgrade() -> None:

    op.drop_index(op.f('ix_ingestion_proposals_status'), table_name='ingestion_proposals')
    op.drop_index(op.f('ix_ingestion_proposals_ingestion_id'), table_name='ingestion_proposals')
    op.drop_index(op.f('ix_ingestion_proposals_candidate_id'), table_name='ingestion_proposals')
    op.drop_table('ingestion_proposals')
    op.drop_index(op.f('ix_document_ingestions_evidence_id'), table_name='document_ingestions')
    op.drop_index(op.f('ix_document_ingestions_candidate_id'), table_name='document_ingestions')
    op.drop_table('document_ingestions')

