"""Qualification Criteria v1: the CLI (4 of the 54 scenarios) - `career-agent qualification ...`
and `career-agent opportunities ...`. The other 50 scenarios live in tests/test_qualification_v1.py
and tests/test_qualification_v1_api.py.
"""

from collections.abc import Callable, Iterator

import pytest
from sqlalchemy.orm import Session

from app.cli import main as cli_main
from app.models.enums import EmploymentType
from tests.targets_factory import candidate, company, opportunity, target


@pytest.fixture
def seeded(configured_database: Callable[[], Session]) -> Iterator[dict[str, int]]:
    with configured_database() as session:
        cand = candidate(session)
        co = company(session, name="Backend Corp")
        offer = opportunity(
            session,
            co.id,
            title="Backend Developer Alternance",
            contract_type=EmploymentType.APPRENTICESHIP,
        )
        good = target(session, cand.id, co.id, offer.id)

        co2 = company(session, name="Ambiguous Corp", domain="ambiguous.example.invalid")
        offer2 = opportunity(
            session,
            co2.id,
            title="Coordinateur Regional Alternance",
            contract_type=EmploymentType.APPRENTICESHIP,
            description_text="Vous accompagnerez les equipes locales.",
        )
        ambiguous = target(session, cand.id, co2.id, offer2.id)
        session.commit()
        ids = {"good": good.id, "ambiguous": ambiguous.id}
    yield ids


def test_cli_qualification_run_prints_the_decision(
    seeded: dict[str, int], capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli_main(["qualification", "run", str(seeded["good"])])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "QUALIFIED" in out
    assert f"Target #{seeded['good']}" in out


def test_cli_qualification_show_reflects_the_run(
    seeded: dict[str, int], capsys: pytest.CaptureFixture[str]
) -> None:
    cli_main(["qualification", "run", str(seeded["good"])])
    capsys.readouterr()
    exit_code = cli_main(["qualification", "show", str(seeded["good"])])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "QUALIFIED" in out
    assert "Stale:" in out


def test_cli_opportunities_qualified_lists_the_qualified_target(
    seeded: dict[str, int], capsys: pytest.CaptureFixture[str]
) -> None:
    cli_main(["qualification", "run", str(seeded["good"])])
    cli_main(["qualification", "run", str(seeded["ambiguous"])])
    capsys.readouterr()
    cli_main(["opportunities", "qualified"])
    out = capsys.readouterr().out
    assert "Backend Corp" in out
    assert "Ambiguous Corp" not in out


def test_cli_opportunities_uncertain_lists_only_the_uncertain_target(
    seeded: dict[str, int], capsys: pytest.CaptureFixture[str]
) -> None:
    cli_main(["qualification", "run", str(seeded["good"])])
    cli_main(["qualification", "run", str(seeded["ambiguous"])])
    capsys.readouterr()
    cli_main(["opportunities", "uncertain"])
    out = capsys.readouterr().out
    assert "Ambiguous Corp" in out
    assert "Backend Corp" not in out
