"""SQLAlchemy models. Importing this package registers every model on `Base.metadata`
(required by Alembic autogenerate)."""

from app.models.application_event import ApplicationEvent
from app.models.application_package import ApplicationPackage
from app.models.audit import AuditEvent, AuditEventType
from app.models.base import Base
from app.models.campaign import Campaign
from app.models.candidate import Candidate
from app.models.companies import Company, Opportunity
from app.models.company_research import CompanyResearchFact
from app.models.contact_research import ContactResearchObservation
from app.models.contacts import Contact, ContactChannel
from app.models.drafts import ApplicationDraft
from app.models.evidence import Evidence, EvidenceLink
from app.models.facts import Certification, Education, Experience, Language, Project, Skill
from app.models.ingestion import DocumentIngestion, IngestionProposal
from app.models.preferences import CandidateConstraint, CandidatePreference
from app.models.qualification import CriterionResult, Qualification, QualificationReason
from app.models.requirements import RequirementMatch, RequirementMatchFact, TargetRequirement
from app.models.search import SearchCriterion, SearchProfile
from app.models.send_batch import SendBatch, SendBatchItem
from app.models.sources import Source
from app.models.sourcing import SearchRun, SearchRunItem
from app.models.targets import Target, TargetContact

__all__ = [
    "ApplicationEvent",
    "ApplicationPackage",
    "AuditEvent",
    "ApplicationDraft",
    "AuditEventType",
    "Base",
    "Campaign",
    "Candidate",
    "CandidateConstraint",
    "CandidatePreference",
    "Certification",
    "Company",
    "CompanyResearchFact",
    "Contact",
    "ContactChannel",
    "ContactResearchObservation",
    "CriterionResult",
    "DocumentIngestion",
    "Education",
    "Evidence",
    "EvidenceLink",
    "Experience",
    "IngestionProposal",
    "Language",
    "Opportunity",
    "Qualification",
    "QualificationReason",
    "Project",
    "RequirementMatch",
    "RequirementMatchFact",
    "SearchCriterion",
    "SearchProfile",
    "SearchRun",
    "SearchRunItem",
    "SendBatch",
    "SendBatchItem",
    "Skill",
    "Source",
    "Target",
    "TargetContact",
    "TargetRequirement",
]
