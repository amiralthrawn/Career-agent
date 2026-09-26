"""targets companies contacts

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-26 21:57:00.229892
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:

    op.create_table('sources',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.Enum('manual', 'import_file', 'official_api', 'public_page', name='sourcekind', native_enum=False, length=32), nullable=False),
    sa.Column('label', sa.String(length=120), nullable=False),
    sa.Column('url', sa.String(length=2048), nullable=True),
    sa.Column('reference', sa.String(length=100), nullable=True),
    sa.Column('retrieved_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("kind <> 'import_file' OR reference IS NOT NULL", name=op.f('ck_sources_import_needs_reference')),
    sa.CheckConstraint("kind NOT IN ('official_api', 'public_page') OR url IS NOT NULL OR reference IS NOT NULL", name=op.f('ck_sources_external_needs_locator')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sources'))
    )
    op.create_table('companies',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('name_key', sa.String(length=255), nullable=False),
    sa.Column('domain', sa.String(length=255), nullable=True),
    sa.Column('website_url', sa.String(length=2048), nullable=True),
    sa.Column('careers_url', sa.String(length=2048), nullable=True),
    sa.Column('siren', sa.String(length=9), nullable=True),
    sa.Column('location', sa.String(length=255), nullable=True),
    sa.Column('country_code', sa.String(length=2), nullable=True),
    sa.Column('sector', sa.String(length=255), nullable=True),
    sa.Column('contact_research', sa.Enum('not_started', 'found', 'not_found', name='contactresearchstatus', native_enum=False, length=32), nullable=False),
    sa.Column('contact_research_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], name=op.f('fk_companies_source_id_sources')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_companies')),
    sa.UniqueConstraint('domain', name=op.f('uq_companies_domain')),
    sa.UniqueConstraint('siren', name=op.f('uq_companies_siren'))
    )
    op.create_index(op.f('ix_companies_name_key'), 'companies', ['name_key'], unique=False)
    op.create_index(op.f('ix_companies_source_id'), 'companies', ['source_id'], unique=False)
    op.create_table('contacts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('company_id', sa.Integer(), nullable=False),
    sa.Column('full_name', sa.String(length=255), nullable=True),
    sa.Column('name_key', sa.String(length=255), nullable=True),
    sa.Column('is_generic', sa.Boolean(), nullable=False),
    sa.Column('role_title', sa.String(length=255), nullable=True),
    sa.Column('role_category', sa.Enum('hr', 'recruiter', 'tech', 'manager', 'founder', 'other', 'unknown', name='rolecategory', native_enum=False, length=32), nullable=False),
    sa.Column('status', sa.Enum('found', 'uncertain', name='infostatus', native_enum=False, length=32), nullable=False),
    sa.Column('verified', sa.Boolean(), nullable=False),
    sa.Column('do_not_contact', sa.Boolean(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint('full_name IS NOT NULL OR is_generic', name=op.f('ck_contacts_named_or_generic')),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name=op.f('fk_contacts_company_id_companies'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], name=op.f('fk_contacts_source_id_sources')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_contacts')),
    sa.UniqueConstraint('id', 'company_id', name=op.f('uq_contacts_id_company_id'))
    )
    op.create_index(op.f('ix_contacts_company_id'), 'contacts', ['company_id'], unique=False)
    op.create_index(op.f('ix_contacts_source_id'), 'contacts', ['source_id'], unique=False)
    op.create_index('uq_contacts_company_name_key', 'contacts', ['company_id', 'name_key'], unique=True, postgresql_where=sa.text('name_key IS NOT NULL'), sqlite_where=sa.text('name_key IS NOT NULL'))
    op.create_table('opportunities',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('company_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('title_key', sa.String(length=255), nullable=False),
    sa.Column('url', sa.String(length=2048), nullable=True),
    sa.Column('external_id', sa.String(length=100), nullable=True),
    sa.Column('contract_type', sa.Enum('full_time', 'part_time', 'internship', 'apprenticeship', 'fixed_term', 'freelance', 'volunteer', 'other', name='employmenttype', native_enum=False, length=32), nullable=True),
    sa.Column('location', sa.String(length=255), nullable=True),
    sa.Column('posted_on', sa.String(length=10), nullable=True),
    sa.Column('description_text', sa.Text(), nullable=True),
    sa.Column('status', sa.Enum('open', 'closed', 'unknown', name='opportunitystatus', native_enum=False, length=32), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name=op.f('fk_opportunities_company_id_companies'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], name=op.f('fk_opportunities_source_id_sources')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_opportunities')),
    sa.UniqueConstraint('id', 'company_id', name=op.f('uq_opportunities_id_company_id'))
    )
    op.create_index(op.f('ix_opportunities_company_id'), 'opportunities', ['company_id'], unique=False)
    op.create_index(op.f('ix_opportunities_source_id'), 'opportunities', ['source_id'], unique=False)
    op.create_index(op.f('ix_opportunities_title_key'), 'opportunities', ['title_key'], unique=False)
    op.create_index('uq_opportunities_company_external_id', 'opportunities', ['company_id', 'external_id'], unique=True, postgresql_where=sa.text('external_id IS NOT NULL'), sqlite_where=sa.text('external_id IS NOT NULL'))
    op.create_index('uq_opportunities_company_url', 'opportunities', ['company_id', 'url'], unique=True, postgresql_where=sa.text('url IS NOT NULL'), sqlite_where=sa.text('url IS NOT NULL'))
    op.create_table('contact_channels',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('contact_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.Enum('email', 'phone', 'linkedin_url', 'contact_form_url', name='channelkind', native_enum=False, length=32), nullable=False),
    sa.Column('value', sa.String(length=2048), nullable=False),
    sa.Column('status', sa.Enum('found', 'uncertain', name='infostatus', native_enum=False, length=32), nullable=False),
    sa.Column('verified', sa.Boolean(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], name=op.f('fk_contact_channels_contact_id_contacts'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], name=op.f('fk_contact_channels_source_id_sources')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_contact_channels')),
    sa.UniqueConstraint('contact_id', 'kind', 'value', name=op.f('uq_contact_channels_contact_id_kind_value'))
    )
    op.create_index(op.f('ix_contact_channels_contact_id'), 'contact_channels', ['contact_id'], unique=False)
    op.create_index(op.f('ix_contact_channels_source_id'), 'contact_channels', ['source_id'], unique=False)
    op.create_table('targets',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('company_id', sa.Integer(), nullable=False),
    sa.Column('opportunity_id', sa.Integer(), nullable=True),
    sa.Column('contract_type', sa.Enum('full_time', 'part_time', 'internship', 'apprenticeship', 'fixed_term', 'freelance', 'volunteer', 'other', name='employmenttype', native_enum=False, length=32), nullable=True),
    sa.Column('status', sa.Enum('new', 'shortlisted', 'dismissed', name='targetstatus', native_enum=False, length=32), nullable=False),
    sa.Column('dismissed_reason', sa.String(length=500), nullable=True),
    sa.Column('relevance_note', sa.Text(), nullable=True),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('candidate_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_targets_candidate_id_candidates'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name=op.f('fk_targets_company_id_companies')),
    sa.ForeignKeyConstraint(['opportunity_id', 'company_id'], ['opportunities.id', 'opportunities.company_id'], name='fk_targets_opportunity_company'),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], name=op.f('fk_targets_source_id_sources')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_targets')),
    sa.UniqueConstraint('id', 'company_id', name=op.f('uq_targets_id_company_id'))
    )
    op.create_index(op.f('ix_targets_candidate_id'), 'targets', ['candidate_id'], unique=False)
    op.create_index(op.f('ix_targets_company_id'), 'targets', ['company_id'], unique=False)
    op.create_index(op.f('ix_targets_opportunity_id'), 'targets', ['opportunity_id'], unique=False)
    op.create_index(op.f('ix_targets_source_id'), 'targets', ['source_id'], unique=False)
    op.create_index('uq_targets_offer', 'targets', ['candidate_id', 'opportunity_id'], unique=True, postgresql_where=sa.text('opportunity_id IS NOT NULL'), sqlite_where=sa.text('opportunity_id IS NOT NULL'))
    op.create_index('uq_targets_spontaneous', 'targets', ['candidate_id', 'company_id'], unique=True, postgresql_where=sa.text('opportunity_id IS NULL'), sqlite_where=sa.text('opportunity_id IS NULL'))
    op.create_table('target_contacts',
    sa.Column('target_id', sa.Integer(), nullable=False),
    sa.Column('contact_id', sa.Integer(), nullable=False),
    sa.Column('company_id', sa.Integer(), nullable=False),
    sa.Column('is_primary', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['contact_id', 'company_id'], ['contacts.id', 'contacts.company_id'], name='fk_target_contacts_contact_company', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['target_id', 'company_id'], ['targets.id', 'targets.company_id'], name='fk_target_contacts_target_company', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('target_id', 'contact_id', name=op.f('pk_target_contacts'))
    )
    op.create_index('uq_target_contacts_primary', 'target_contacts', ['target_id'], unique=True, postgresql_where=sa.text('is_primary'), sqlite_where=sa.text('is_primary'))



def downgrade() -> None:

    op.drop_index('uq_target_contacts_primary', table_name='target_contacts', postgresql_where=sa.text('is_primary'), sqlite_where=sa.text('is_primary'))
    op.drop_table('target_contacts')
    op.drop_index('uq_targets_spontaneous', table_name='targets', postgresql_where=sa.text('opportunity_id IS NULL'), sqlite_where=sa.text('opportunity_id IS NULL'))
    op.drop_index('uq_targets_offer', table_name='targets', postgresql_where=sa.text('opportunity_id IS NOT NULL'), sqlite_where=sa.text('opportunity_id IS NOT NULL'))
    op.drop_index(op.f('ix_targets_source_id'), table_name='targets')
    op.drop_index(op.f('ix_targets_opportunity_id'), table_name='targets')
    op.drop_index(op.f('ix_targets_company_id'), table_name='targets')
    op.drop_index(op.f('ix_targets_candidate_id'), table_name='targets')
    op.drop_table('targets')
    op.drop_index(op.f('ix_contact_channels_source_id'), table_name='contact_channels')
    op.drop_index(op.f('ix_contact_channels_contact_id'), table_name='contact_channels')
    op.drop_table('contact_channels')
    op.drop_index('uq_opportunities_company_url', table_name='opportunities', postgresql_where=sa.text('url IS NOT NULL'), sqlite_where=sa.text('url IS NOT NULL'))
    op.drop_index('uq_opportunities_company_external_id', table_name='opportunities', postgresql_where=sa.text('external_id IS NOT NULL'), sqlite_where=sa.text('external_id IS NOT NULL'))
    op.drop_index(op.f('ix_opportunities_title_key'), table_name='opportunities')
    op.drop_index(op.f('ix_opportunities_source_id'), table_name='opportunities')
    op.drop_index(op.f('ix_opportunities_company_id'), table_name='opportunities')
    op.drop_table('opportunities')
    op.drop_index('uq_contacts_company_name_key', table_name='contacts', postgresql_where=sa.text('name_key IS NOT NULL'), sqlite_where=sa.text('name_key IS NOT NULL'))
    op.drop_index(op.f('ix_contacts_source_id'), table_name='contacts')
    op.drop_index(op.f('ix_contacts_company_id'), table_name='contacts')
    op.drop_table('contacts')
    op.drop_index(op.f('ix_companies_source_id'), table_name='companies')
    op.drop_index(op.f('ix_companies_name_key'), table_name='companies')
    op.drop_table('companies')
    op.drop_table('sources')

