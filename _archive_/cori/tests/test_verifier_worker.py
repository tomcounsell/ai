"""`workers/verifier.py` and the prompts under `prompts/verifier/`. Plan 11
task 7; seams §1.6, §7; architecture §5, §6."""

from typing import get_args

from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from schemas.objective import ArtifactKind
from schemas.report import CriterionResult, Verdict, derive_outcome as schema_rule
from workers import verifier

KINDS = [k for k in get_args(ArtifactKind) if k != "decision_brief"]
text = st.text(min_size=1, max_size=30).filter(str.strip)


def results(strings: list[str], mets: list[bool | None]) -> list[CriterionResult]:
    return [
        CriterionResult(criterion=s, met=m, reason="judged")
        for s, m in zip(strings, mets)
    ]


@st.composite
def criterion_results(draw):
    n = draw(st.integers(min_value=1, max_value=8))
    strings = draw(st.lists(text, min_size=n, max_size=n, unique=True))
    mets = draw(st.lists(st.sampled_from([True, False, None]), min_size=n, max_size=n))
    return results(strings, mets)


@given(criterion_results())
@settings(max_examples=200)
def test_derive_outcome(criteria):
    outcome = verifier.derive_outcome(criteria)
    mets = [c.met for c in criteria]
    if any(m is False for m in mets):
        assert outcome == "fail"
    elif any(m is None for m in mets):
        assert outcome == "abstain"
    else:
        assert outcome == "pass"
    assert verifier.derive_outcome is schema_rule


@given(criterion_results(), st.sampled_from(["pass", "fail", "abstain"]))
@settings(max_examples=200)
def test_derive_outcome_agrees_with_verdict_validator(criteria, outcome):
    """Every Verdict the schema accepts has outcome == derive_outcome(criteria);
    every other outcome is refused."""
    try:
        verdict = Verdict(
            outcome=outcome,
            predicted_failure=0.5,
            criteria=criteria,
            scope_findings=[],
            summary="s",
        )
    except ValidationError:
        assert outcome != verifier.derive_outcome(criteria)
    else:
        assert verdict.outcome == verifier.derive_outcome(verdict.criteria)


def verdict(strings, mets=None):
    mets = mets if mets is not None else [True] * len(strings)
    criteria = results(strings, mets)
    return Verdict(
        outcome=verifier.derive_outcome(criteria),
        predicted_failure=0.2,
        criteria=criteria,
        scope_findings=[],
        summary="s",
    )


def test_validate_rejects_missing_or_extra_criterion():
    contract = ["a", "b", "c"]
    assert verifier.validate_verdict(verdict(["a", "b", "c"]), criteria=contract) == []
    assert verifier.validate_verdict(verdict(["c", "a", "b"]), criteria=contract) == []
    missing = verifier.validate_verdict(verdict(["a", "b"]), criteria=contract)
    assert missing == ["criterion not judged: 'c'"]
    extra = verifier.validate_verdict(verdict(["a", "b", "c", "d"]), criteria=contract)
    assert extra == ["criterion not in the contract: 'd'"]
    twice = verifier.validate_verdict(verdict(["a", "a", "b", "c"]), criteria=contract)
    assert twice == ["criterion judged 2 times: 'a'"]
    both = verifier.validate_verdict(verdict(["a", "x"]), criteria=contract)
    assert set(both) == {
        "criterion not judged: 'b'",
        "criterion not judged: 'c'",
        "criterion not in the contract: 'x'",
    }


@given(
    st.lists(text, min_size=1, max_size=5, unique=True),
    st.lists(text, min_size=1, max_size=5, unique=True),
)
@settings(max_examples=100)
def test_validate_accepts_exactly_the_contract_once_each(contract, named):
    problems = verifier.validate_verdict(verdict(named), criteria=contract)
    assert (problems == []) == (sorted(named) == sorted(contract))


def test_tools_and_prompts_exist():
    assert verifier.TOOLS == ("read", "bash")
    for kind in KINDS:
        text_, sha = verifier.load_prompt(kind)
        assert text_.startswith("This seat is the Verifier")
        assert len(sha) == 64
    assert len({verifier.load_prompt(k)[1] for k in KINDS}) == len(KINDS)
    for path in verifier.PROMPTS_DIR.glob("*.md"):
        body = path.read_text()
        assert "—" not in body, path
        assert "Cori" not in body, path


def test_prompt_sha_changes_when_a_checklist_changes(tmp_path, monkeypatch):
    d = tmp_path / "prompts"
    d.mkdir()
    (d / "seat.md").write_text("seat\n")
    for kind in KINDS:
        (d / f"{kind}.md").write_text(f"{kind} checklist\n")
    monkeypatch.setattr(verifier, "PROMPTS_DIR", d)
    before = {k: verifier.load_prompt(k)[1] for k in KINDS}
    (d / "code.md").write_text("code checklist, revised\n")
    after = {k: verifier.load_prompt(k)[1] for k in KINDS}
    assert after["code"] != before["code"]
    assert after["document"] == before["document"]
    assert after["message"] == before["message"]
    (d / "seat.md").write_text("seat, revised\n")
    again = {k: verifier.load_prompt(k)[1] for k in KINDS}
    assert all(again[k] != after[k] for k in KINDS)
