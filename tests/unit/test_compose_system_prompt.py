"""Unit tests for `compose_system_prompt` and the (persona × access-level) matrix.

Covers:
1. Byte-stability of the (DEVELOPER, WORKER) and (PROJECT_MANAGER, PM_READONLY)
   cells against per-machine fixtures (issue #1227 prompt-cache invariant).
2. One smoke test per (persona × access-level) cell — composer returns a
   non-empty string and does not raise.
3. Startup-lint invariants: PM cell stays under 80K chars, no leftover
   `{{identity.*}}` markers, WORKER_RULES precedes the persona overlay text in
   the WORKER cell, PM_READONLY cell does NOT contain WORKER_RULES.
4. Argument-validation contract: TypeError on bad enum, ValueError on
   PM_READONLY without working_directory.

The byte-stability test SKIPs (does not FAIL) when the local machine has not
captured a baseline yet — see `scripts/capture_persona_baseline.py`.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from agent.sdk_client import (
    WORKER_RULES,
    compose_system_prompt,
    load_pm_system_prompt,
    load_system_prompt,
)
from config.enums import AccessLevel, PersonaType


def _machine_slug() -> str:
    return socket.gethostname().replace(".", "-").replace("/", "-").replace(" ", "-")


def _fixture_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "fixtures" / _machine_slug()


def _local_work_vault() -> str:
    """Best-effort local work-vault path for PM cell tests.

    Falls back to the repo root if the production layout is missing — the test
    only exercises the composer against itself, not against a specific
    CLAUDE.md content.
    """
    candidate = Path.home() / "work-vault" / "AI Valor Engels System"
    if candidate.exists():
        return str(candidate)
    return str(Path(__file__).resolve().parent.parent.parent)


# --- 1. Byte-stability ------------------------------------------------------


# Pin both byte-stability tests to a single xdist worker. They compose the
# prompt from on-disk persona/segment files and the live work-vault CLAUDE.md
# and compare byte-for-byte against a fixture; any concurrent test that mutates
# shared global state (env vars, persona files) the composer reads can perturb
# the bytes and flake the comparison under parallelism. Grouping isolates them
# deterministically regardless of the --dist mode (issue #1578, Category E).
@pytest.mark.xdist_group(name="compose_system_prompt_byte_stable")
def test_dev_cell_byte_stable_against_local_fixture():
    """`(DEVELOPER, WORKER)` composer output must equal the local-machine
    baseline captured from `load_system_prompt()` on main."""
    baseline = _fixture_dir() / "dev_system_prompt_baseline.txt"
    if not baseline.exists():
        pytest.skip(
            f"No baseline for hostname '{_machine_slug()}'; run "
            "scripts/capture_persona_baseline.py to record one."
        )
    composed = compose_system_prompt(PersonaType.DEVELOPER, AccessLevel.WORKER)
    assert composed == baseline.read_text(), (
        "DEV cell drifted from baseline — prompt cache invariant (#1227) "
        "would break. Re-run scripts/capture_persona_baseline.py if the change "
        "is intentional."
    )


@pytest.mark.xdist_group(name="compose_system_prompt_byte_stable")
def test_pm_cell_byte_stable_against_local_fixture():
    """`(PROJECT_MANAGER, PM_READONLY)` composer output must equal the
    local-machine baseline captured from `load_pm_system_prompt(work_dir)` on
    main."""
    baseline = _fixture_dir() / "pm_system_prompt_baseline.txt"
    if not baseline.exists():
        pytest.skip(
            f"No baseline for hostname '{_machine_slug()}'; run "
            "scripts/capture_persona_baseline.py to record one."
        )
    composed = compose_system_prompt(
        PersonaType.PROJECT_MANAGER,
        AccessLevel.PM_READONLY,
        working_directory=_local_work_vault(),
    )
    assert composed == baseline.read_text(), (
        "PM cell drifted from baseline — prompt cache invariant (#1227) "
        "would break. Re-run scripts/capture_persona_baseline.py if the change "
        "is intentional."
    )


def test_load_system_prompt_wrapper_matches_composer():
    """The legacy `load_system_prompt()` shim must equal direct composer call."""
    assert load_system_prompt() == compose_system_prompt(PersonaType.DEVELOPER, AccessLevel.WORKER)


def test_load_pm_system_prompt_wrapper_matches_composer():
    """The legacy `load_pm_system_prompt()` shim must equal direct composer call."""
    work_dir = _local_work_vault()
    assert load_pm_system_prompt(work_dir) == compose_system_prompt(
        PersonaType.PROJECT_MANAGER,
        AccessLevel.PM_READONLY,
        working_directory=work_dir,
    )


# --- 2. (persona × access-level) matrix -------------------------------------


@pytest.mark.parametrize(
    "persona,access_level",
    [
        (PersonaType.DEVELOPER, AccessLevel.WORKER),
        (PersonaType.PROJECT_MANAGER, AccessLevel.PM_READONLY),
        (PersonaType.TEAMMATE, AccessLevel.TEAMMATE),
        (PersonaType.CUSTOMER_SERVICE, AccessLevel.CUSTOMER_SERVICE),
    ],
)
def test_compose_cell_returns_nonempty_string(persona, access_level):
    """Every supported (persona × access-level) cell must compose without
    error and produce a non-empty prompt."""
    kwargs: dict = {}
    if access_level == AccessLevel.PM_READONLY:
        kwargs["working_directory"] = _local_work_vault()
    prompt = compose_system_prompt(persona, access_level, **kwargs)
    assert isinstance(prompt, str)
    assert prompt.strip(), f"empty prompt for ({persona}, {access_level})"


# --- 3. Startup-lint invariants ---------------------------------------------


def test_pm_cell_under_cache_budget():
    """PM cell prompt must stay under 80K chars (Anthropic prompt cache
    budget — see #1227)."""
    prompt = compose_system_prompt(
        PersonaType.PROJECT_MANAGER,
        AccessLevel.PM_READONLY,
        working_directory=_local_work_vault(),
    )
    assert len(prompt) < 80_000, f"PM prompt over budget: {len(prompt)} chars"


def test_no_unsubstituted_identity_markers():
    """No `{{identity.*}}` template markers should remain in any composed cell."""
    cells = [
        (PersonaType.DEVELOPER, AccessLevel.WORKER, {}),
        (
            PersonaType.PROJECT_MANAGER,
            AccessLevel.PM_READONLY,
            {"working_directory": _local_work_vault()},
        ),
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
    (DEVELOPER, WORKER) cell — safety rails take precedence over persona."""
    prompt = compose_system_prompt(PersonaType.DEVELOPER, AccessLevel.WORKER)
    wr_idx = prompt.find(WORKER_RULES)
    assert wr_idx == 0, (
        f"WORKER_RULES must be at offset 0; found at {wr_idx} (composition order regression)."
    )


def test_pm_readonly_cell_does_not_contain_worker_rules():
    """PM_READONLY rails must NOT include WORKER_RULES (preserves the
    `load_pm_system_prompt` invariant from sdk_client.py docstring)."""
    prompt = compose_system_prompt(
        PersonaType.PROJECT_MANAGER,
        AccessLevel.PM_READONLY,
        working_directory=_local_work_vault(),
    )
    assert WORKER_RULES not in prompt, (
        "PM_READONLY cell contains WORKER_RULES — safety rails leaked into "
        "PM mode (#1148 invariant)."
    )


# --- 4. Argument validation -------------------------------------------------


def test_compose_rejects_non_persona_type():
    with pytest.raises(TypeError, match="persona must be a PersonaType"):
        compose_system_prompt("developer", AccessLevel.WORKER)  # type: ignore[arg-type]


def test_compose_rejects_non_access_level():
    with pytest.raises(TypeError, match="access_level must be an AccessLevel"):
        compose_system_prompt(PersonaType.DEVELOPER, "worker")  # type: ignore[arg-type]


def test_pm_readonly_requires_working_directory():
    with pytest.raises(ValueError, match="PM_READONLY requires working_directory"):
        compose_system_prompt(PersonaType.PROJECT_MANAGER, AccessLevel.PM_READONLY)
