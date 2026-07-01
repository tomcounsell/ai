"""Shared ollama-substrate env construction for the granite harness.

Substrate B (the ollama-backed real ``claude`` E2E) and the golden-recorder
both need to point the real ``claude`` binary at ollama's Anthropic-compatible
endpoint. Per the ollama integration docs that means setting three env vars::

    ANTHROPIC_BASE_URL=http://localhost:11434
    ANTHROPIC_AUTH_TOKEN=ollama
    ANTHROPIC_API_KEY=""

The load-bearing subtlety (plan Research + critique BLOCKER): production
``PTYDriver._build_env`` forwards ``CLAUDE_CODE_OAUTH_TOKEN`` into the child
when it is present, and ``PTYDriver.spawn`` applies the per-session env overlay
with ``env.update()`` — which only ADDS/overwrites keys, never removes one.
Overlaying just the three ollama vars therefore leaves a forwarded OAuth token
in place; on a machine already logged in for production granite that reproduces
the PR #1612 "issue with the selected model" failure (OAuth login present AND
``ANTHROPIC_BASE_URL`` pointed at ollama simultaneously) and silently
invalidates the canary.

So both Substrate B and the recorder MUST explicitly
``env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)`` alongside setting the three ollama
vars, and MUST assert no OAuth token leaks into the child env before ``spawn``.
Those two operations live here so there is exactly one source of truth.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request
from collections.abc import Mapping

OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_AUTH_TOKEN = "ollama"
OAUTH_TOKEN_VAR = "CLAUDE_CODE_OAUTH_TOKEN"


def build_ollama_child_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build the child env that points the real ``claude`` binary at ollama.

    Starts from ``base`` (defaults to a copy of ``os.environ``), sets the three
    ollama vars, and — the blocker fix — pops ``CLAUDE_CODE_OAUTH_TOKEN`` so no
    forwarded OAuth credential can coexist with the ollama base URL.
    """
    env = dict(base if base is not None else os.environ)
    env["ANTHROPIC_BASE_URL"] = OLLAMA_BASE_URL
    env["ANTHROPIC_AUTH_TOKEN"] = OLLAMA_AUTH_TOKEN
    env["ANTHROPIC_API_KEY"] = ""
    # Blocker fix: env.update()-based overlays never REMOVE a key, so the pop
    # must happen here where the final child env is assembled.
    env.pop(OAUTH_TOKEN_VAR, None)
    return env


def assert_no_oauth_leak(env: Mapping[str, str]) -> None:
    """Raise if a ``CLAUDE_CODE_OAUTH_TOKEN`` survives into the child env.

    Called on the fully-assembled child env immediately before ``spawn`` in
    both Substrate B and the recorder. A surviving token is the exact PR #1612
    reproduction; failing loud here keeps the ollama canary honest.
    """
    if OAUTH_TOKEN_VAR in env:
        raise AssertionError(
            f"{OAUTH_TOKEN_VAR} leaked into the ollama child env — this "
            "reproduces the PR #1612 'issue with the selected model' failure "
            "(OAuth login + ollama base URL at once). Pop it before spawn()."
        )


def _list_ollama_models(timeout_s: float = 10.0) -> list[str]:
    """Return the tags ollama currently serves, or ``[]`` if unreachable."""
    try:
        tags = json.loads(
            urllib.request.urlopen(f"{OLLAMA_BASE_URL}/api/tags", timeout=timeout_s).read()
        )
    except Exception:
        return []
    return [m["name"] for m in tags.get("models", [])]


def pick_ollama_model(names: list[str] | None = None) -> str | None:
    """Pick a tool-capable ollama model tag for the ``claude`` substrate.

    Claude Code always sends tool definitions, so the substrate model MUST
    support tools (``gemma*`` chat models return HTTP 400
    "does not support tools" and are unusable here — observed in Task 0).
    We prefer, in order: a qwen coding tag, gpt-oss, then any non-embedding
    tag. Returns ``None`` when nothing usable is served.
    """
    names = _list_ollama_models() if names is None else names
    if not names:
        return None
    # gemma/embedding tags are known-unusable for the tool-carrying claude
    # substrate; drop them before ranking.
    usable = [n for n in names if not n.startswith(("gemma", "nomic"))]
    if not usable:
        return None
    for prefix in ("qwen", "gpt-oss", "granite"):
        pick = next((n for n in usable if n.startswith(prefix)), None)
        if pick:
            return pick
    return usable[0]


def ollama_substrate_reachable(model: str | None = None, probe_timeout_s: float = 240.0) -> bool:
    """Mirror ``test_granite_container_loop._model_reachable`` for the ollama substrate.

    True only when the ``claude`` binary is on PATH AND a tool-capable ollama
    model answers a ``--print`` ping over the OAuth-stripped ollama env. The
    ping runs in a scratch cwd so a large project ``CLAUDE.md`` cannot blow the
    prefill budget (Task 0 finding: repo-context prefill pushed a warm model
    past 150s; a scratch cwd returns in ~70s).
    """
    if not shutil.which("claude"):
        return False
    pick = model or pick_ollama_model()
    if not pick:
        return False
    import tempfile

    env = build_ollama_child_env()
    try:
        assert_no_oauth_leak(env)
    except AssertionError:
        return False
    try:
        with tempfile.TemporaryDirectory() as scratch:
            r = subprocess.run(
                [
                    "claude",
                    "--permission-mode",
                    "bypassPermissions",
                    "--model",
                    pick,
                    "--print",
                    "Reply with exactly the word PONG and nothing else.",
                ],
                cwd=scratch,
                env=env,
                capture_output=True,
                text=True,
                timeout=probe_timeout_s,
            )
        return r.returncode == 0
    except Exception:
        return False
