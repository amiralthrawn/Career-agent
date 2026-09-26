"""candidate brain

Revision ID: 0001
Revises: 
Create Date: 2026-09-25 23:54:35.322907
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:

    op.create_table('candidates',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('first_name', sa.String(length=100), nullable=False),
    sa.Column('last_name', sa.String(length=100), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=True),
    sa.Column('phone', sa.String(length=50), nullable=True),
    sa.Column('headline', sa.String(length=255), nullable=True),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('location', sa.String(length=255), nullable=True),
    sa.Column('availability', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_candidates'))
    )
    op.create_table('candidate_constraints',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('constraint_type', sa.Enum('geographic', 'contract', 'availability', 'salary', 'schedule', 'other', name='constrainttype', native_enum=False, length=32), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('value', sa.JSON(), nullable=True),
    sa.Column('is_hard', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('candidate_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_candidate_constraints_candidate_id_candidates'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_candidate_constraints'))
    )
    op.create_index(op.f('ix_candidate_constraints_candidate_id'), 'candidate_constraints', ['candidate_id'], unique=False)
    op.create_table('candidate_preferences',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('candidate_id', sa.Integer(), nullable=False),
    sa.Column('target_roles', sa.JSON(), nullable=False),
    sa.Column('contract_types', sa.JSON(), nullable=False),
    sa.Column('preferred_locations', sa.JSON(), nullable=False),
    sa.Column('remote_preference', sa.Enum('onsite', 'hybrid', 'remote', 'no_preference', name='remotepreference', native_enum=False, length=32), nullable=True),
    sa.Column('target_sectors', sa.JSON(), nullable=False),
    sa.Column('target_domains', sa.JSON(), nullable=False),
    sa.Column('minimum_salary', sa.Integer(), nullable=True),
    sa.Column('preferred_salary', sa.Integer(), nullable=True),
    sa.Column('salary_currency', sa.String(length=3), nullable=True),
    sa.Column('target_companies', sa.JSON(), nullable=False),
    sa.Column('company_size_preferences', sa.JSON(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_candidate_preferences_candidate_id_candidates'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_candidate_preferences')),
    sa.UniqueConstraint('candidate_id', name=op.f('uq_candidate_preferences_candidate_id'))
    )
    op.create_table('certifications',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('issuer', sa.String(length=255), nullable=True),
    sa.Column('issue_date', sa.Date(), nullable=True),
    sa.Column('expiration_date', sa.Date(), nullable=True),
    sa.Column('credential_url', sa.String(length=2048), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('candidate_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_certifications_candidate_id_candidates'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_certifications'))
    )
    op.create_index(op.f('ix_certifications_candidate_id'), 'certifications', ['candidate_id'], unique=False)
    op.create_table('education',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('institution', sa.String(length=255), nullable=False),
    sa.Column('degree', sa.String(length=255), nullable=True),
    sa.Column('field_of_study', sa.String(length=255), nullable=True),
    sa.Column('start_date', sa.Date(), nullable=True),
    sa.Column('end_date', sa.Date(), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('status', sa.Enum('planned', 'in_progress', 'completed', 'abandoned', name='educationstatus', native_enum=False, length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('candidate_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_education_candidate_id_candidates'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_education'))
    )
    op.create_index(op.f('ix_education_candidate_id'), 'education', ['candidate_id'], unique=False)
    op.create_table('evidence',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_type', sa.Enum('cv', 'github', 'portfolio', 'diploma', 'certification', 'cover_letter', 'document', 'linkedin_export', 'other', name='sourcetype', native_enum=False, length=32), nullable=False),
    sa.Column('source_name', sa.String(length=255), nullable=False),
    sa.Column('source_uri', sa.String(length=2048), nullable=True),
    sa.Column('extracted_text', sa.Text(), nullable=True),
    sa.Column('source_metadata', sa.JSON(), nullable=False),
    sa.Column('confidence', sa.Enum('low', 'medium', 'high', name='confidence', native_enum=False, length=32), nullable=False),
    sa.Column('verified', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('candidate_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_evidence_candidate_id_candidates'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_evidence'))
    )
    op.create_index(op.f('ix_evidence_candidate_id'), 'evidence', ['candidate_id'], unique=False)
    op.create_table('experiences',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('company', sa.String(length=255), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=True),
    sa.Column('end_date', sa.Date(), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('employment_type', sa.Enum('full_time', 'part_time', 'internship', 'apprenticeship', 'fixed_term', 'freelance', 'volunteer', 'other', name='employmenttype', native_enum=False, length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('candidate_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_experiences_candidate_id_candidates'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_experiences'))
    )
    op.create_index(op.f('ix_experiences_candidate_id'), 'experiences', ['candidate_id'], unique=False)
    op.create_table('languages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('language', sa.String(length=100), nullable=False),
    sa.Column('level', sa.Enum('a1', 'a2', 'b1', 'b2', 'c1', 'c2', 'native', name='languagelevel', native_enum=False, length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('candidate_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_languages_candidate_id_candidates'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_languages')),
    sa.UniqueConstraint('candidate_id', 'language', name=op.f('uq_languages_candidate_id_language'))
    )
    op.create_index(op.f('ix_languages_candidate_id'), 'languages', ['candidate_id'], unique=False)
    op.create_table('projects',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('url', sa.String(length=2048), nullable=True),
    sa.Column('repository_url', sa.String(length=2048), nullable=True),
    sa.Column('domain', sa.String(length=255), nullable=True),
    sa.Column('start_date', sa.Date(), nullable=True),
    sa.Column('end_date', sa.Date(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('candidate_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_projects_candidate_id_candidates'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_projects'))
    )
    op.create_index(op.f('ix_projects_candidate_id'), 'projects', ['candidate_id'], unique=False)
    op.create_table('skills',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('category', sa.String(length=100), nullable=True),
    sa.Column('level', sa.Enum('beginner', 'intermediate', 'advanced', 'expert', name='skilllevel', native_enum=False, length=32), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('candidate_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_skills_candidate_id_candidates'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_skills')),
    sa.UniqueConstraint('candidate_id', 'name', name=op.f('uq_skills_candidate_id_name'))
    )
    op.create_index(op.f('ix_skills_candidate_id'), 'skills', ['candidate_id'], unique=False)
    op.create_table('evidence_links',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('evidence_id', sa.Integer(), nullable=False),
    sa.Column('skill_id', sa.Integer(), nullable=True),
    sa.Column('project_id', sa.Integer(), nullable=True),
    sa.Column('experience_id', sa.Integer(), nullable=True),
    sa.Column('education_id', sa.Integer(), nullable=True),
    sa.Column('certification_id', sa.Integer(), nullable=True),
    sa.Column('language_id', sa.Integer(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint('(skill_id IS NOT NULL) + (project_id IS NOT NULL) + (experience_id IS NOT NULL) + (education_id IS NOT NULL) + (certification_id IS NOT NULL) + (language_id IS NOT NULL) = 1', name=op.f('ck_evidence_links_exactly_one_target')),
    sa.ForeignKeyConstraint(['certification_id'], ['certifications.id'], name=op.f('fk_evidence_links_certification_id_certifications'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['education_id'], ['education.id'], name=op.f('fk_evidence_links_education_id_education'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['evidence_id'], ['evidence.id'], name=op.f('fk_evidence_links_evidence_id_evidence'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['experience_id'], ['experiences.id'], name=op.f('fk_evidence_links_experience_id_experiences'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['language_id'], ['languages.id'], name=op.f('fk_evidence_links_language_id_languages'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_evidence_links_project_id_projects'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['skill_id'], ['skills.id'], name=op.f('fk_evidence_links_skill_id_skills'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_evidence_links')),
    sa.UniqueConstraint('evidence_id', 'certification_id', name=op.f('uq_evidence_links_evidence_id_certification_id')),
    sa.UniqueConstraint('evidence_id', 'education_id', name=op.f('uq_evidence_links_evidence_id_education_id')),
    sa.UniqueConstraint('evidence_id', 'experience_id', name=op.f('uq_evidence_links_evidence_id_experience_id')),
    sa.UniqueConstraint('evidence_id', 'language_id', name=op.f('uq_evidence_links_evidence_id_language_id')),
    sa.UniqueConstraint('evidence_id', 'project_id', name=op.f('uq_evidence_links_evidence_id_project_id')),
    sa.UniqueConstraint('evidence_id', 'skill_id', name=op.f('uq_evidence_links_evidence_id_skill_id'))
    )
    op.create_index(op.f('ix_evidence_links_certification_id'), 'evidence_links', ['certification_id'], unique=False)
    op.create_index(op.f('ix_evidence_links_education_id'), 'evidence_links', ['education_id'], unique=False)
    op.create_index(op.f('ix_evidence_links_evidence_id'), 'evidence_links', ['evidence_id'], unique=False)
    op.create_index(op.f('ix_evidence_links_experience_id'), 'evidence_links', ['experience_id'], unique=False)
    op.create_index(op.f('ix_evidence_links_language_id'), 'evidence_links', ['language_id'], unique=False)
    op.create_index(op.f('ix_evidence_links_project_id'), 'evidence_links', ['project_id'], unique=False)
    op.create_index(op.f('ix_evidence_links_skill_id'), 'evidence_links', ['skill_id'], unique=False)



def downgrade() -> None:

    op.drop_index(op.f('ix_evidence_links_skill_id'), table_name='evidence_links')
    op.drop_index(op.f('ix_evidence_links_project_id'), table_name='evidence_links')
    op.drop_index(op.f('ix_evidence_links_language_id'), table_name='evidence_links')
    op.drop_index(op.f('ix_evidence_links_experience_id'), table_name='evidence_links')
    op.drop_index(op.f('ix_evidence_links_evidence_id'), table_name='evidence_links')
    op.drop_index(op.f('ix_evidence_links_education_id'), table_name='evidence_links')
    op.drop_index(op.f('ix_evidence_links_certification_id'), table_name='evidence_links')
    op.drop_table('evidence_links')
    op.drop_index(op.f('ix_skills_candidate_id'), table_name='skills')
    op.drop_table('skills')
    op.drop_index(op.f('ix_projects_candidate_id'), table_name='projects')
    op.drop_table('projects')
    op.drop_index(op.f('ix_languages_candidate_id'), table_name='languages')
    op.drop_table('languages')
    op.drop_index(op.f('ix_experiences_candidate_id'), table_name='experiences')
    op.drop_table('experiences')
    op.drop_index(op.f('ix_evidence_candidate_id'), table_name='evidence')
    op.drop_table('evidence')
    op.drop_index(op.f('ix_education_candidate_id'), table_name='education')
    op.drop_table('education')
    op.drop_index(op.f('ix_certifications_candidate_id'), table_name='certifications')
    op.drop_table('certifications')
    op.drop_table('candidate_preferences')
    op.drop_index(op.f('ix_candidate_constraints_candidate_id'), table_name='candidate_constraints')
    op.drop_table('candidate_constraints')
    op.drop_table('candidates')

