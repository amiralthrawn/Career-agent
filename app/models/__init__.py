"""SQLAlchemy models. Importing this package registers every model on `Base.metadata`
(required by Alembic autogenerate)."""

from app.models.audit import AuditEvent, AuditEventType
from app.models.base import Base
from app.models.candidate import Candidate
from app.models.companies import Company, Opportunity
from app.models.contacts import Contact, ContactChannel
from app.models.evidence import Evidence, EvidenceLink
from app.models.facts import Certification, Education, Experience, Language, Project, Skill
from app.models.ingestion import DocumentIngestion, IngestionProposal
from app.models.preferences import CandidateConstraint, CandidatePreference
from app.models.sources import Source
from app.models.targets import Target, TargetContact

__all__ = [
    "AuditEvent",
    "AuditEventType",
    "Base",
    "Candidate",
    "CandidateConstraint",
    "CandidatePreference",
    "Certification",
    "Company",
    "Contact",
    "ContactChannel",
    "DocumentIngestion",
    "Education",
    "Evidence",
    "EvidenceLink",
    "Experience",
    "IngestionProposal",
    "Language",
    "Opportunity",
    "Project",
    "Skill",
    "Source",
    "Target",
    "TargetContact",
]
