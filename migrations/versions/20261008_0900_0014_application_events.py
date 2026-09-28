"""application events (step 11)

`application_events` is the append-only, immutable lifecycle history of one candidature:
prepared/approved/sent (recorded automatically by the existing step 9/10 services, `origin=
system`, always confirmed) and everything after - response, interview, rejection, offer,
withdrawal, follow-up - entered manually or (later) detected from Gmail, always `UNCERTAIN`
(`InfoStatus`, reused) until a human confirms it. A correction is a NEW row referencing the one
it corrects (`corrected_event_id`) - the original is never updated or deleted, so the full
history, including a mistake, stays reconstructible. Fully immutable (append-only, as
`company_research_facts` in 0010): every column is frozen by a database trigger.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-08 09:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0014'
down_revision: str | None = '0013'
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
            "CREATE TRIGGER IF NOT EXISTS application_events_immutable BEFORE UPDATE "
            "ON application_events "
            "BEGIN SELECT RAISE(ABORT, 'application_events rows are immutable'); END"
        )
    elif dialect == "postgresql":
        op.execute(PG_FUNCTION)  # shared with earlier triggers; CREATE OR REPLACE is idempotent
        op.execute(
            "CREATE TRIGGER application_events_immutable BEFORE UPDATE "
            "ON application_events FOR EACH ROW EXECUTE FUNCTION forbid_row_update()"
        )


def _drop_trigger() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS application_events_immutable")
    elif dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS application_events_immutable ON application_events")
    # forbid_row_update() is still used by earlier triggers: it is NOT dropped here.


def upgrade() -> None:
    op.create_table('application_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('application_package_id', sa.Integer(), nullable=False),
    sa.Column('event_type', sa.Enum('prepared', 'approved', 'sent', 'acknowledged', 'response_received', 'interview_proposed', 'interview_scheduled', 'interview_completed', 'rejected', 'offer_received', 'withdrawn', 'follow_up_sent', name='applicationeventtype', native_enum=False, length=32), nullable=False),
    sa.Column('origin', sa.Enum('system', 'manual', 'gmail', name='applicationeventorigin', native_enum=False, length=32), nullable=False),
    sa.Column('status', sa.Enum('found', 'uncertain', name='infostatus', native_enum=False, length=32), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('reference', sa.String(length=255), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('corrected_event_id', sa.Integer(), nullable=True),
    sa.Column('details', sa.JSON(), nullable=False),
    sa.Column('actor', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('candidate_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['application_package_id'], ['application_packages.id'], name=op.f('fk_application_events_application_package_id_application_packages'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_application_events_candidate_id_candidates'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['corrected_event_id'], ['application_events.id'], name=op.f('fk_application_events_corrected_event_id_application_events')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_application_events'))
    )
    op.create_index(op.f('ix_application_events_application_package_id'), 'application_events', ['application_package_id'], unique=False)
    op.create_index(op.f('ix_application_events_candidate_id'), 'application_events', ['candidate_id'], unique=False)
    op.create_index(op.f('ix_application_events_event_type'), 'application_events', ['event_type'], unique=False)
    op.create_index('uq_application_events_reference', 'application_events', ['application_package_id', 'event_type', 'reference'], unique=True, postgresql_where=sa.text('reference IS NOT NULL AND corrected_event_id IS NULL'), sqlite_where=sa.text('reference IS NOT NULL AND corrected_event_id IS NULL'))
    _install_trigger()


def downgrade() -> None:
    _drop_trigger()
    op.drop_index('uq_application_events_reference', table_name='application_events', postgresql_where=sa.text('reference IS NOT NULL AND corrected_event_id IS NULL'), sqlite_where=sa.text('reference IS NOT NULL AND corrected_event_id IS NULL'))
    op.drop_index(op.f('ix_application_events_event_type'), table_name='application_events')
    op.drop_index(op.f('ix_application_events_candidate_id'), table_name='application_events')
    op.drop_index(op.f('ix_application_events_application_package_id'), table_name='application_events')
    op.drop_table('application_events')
