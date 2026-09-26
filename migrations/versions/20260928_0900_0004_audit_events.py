"""audit events

Append-only audit trail. Immutability is enforced in the database itself: triggers reject
UPDATE and DELETE (and TRUNCATE on PostgreSQL). A database owner can still drop the triggers;
that is an administrative action, outside what the application can do.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQLITE_TRIGGERS = (
    "CREATE TRIGGER IF NOT EXISTS audit_events_no_update BEFORE UPDATE ON audit_events "
    "BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS audit_events_no_delete BEFORE DELETE ON audit_events "
    "BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END",
)
POSTGRESQL_STATEMENTS = (
    "CREATE OR REPLACE FUNCTION audit_events_immutable() RETURNS trigger AS $$ "
    "BEGIN RAISE EXCEPTION 'audit_events is append-only'; END; $$ LANGUAGE plpgsql",
    "CREATE TRIGGER audit_events_no_change BEFORE UPDATE OR DELETE ON audit_events "
    "FOR EACH ROW EXECUTE FUNCTION audit_events_immutable()",
    "CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events "
    "FOR EACH STATEMENT EXECUTE FUNCTION audit_events_immutable()",
)


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "event_type",
            sa.Enum(
                "app.started",
                "secret.set",
                "secret.deleted",
                "send.blocked",
                "send.dry_run",
                "send.approved",
                "send.sent",
                "send.failed",
                name="auditeventtype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("actor", sa.String(length=16), nullable=False),
        sa.Column("subject", sa.String(length=100), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index(op.f("ix_audit_events_occurred_at"), "audit_events", ["occurred_at"])
    op.create_index(op.f("ix_audit_events_event_type"), "audit_events", ["event_type"])

    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for statement in SQLITE_TRIGGERS:
            op.execute(statement)
    elif dialect == "postgresql":
        for statement in POSTGRESQL_STATEMENTS:
            op.execute(statement)


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events")
        op.execute("DROP TRIGGER IF EXISTS audit_events_no_change ON audit_events")
        op.execute("DROP FUNCTION IF EXISTS audit_events_immutable()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS audit_events_no_update")
        op.execute("DROP TRIGGER IF EXISTS audit_events_no_delete")
    op.drop_index(op.f("ix_audit_events_event_type"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_occurred_at"), table_name="audit_events")
    op.drop_table("audit_events")
