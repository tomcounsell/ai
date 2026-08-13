# Feature markers are auto-applied by the root tests/conftest.py

import subprocess
import sys
from pathlib import Path

import pytest


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test",
            *args,
        ],
        cwd=str(root),
        check=True,
        capture_output=True,
        timeout=30,
    )


@pytest.fixture
def cross_lane_repo():
    """Factory for the #2689 cross-lane reproducer repo, shared by the hook-validator tests.

    Call it as ``cross_lane_repo(root, anchor_body, other_lane_body)``. It builds
    a repo whose ``docs/plans/`` holds a TRACKED anchor doc plus another lane's
    untracked, deficient, newest-by-mtime plan, and returns ``root``.

    The tracked anchor is load-bearing, not decoration. With no tracked file
    under ``docs/plans/``, ``git status --porcelain docs/plans/`` collapses the
    whole directory to a single ``?? docs/plans/`` line whose path does not end
    in ``.md``. The deleted guessers' suffix filter dropped that line, returned
    None, and the validator exited 0 looking innocent — so a fixture without the
    anchor passes against the *unfixed* code and proves nothing (#2689, and the
    "Data prerequisite" note under Race 1 of the plan).

    Verified: against ``git show main:…`` each ported validator exits 2 on this
    fixture given a payload naming ``docs/features/mine.md``, and the
    anchor-less variant exits 0.

    Each test module keeps its own ``run_hook`` helper — that duplication is a
    deliberate choice recorded in the plan's Technical Approach, because a
    shared subprocess harness couples the test files to each other. Only the
    fixture builder is shared.
    """

    def _make(root: Path, anchor_body: str, other_lane_body: str) -> Path:
        plans = root / "docs" / "plans"
        plans.mkdir(parents=True, exist_ok=True)
        (plans / "anchor.md").write_text(anchor_body)
        _git(root, "init", "-q")
        _git(root, "add", "docs/plans/anchor.md")
        _git(root, "commit", "-q", "-m", "anchor")
        # The other lane's in-progress plan: untracked, deficient, newest by mtime.
        (plans / "zzz-other-lane.md").write_text(other_lane_body)
        return root

    return _make


@pytest.fixture(autouse=True)
def _no_live_embedding_provider():
    """Null out popoto's global embedding provider for every unit test.

    Importing ``models.memory`` (directly or via ``tools.memory_search``)
    runs ``config.memory_defaults.apply_defaults()``, which registers a live
    ``OllamaEmbeddingProvider`` as popoto's process-global default when Ollama
    is reachable. From then on every ``Memory.save()`` makes a real HTTP POST
    to ``localhost:11434`` with a 5s read timeout.

    Serially that is fast enough to go unnoticed, but under
    ``pytest -n auto`` ten workers hammer Ollama concurrently and the embed
    calls time out. The RuntimeError propagates out of ``save()`` (or turns
    ``safe_save()`` into a ``None`` return), failing any test that saves a
    Memory — the classic "passes with -n0, fails under xdist" flake
    (test_memory_model, test_memory_timeline, test_memory_ingestion,
    test_daily_log_aggregator).

    Unit tests must not depend on a live Ollama. With the provider set to
    ``None``, ``EmbeddingField.on_save`` skips embedding cleanly (and stops
    writing ``.npy`` files under ``~/.popoto/content`` from unit runs).
    Tests that exercise embedding behavior install their own stub via
    ``set_default_provider(...)`` or patch ``get_default_provider`` — both
    override this fixture within the test, and the teardown restore still
    puts the original process-global provider back afterward.

    Gated on the module already being imported so pure-logic tests keep the
    zero-import fast path (mirrors the ``redis_test_db`` gate in
    tests/conftest.py).
    """
    embedding_field = sys.modules.get("popoto.fields.embedding_field")
    if embedding_field is None:
        yield
        return

    original = embedding_field.get_default_provider()
    embedding_field.set_default_provider(None)
    try:
        yield
    finally:
        embedding_field.set_default_provider(original)
