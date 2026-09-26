"""Immutability of rows, enforced by the database itself (SQLite and PostgreSQL).

Used for qualification results (append-only: a qualification is never edited, a new one is
computed) and for the content of search criteria (a criterion is never edited: a change creates
a new version and deactivates the old one). Deleting stays possible so that cascades work.
"""

from sqlalchemy import DDL, Table, event

PG_FUNCTION = (
    "CREATE OR REPLACE FUNCTION forbid_row_update() RETURNS trigger AS $$ "
    "BEGIN RAISE EXCEPTION '% rows are immutable', TG_TABLE_NAME; END; $$ LANGUAGE plpgsql"
)


def sqlite_trigger(table: str, columns: tuple[str, ...] | None = None) -> str:
    scope = f" OF {', '.join(columns)}" if columns else ""
    return (
        f"CREATE TRIGGER IF NOT EXISTS {table}_immutable BEFORE UPDATE{scope} ON {table} "
        f"BEGIN SELECT RAISE(ABORT, '{table} rows are immutable'); END"
    )


def postgresql_trigger(table: str, columns: tuple[str, ...] | None = None) -> str:
    scope = f" OF {', '.join(columns)}" if columns else ""
    return (
        f"CREATE TRIGGER {table}_immutable BEFORE UPDATE{scope} ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION forbid_row_update()"
    )


def _install(table: Table, statement: str, dialect: str) -> None:
    ddl = DDL(statement).execute_if(dialect=dialect)  # type: ignore[no-untyped-call]
    event.listen(table, "after_create", ddl)


def make_immutable(table: Table, columns: tuple[str, ...] | None = None) -> None:
    """Reject UPDATE of the table (or of the given columns) at the database level."""
    _install(table, sqlite_trigger(table.name, columns), "sqlite")
    _install(table, PG_FUNCTION, "postgresql")
    _install(table, postgresql_trigger(table.name, columns), "postgresql")
