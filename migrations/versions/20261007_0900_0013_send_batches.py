"""send batches (step 10)

`send_batches`/`send_batch_items`: controlled batch sending, on top of individually-approved
`application_packages` (step 9, unchanged). No content is immutable here - a `SendBatchItem` is
operational tracking state (attempts, outcome), not generated content, closer to
`ContactResearchObservation`'s mutable-in-place convention than to the append-only drafts/
packages.

The core idempotence guarantee is `uq_send_batch_items_sent_once`: a partial UNIQUE INDEX
enforcing, at the database level, that at most one `SendBatchItem` can ever be `sent` for a given
`application_package_id`, across every batch it was ever part of - regardless of retries,
concurrent batch executions, or application bugs.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-07 09:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0013'
down_revision: str | None = '0012'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('send_batches',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('draft', 'approved', 'executing', 'completed', 'partially_failed', name='sendbatchstatus', native_enum=False, length=32), nullable=False),
    sa.Column('correlation_id', sa.String(length=32), nullable=False),
    sa.Column('idempotency_key', sa.String(length=120), nullable=True),
    sa.Column('created_by', sa.String(length=16), nullable=False),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('approved_by', sa.String(length=16), nullable=True),
    sa.Column('candidate_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_send_batches_candidate_id_candidates'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_send_batches')),
    sa.UniqueConstraint('candidate_id', 'idempotency_key', name='uq_send_batches_idempotency')
    )
    op.create_index(op.f('ix_send_batches_candidate_id'), 'send_batches', ['candidate_id'], unique=False)
    op.create_index(op.f('ix_send_batches_correlation_id'), 'send_batches', ['correlation_id'], unique=True)
    op.create_index(op.f('ix_send_batches_status'), 'send_batches', ['status'], unique=False)
    op.create_table('send_batch_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('batch_id', sa.Integer(), nullable=False),
    sa.Column('application_package_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('pending', 'sending', 'sent', 'failed', 'excluded', 'skipped_already_sent', name='sendbatchitemstatus', native_enum=False, length=32), nullable=False),
    sa.Column('failure_reason', sa.String(length=64), nullable=True),
    sa.Column('provider', sa.String(length=32), nullable=True),
    sa.Column('provider_message_id', sa.String(length=255), nullable=True),
    sa.Column('thread_id', sa.String(length=255), nullable=True),
    sa.Column('message_id', sa.String(length=255), nullable=True),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('details', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['application_package_id'], ['application_packages.id'], name=op.f('fk_send_batch_items_application_package_id_application_packages'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['batch_id'], ['send_batches.id'], name=op.f('fk_send_batch_items_batch_id_send_batches'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_send_batch_items')),
    sa.UniqueConstraint('batch_id', 'application_package_id', name='uq_send_batch_items_package')
    )
    op.create_index(op.f('ix_send_batch_items_application_package_id'), 'send_batch_items', ['application_package_id'], unique=False)
    op.create_index(op.f('ix_send_batch_items_batch_id'), 'send_batch_items', ['batch_id'], unique=False)
    op.create_index(op.f('ix_send_batch_items_status'), 'send_batch_items', ['status'], unique=False)
    op.create_index('uq_send_batch_items_sent_once', 'send_batch_items', ['application_package_id'], unique=True, postgresql_where=sa.text("status = 'sent'"), sqlite_where=sa.text("status = 'sent'"))


def downgrade() -> None:
    op.drop_index('uq_send_batch_items_sent_once', table_name='send_batch_items', postgresql_where=sa.text("status = 'sent'"), sqlite_where=sa.text("status = 'sent'"))
    op.drop_index(op.f('ix_send_batch_items_status'), table_name='send_batch_items')
    op.drop_index(op.f('ix_send_batch_items_batch_id'), table_name='send_batch_items')
    op.drop_index(op.f('ix_send_batch_items_application_package_id'), table_name='send_batch_items')
    op.drop_table('send_batch_items')
    op.drop_index(op.f('ix_send_batches_status'), table_name='send_batches')
    op.drop_index(op.f('ix_send_batches_correlation_id'), table_name='send_batches')
    op.drop_index(op.f('ix_send_batches_candidate_id'), table_name='send_batches')
    op.drop_table('send_batches')
