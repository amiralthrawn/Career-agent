"""Job-family vocabulary for "Criteria v1" (step 3a, see qualification_v1.md).

A SINGLE source of terms, used two ways:
- `app.services.criteria_v1` flattens every term below into ONE required `keyword` criterion
  (`ANY_OF`) on the "Criteria v1" search profile - a positive, versioned list, never a blacklist
  (section 11 of the spec forbids blacklists, not a required list of what the search IS about);
- `app.services.role_family` searches the SAME terms, per family, to label a match for a human
  (contextual only - see `RoleFamily`'s docstring in `app.models.enums`).

Terms are plain lowercase words/phrases; matching already goes through
`app.core.normalize.normalize_text` (accent- and case-insensitive, whole-word), so no accented
variant needs to be listed separately.

Bump `ROLE_TAXONOMY_VERSION` whenever a term is added, removed or moved between families: it is
folded into "Criteria v1"'s criterion values, so a change is visible as a new criterion version
(see `app.services.criteria_v1`), never a silent reinterpretation of past qualifications.
"""

from app.models.enums import RoleFamily

ROLE_TAXONOMY_VERSION = "v1"

# --- Section 9: targeted job families ----------------------------------------------------

DEVELOPMENT_TERMS: tuple[str, ...] = (
    "software developer",
    "software engineer",
    "backend developer",
    "backend engineer",
    "frontend developer",
    "frontend engineer",
    "front end developer",
    "back end developer",
    "full stack developer",
    "full stack engineer",
    "fullstack developer",
    "web developer",
    "web engineer",
    "mobile developer",
    "mobile engineer",
    "application developer",
    "application engineer",
    "embedded developer",
    "embedded engineer",
    "firmware developer",
    "firmware engineer",
    "developpeur",
    "developpeur web",
    "developpeur mobile",
    "developpeur full stack",
    "developpeur logiciel",
    "developpeur backend",
    "developpeur frontend",
    "ingenieur logiciel",
    "ingenieur developpement",
    "ingenieur developpement logiciel",
)

DATA_AI_TERMS: tuple[str, ...] = (
    "data analyst",
    "data scientist",
    "data engineer",
    "machine learning engineer",
    "ml engineer",
    "ai engineer",
    "artificial intelligence engineer",
    "analytics engineer",
    "bi developer",
    "bi engineer",
    "business intelligence developer",
    "business intelligence engineer",
    "data science",
    "analyste de donnees",
    "ingenieur donnees",
    "ingenieur ia",
    "ingenieur intelligence artificielle",
    "data ia",
)

CYBERSECURITY_TERMS: tuple[str, ...] = (
    "cybersecurity",
    "cyber security",
    "security analyst",
    "security engineer",
    "soc analyst",
    "pentester",
    "penetration tester",
    "ethical hacker",
    "devsecops",
    "analyste securite",
    "analyste cybersecurite",
    "ingenieur securite",
    "ingenieur cybersecurite",
    "hacker ethique",
    "cybersecurite",
)

INFRA_CLOUD_DEVOPS_TERMS: tuple[str, ...] = (
    "devops engineer",
    "devops",
    "cloud engineer",
    "site reliability engineer",
    "sre",
    "infrastructure engineer",
    "systems engineer",
    "network engineer",
    "systems administrator",
    "network administrator",
    "sysadmin",
    "ingenieur systemes",
    "ingenieur reseaux",
    "ingenieur infrastructure",
    "administrateur systemes",
    "administrateur reseau",
    "ingenieur cloud",
)

QA_TESTING_TERMS: tuple[str, ...] = (
    "qa engineer",
    "test engineer",
    "automation engineer",
    "sdet",
    "software quality engineer",
    "quality assurance engineer",
    "quality assurance",
    "testeur",
    "ingenieur qualite logicielle",
    "ingenieur test",
    "ingenieur validation",
)

IT_TECHNICAL_TERMS: tuple[str, ...] = (
    "it project manager",
    "technical project manager",
    "chef de projet informatique",
    "chef de projet technique",
    "it consultant",
    "technical consultant",
    "consultant technique",
    "consultant it",
    "solutions engineer",
    "solutions architect",
    "architecte solutions",
    "technical analyst",
    "it business analyst",
)

# --- Section 10: adjacent, non-blacklisted, deterministically recognised terms ------------

ADJACENT_TECHNICAL_TERMS: tuple[str, ...] = (
    "automation",
    "technical operations",
    "developer tooling",
    "platform engineering",
    "data operations",
    "technical support",
    "support technique",
    "operations techniques",
    "ingenieur support",
)

FAMILY_TERMS: dict[RoleFamily, tuple[str, ...]] = {
    RoleFamily.DEVELOPMENT: DEVELOPMENT_TERMS,
    RoleFamily.DATA_AI: DATA_AI_TERMS,
    RoleFamily.CYBERSECURITY: CYBERSECURITY_TERMS,
    RoleFamily.INFRA_CLOUD_DEVOPS: INFRA_CLOUD_DEVOPS_TERMS,
    RoleFamily.QA_TESTING: QA_TESTING_TERMS,
    RoleFamily.IT_TECHNICAL: IT_TECHNICAL_TERMS,
    RoleFamily.ADJACENT_TECHNICAL: ADJACENT_TECHNICAL_TERMS,
}


def all_terms() -> list[str]:
    """Every recognised term, targeted families first, then adjacent-technical. Order is
    stable (dict insertion order), so the resulting criterion's `values` are reproducible."""
    seen: list[str] = []
    for terms in FAMILY_TERMS.values():
        for term in terms:
            if term not in seen:
                seen.append(term)
    return seen
