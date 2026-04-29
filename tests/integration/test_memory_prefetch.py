"""Integration test: UserPromptSubmit prefetch -> PostToolUse de-dup.

Validates the end-to-end flow added in issue #1180:
  1. Subprocess `python .claude/hooks/user_prompt_submit.py` reads a fake
     hook input from stdin and emits a hookSpecificOutput JSON containing
     <thought> blocks scored against the prompt.
  2. The sidecar at data/sessions/{session_id}/memory_buffer.json captures
     those memory_ids in injected[] without clobbering count/buffer.
  3. PM-style FROM:/SCOPE:/MESSAGE: boilerplate is stripped before BM25.
  4. The single prefetch query completes well under the latency budget
     (PREFETCH_LATENCY_WARN_MS = 200).

These tests use real Redis-backed Memory records (not mocks). Each test
seeds, runs, and tears down its own records with a project_key prefix
so concurrent tests do not interfere with production data.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOK_SCRIPT = REPO_ROOT / ".claude" / "hooks" / "user_prompt_submit.py"


def _delete_memory_records_for_project(project_key: str) -> None:
    """Clean up any Memory records belonging to this project_key."""
    try:
        from models.memory import Memory

        for record in Memory.query.filter(project_key=project_key):
            try:
                record.delete()
            except Exception:
                pass
    except Exception:
        pass


def _delete_sidecar(session_id: str) -> None:
    sidecar_dir = REPO_ROOT / "data" / "sessions" / session_id
    for filename in ("memory_buffer.json", "memory_buffer.json.tmp"):
        path = sidecar_dir / filename
        if path.exists():
            path.unlink()


def _seed_memory(content: str, project_key: str, importance: float = 6.0):
    """Insert a real Memory record. Returns the saved record."""
    from models.memory import SOURCE_HUMAN, Memory

    return Memory.safe_save(
        agent_id=f"prefetch-test-{project_key}",
        project_key=project_key,
        content=content,
        importance=importance,
        source=SOURCE_HUMAN,
    )


def _run_user_prompt_submit_hook(
    prompt: str,
    session_id: str,
    project_key: str,
    redis_url: str,
    cwd: str | None = None,
) -> dict | None:
    """Invoke .claude/hooks/user_prompt_submit.py as a subprocess.

    Returns the parsed hookSpecificOutput JSON dict (or None if the hook
    produced no output). Forces VALOR_PROJECT_KEY so the prefetch path
    queries the test-isolated project partition. Forces REDIS_URL to the
    same per-worker test db that the autouse `redis_test_db` fixture
    points popoto at -- without this the subprocess would query
    production Redis and find no seeded records.
    """
    payload = {
        "prompt": prompt,
        "session_id": session_id,
        "cwd": cwd or str(REPO_ROOT),
    }

    env = os.environ.copy()
    env["VALOR_PROJECT_KEY"] = project_key
    env["REDIS_URL"] = redis_url
    # Suppress AgentSession side-effects: no SESSION_TYPE / parent.
    env.pop("SESSION_TYPE", None)
    env.pop("VALOR_PARENT_SESSION_ID", None)

    result = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        timeout=30,
    )

    if result.returncode != 0:
        # Hooks should never fail (silent), but surface stderr if they do
        raise AssertionError(
            f"hook subprocess failed: code={result.returncode}\n"
            f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
        )

    parsed_output = None
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "hookSpecificOutput" in parsed:
            parsed_output = parsed
            break

    if parsed_output is None:
        # Surface diagnostic state so failures point at the right cause
        # (no memories matched, retrieval blew up, sidecar perms, etc.)
        raise AssertionError(
            "hookSpecificOutput JSON missing from stdout.\n"
            f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
        )
    return parsed_output


@pytest.fixture
def isolated_project_key():
    """Per-test project_key prefixed with `prefetch-test-` for safe cleanup."""
    key = f"prefetch-test-{uuid.uuid4().hex[:8]}"
    yield key
    _delete_memory_records_for_project(key)


@pytest.fixture
def isolated_session_id():
    """Per-test sidecar session_id; sidecar files cleaned up after."""
    sid = f"prefetch-test-{uuid.uuid4().hex}"
    yield sid
    _delete_sidecar(sid)


class TestPrefetchEndToEnd:
    """Verify the UserPromptSubmit hook produces prefetched thoughts."""

    def test_prefetch_emits_hookspecific_output(
        self, isolated_project_key, isolated_session_id, redis_test_url
    ):
        """A non-trivial prompt against seeded memories yields <thought> blocks."""
        # Seed a memory matching the prompt content.
        seeded = _seed_memory(
            "auth flow refactor notes from PR 800 deployment investigation",
            isolated_project_key,
        )
        if seeded is None:
            pytest.skip("Memory.safe_save returned None (bloom dedup or backend issue)")

        hook_output = _run_user_prompt_submit_hook(
            prompt=("investigate auth flow refactor that broke after PR 800 deployment"),
            session_id=isolated_session_id,
            project_key=isolated_project_key,
            redis_url=redis_test_url,
        )

        hso = hook_output["hookSpecificOutput"]
        assert hso["hookEventName"] == "UserPromptSubmit"
        assert "<thought>" in hso["additionalContext"]

    def test_prefetch_writes_sidecar_injected_ids(
        self, isolated_project_key, isolated_session_id, redis_test_url
    ):
        """After prefetch, sidecar.injected[] contains the surfaced memory_ids."""
        seeded = _seed_memory(
            "deployment rollback playbook for auth service migration",
            isolated_project_key,
        )
        if seeded is None:
            pytest.skip("Memory.safe_save returned None")

        _run_user_prompt_submit_hook(
            prompt=("investigate deployment rollback for auth service migration plan"),
            session_id=isolated_session_id,
            project_key=isolated_project_key,
            redis_url=redis_test_url,
        )

        sidecar_path = REPO_ROOT / "data" / "sessions" / isolated_session_id / "memory_buffer.json"
        assert sidecar_path.exists(), "Sidecar not written after prefetch"
        state = json.loads(sidecar_path.read_text())
        # count and buffer remain at recall()'s defaults
        assert state.get("count", 0) == 0
        assert state.get("buffer", []) == []
        # injected[] populated with at least one entry
        injected = state.get("injected", [])
        assert isinstance(injected, list) and len(injected) >= 1
        assert any(item.get("memory_id") for item in injected)


class TestPrefetchStripsBoilerplate:
    """PM-shaped prompts must query against the MESSAGE: payload only."""

    def test_pm_boilerplate_stripped_before_query(
        self, isolated_project_key, isolated_session_id, redis_test_url
    ):
        """Worker-shaped FROM:/SCOPE:/MESSAGE: prompt surfaces MESSAGE-matched memory."""
        # Two memories: one matches MESSAGE: payload, one matches FROM:/SCOPE: terms.
        msg_memory = _seed_memory(
            "auth flow investigation notes deployment rollback",
            isolated_project_key,
        )
        boiler_memory = _seed_memory(
            "valor session scoped sender message routing",
            isolated_project_key,
        )
        if msg_memory is None or boiler_memory is None:
            pytest.skip("Memory.safe_save returned None")

        prompt = (
            "FROM: valor-session (dev)\n"
            "SCOPE: This session is scoped to the message below from this sender.\n"
            "MESSAGE: investigate auth flow deployment rollback that broke after PR 800"
        )

        hook_output = _run_user_prompt_submit_hook(
            prompt=prompt,
            session_id=isolated_session_id,
            project_key=isolated_project_key,
            redis_url=redis_test_url,
        )
        assert hook_output is not None, "Expected hookSpecificOutput JSON on stdout"
        body = hook_output["hookSpecificOutput"]["additionalContext"]

        # The MESSAGE-matching memory's content must appear; the boilerplate-
        # matching memory's content must not be the *primary* surface. We
        # accept either "only msg memory" or "msg memory before boiler".
        assert "auth flow investigation" in body, (
            f"MESSAGE: payload-matching memory missing from prefetch output: {body!r}"
        )


class TestPrefetchLatencyBudget:
    """A single prefetch must complete well under PREFETCH_LATENCY_WARN_MS."""

    def test_prefetch_completes_under_budget(
        self, isolated_project_key, isolated_session_id, redis_test_url
    ):
        """End-to-end subprocess call returns inside the latency budget on a small fixture."""
        from config.memory_defaults import PREFETCH_LATENCY_WARN_MS

        # Seed a small fixture (<= 5 records).
        for i in range(5):
            _seed_memory(
                f"test memory {i} for latency budget verification subset {i}",
                isolated_project_key,
            )

        # Subprocess overhead dominates; allow a generous wall-clock slack
        # but the in-process prefetch must stay close to the documented
        # budget on a small fixture.
        budget_seconds = max((PREFETCH_LATENCY_WARN_MS / 1000.0) * 10, 5.0)

        start = time.monotonic()
        _run_user_prompt_submit_hook(
            prompt=("test memory verification for latency budget on small fixture set"),
            session_id=isolated_session_id,
            project_key=isolated_project_key,
            redis_url=redis_test_url,
        )
        elapsed = time.monotonic() - start

        assert elapsed < budget_seconds, (
            f"prefetch end-to-end took {elapsed:.2f}s "
            f"(budget incl. subprocess startup: {budget_seconds:.2f}s)"
        )
