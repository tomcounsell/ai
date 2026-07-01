"""Substrate A deterministic fault injectors for the granite harness.

Each failure class in the plan's Substrate A table gets ONE deterministic
injector that targets a real granite seam and asserts the recovery /
detection path fires — with no ollama, no model, no network, and no real
``claude`` spawn. This module holds the reusable building blocks; the
assertions live in ``tests/unit/granite_container/test_fault_injection.py``.

Design constraints (plan + critique):
- TEST-ONLY. Nothing here imports for side effects into production; the
  injectors reuse the real ``read_until_idle`` / ``parse_startup_frame`` /
  ``Container.run`` seams and only *feed* them synthetic input.
- Never spawn a real child. Every fake PTY child is a ``MagicMock`` fed to
  ``PTYDriver._child`` directly, so the autouse ``_block_real_claude_spawn``
  guard in ``conftest.py`` is never tripped and no orphan PID can leak.
- Assert against the *mutation*, not exact fixture bytes (plan Rabbit Holes:
  no byte-fidelity coupling).
"""

from __future__ import annotations

import pathlib
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pexpect

from agent.granite_container.pty_driver import PTYDriver

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Fixtures + mutators (class 1 / class 2 / class 6)
# ---------------------------------------------------------------------------


def load_fixture(name: str) -> str:
    """Read a recorded frame fixture from ``fixtures/`` verbatim."""
    return (FIXTURES_DIR / name).read_text()


# The bypass-permissions bar is the load-bearing idle signal
# (``pty_driver.IDLE_BAR`` = ``bypass.{0,30}permissions``). Removing or
# renaming it is exactly what a Claude Code UI revision does when it breaks
# the granite idle heuristic — the failure the harness reproduces.
_BAR_TEXT_RE = re.compile(r"bypass(.{0,30})permissions", re.IGNORECASE)


def remove_idle_bar(frames: str) -> str:
    """Strip the bypass-permissions bar text so ``IDLE_BAR`` cannot match.

    Models a TUI upgrade that drops the bottom bar entirely — the idle
    heuristic then never fires and the session wedges silently.
    """
    return _BAR_TEXT_RE.sub("workspace trust settings", frames)


def rename_idle_bar(frames: str, new_text: str = "sandbox mode active") -> str:
    """Rename the bar to a plausible new phrase ``IDLE_BAR`` does not match.

    Models Anthropic reflowing the bottom bar wording in a new release.
    """
    return _BAR_TEXT_RE.sub(new_text, frames)


def drop_login_phrases(frame: str) -> str:
    """Remove the phrases ``startup_parser`` keys the login classification on.

    Models a re-auth frame whose wording drifted past every known login
    pattern — ``parse_startup_frame`` then returns ``UNKNOWN`` and the
    container cannot resolve the dialog (the ``startup_unresolved`` path).
    """
    out = frame
    for phrase in ("Select login method", "Sign in to continue", "paste"):
        out = re.sub(re.escape(phrase), "welcome aboard", out, flags=re.IGNORECASE)
    return out


# ---------------------------------------------------------------------------
# Fake pexpect children (class 1 / class 3 / class 6)
# ---------------------------------------------------------------------------


def fake_child_frames(chunks: Iterable[str]) -> MagicMock:
    """A fake pexpect child that yields ``chunks`` once, then stays silent.

    Mirrors the ``_driver_with_mock`` pattern in ``test_pty_driver.py``:
    each ``read_nonblocking`` call pulls the next chunk; once exhausted it
    raises ``pexpect.TIMEOUT`` (the "no new bytes this tick" signal the
    driver loops on) forever. A settled/quiescent PTY.
    """
    mock = MagicMock()
    it = iter(list(chunks))

    def _read_nonblocking(size: int, timeout: float) -> str:
        try:
            return next(it)
        except StopIteration as exc:
            raise pexpect.TIMEOUT("fake child silent") from exc

    mock.read_nonblocking.side_effect = _read_nonblocking
    mock.isalive.return_value = True
    return mock


def fake_child_hung(*, honor_timeout: bool, block_seconds: float = 3.0) -> MagicMock:
    """A fake child that never paints — models a process hang / U-state.

    ``honor_timeout=True`` emulates a *silent* hung child the way real
    pexpect does: each ``read_nonblocking`` returns quickly at its read
    timeout with no bytes (``pexpect.TIMEOUT``). The driver's outer
    deadline bounds the total wait — this is the GREEN, recovered path.

    ``honor_timeout=False`` emulates an uninterruptible-sleep (U-state)
    child whose single ``os.read`` blocks ``block_seconds`` past the read
    timeout. The driver cannot re-check its deadline until that call
    returns, so the read is UNBOUNDED — the fault the class-3 assertion
    detects (used for the red-first demonstration).
    """
    mock = MagicMock()

    def _read_nonblocking(size: int, timeout: float) -> str:
        if honor_timeout:
            # Emulate pexpect's select() timing out with no data. Sleep a
            # tiny slice (never the full requested timeout) so the test is
            # sub-second while still exercising the poll loop.
            time.sleep(min(timeout, 0.02))
            raise pexpect.TIMEOUT("hung child, no bytes")
        # U-state: block past the caller's deadline, ignoring `timeout`.
        time.sleep(block_seconds)
        raise pexpect.TIMEOUT("blocked child")

    mock.read_nonblocking.side_effect = _read_nonblocking
    mock.isalive.return_value = True
    return mock


