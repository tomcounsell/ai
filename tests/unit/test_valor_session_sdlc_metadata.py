"""Regression tests for SDLC metadata on ``valor-session create`` (#2140)."""

from __future__ import annotations

import argparse
from pathlib import Path

from tools import valor_session


def _make_args(**overrides) -> argparse.Namespace:
    defaults = {
        "role": "eng",
        "message": "Run the SDLC pipeline for issue #2140",
        "chat_id": "123",
        "parent": None,
        "model": None,
        "slug": "sdlc-2140",
        "project_key": "valor",
        "json": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _stub_create(monkeypatch, tmp_path: Path, captured: dict) -> None:
    import agent.agent_session_queue as queue

    async def fake_push(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(queue, "_push_agent_session", fake_push)
    monkeypatch.setattr(valor_session, "_check_worker_health", lambda: (True, 1))
    monkeypatch.setattr(
        valor_session,
        "_resolve_project_working_directory",
        lambda key: (
            tmp_path,
            {
                "working_directory": str(tmp_path),
                "github": {"org": "tomcounsell", "repo": "ai"},
            },
        ),
    )
    monkeypatch.setattr("agent.worktree_manager.get_or_create_worktree", lambda *_: tmp_path)
    monkeypatch.setattr("agent.worktree_manager._validate_slug", lambda *_: None)


def test_create_issue_session_sets_sdlc_metadata(monkeypatch, tmp_path):
    captured: dict = {}
    _stub_create(monkeypatch, tmp_path, captured)

    assert valor_session.cmd_create(_make_args()) == 0

    assert captured["classification_type"] == "sdlc"
    assert captured["issue_url"] == "https://github.com/tomcounsell/ai/issues/2140"


def test_create_explicit_issue_url_preserves_its_repository(monkeypatch, tmp_path):
    captured: dict = {}
    _stub_create(monkeypatch, tmp_path, captured)

    message = "Run /sdlc https://github.com/example/project/issues/99"
    assert valor_session.cmd_create(_make_args(message=message, slug="sdlc-99")) == 0

    assert captured["classification_type"] == "sdlc"
    assert captured["issue_url"] == "https://github.com/example/project/issues/99"


def test_create_non_sdlc_teammate_session_leaves_metadata_unset(monkeypatch, tmp_path):
    captured: dict = {}
    _stub_create(monkeypatch, tmp_path, captured)

    assert valor_session.cmd_create(
        _make_args(role="teammate", slug=None, message="Draft a status update")
    ) == 0

    assert captured["classification_type"] is None
    assert captured["issue_url"] is None
