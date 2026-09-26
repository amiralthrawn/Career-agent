"""requirements and requirement matches (step 3b)

Requirements of a target (with their exact excerpt and source) and, for each qualification, what the
Candidate Brain establishes about each requirement.

Everything is append-only, enforced by database triggers (UPDATE is rejected; DELETE stays possible
so cascades work). The only exception is a requirement's `active` / `deactivated_at` /
`superseded_by_id`: a requirement is never edited, it is superseded by a new version.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-27 00:09:28.087022
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IMMUTABLE_TABLES = ("requirement_matches", "requirement_match_facts")
REQUIREMENT_CONTENT_COLUMNS = (
    "candidate_id",
    "target_id",
    "kind",
    "key",
    "label",
    "importance",
    "origin",
    "source_field",
    "source_id",
    "source_hash",
    "excerpt",
    "value",
    "qualifier",
    "extractor_version",
    "created_at",
)
PG_FUNCTION = (
    "CREATE OR REPLACE FUNCTION forbid_row_update() RETURNS trigger AS $$ "
    "BEGIN RAISE EXCEPTION '% rows are immutable', TG_TABLE_NAME; END; $$ LANGUAGE plpgsql"
)


def _triggers() -> list[tuple[str, tuple[str, ...] | None]]:
    return [(table, None) for table in IMMUTABLE_TABLES] + [
        ("target_requirements", REQUIREMENT_CONTENT_COLUMNS)
    ]


def _install_triggers() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(PG_FUNCTION)  # shared with 0006; CREATE OR REPLACE is idempotent
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
    # forbid_row_update() is still used by the 0006 triggers: it is NOT dropped here.


def upgrade() -> None:
    op.create_table('target_requirements',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('target_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.Enum('skill', 'experience', name='requirementkind', native_enum=False, length=32), nullable=False),
    sa.Column('key', sa.String(length=120), nullable=False),
    sa.Column('label', sa.String(length=255), nullable=False),
    sa.Column('importance', sa.Enum('required', 'nice_to_have', 'unspecified', name='requirementimportance', native_enum=False, length=32), nullable=False),
    sa.Column('origin', sa.Enum('offer_text', 'manual', 'company_signal', name='requirementorigin', native_enum=False, length=32), nullable=False),
    sa.Column('source_field', sa.String(length=64), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('source_hash', sa.String(length=64), nullable=False),
    sa.Column('excerpt', sa.Text(), nullable=False),
    sa.Column('value', sa.String(length=64), nullable=True),
    sa.Column('qualifier', sa.String(length=255), nullable=True),
    sa.Column('extractor_version', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('deactivated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('superseded_by_id', sa.Integer(), nullable=True),
    sa.Column('candidate_id', sa.Integer(), nullable=False),
    sa.CheckConstraint('length(excerpt) > 0', name=op.f('ck_target_requirements_excerpt_not_empty')),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_target_requirements_candidate_id_candidates'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], name=op.f('fk_target_requirements_source_id_sources')),
    sa.ForeignKeyConstraint(['superseded_by_id'], ['target_requirements.id'], name=op.f('fk_target_requirements_superseded_by_id_target_requirements')),
    sa.ForeignKeyConstraint(['target_id'], ['targets.id'], name=op.f('fk_target_requirements_target_id_targets'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_target_requirements'))
    )
    op.create_index(op.f('ix_target_requirements_candidate_id'), 'target_requirements', ['candidate_id'], unique=False)
    op.create_index(op.f('ix_target_requirements_source_id'), 'target_requirements', ['source_id'], unique=False)
    op.create_index(op.f('ix_target_requirements_target_id'), 'target_requirements', ['target_id'], unique=False)
    op.create_index('uq_target_requirements_active', 'target_requirements', ['target_id', 'kind', 'key'], unique=True, postgresql_where=sa.text('active'), sqlite_where=sa.text('active'))
    op.create_table('requirement_matches',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('qualification_id', sa.Integer(), nullable=False),
    sa.Column('requirement_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('covered', 'weak', 'gap', 'unmeasurable', name='matchstatus', native_enum=False, length=32), nullable=False),
    sa.Column('note', sa.Enum('skill_established', 'skill_implied', 'skill_unconfirmed', 'no_skill_in_brain', 'mentioned_by_project_only', 'experience_established', 'experience_insufficient', 'experience_none_in_brain', 'experience_unconfirmed', 'experience_dates_insufficient', 'experience_scope_not_measurable', 'experience_value_unreadable', name='matchnote', native_enum=False, length=32), nullable=False),
    sa.ForeignKeyConstraint(['qualification_id'], ['qualifications.id'], name=op.f('fk_requirement_matches_qualification_id_qualifications'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['requirement_id'], ['target_requirements.id'], name=op.f('fk_requirement_matches_requirement_id_target_requirements'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_requirement_matches')),
    sa.UniqueConstraint('qualification_id', 'requirement_id', name=op.f('uq_requirement_matches_qualification_id_requirement_id'))
    )
    op.create_index(op.f('ix_requirement_matches_qualification_id'), 'requirement_matches', ['qualification_id'], unique=False)
    op.create_index(op.f('ix_requirement_matches_requirement_id'), 'requirement_matches', ['requirement_id'], unique=False)
    op.create_table('requirement_match_facts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('match_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.Enum('establishes', 'supports', 'mentions', name='matchfactrole', native_enum=False, length=32), nullable=False),
    sa.Column('state', sa.Enum('unknown', 'uncertain', 'known', 'verified', name='informationstate', native_enum=False, length=32), nullable=False),
    sa.Column('skill_id', sa.Integer(), nullable=True),
    sa.Column('project_id', sa.Integer(), nullable=True),
    sa.Column('experience_id', sa.Integer(), nullable=True),
    sa.CheckConstraint('(skill_id IS NOT NULL) + (project_id IS NOT NULL) + (experience_id IS NOT NULL) = 1', name=op.f('ck_requirement_match_facts_exactly_one_fact')),
    sa.ForeignKeyConstraint(['experience_id'], ['experiences.id'], name=op.f('fk_requirement_match_facts_experience_id_experiences'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['match_id'], ['requirement_matches.id'], name=op.f('fk_requirement_match_facts_match_id_requirement_matches'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_requirement_match_facts_project_id_projects'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['skill_id'], ['skills.id'], name=op.f('fk_requirement_match_facts_skill_id_skills'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_requirement_match_facts'))
    )
    op.create_index(op.f('ix_requirement_match_facts_match_id'), 'requirement_match_facts', ['match_id'], unique=False)
    _install_triggers()


def downgrade() -> None:
    _drop_triggers()
    op.drop_index(op.f('ix_requirement_match_facts_match_id'), table_name='requirement_match_facts')
    op.drop_table('requirement_match_facts')
    op.drop_index(op.f('ix_requirement_matches_requirement_id'), table_name='requirement_matches')
    op.drop_index(op.f('ix_requirement_matches_qualification_id'), table_name='requirement_matches')
    op.drop_table('requirement_matches')
    op.drop_index('uq_target_requirements_active', table_name='target_requirements', postgresql_where=sa.text('active'), sqlite_where=sa.text('active'))
    op.drop_index(op.f('ix_target_requirements_target_id'), table_name='target_requirements')
    op.drop_index(op.f('ix_target_requirements_source_id'), table_name='target_requirements')
    op.drop_index(op.f('ix_target_requirements_candidate_id'), table_name='target_requirements')
    op.drop_table('target_requirements')