def driver_with_child(child: MagicMock, *, role: str = "pm", timeout_s: float = 2.0) -> PTYDriver:
    """Attach a fake pexpect child to a real ``PTYDriver`` (no spawn)."""
    driver = PTYDriver(role=role, timeout_s=timeout_s)
    driver._child = child
    return driver


# ---------------------------------------------------------------------------
# Scripted container run (class 4 / class 5)
# ---------------------------------------------------------------------------


@dataclass
class ScriptedRun:
    """Result bundle from :func:`run_scripted_container`."""

    result: Any  # ContainerResult
    user_deliveries: list[str]
    complete_deliveries: list[str]


def run_scripted_container(
    *,
    pm_text: str,
    dev_text: str = "Dev finished the work.",
    user_message: str = "do the work",
    max_turns: int = 2,
    last_assistant_side_effect: Callable[..., str] | None = None,
) -> ScriptedRun:
    """Drive a real ``Container.run`` with mocked PTYs and a scripted classifier.

    The PM's assistant text is fixed (``pm_text``) so ``classify_pm_prefix``
    routes deterministically every turn — e.g. an always-``[/dev]`` script
    reproduces the loop / non-convergence class. ``last_assistant_side_effect``
    overrides the transcript read entirely (used to inject a crash, e.g. a
    corrupt-JSONL ``ValueError``).

    Only REAL container methods are patched (``_spawn_pair`` /
    ``_close_pair_and_reap`` / ``_prime_session``); the deleted
    ``_run_pkill_fallback`` is intentionally NOT referenced. PTYs are
    ``MagicMock(spec=PTYDriver)`` so ``_close_pair_and_reap`` never calls
    ``os.getpgid`` on a real PID.
    """
    # Imported lazily so this module has no import-time dependency on the
    # container beyond the driver seam.
    from agent.granite_container.container import Container
    from tests.granite_faults.mocks import _idle_result, _mock_dev, _mock_pm

    user_deliveries: list[str] = []
    complete_deliveries: list[str] = []

    container = Container(
        user_message=user_message,
        max_turns=max_turns,
        on_user_payload=user_deliveries.append,
        on_complete_payload=complete_deliveries.append,
    )
    pm_mock = _mock_pm("")
    dev_mock = _mock_dev("")
    # Constant idle returns: the PM always settles carrying `pm_text`; the
    # Dev always settles carrying a report. Call-count is irrelevant.
    pm_mock.read_until_idle.return_value = _idle_result(pm_text, saw_idle=True)
    dev_mock.read_until_idle.return_value = _idle_result(dev_text, saw_idle=True)

    if last_assistant_side_effect is not None:
        _lat = last_assistant_side_effect
    else:

        def _lat(path: str | None, *, baseline_text_count: int | None = None) -> str:
            if not path:
                return ""
            if "mock-session-dev" in path:
                return dev_text
            return pm_text

    with (
        patch.object(container, "_spawn_pair"),
        patch.object(container, "_close_pair_and_reap"),
        patch.object(container, "_prime_session"),
        patch(
            "agent.granite_container.container.last_assistant_text",
            side_effect=_lat,
        ),
    ):
        container._pm_pty = pm_mock
        container._dev_pty = dev_mock
        result = container.run()

    return ScriptedRun(
        result=result,
        user_deliveries=user_deliveries,
        complete_deliveries=complete_deliveries,
    )


# ---------------------------------------------------------------------------
# Scenario registry (one FaultScenario per failure class)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FaultScenario:
    """Descriptor for one Substrate A failure class.

    The registry lets a meta-test assert coverage (one injector per class)
    and lets each injector reference its plan row by number.
    """

    failure_class: int
    name: str
    seam: str
    description: str


SCENARIOS: tuple[FaultScenario, ...] = (
    FaultScenario(
        failure_class=1,
        name="turn_detection_wedge",
        seam="pty_driver.read_until_idle",
        description=(
            "Recorded idle frame with the IDLE_BAR text removed/renamed → "
            "the C5 idle heuristic never fires → saw_idle=False, bounded wait."
        ),
    ),
    FaultScenario(
        failure_class=2,
        name="startup_login_wedge",
        seam="startup_parser.parse_startup_frame",
        description=(
            "Login frame with the known login phrases dropped → parser returns "
            "UNKNOWN (the startup_unresolved path) instead of LOGIN_PROMPT."
        ),
    ),
    FaultScenario(
        failure_class=3,
        name="process_hang_ustate",
        seam="pty_driver.read_until_idle",
        description=(
            "A silent hung PTY child → read_until_idle's deadline bounds the "
            "wait (no unbounded block); elapsed honors the timeout."
        ),
    ),
    FaultScenario(
        failure_class=4,
        name="loop_non_convergence",
        seam="container.run",
        description=(
            "Scripted PM always emits [/dev] → max_turns guard fires and the "
            "wrap-up guard delivers a user-facing terminal message (#1647/#1719)."
        ),
    ),
    FaultScenario(
        failure_class=5,
        name="crash_fail_loud",
        seam="container.run",
        description=(
            "Transcript read raises (corrupt JSONL / killed classifier) → the "
            "run exits fail-loud (exit_reason=exception + message), not silent "
            "(#1816 ollama degradation / OAuth failure class)."
        ),
    ),
    FaultScenario(
        failure_class=6,
        name="silent_no_progress_tail",
        seam="pty_driver.read_until_idle",
        description=(
            "Stub emits progress frames then goes quiet with no idle bar → "
            "silence is observable via the EXISTING seam: saw_idle=False with "
            "elapsed_ms surfacing the elapsed-since-last-frame wait. No detector "
            "is wired here — out of scope (#1688 / No-Gos)."
        ),
    ),
)
