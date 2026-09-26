"""search profiles and qualifications

Also adds `opportunities.remote_mode` (onsite / hybrid / remote, NULL = not stated): the data that
the `remote_mode` criterion dimension reads.

Qualification rows are immutable and search criteria keep their content forever: both are
enforced by database triggers (UPDATE is rejected; DELETE stays possible so cascades work).

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-26 23:06:59.199364
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IMMUTABLE_TABLES = ("qualifications", "criterion_results", "qualification_reasons")
CRITERION_CONTENT_COLUMNS = (
    "profile_id",
    "dimension",
    "operator",
    "match_values",
    "level",
    "note",
    "origin",
    "origin_ref",
)
PG_FUNCTION = (
    "CREATE OR REPLACE FUNCTION forbid_row_update() RETURNS trigger AS $$ "
    "BEGIN RAISE EXCEPTION '% rows are immutable', TG_TABLE_NAME; END; $$ LANGUAGE plpgsql"
)


def _triggers() -> list[tuple[str, tuple[str, ...] | None]]:
    return [(table, None) for table in IMMUTABLE_TABLES] + [
        ("search_criteria", CRITERION_CONTENT_COLUMNS)
    ]


def _install_triggers() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(PG_FUNCTION)
    for table, columns in _triggers():
        scope = f" OF {', '.join(columns)}" if columns else ""
        if dialect == "sqlite":
            op.execute(
                f"CREATE TRIGGER IF NOT EXISTS {table}_immutable BEFORE UPDATE{scope} ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{table} rows are immutable'); END"
            )
        elif dialect == "postgresql":
            op.execute(
                f"CREATE TRIGGER {table}_immutable BEFORE UPDATE{scope} ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION forbid_row_update()"
            )


def _drop_triggers() -> None:
    dialect = op.get_bind().dialect.name
    for table, _ in _triggers():
        if dialect == "sqlite":
            op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable")
        elif dialect == "postgresql":
            op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
    if dialect == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS forbid_row_update()")


def upgrade() -> None:
    op.add_column(
        "opportunities",
        sa.Column(
            "remote_mode",
            sa.Enum("onsite", "hybrid", "remote", name="remotemode", native_enum=False, length=32),
            nullable=True,
        ),
    )

    op.create_table('search_profiles',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('description', sa.String(length=500), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('origin', sa.Enum('manual', 'from_preferences', name='profileorigin', native_enum=False, length=32), nullable=False),
    sa.Column('unmapped', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('candidate_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_search_profiles_candidate_id_candidates'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_search_profiles')),
    sa.UniqueConstraint('candidate_id', 'name', name=op.f('uq_search_profiles_candidate_id_name'))
    )
    op.create_index(op.f('ix_search_profiles_candidate_id'), 'search_profiles', ['candidate_id'], unique=False)
    op.create_index('uq_search_profiles_active', 'search_profiles', ['candidate_id'], unique=True, postgresql_where=sa.text('is_active'), sqlite_where=sa.text('is_active'))
    op.create_table('search_criteria',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('profile_id', sa.Integer(), nullable=False),
    sa.Column('dimension', sa.Enum('contract_type', 'country', 'company', 'location', 'sector', 'role', 'keyword', name='criteriondimension', native_enum=False, length=32), nullable=False),
    sa.Column('operator', sa.Enum('any_of', 'none_of', name='criterionoperator', native_enum=False, length=32), nullable=False),
    sa.Column('match_values', sa.JSON(), nullable=False),
    sa.Column('level', sa.Enum('required', 'preferred', 'flexible', name='criterionlevel', native_enum=False, length=32), nullable=False),
    sa.Column('note', sa.String(length=255), nullable=True),
    sa.Column('origin', sa.Enum('manual', 'preference', 'constraint', name='criterionorigin', native_enum=False, length=32), nullable=False),
    sa.Column('origin_ref', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('deactivated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('superseded_by_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['profile_id'], ['search_profiles.id'], name=op.f('fk_search_criteria_profile_id_search_profiles'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['superseded_by_id'], ['search_criteria.id'], name=op.f('fk_search_criteria_superseded_by_id_search_criteria')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_search_criteria'))
    )
    op.create_index(op.f('ix_search_criteria_profile_id'), 'search_criteria', ['profile_id'], unique=False)
    op.create_table('qualifications',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('target_id', sa.Integer(), nullable=False),
    sa.Column('profile_id', sa.Integer(), nullable=False),
    sa.Column('method', sa.Enum('deterministic', 'ai', 'human', name='qualificationmethod', native_enum=False, length=32), nullable=False),
    sa.Column('inputs_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('status', sa.Enum('excluded', 'needs_information', 'candidate', name='qualificationstatus', native_enum=False, length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('candidate_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_qualifications_candidate_id_candidates'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['profile_id'], ['search_profiles.id'], name=op.f('fk_qualifications_profile_id_search_profiles'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['target_id'], ['targets.id'], name=op.f('fk_qualifications_target_id_targets'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_qualifications'))
    )
    op.create_index(op.f('ix_qualifications_candidate_id'), 'qualifications', ['candidate_id'], unique=False)
    op.create_index('ix_qualifications_target_profile', 'qualifications', ['target_id', 'profile_id', 'id'], unique=False)
    op.create_table('criterion_results',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('qualification_id', sa.Integer(), nullable=False),
    sa.Column('criterion_id', sa.Integer(), nullable=False),
    sa.Column('outcome', sa.Enum('satisfied', 'incompatible', 'not_matched', 'unknown', name='criterionoutcome', native_enum=False, length=32), nullable=False),
    sa.Column('code', sa.Enum('match', 'excluded_term_absent', 'excluded_value_present', 'not_in_allowed_set', 'no_match_found', 'data_missing', 'no_offer', 'description_not_provided', name='evaluationcode', native_enum=False, length=32), nullable=False),
    sa.Column('observed', sa.String(length=255), nullable=True),
    sa.ForeignKeyConstraint(['criterion_id'], ['search_criteria.id'], name=op.f('fk_criterion_results_criterion_id_search_criteria')),
    sa.ForeignKeyConstraint(['qualification_id'], ['qualifications.id'], name=op.f('fk_criterion_results_qualification_id_qualifications'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_criterion_results')),
    sa.UniqueConstraint('qualification_id', 'criterion_id', name=op.f('uq_criterion_results_qualification_id_criterion_id'))
    )
    op.create_index(op.f('ix_criterion_results_criterion_id'), 'criterion_results', ['criterion_id'], unique=False)
    op.create_index(op.f('ix_criterion_results_qualification_id'), 'criterion_results', ['qualification_id'], unique=False)
    op.create_table('qualification_reasons',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('qualification_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('code', sa.Enum('required_satisfied', 'excluded_by_required', 'open_question_required', 'preferred_satisfied', 'flexible_matched', 'no_offer_published', name='reasoncode', native_enum=False, length=32), nullable=False),
    sa.Column('criterion_result_id', sa.Integer(), nullable=True),
    sa.Column('origin', sa.Enum('deterministic', 'ai', 'human', name='qualificationmethod', native_enum=False, length=32), nullable=False),
    sa.ForeignKeyConstraint(['criterion_result_id'], ['criterion_results.id'], name=op.f('fk_qualification_reasons_criterion_result_id_criterion_results')),
    sa.ForeignKeyConstraint(['qualification_id'], ['qualifications.id'], name=op.f('fk_qualification_reasons_qualification_id_qualifications'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_qualification_reasons'))
    )
    op.create_index(op.f('ix_qualification_reasons_qualification_id'), 'qualification_reasons', ['qualification_id'], unique=False)
    _install_triggers()


def downgrade() -> None:
    _drop_triggers()

    op.drop_index(op.f('ix_qualification_reasons_qualification_id'), table_name='qualification_reasons')
    op.drop_table('qualification_reasons')
    op.drop_index(op.f('ix_criterion_results_qualification_id'), table_name='criterion_results')
    op.drop_index(op.f('ix_criterion_results_criterion_id'), table_name='criterion_results')
    op.drop_table('criterion_results')
    op.drop_index('ix_qualifications_target_profile', table_name='qualifications')
    op.drop_index(op.f('ix_qualifications_candidate_id'), table_name='qualifications')
    op.drop_table('qualifications')
    op.drop_index(op.f('ix_search_criteria_profile_id'), table_name='search_criteria')
    op.drop_table('search_criteria')
    op.drop_index('uq_search_profiles_active', table_name='search_profiles', postgresql_where=sa.text('is_active'), sqlite_where=sa.text('is_active'))
    op.drop_index(op.f('ix_search_profiles_candidate_id'), table_name='search_profiles')
    op.drop_table('search_profiles')
    with op.batch_alter_table("opportunities") as batch:
        batch.drop_column("remote_mode")
