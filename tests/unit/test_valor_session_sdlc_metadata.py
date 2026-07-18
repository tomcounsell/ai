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
    assert captured, "cmd_create succeeded without calling _push_agent_session"

    assert captured.get("classification_type") == "sdlc"
    assert captured.get("issue_url") == "https://github.com/tomcounsell/ai/issues/2140"


def test_create_explicit_issue_url_preserves_its_repository(monkeypatch, tmp_path):
    captured: dict = {}
    _stub_create(monkeypatch, tmp_path, captured)

    message = "Run /sdlc https://github.com/example/project/issues/99"
    assert valor_session.cmd_create(_make_args(message=message, slug="sdlc-99")) == 0
    assert captured, "cmd_create succeeded without calling _push_agent_session"

    assert captured.get("classification_type") == "sdlc"
    assert captured.get("issue_url") == "https://github.com/example/project/issues/99"


def test_create_non_sdlc_teammate_session_leaves_metadata_unset(monkeypatch, tmp_path):
    captured: dict = {}
    _stub_create(monkeypatch, tmp_path, captured)

    assert (
        valor_session.cmd_create(
            _make_args(role="teammate", slug=None, message="Draft a status update")
        )
        == 0
    )
    assert captured, "cmd_create succeeded without calling _push_agent_session"

    assert captured.get("classification_type") is None
    assert captured.get("issue_url") is None


# ---------------------------------------------------------------------------
# _derive_sdlc_metadata — pure-function edge cases (#2140 AC1, Failure Path)
# ---------------------------------------------------------------------------


def test_derive_metadata_empty_message_is_none():
    assert valor_session._derive_sdlc_metadata("", None) == (None, None)


def test_derive_metadata_none_message_is_none():
    # Guards `not message` before any regex — no AttributeError on None.
    assert valor_session._derive_sdlc_metadata(None, None) == (None, None)


def test_derive_metadata_issue_ref_with_none_config_classifies_without_url():
    # C1 (critique): `(project_config or {})` guard must not raise on None.
    cls, url = valor_session._derive_sdlc_metadata("work on issue #2140", None)
    assert cls == "sdlc"
    assert url is None


def test_derive_metadata_issue_ref_missing_github_key_classifies_without_url():
    cls, url = valor_session._derive_sdlc_metadata(
        "work on issue #2140", {"working_directory": "/tmp"}
    )
    assert cls == "sdlc"
    assert url is None


def test_derive_metadata_issue_ref_builds_url_from_project_github():
    cls, url = valor_session._derive_sdlc_metadata(
        "work on issue #2140", {"github": {"org": "tomcounsell", "repo": "ai"}}
    )
    assert cls == "sdlc"
    assert url == "https://github.com/tomcounsell/ai/issues/2140"


def test_derive_metadata_explicit_url_wins_over_project_config():
    cls, url = valor_session._derive_sdlc_metadata(
        "fix https://github.com/example/project/issues/99",
        {"github": {"org": "tomcounsell", "repo": "ai"}},
    )
    assert cls == "sdlc"
    assert url == "https://github.com/example/project/issues/99"


def test_derive_metadata_conversational_message_is_none():
    cls, url = valor_session._derive_sdlc_metadata(
        "please summarize yesterday's standup", {"github": {"org": "x", "repo": "y"}}
    )
    assert cls is None
    assert url is None


def test_derive_metadata_bare_pr_reference_is_not_a_trigger():
    # Detection is anchored to issue references only (plan Rabbit Holes). A bare
    # PR reference cannot identify an issue and must not classify.
    cls, url = valor_session._derive_sdlc_metadata(
        "take a look at pr #99", {"github": {"org": "x", "repo": "y"}}
    )
    assert cls is None
    assert url is None


def test_derive_metadata_no_false_positive_on_pr_substring_prose():
    # Regression: an unbounded `pr` matcher misclassified prose like "compr 5"
    # and "expr 12" as SDLC. Anchoring to issue refs removes the hazard.
    for prose in ("the compr 5 metric looks off", "expr 12 evaluation failed"):
        cls, url = valor_session._derive_sdlc_metadata(prose, {"github": {"org": "x", "repo": "y"}})
        assert cls is None, f"prose misclassified as SDLC: {prose!r}"
        assert url is None


# ---------------------------------------------------------------------------
# Output-router auto-continue parity for CLI-created SDLC sessions (#2140 AC3)
# ---------------------------------------------------------------------------


def test_cli_sdlc_classification_routes_nudge_continue():
    """The classification the CLI now derives drives the output router's
    auto-continue rule (agent/output_router.py:158). A turn-end status update
    on an eng+sdlc session nudges instead of delivering — i.e. the pipeline
    keeps moving rather than pausing as if awaiting a human."""
    from agent.output_router import MAX_NUDGE_COUNT, determine_delivery_action

    cls, _ = valor_session._derive_sdlc_metadata(
        "Run the SDLC pipeline for issue #2140",
        {"github": {"org": "tomcounsell", "repo": "ai"}},
    )
    action = determine_delivery_action(
        msg="PLAN stage complete. Moving to CRITIQUE.",
        stop_reason="end_turn",
        auto_continue_count=0,
        max_nudge_count=MAX_NUDGE_COUNT,
        session_status="running",
        session_type="eng",
        classification_type=cls,
    )
    assert action == "nudge_continue"


# ---------------------------------------------------------------------------
# Enqueue-time stage_states init on the CLI creation path (#2140 AC2, AC4)
# ---------------------------------------------------------------------------


def test_enqueue_stage_states_init_fires_for_cli_sdlc_classification():
    """AC4: the ``classification_type='sdlc'`` the CLI now derives satisfies the
    enqueue-time ``stage_states`` init gate (``agent/agent_session_queue.py:360``,
    ``classification_type == ClassificationType.SDLC``) and ``PipelineStateMachine``
    populates ``stage_states`` with ISSUE ready — so the dashboard shows stage
    progression from enqueue for CLI-created SDLC sessions rather than ``stages: []``.
    """
    from unittest.mock import MagicMock

    from agent.pipeline_state import PipelineStateMachine
    from config.enums import ClassificationType

    cli_classification, _ = valor_session._derive_sdlc_metadata(
        "Run the SDLC pipeline for issue #2140",
        {"github": {"org": "tomcounsell", "repo": "ai"}},
    )
    # Gate parity: the literal string the CLI stores must compare equal to the
    # enum the queue checks (StrEnum — see config/enums.py). If this ever
    # regresses, the init block silently skips and the dashboard goes blank.
    assert cli_classification == ClassificationType.SDLC

    # Init block behavior (mirrors _push_agent_session lines 358-375): a fresh
    # pending session with empty stage_states gets initialized and persisted.
    session = MagicMock()
    session.session_id = "test-cli-sdlc-2140"
    session.stage_states = None  # real None so _save's merge/reload is well-typed
    session.save = MagicMock()

    sm = PipelineStateMachine(session)
    assert not session.stage_states  # empty before init, matching the gate's guard
    sm._save()

    assert session.save.called, "stage_states init must persist via session.save()"
    assert session.stage_states, "stage_states must be populated after init"
    assert sm.states["ISSUE"] == "ready"
