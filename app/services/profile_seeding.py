"""Turn CandidatePreference + CandidateConstraint into criteria drafts (explicit, reviewable).

Nothing is guessed, and a HARD constraint is never silently dropped.

Preferences (level `preferred`, except where noted):
- target_roles -> role;   contract_types -> contract_type (`required`: the contract really sought);
- preferred_locations -> location;   target_sectors -> sector;   target_companies -> company;
- remote_preference -> remote_mode (`no_preference` states no criterion, so none is created);
- target_domains -> keyword (`flexible`: orients the search); a slash separates alternatives
  (`Data/IA` -> `Data`, `IA`).
Not evaluable with the data we hold (reported in `unmapped`): company_size_preferences, salaries.

Constraints (level `required` if `is_hard`, else `preferred`), from the `value` JSON:
- geographic: countries / excluded_countries / locations / excluded_locations;
- contract: contract_types / excluded_contract_types.

Whatever part of a constraint cannot be evaluated is handled by hardness:
- a HARD constraint (or hard part) that cannot be evaluated becomes a REQUIRED criterion on the
  `constraint` dimension, which is always `unknown`: every target then needs information
  (`required + unknown -> needs_information`) until a human resolves it (deactivating or
  re-levelling the criterion is an explicit, versioned action). It never excludes by itself. It
  is also listed in `unmapped` (`hard_constraint_unevaluable` / `hard_constraint_partially_held`);
- a SOFT constraint that cannot be evaluated is only listed in `unmapped`
  (`unsupported_constraint` / `partially_mapped` / `invalid_value`).
"""

from dataclasses import dataclass

from pydantic import ValidationError

from app.models import CandidateConstraint, CandidatePreference
from app.models.enums import (
    ConstraintType,
    CriterionDimension,
    CriterionLevel,
    CriterionOperator,
    CriterionOrigin,
    RemotePreference,
)
from app.schemas.search import CriterionInput

D = CriterionDimension
LEVELS = CriterionLevel

# (preference attribute, dimension, default level)
PREFERENCE_MAPPING: tuple[tuple[str, CriterionDimension, CriterionLevel], ...] = (
    ("target_roles", D.ROLE, LEVELS.PREFERRED),
    ("contract_types", D.CONTRACT_TYPE, LEVELS.REQUIRED),
    ("preferred_locations", D.LOCATION, LEVELS.PREFERRED),
    ("target_sectors", D.SECTOR, LEVELS.PREFERRED),
    ("target_companies", D.COMPANY, LEVELS.PREFERRED),
    ("target_domains", D.KEYWORD, LEVELS.FLEXIBLE),
)
NOT_EVALUABLE_PREFERENCES = (
    "company_size_preferences",
    "minimum_salary",
    "preferred_salary",
)
# constraint type -> {value key: (dimension, operator)}
CONSTRAINT_MAPPING: dict[
    ConstraintType, dict[str, tuple[CriterionDimension, CriterionOperator]]
] = {
    ConstraintType.GEOGRAPHIC: {
        "countries": (D.COUNTRY, CriterionOperator.ANY_OF),
        "excluded_countries": (D.COUNTRY, CriterionOperator.NONE_OF),
        "locations": (D.LOCATION, CriterionOperator.ANY_OF),
        "excluded_locations": (D.LOCATION, CriterionOperator.NONE_OF),
    },
    ConstraintType.CONTRACT: {
        "contract_types": (D.CONTRACT_TYPE, CriterionOperator.ANY_OF),
        "excluded_contract_types": (D.CONTRACT_TYPE, CriterionOperator.NONE_OF),
    },
}
HELD_NOTE = "Declared hard constraint that cannot be evaluated yet"


@dataclass(frozen=True)
class CriterionDraft:
    data: CriterionInput
    origin: CriterionOrigin
    origin_ref: str


@dataclass(frozen=True)
class Seed:
    drafts: list[CriterionDraft]
    unmapped: list[dict[str, str]]


def _build(
    dimension: CriterionDimension,
    operator: CriterionOperator,
    values: object,
    level: CriterionLevel,
) -> tuple[CriterionInput | None, bool]:
    """(criterion, failed). An empty list means "nothing declared": not a failure."""
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        return None, True
    if not values:
        return None, False
    try:
        data = CriterionInput(dimension=dimension, operator=operator, values=values, level=level)
    except ValidationError:
        return None, True
    return data, False


