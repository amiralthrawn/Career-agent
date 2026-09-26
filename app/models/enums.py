"""Controlled vocabularies shared by models and schemas.

Stored as plain VARCHAR (not native PostgreSQL enums) so that adding a value never
requires an `ALTER TYPE` migration.
"""

from enum import StrEnum

from sqlalchemy import Enum


def enum_column[E: StrEnum](enum_cls: type[E]) -> Enum:
    """SQLAlchemy column type storing the enum *values* in a VARCHAR."""
    return Enum(
        enum_cls,
        native_enum=False,
        length=32,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


class SourceType(StrEnum):
    CV = "cv"
    GITHUB = "github"
    PORTFOLIO = "portfolio"
    DIPLOMA = "diploma"
    CERTIFICATION = "certification"
    COVER_LETTER = "cover_letter"
    DOCUMENT = "document"
    LINKEDIN_EXPORT = "linkedin_export"
    OTHER = "other"


class Confidence(StrEnum):
    """How much the candidate/system trusts an evidence. Not an AI score."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class InformationState(StrEnum):
    """Derived (never stored) state of a fact, computed from its evidence.

    Every state describes a fact that EXISTS in the Brain. There is deliberately no "absent",
    "false" or "negative" member: absence of information is not negative information, and a
    missing fact (no row) is simply not known.
    """

    UNKNOWN = "unknown"  # fact exists, no usable evidence (NOT "false")
    UNCERTAIN = "uncertain"  # only low-confidence, unverified evidence
    KNOWN = "known"  # medium/high-confidence evidence, not verified
    VERIFIED = "verified"  # at least one verified evidence


class EvidenceTargetType(StrEnum):
    SKILL = "skill"
    PROJECT = "project"
    EXPERIENCE = "experience"
    EDUCATION = "education"
    CERTIFICATION = "certification"
    LANGUAGE = "language"


class ProposalStatus(StrEnum):
    """Lifecycle of an ingestion proposal. Only a human decision leaves `pending`."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class SkillLevel(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


class LanguageLevel(StrEnum):
    A1 = "a1"
    A2 = "a2"
    B1 = "b1"
    B2 = "b2"
    C1 = "c1"
    C2 = "c2"
    NATIVE = "native"


class EducationStatus(StrEnum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class EmploymentType(StrEnum):
    """Used for past experiences and for contract preferences."""

    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    INTERNSHIP = "internship"
    APPRENTICESHIP = "apprenticeship"
    FIXED_TERM = "fixed_term"
    FREELANCE = "freelance"
    VOLUNTEER = "volunteer"
    OTHER = "other"


class RemotePreference(StrEnum):
    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"
    NO_PREFERENCE = "no_preference"


class ConstraintType(StrEnum):
    GEOGRAPHIC = "geographic"
    CONTRACT = "contract"
    AVAILABILITY = "availability"
    SALARY = "salary"
    SCHEDULE = "schedule"
    OTHER = "other"


class SourceKind(StrEnum):
    """Where a piece of information about a company or a contact comes from.

    An AI is never a source: the underlying page, API or file is.
    """

    MANUAL = "manual"
    IMPORT_FILE = "import_file"
    OFFICIAL_API = "official_api"
    PUBLIC_PAGE = "public_page"


class TargetStatus(StrEnum):
    NEW = "new"
    SHORTLISTED = "shortlisted"
    DISMISSED = "dismissed"


class OpportunityStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    UNKNOWN = "unknown"  # default: not knowing is not "closed"


class RoleCategory(StrEnum):
    HR = "hr"
    RECRUITER = "recruiter"
    TECH = "tech"
    MANAGER = "manager"
    FOUNDER = "founder"
    OTHER = "other"
    UNKNOWN = "unknown"


class ChannelKind(StrEnum):
    EMAIL = "email"
    PHONE = "phone"
    LINKEDIN_URL = "linkedin_url"
    CONTACT_FORM_URL = "contact_form_url"


class InfoStatus(StrEnum):
    """State of a found piece of information. Absence is NO row, never a status."""

    FOUND = "found"  # explicitly stated in the cited source
    UNCERTAIN = "uncertain"  # found, but the source is ambiguous, old or indirect


class ContactResearchStatus(StrEnum):
    NOT_STARTED = "not_started"
    FOUND = "found"
    # Only means: "the public sources consulted gave no contact". Never "the company has none".
    NOT_FOUND = "not_found"


# --- Search criteria and qualification (step 3a) ---------------------------------------


class CriterionDimension(StrEnum):
    """What a criterion is compared with. Only dimensions that existing data can evaluate."""

    CONTRACT_TYPE = "contract_type"  # closed vocabulary: EmploymentType values
    COUNTRY = "country"  # closed vocabulary: 2-letter codes
    COMPANY = "company"  # identity: normalised name or domain
    LOCATION = "location"  # free text
    SECTOR = "sector"  # free text (company sector)
    ROLE = "role"  # free text (offer title)
    KEYWORD = "keyword"  # free text (offer title/description, company sector)
    REMOTE_MODE = "remote_mode"  # closed vocabulary: RemoteMode (from the offer)
    # A declared HARD constraint that no data can evaluate yet: always `unknown`, so it can never
    # be silently ignored (required + unknown -> needs_information) until a human resolves it.
    CONSTRAINT = "constraint"


class RemoteMode(StrEnum):
    """How an offer is worked (a fact about the offer, unlike `RemotePreference`)."""

    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"


class CriterionOperator(StrEnum):
    ANY_OF = "any_of"
    NONE_OF = "none_of"


class CriterionLevel(StrEnum):
    REQUIRED = "required"  # a KNOWN incompatibility excludes the target
    PREFERRED = "preferred"  # informs, never excludes
    FLEXIBLE = "flexible"  # orients the search, never excludes


class CriterionOrigin(StrEnum):
    MANUAL = "manual"
    PREFERENCE = "preference"  # seeded from CandidatePreference
    CONSTRAINT = "constraint"  # seeded from CandidateConstraint


class ProfileOrigin(StrEnum):
    MANUAL = "manual"
    FROM_PREFERENCES = "from_preferences"


class CriterionOutcome(StrEnum):
    """Result of comparing one criterion with a target. Epistemic, not a score.

    - `satisfied`: compatible, with evidence;
    - `incompatible`: a KNOWN incompatibility (closed vocabulary, or an excluded value found);
    - `not_matched`: free text was available but shows no match. This is NOT a proof of
      incompatibility (wording differs, a suburb is not the city...), so it never excludes;
    - `unknown`: the data needed is missing (never a violation).
    """

    SATISFIED = "satisfied"
    INCOMPATIBLE = "incompatible"
    NOT_MATCHED = "not_matched"
    UNKNOWN = "unknown"


class QualificationStatus(StrEnum):
    """Decision support, distinct from the human `TargetStatus`. Never a score."""

    EXCLUDED = "excluded"  # a required criterion is KNOWN to be incompatible
    NEEDS_INFORMATION = "needs_information"  # no known incompatibility, some required unresolved
    CANDIDATE = "candidate"  # every required criterion is satisfied


class QualificationMethod(StrEnum):
    DETERMINISTIC = "deterministic"
    AI = "ai"  # later steps
    HUMAN = "human"


class EvaluationCode(StrEnum):
    """Why a criterion has its outcome (machine-readable, no free text)."""

    MATCH = "match"
    EXCLUDED_TERM_ABSENT = "excluded_term_absent"
    EXCLUDED_VALUE_PRESENT = "excluded_value_present"
    NOT_IN_ALLOWED_SET = "not_in_allowed_set"
    NO_MATCH_FOUND = "no_match_found"
    DATA_MISSING = "data_missing"
    NO_OFFER = "no_offer"
    DESCRIPTION_NOT_PROVIDED = "description_not_provided"
    NOT_EVALUABLE = "not_evaluable"  # no data can evaluate this criterion yet


class ReasonCode(StrEnum):
    """Why a target may deserve an application, or what stands in the way. Refs, not prose."""

    REQUIRED_SATISFIED = "required_satisfied"
    EXCLUDED_BY_REQUIRED = "excluded_by_required"
    OPEN_QUESTION_REQUIRED = "open_question_required"
    PREFERRED_SATISFIED = "preferred_satisfied"
    FLEXIBLE_MATCHED = "flexible_matched"
    NO_OFFER_PUBLISHED = "no_offer_published"  # informative: not a negative signal


# --- Requirements and matching (step 3b) ---------------------------------------------------


class RequirementKind(StrEnum):
    SKILL = "skill"  # a technology / tool / topic of the taxonomy (or a manual label)
    EXPERIENCE = "experience"  # an explicit duration of experience, kept as written


class RequirementImportance(StrEnum):
    """Only what the SOURCE says. `unspecified` without an explicit, unambiguous marker."""

    REQUIRED = "required"
    NICE_TO_HAVE = "nice_to_have"
    UNSPECIFIED = "unspecified"


class RequirementOrigin(StrEnum):
    OFFER_TEXT = "offer_text"  # extracted, deterministically, from the offer description
    MANUAL = "manual"  # entered by the human, with its excerpt and where it comes from
    COMPANY_SIGNAL = "company_signal"  # reserved for a later step: nothing creates it yet


class MatchStatus(StrEnum):
    """What the Candidate Brain establishes about ONE requirement. Never a score.

    - `covered`: a Skill (or dated experience) with evidence state known/verified;
    - `weak`: the matching fact exists but is unknown/uncertain: an open question;
    - `gap`: NOT ESTABLISHED by the Brain (never "the candidate lacks it");
    - `unmeasurable`: the data does not allow a reliable conclusion.
    """

    COVERED = "covered"
    WEAK = "weak"
    GAP = "gap"
    UNMEASURABLE = "unmeasurable"


class MatchNote(StrEnum):
    """Why a requirement has its status (code only; the text is rendered from a template)."""

    SKILL_ESTABLISHED = "skill_established"
    SKILL_IMPLIED = "skill_implied"  # covered by a more specific Skill (taxonomy `implies`)
    SKILL_UNCONFIRMED = "skill_unconfirmed"
    NO_SKILL_IN_BRAIN = "no_skill_in_brain"
    MENTIONED_BY_PROJECT_ONLY = "mentioned_by_project_only"
    EXPERIENCE_ESTABLISHED = "experience_established"
    EXPERIENCE_INSUFFICIENT = "experience_insufficient"
    EXPERIENCE_NONE_IN_BRAIN = "experience_none_in_brain"
    EXPERIENCE_UNCONFIRMED = "experience_unconfirmed"
    EXPERIENCE_DATES_INSUFFICIENT = "experience_dates_insufficient"
    EXPERIENCE_SCOPE_NOT_MEASURABLE = "experience_scope_not_measurable"
    EXPERIENCE_VALUE_UNREADABLE = "experience_value_unreadable"


class MatchFactRole(StrEnum):
    ESTABLISHES = "establishes"  # the fact the status rests on (a Skill, or dated experiences)
    SUPPORTS = "supports"  # a project / experience that mentions an existing Skill
    MENTIONS = "mentions"  # a project mentions the skill but no Skill exists: an open question
