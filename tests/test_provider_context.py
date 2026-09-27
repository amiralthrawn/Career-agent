"""AI Provider Context / Contract (step 6): the shared abstraction, and each declared contract."""

from app.integrations.llm.ports import TokenUsage as LLMTokenUsage
from app.integrations.provider_context import ProviderContract, TokenUsage, render_contract
from app.integrations.research.perplexity import COMPANY_RESEARCH_CONTRACT
from app.services.draft_generation import DRAFT_GENERATION_CONTRACT

CONTRACTS = {
    "perplexity.company_research": COMPANY_RESEARCH_CONTRACT,
    "generation.application_draft": DRAFT_GENERATION_CONTRACT,
}


def test_every_declared_contract_has_all_five_parts() -> None:
    for name, contract in CONTRACTS.items():
        assert contract.name == name
        assert contract.purpose.strip()
        assert contract.responsibilities and all(item.strip() for item in contract.responsibilities)
        assert contract.boundaries and all(item.strip() for item in contract.boundaries)
        assert contract.upstream_context and all(item.strip() for item in contract.upstream_context)
        assert contract.downstream_role and all(item.strip() for item in contract.downstream_role)


def test_the_perplexity_contract_matches_the_specified_role() -> None:
    contract = COMPANY_RESEARCH_CONTRACT

    assert "external" in contract.purpose.lower() or "research" in contract.purpose.lower()
    assert any("provenance" in item for item in contract.responsibilities)
    assert any("source" in item for item in contract.responsibilities)
    assert any("qualify" in item for item in contract.boundaries)
    assert any("invent candidate" in item for item in contract.boundaries)
    assert any("send" in item and "email" in item for item in contract.boundaries)
    assert any("modify" in item and "database" in item for item in contract.boundaries)
    assert any("evidence" in item or "subsequent" in item for item in contract.downstream_role)


def test_rendering_is_deterministic_structured_and_bounded() -> None:
    text = render_contract(COMPANY_RESEARCH_CONTRACT)

    assert text == render_contract(COMPANY_RESEARCH_CONTRACT)  # same contract -> same text
    for heading in (
        "PURPOSE:",
        "RESPONSIBILITIES:",
        "BOUNDARIES:",
        "UPSTREAM_CONTEXT",
        "DOWNSTREAM_ROLE",
    ):
        assert heading in text
    assert len(text) < 2000  # structured and reusable, never an unboundedly verbose prompt


def test_rendering_reflects_content_changes_and_nothing_else() -> None:
    base = ProviderContract("x", "purpose", ("r",), ("b",), ("u",), ("d",))
    changed = ProviderContract("x", "purpose", ("r", "r2"), ("b",), ("u",), ("d",))

    assert render_contract(base) != render_contract(changed)
    assert "r2" in render_contract(changed) and "r2" not in render_contract(base)


def test_two_contracts_of_the_same_role_never_collide_by_name() -> None:
    assert COMPANY_RESEARCH_CONTRACT.name != DRAFT_GENERATION_CONTRACT.name


def test_token_usage_is_shared_between_llm_and_research_ports() -> None:
    # `app.integrations.llm.ports.TokenUsage` re-exports the same shared type: no silent drift
    # between what a generation call and a research call report as usage.
    assert LLMTokenUsage is TokenUsage