def _held(constraint: CandidateConstraint) -> CriterionDraft:
    """The always-`unknown` REQUIRED criterion that keeps a hard constraint from being ignored."""
    return CriterionDraft(
        CriterionInput(
            dimension=D.CONSTRAINT,
            values=[constraint.constraint_type.value],
            level=LEVELS.REQUIRED,
            note=HELD_NOTE,
        ),
        CriterionOrigin.CONSTRAINT,
        f"constraint:{constraint.id}",
    )


def _seed_preferences(
    preference: CandidatePreference,
    level_overrides: dict[CriterionDimension, CriterionLevel],
    drafts: list[CriterionDraft],
    unmapped: list[dict[str, str]],
) -> None:
    for attribute, dimension, default in PREFERENCE_MAPPING:
        values = list(getattr(preference, attribute) or [])
        if attribute == "target_domains":
            # "Data/IA" lists alternatives: each part is searched on its own.
            values = [part.strip() for value in values for part in value.split("/")]
            values = [value for value in values if value]
        ref = f"preference:{attribute}"
        data, failed = _build(
            dimension, CriterionOperator.ANY_OF, values, level_overrides.get(dimension, default)
        )
        if failed:
            unmapped.append({"source": ref, "code": "invalid_value"})
        elif data:
            drafts.append(CriterionDraft(data, CriterionOrigin.PREFERENCE, ref))

    remote = preference.remote_preference
    if remote is not None and remote is not RemotePreference.NO_PREFERENCE:
        data, _ = _build(
            D.REMOTE_MODE,
            CriterionOperator.ANY_OF,
            [remote.value],
            level_overrides.get(D.REMOTE_MODE, LEVELS.PREFERRED),
        )
        if data:
            drafts.append(
                CriterionDraft(data, CriterionOrigin.PREFERENCE, "preference:remote_preference")
            )

    for attribute in NOT_EVALUABLE_PREFERENCES:
        if getattr(preference, attribute) not in (None, [], ""):
            unmapped.append({"source": f"preference:{attribute}", "code": "no_evaluable_data"})


def _seed_constraint(
    constraint: CandidateConstraint, drafts: list[CriterionDraft], unmapped: list[dict[str, str]]
) -> None:
    ref = f"constraint:{constraint.id}"
    hard = constraint.is_hard
    level = LEVELS.REQUIRED if hard else LEVELS.PREFERRED
    mapping = CONSTRAINT_MAPPING.get(constraint.constraint_type, {})
    value = constraint.value
    recognised = [key for key in value if key in mapping] if isinstance(value, dict) else []

    if not recognised or not isinstance(value, dict):  # nothing in it can be evaluated
        if hard:
            drafts.append(_held(constraint))
            unmapped.append({"source": ref, "code": "hard_constraint_unevaluable"})
        else:
            unmapped.append({"source": ref, "code": "unsupported_constraint"})
        return

    something_unresolved = len(recognised) != len(value)
    failed_invalid = False
    for key in recognised:
        dimension, operator = mapping[key]
        data, failed = _build(dimension, operator, value[key], level)
        if failed:
            failed_invalid = True
        elif data:
            drafts.append(CriterionDraft(data, CriterionOrigin.CONSTRAINT, ref))

    if hard and (something_unresolved or failed_invalid):
        # The recognised, valid parts are enforced; the rest is held so it cannot be ignored.
        drafts.append(_held(constraint))
        code = "hard_constraint_unevaluable" if failed_invalid else "hard_constraint_partially_held"
        unmapped.append({"source": ref, "code": code})
    elif failed_invalid:
        unmapped.append({"source": ref, "code": "invalid_value"})
    elif something_unresolved:
        unmapped.append({"source": ref, "code": "partially_mapped"})


def seed_criteria(
    preference: CandidatePreference | None,
    constraints: list[CandidateConstraint],
    level_overrides: dict[CriterionDimension, CriterionLevel],
) -> Seed:
    drafts: list[CriterionDraft] = []
    unmapped: list[dict[str, str]] = []
    if preference is not None:
        _seed_preferences(preference, level_overrides, drafts, unmapped)
    for constraint in constraints:
        _seed_constraint(constraint, drafts, unmapped)
    return Seed(drafts, unmapped)
