# Feature markers are auto-applied by the root tests/conftest.py

import sys

import pytest


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
