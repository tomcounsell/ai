"""Tests for utils.api_keys — self-healing empty-cache behavior (#1899).

Sentry reported "No Anthropic API key found for classification" persisting for
the lifetime of a process after a single transient resolution failure. Root
cause: the module cached an empty resolution just like a real key, so a
one-time startup race (env/.env not yet readable) poisoned every subsequent
call. These tests pin the fix: only a truthy key is cached; an absent
resolution returns None without caching, so the next call re-reads env/.env.
"""

import inspect
from pathlib import Path

import utils.api_keys as api_keys_module
from utils.api_keys import get_anthropic_api_key


def _reset_cache():
    api_keys_module._cached_anthropic_key = None


def _force_no_env_files_found(monkeypatch):
    """Make every candidate .env path resolve to nonexistent, regardless of
    what's actually on disk on the machine running the tests.
    """
    monkeypatch.setattr(Path, "exists", lambda self: False)


def test_absent_resolution_returns_none_and_does_not_cache(monkeypatch):
    """No env var and no .env files found -> returns None, cache stays empty
    (never poisoned with "")."""
    _reset_cache()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _force_no_env_files_found(monkeypatch)

    result = get_anthropic_api_key()

    assert result is None
    assert api_keys_module._cached_anthropic_key is None


def test_empty_cache_self_heals_on_next_call(monkeypatch):
    """A prior empty/no-key resolution must not poison later calls: once the
    env var becomes available, the very next call must pick it up instead of
    returning a stale empty result.
    """
    _reset_cache()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _force_no_env_files_found(monkeypatch)

    first = get_anthropic_api_key()
    assert first is None

    # Simulate the startup race settling: env var becomes readable.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key-123")
    second = get_anthropic_api_key()

    assert second == "sk-ant-test-key-123"


def test_truthy_key_is_cached(monkeypatch):
    """A populated key is cached and returned on subsequent calls unchanged
    (no behavior change vs. before the fix)."""
    _reset_cache()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-cached-key")

    first = get_anthropic_api_key()
    assert first == "sk-ant-cached-key"
    assert api_keys_module._cached_anthropic_key == "sk-ant-cached-key"

    # Even if env changes afterward, the cached truthy value wins.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-different-key")
    second = get_anthropic_api_key()
    assert second == "sk-ant-cached-key"


def test_return_type_allows_none():
    """The resolver's annotation must be str | None (ref #1899)."""
    sig = inspect.signature(get_anthropic_api_key)
    assert sig.return_annotation in ("str | None", str | None)
