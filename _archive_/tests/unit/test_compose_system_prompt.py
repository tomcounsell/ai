"""Unit tests for `compose_system_prompt` and the (persona x access-level) matrix.

Covers:
1. Byte-stability of the (ENGINEER, WORKER) cell against one checked-in
   baseline (issue #1227 prompt-cache invariant).
2. One smoke test per (persona x access-level) cell -- composer returns a
   non-empty string and does not raise.
3. Startup-lint invariants: WORKER cell contains WORKER_RULES, TEAMMATE/
   CUSTOMER_SERVICE cells do not.
4. Argument-validation contract: TypeError on bad enum.
5. WORKER cell with working_directory: vault CLAUDE.md appended when present,
   skipped silently when absent (re-gated from WORKER branch).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.sdk_client import (
    WORKER_RULES,
    compose_system_prompt,
    load_eng_system_prompt,
    load_system_prompt,
)
from config.enums import AccessLevel, PersonaType
from scripts.capture_persona_baseline import (
    BASELINE_PATH,
    PRINCIPAL_FIXTURE_PATH,
    compose_repo_only_eng_worker_prompt,
)


def _local_work_vault() -> str:
    """Best-effort local work-vault path for WORKER cell tests.

    Falls back to the repo root if the production layout is missing -- the test
    only exercises the composer against itself, not against a specific
    CLAUDE.md content.
    """
    candidate = Path.home() / "work-vault" / "AI Valor Engels System"
    if candidate.exists():
        return str(candidate)
    return str(Path(__file__).resolve().parent.parent.parent)


# --- 1. Byte-stability ------------------------------------------------------


# Pin byte-stability tests to a single xdist worker. They compose the
# prompt from on-disk persona/segment files and compare byte-for-byte against
# a fixture; any concurrent test that mutates shared global state (env vars,
# persona files) the composer reads can perturb the bytes and flake the
# comparison under parallelism. Grouping isolates them deterministically
# regardless of the --dist mode (issue #1578, Category E).
@pytest.mark.xdist_group(name="compose_system_prompt_byte_stable")
def test_the_baseline_exists_on_every_host():
    """A missing baseline is a failure, never a skip (#2555).

    The baseline used to live in ``tests/fixtures/{socket.gethostname()}/``, so
    the guard ran only on a machine whose name matched a checked-in directory
    and skipped silently on every other one. Only ``Mac-local`` was ever
    committed, which is no host in the current fleet, so the guard was inert
    everywhere and a passing run looked identical to a running one. There is
    now one baseline and it is either present or the suite says so.
    """
    assert BASELINE_PATH.exists(), (
        f"{BASELINE_PATH} is missing. Run scripts/capture_persona_baseline.py. "
        "This file is checked in and host-independent; it is not generated per machine."
    )
    assert PRINCIPAL_FIXTURE_PATH.exists(), (
        f"{PRINCIPAL_FIXTURE_PATH} is missing; the baseline cannot be reproduced without it."
    )


@pytest.mark.xdist_group(name="compose_system_prompt_byte_stable")
def test_eng_cell_byte_stable_against_baseline():
    """The (ENGINEER, WORKER) cell must compose to the recorded bytes.

    This is the freshness enforcement as well as the drift guard: the baseline
    is a pure function of repo-tracked inputs, so a stale baseline and a drifted
    persona are the same failure and neither can hide behind the other. Edit a
    segment, ``config/personas/engineer.md``, ``manifest.json``,
    ``WORKER_RULES``, the composition order, or ``CLAUDE.md``'s completion
    criteria, and this goes red until someone re-records deliberately.

    The three machine-local inputs are pinned out by
    ``compose_repo_only_eng_worker_prompt``. They are absent from the repo, so
    no pull request can drift them and pinning them costs no coverage.
    """
    composed = compose_repo_only_eng_worker_prompt()
    baseline = BASELINE_PATH.read_text()
    assert composed == baseline, (
        f"ENG cell drifted from the baseline: composed {len(composed)} chars, "
        f"baseline {len(baseline)} chars. The prompt-cache prefix invariant (#1227) "
        "breaks on a changed prefix. Run `python scripts/capture_persona_baseline.py` "
        "to re-record if the change is intentional, and include the new baseline in "
        "the same commit."
    )


@pytest.mark.xdist_group(name="compose_system_prompt_byte_stable")
def test_the_baseline_pins_out_the_private_layer_it_claims_to():
    """The pinning must actually change the composition on a host that has one.

    Without this, a broken pin would go unnoticed on any machine whose private
    layer happens to be absent: the pinned and unpinned compositions would be
    identical and the baseline would quietly go back to being host-dependent.
    """
    import agent.sdk_client as sdk_client

    has_private_layer = (
        sdk_client.PRIVATE_IDENTITY_PATH.exists()
        or (sdk_client.PERSONAS_OVERLAY_DIR / "engineer.md").exists()
        or sdk_client.PRINCIPAL_PATH.exists()
    )
    if not has_private_layer:
        pytest.skip(
            "this host has no private persona layer, so there is nothing for the pin to "
            "override. The byte-stability guard above still runs; only this negative "
            "control needs a host that has one."
        )

    unpinned = compose_system_prompt(PersonaType.ENGINEER, AccessLevel.WORKER)
    assert compose_repo_only_eng_worker_prompt() != unpinned, (
        "the pinned composition equals the unpinned one on a host that HAS a private "
        "persona layer, so the pin is not taking effect and the baseline is measuring "
        "this machine rather than the repo"
    )


def test_load_system_prompt_wrapper_matches_composer():
    """The legacy `load_system_prompt()` shim must equal direct composer call."""
    assert load_system_prompt() == compose_system_prompt(PersonaType.ENGINEER, AccessLevel.WORKER)


def test_load_eng_system_prompt_wrapper_matches_composer():
    """The `load_eng_system_prompt()` wrapper must equal direct composer call with work_dir."""
    work_dir = _local_work_vault()
    assert load_eng_system_prompt(work_dir) == compose_system_prompt(
        PersonaType.ENGINEER,
        AccessLevel.WORKER,
        working_directory=work_dir,
    )


# --- 2. (persona x access-level) matrix -------------------------------------


@pytest.mark.parametrize(
    "persona,access_level",
    [
        (PersonaType.ENGINEER, AccessLevel.WORKER),
        (PersonaType.TEAMMATE, AccessLevel.TEAMMATE),
        (PersonaType.CUSTOMER_SERVICE, AccessLevel.CUSTOMER_SERVICE),
    ],
)
def test_compose_cell_returns_nonempty_string(persona, access_level):
    """Every supported (persona x access-level) cell must compose without
    error and produce a non-empty prompt."""
    prompt = compose_system_prompt(persona, access_level)
    assert isinstance(prompt, str)
    assert prompt.strip(), f"empty prompt for ({persona}, {access_level})"


# --- 3. Startup-lint invariants ---------------------------------------------


def test_worker_cell_under_cache_budget():
    """The repo-controlled WORKER cell prompt must stay under 50K chars.

    The bound is deliberately tighter than Anthropic's prompt-cache budget
    (#1227's original 80K): #3069's owner ruling set 50K so headroom pressure
    points toward trimming the feeds, never raising the line. Raise this
    constant only with an owner decision recorded on an issue.

    Measures the deterministic repo-only composition (private persona layers
    pinned out, principal from the checked-in fixture -- same inputs as the
    byte-stability baseline, #2555) plus this repo's own ``CLAUDE.md`` as the
    appended working-directory instructions. The previous form composed against
    the live host layout (private overlay, ``~/work-vault`` fallback), so its
    verdict varied by machine -- the host-dependence flaw named in #3069's
    second finding.
    """
    repo_root = Path(__file__).resolve().parent.parent.parent
    claude_md = (repo_root / "CLAUDE.md").read_text()
    prompt = f"{compose_repo_only_eng_worker_prompt()}\n\n---\n\n{claude_md}"
    assert len(prompt) < 50_000, f"WORKER prompt over budget: {len(prompt)} chars"


def test_no_unsubstituted_identity_markers():
    """No `{{identity.*}}` template markers should remain in any composed cell."""
    cells = [
        (PersonaType.ENGINEER, AccessLevel.WORKER, {}),
        (PersonaType.TEAMMATE, AccessLevel.TEAMMATE, {}),
        (PersonaType.CUSTOMER_SERVICE, AccessLevel.CUSTOMER_SERVICE, {}),
    ]
    for persona, access_level, kwargs in cells:
        prompt = compose_system_prompt(persona, access_level, **kwargs)
        assert "{{identity." not in prompt, (
            f"unsubstituted identity marker in ({persona}, {access_level}) cell"
        )


def test_worker_rules_precede_persona_in_worker_cell():
    """WORKER_RULES must appear before any persona overlay text in the
    (ENGINEER, WORKER) cell -- safety rails take precedence over persona."""
    prompt = compose_system_prompt(PersonaType.ENGINEER, AccessLevel.WORKER)
    wr_idx = prompt.find(WORKER_RULES)
    assert wr_idx == 0, (
        f"WORKER_RULES must be at offset 0; found at {wr_idx} (composition order regression)."
    )


def test_teammate_cell_does_not_contain_worker_rules():
    """TEAMMATE rails must NOT include WORKER_RULES."""
    prompt = compose_system_prompt(PersonaType.TEAMMATE, AccessLevel.TEAMMATE)
    assert WORKER_RULES not in prompt, (
        "TEAMMATE cell contains WORKER_RULES -- safety rails leaked into teammate mode."
    )


# --- 4. Argument validation -------------------------------------------------


def test_compose_rejects_non_persona_type():
    with pytest.raises(TypeError, match="persona must be a PersonaType"):
        compose_system_prompt("engineer", AccessLevel.WORKER)  # type: ignore[arg-type]


def test_compose_rejects_non_access_level():
    with pytest.raises(TypeError, match="access_level must be an AccessLevel"):
        compose_system_prompt(PersonaType.ENGINEER, "worker")  # type: ignore[arg-type]


# --- 5. WORKER cell vault CLAUDE.md re-gate ----------------------------------


def test_worker_cell_appends_vault_claude_md_when_present(tmp_path):
    """When `working_directory` is provided to the WORKER cell and a CLAUDE.md
    exists there, its contents must be appended to the composed prompt."""
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# Eng Instructions\nYou are an engineer.")
    prompt = compose_system_prompt(
        PersonaType.ENGINEER,
        AccessLevel.WORKER,
        working_directory=str(tmp_path),
    )
    assert "Eng Instructions" in prompt
    assert "You are an engineer." in prompt


def test_worker_cell_skips_vault_layer_when_no_claude_md(tmp_path):
    """When `working_directory` is provided but no CLAUDE.md exists, the
    WORKER cell must not raise and must still return a valid prompt."""
    # tmp_path has no CLAUDE.md
    prompt = compose_system_prompt(
        PersonaType.ENGINEER,
        AccessLevel.WORKER,
        working_directory=str(tmp_path),
    )
    assert isinstance(prompt, str)
    assert prompt.strip()
    assert WORKER_RULES in prompt


def test_worker_cell_without_working_directory_no_raise():
    """The WORKER cell must work fine when `working_directory` is None
    (no vault layer appended, no raise)."""
    prompt = compose_system_prompt(PersonaType.ENGINEER, AccessLevel.WORKER)
    assert isinstance(prompt, str)
    assert WORKER_RULES in prompt
