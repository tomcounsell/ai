"""Unit tests for `compose_system_prompt` and the (persona x access-level) matrix.

Covers:
1. Byte-stability of the (ENGINEER, WORKER) cell against per-machine fixtures
   (issue #1227 prompt-cache invariant).
2. One smoke test per (persona x access-level) cell -- composer returns a
   non-empty string and does not raise.
3. Startup-lint invariants: WORKER cell contains WORKER_RULES, TEAMMATE/
   CUSTOMER_SERVICE cells do not.
4. Argument-validation contract: TypeError on bad enum.
5. WORKER cell with working_directory: vault CLAUDE.md appended when present,
   skipped silently when absent (re-gated from WORKER branch).
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from agent.sdk_client import (
    WORKER_RULES,
    compose_system_prompt,
    load_eng_system_prompt,
    load_system_prompt,
)
from config.enums import AccessLevel, PersonaType


def _machine_slug() -> str:
    return socket.gethostname().replace(".", "-").replace("/", "-").replace(" ", "-")


def _fixture_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "fixtures" / _machine_slug()


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
def test_eng_cell_byte_stable_against_local_fixture():
    """`(ENGINEER, WORKER)` composer output must equal the local-machine
    baseline captured from `load_system_prompt()` on main."""
    baseline = _fixture_dir() / "dev_system_prompt_baseline.txt"
    if not baseline.exists():
        pytest.skip(
            f"No baseline for hostname '{_machine_slug()}'; run "
            "scripts/capture_persona_baseline.py to record one."
        )
    composed = compose_system_prompt(PersonaType.ENGINEER, AccessLevel.WORKER)
    assert composed == baseline.read_text(), (
        "ENG cell drifted from baseline -- prompt cache invariant (#1227) "
        "would break. Re-run scripts/capture_persona_baseline.py if the change "
        "is intentional."
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
    """WORKER cell prompt (with work_dir) must stay under 80K chars (Anthropic
    prompt cache budget -- see #1227)."""
    prompt = compose_system_prompt(
        PersonaType.ENGINEER,
        AccessLevel.WORKER,
        working_directory=_local_work_vault(),
    )
    assert len(prompt) < 80_000, f"WORKER prompt over budget: {len(prompt)} chars"


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
