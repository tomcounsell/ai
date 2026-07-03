"""Granite classifier for the granite interactive-TUI session runner.

`granite_classifier` is the classification-only module for the PTY
container: it performs a deterministic regex parse on PM's prefix
token and routes to dev/user/complete/unknown. No ollama call on the
routing path.

Why classification-only (no LLM translation):
  - The classification decision is a regex parse of the first line
    of PM's transcript text — the `[/dev]/[/user|/complete]`
    convention PM was primed to follow. It is not an LLM call.
  - The verbatim text following the prefix token is forwarded
    directly to the next stage: [/dev] payload goes to Dev, [/user]
    and [/complete] payloads go to the user channel. No rewriting.

Event-bridge and prefix-token design:
  - PM prefix-token compliance: the classifier is a
    deterministic regex parse (`classify_pm_prefix`), not an LLM
    call.

The classifier is stateless: each call sees only the text of the
current PM transcript entry. There is no cross-turn history.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

# Single source of truth for the granite classifier model id (config/models.py).
from config.models import OLLAMA_CLASSIFIER_MODEL as DEFAULT_MODEL  # noqa: E402


def ensure_granite_model(
    model: str = DEFAULT_MODEL,
    *,
    pull_if_missing: bool = True,
    probe_timeout: float = 60.0,
    pull_timeout: float = 900.0,
) -> tuple[bool, str]:
    """Verify the granite classifier model is present and responsive.

    This is the ONLY ollama call in the worker's startup path. Note:
    - ``classify_pm_prefix`` is pure regex — zero ollama calls at runtime.
    - PTY sessions (PM/Dev TUI) run on Claude OAuth subscription, not ollama.
    - ollama is used exclusively for this startup health probe and the
      background ``_granite_reprobe_loop`` circuit breaker in worker/__main__.py.

    When the probe fails, worker starts in degraded mode (granite_available=False)
    and defers ENG sessions to paused_circuit until the background reprobe loop
    restores granite_available=True. This replaces the previous hard sys.exit(1).

    Checks, in order: the ollama python client is importable, the ``ollama``
    CLI/daemon is reachable, and the model answers a trivial prompt. When
    ``pull_if_missing`` and the probe fails, attempts ``ollama pull <model>``
    once before a final probe.

    Returns ``(ok, detail)`` — ``detail`` is a human-readable reason suitable
    for a log line.
    """
    try:
        import ollama  # noqa: F401
    except ImportError:
        return False, "ollama python client is not importable"
    if shutil.which("ollama") is None:
        return False, "ollama CLI not found on PATH"

    def _probe() -> bool:
        try:
            r = subprocess.run(
                ["ollama", "run", model, "reply with the single word: ready"],
                capture_output=True,
                text=True,
                timeout=probe_timeout,
            )
        except subprocess.TimeoutExpired:
            return False
        return r.returncode == 0 and bool(r.stdout.strip())

    if _probe():
        return True, f"{model} responsive"
    if not pull_if_missing:
        return False, f"{model} not responsive"

    logger.warning("granite model %s not responsive — attempting pull...", model)
    try:
        subprocess.run(
            ["ollama", "pull", model],
            check=True,
            capture_output=True,
            text=True,
            timeout=pull_timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out pulling {model}"
    except subprocess.CalledProcessError as e:
        return False, f"failed to pull {model}: {(e.stderr or '').strip() or e}"

    if _probe():
        return True, f"{model} pulled and responsive"
    return False, f"{model} still not responsive after pull"


# The prefix-token convention. The PM persona body (in
# .claude/commands/granite/prime-pm-role.md) primes PM to begin
# every output with one of these three literal tokens on a line of
# its own. The classifier's `classify_pm_prefix` parses the first
# line; if no token is present, the result is `unknown` and the
# container logs a compliance miss.
#
# The strict regex requires the token to be the entire content of
# its line (no trailing text, allowed trailing whitespace). It is
# matched against the **first non-empty line** of PM's tail using
# re.match (which anchors at the start of the line).
PREFIX_TOKEN_RE = re.compile(r"^\[/(dev|user|complete)(?::([a-z0-9_-]+))?\]\s*$")
PREFIX_TOKEN_FALLBACK_RE = re.compile(r"\[/(dev|user|complete)(?::([a-z0-9_-]+))?\]")

# Destination: which PTY the routed output goes to.
Destination = Literal["dev", "user", "complete", "unknown"]


@dataclass
class ClassificationResult:
    """The classifier's routing decision for a PM turn.

    `destination` is the routing target. `payload` is the verbatim
    text following the prefix token — for `dev` and `user`, the
    instruction or user-facing message extracted from PM's transcript;
    for `complete`, the trailing one-sentence summary from PM; for
    `unknown`, an empty string (the container surfaces a compliance
    miss to the results JSON).

    `compliance_miss` is True iff the PM tail had no prefix token
    on its first line. The container uses this to compute the
    compliance rate; the results doc reports it.

    `harness` is the optional builder harness name from the prefix
    token (e.g. ``[/dev:pi]`` → ``harness="pi"``). ``None`` means
    the default claude harness. Only populated when destination is
    ``"dev"``; always ``None`` for ``"user"`` / ``"complete"`` /
    ``"unknown"``.
    """

    destination: Destination
    payload: str
    compliance_miss: bool
    raw_first_line: str
    harness: str | None = None


# ---------------------------------------------------------------------------
# Classification (deterministic regex parse, no ollama on the routing path)
# ---------------------------------------------------------------------------


def classify_pm_prefix(pm_tail: str) -> ClassificationResult:
    """Classify PM's tail by parsing the first line for a prefix token.

    The PM persona body primes PM to begin every output with one of:
      `[/dev]` — followed by the developer instruction
      `[/user]` — followed by the user-facing message
      `[/complete]` — followed by a one-sentence completion summary

    The first line is parsed with `PREFIX_TOKEN_RE` (strict — token
    must be the only content on the line). If the strict regex
    doesn't match, a fallback regex (PREFIX_TOKEN_FALLBACK_RE) is
    tried on the first 200 chars. If neither matches, the result
    is `unknown` and `compliance_miss=True`.

    The classification is **stateless** — no call history, no PM
    persona context, no ollama call. It is a regex parse.

    Note: the anchored-frame path (⏺/● bullet-matched captures) was
    removed; the container now reads PM's text directly from the JSONL
    transcript rather than from painted PTY frame captures.
    """
    # Strip ANSI escape sequences before parsing. The PTY layer already
    # strips them in read_until_idle, but cursor-positioning escapes
    # can survive and corrupt the first-line check (e.g. the TUI
    # re-renders the status bar after a response, leaving orphan CSI
    # codes ahead of [/dev]). We delegate to the upstream
    # `_strip_ansi` helper so the classifier and the PTY driver stay
    # in lockstep (CSI + OSC + keypad mode all stripped).
    from agent.granite_container.pty_driver import _strip_ansi

    pm_tail = _strip_ansi(pm_tail)

    # Find the first non-empty line.
    first_line = ""
    for line in pm_tail.splitlines():
        if line.strip():
            first_line = line
            break

    if not first_line:
        return ClassificationResult(
            destination="unknown",
            payload="",
            compliance_miss=True,
            raw_first_line="",
            harness=None,
        )

    m = PREFIX_TOKEN_RE.match(first_line)
    if m:
        token = m.group(1)
        harness = m.group(2)  # None when no ":name" suffix (bare [/dev])
        # The payload is the rest of the tail (the lines after the
        # prefix token), stripped of leading/trailing whitespace.
        # For complete, the trailing one-sentence summary is the
        # payload; for dev/user, the developer instruction / user
        # message is the payload.
        rest = pm_tail[pm_tail.index(first_line) + len(first_line) :].strip()
        return ClassificationResult(
            destination=token,  # type: ignore[arg-type]
            payload=rest,
            compliance_miss=False,
            raw_first_line=first_line,
            harness=harness,
        )

    # Strict match failed; try a more permissive fallback. PM may
    # have included the token mid-line or with light surrounding
    # text (e.g., "output: [/dev:pi] please ...") — that's a
    # compliance miss by the strict definition but a correct
    # classification. The fallback's `compliance_miss=True` is
    # the right signal: the persona is not strictly enforcing the
    # convention.
    fallback = PREFIX_TOKEN_FALLBACK_RE.search(pm_tail[:200])
    if fallback:
        harness = fallback.group(2)  # group(2) is the harness name; group(1) is destination
        return ClassificationResult(
            destination=fallback.group(1),  # type: ignore[arg-type]
            payload=pm_tail.strip(),
            compliance_miss=True,
            raw_first_line=first_line,
            harness=harness,
        )

    return ClassificationResult(
        destination="unknown",
        payload="",
        compliance_miss=True,
        raw_first_line=first_line,
        harness=None,
    )
