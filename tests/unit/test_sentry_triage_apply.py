"""Unit tests for the Sentry triage auto-action gate (tiers A/B/E).

Covers:
  - _apply_enabled() env var behavior
  - _TIER_ACTION_MAP correctness, especially the ignoreUntilEscalating quirk for tier B
  - _update_sentry_issue() success/failure paths and per-issue isolation
  - dry-run mode makes zero PUT calls
  - apply mode performs the right PUT per tier
  - missing/empty issue id short-circuits without calling Sentry
  - Telegram digest renders auto-actioned counts + failure detail
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
import requests

from config.settings import settings
from reflections import sentry_triage

# ---------------------------------------------------------------------------
# _apply_enabled
# ---------------------------------------------------------------------------


def test_apply_enabled_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SENTRY_TRIAGE_APPLY", raising=False)
    assert sentry_triage._apply_enabled() is False


def test_apply_enabled_zero_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_TRIAGE_APPLY", "0")
    assert sentry_triage._apply_enabled() is False


def test_apply_enabled_one_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_TRIAGE_APPLY", "1")
    assert sentry_triage._apply_enabled() is True


def test_apply_enabled_arbitrary_value_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    # Only the literal "1" enables. "true" / "yes" do not, by design.
    monkeypatch.setenv("SENTRY_TRIAGE_APPLY", "true")
    assert sentry_triage._apply_enabled() is False


# ---------------------------------------------------------------------------
# _TIER_ACTION_MAP
# ---------------------------------------------------------------------------


def test_tier_action_map_a() -> None:
    assert sentry_triage._TIER_ACTION_MAP["A"] == {"status": "ignored"}


def test_tier_action_map_b_includes_ignore_until_escalating() -> None:
    """Tier B MUST include statusDetails.ignoreUntilEscalating=True.

    Without it, Sentry defaults the substatus to 'archived_forever' rather
    than 'archived_until_escalating'. This is the central correctness bug
    the plan calls out.
    """
    payload = sentry_triage._TIER_ACTION_MAP["B"]
    assert payload["status"] == "ignored"
    assert payload["statusDetails"]["ignoreUntilEscalating"] is True


def test_tier_action_map_e() -> None:
    assert sentry_triage._TIER_ACTION_MAP["E"] == {"status": "resolved"}


def test_tier_action_map_no_cd_keys() -> None:
    # C and D are not auto-actioned -- they should be absent.
    assert "C" not in sentry_triage._TIER_ACTION_MAP
    assert "D" not in sentry_triage._TIER_ACTION_MAP


# ---------------------------------------------------------------------------
# _update_sentry_issue
# ---------------------------------------------------------------------------


def _mock_resp(status_code: int, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


def test_update_sentry_issue_success() -> None:
    with patch.object(sentry_triage.requests, "put") as mock_put:
        mock_put.return_value = _mock_resp(200, '{"ok":true}')
        ok, err = sentry_triage._update_sentry_issue(
            "ISSUE-123", "token-abc", {"status": "ignored"}
        )
    assert ok is True
    assert err is None
    mock_put.assert_called_once()
    call_kwargs = mock_put.call_args.kwargs
    assert call_kwargs["json"] == {"status": "ignored"}
    assert call_kwargs["headers"]["Authorization"] == "Bearer token-abc"
    assert call_kwargs["headers"]["Content-Type"] == "application/json"
    assert call_kwargs["timeout"] == settings.timeouts.http_request_s
    url = mock_put.call_args.args[0]
    assert url.endswith("/issues/ISSUE-123/")


def test_update_sentry_issue_non_2xx_returns_failure() -> None:
    with patch.object(sentry_triage.requests, "put") as mock_put:
        mock_put.return_value = _mock_resp(500, "boom internal error")
        ok, err = sentry_triage._update_sentry_issue("ISSUE-X", "tok", {"status": "resolved"})
    assert ok is False
    assert err is not None
    assert "500" in err
    assert "boom" in err


def test_update_sentry_issue_request_exception_returns_failure() -> None:
    with patch.object(sentry_triage.requests, "put") as mock_put:
        mock_put.side_effect = requests.ConnectionError("network down")
        ok, err = sentry_triage._update_sentry_issue("X", "tok", {})
    assert ok is False
    assert err is not None
    assert "network down" in err


def test_update_sentry_issue_missing_id_short_circuits() -> None:
    with patch.object(sentry_triage.requests, "put") as mock_put:
        ok, err = sentry_triage._update_sentry_issue("", "tok", {"status": "ignored"})
    assert ok is False
    assert err == "missing issue id"
    mock_put.assert_not_called()


# ---------------------------------------------------------------------------
# run_sentry_triage end-to-end (dry-run vs apply)
# ---------------------------------------------------------------------------


def _stub_issue(issue_id: str, short_id: str, title: str, count: int = 5) -> dict:
    """Build a Sentry-issue-shaped dict for the classifier.

    lastSeen/firstSeen are computed relative to now — the tier-E stale check
    (`_STALE_DAYS`) runs FIRST in `_classify_issue`, so a hardcoded date would
    silently reclassify every stub as E once it aged past 30 days (this
    actually happened; the original hardcoded 2026-05-25 dates went stale).
    Tests that need a stale issue override ``lastSeen`` explicitly.
    """
    now = datetime.now(UTC)
    return {
        "id": issue_id,
        "shortId": short_id,
        "title": title,
        "count": count,
        "lastSeen": (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "firstSeen": (now - timedelta(days=6)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "permalink": f"https://yudame.sentry.io/issues/{issue_id}/",
        # Carry the owned project id so the ownership filter (default allowlist)
        # passes these stubs through to classification.
        "project": {"id": sentry_triage._DEFAULT_OWNED_PROJECT_IDS[0], "slug": "test-proj"},
    }


def _patch_common(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub auth token + Telegram so triage runs without external side effects.

    Delta-state is stubbed to "state exists, nothing seen before" so every
    current Class C/D issue counts as new — tests that exercise the
    seed-silently and static-backlog paths override ``_load_seen_ids``.
    """
    monkeypatch.setattr(sentry_triage, "_get_auth_token", lambda: "test-token")
    monkeypatch.setattr(sentry_triage, "_get_org_slug", lambda: "test-org")
    monkeypatch.setattr(sentry_triage, "_send_telegram_notification", lambda _m: None)
    monkeypatch.setattr(sentry_triage, "_load_seen_ids", lambda: set())
    monkeypatch.setattr(sentry_triage, "_save_seen_ids", lambda _ids: None)


def test_dry_run_makes_zero_put_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SENTRY_TRIAGE_APPLY", raising=False)
    _patch_common(monkeypatch)

    issues = [
        _stub_issue("1", "PROJ-A1", "test_something exploded"),  # A
        _stub_issue("2", "PROJ-B1", "Connection refused upstream"),  # B
    ]
    monkeypatch.setattr(sentry_triage, "_fetch_unresolved_issues", lambda *_a: issues)

    with patch.object(sentry_triage.requests, "put") as mock_put:
        result = sentry_triage.run_sentry_triage()

    mock_put.assert_not_called()
    assert result["status"] == "ok"
    assert "[DRY RUN]" in result["summary"]


def test_apply_mode_puts_correct_payload_per_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_TRIAGE_APPLY", "1")
    _patch_common(monkeypatch)

    # One issue per auto-actionable tier. Stale E is forced via old lastSeen.
    stale_issue = _stub_issue("3", "PROJ-E1", "ancient regression", count=1)
    stale_issue["lastSeen"] = "2020-01-01T00:00:00Z"  # very old -> tier E

    issues = [
        _stub_issue("1", "PROJ-A1", "test_something exploded"),  # tier A
        _stub_issue("2", "PROJ-B1", "Connection refused upstream"),  # tier B
        stale_issue,  # tier E
    ]
    monkeypatch.setattr(sentry_triage, "_fetch_unresolved_issues", lambda *_a: issues)

    with patch.object(sentry_triage.requests, "put") as mock_put:
        mock_put.return_value = _mock_resp(200, "{}")
        result = sentry_triage.run_sentry_triage()

    # Verify three PUTs happened with the right payloads
    assert mock_put.call_count == 3
    payloads_by_id = {
        call.args[0].split("/issues/")[1].rstrip("/"): call.kwargs["json"]
        for call in mock_put.call_args_list
    }
    assert payloads_by_id["1"] == {"status": "ignored"}
    assert payloads_by_id["2"] == {
        "status": "ignored",
        "statusDetails": {"ignoreUntilEscalating": True},
    }
    assert payloads_by_id["3"] == {"status": "resolved"}

    # Summary should reflect apply mode, NOT dry-run.
    assert "[DRY RUN]" not in result["summary"]
    assert "auto-actioned" in result["summary"]


def test_per_issue_failure_does_not_abort_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Middle PUT raises -- the third issue must still be attempted."""
    monkeypatch.setenv("SENTRY_TRIAGE_APPLY", "1")
    _patch_common(monkeypatch)

    issues = [
        _stub_issue("1", "PROJ-A1", "test_first noise"),  # A
        _stub_issue("2", "PROJ-A2", "test_second noise"),  # A  -- this one fails
        _stub_issue("3", "PROJ-A3", "test_third noise"),  # A
    ]
    monkeypatch.setattr(sentry_triage, "_fetch_unresolved_issues", lambda *_a: issues)

    call_log: list[str] = []

    def fake_put(url: str, **kwargs):  # noqa: ANN001
        issue_id = url.split("/issues/")[1].rstrip("/")
        call_log.append(issue_id)
        if issue_id == "2":
            raise requests.ConnectionError("simulated middle failure")
        return _mock_resp(200, "{}")

    with patch.object(sentry_triage.requests, "put", side_effect=fake_put):
        result = sentry_triage.run_sentry_triage()

    # All three issues were attempted -- second's failure did not abort.
    assert call_log == ["1", "2", "3"]

    # Findings should contain a FAILED line for issue 2 and Auto-actioned for 1 and 3.
    findings_text = "\n".join(result["findings"])
    assert "FAILED: PROJ-A2" in findings_text
    assert "Auto-actioned: PROJ-A1" in findings_text
    assert "Auto-actioned: PROJ-A3" in findings_text


def test_digest_contains_auto_actioned_counts_and_failure_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Telegram digest should surface auto-action counts + failure details."""
    monkeypatch.setenv("SENTRY_TRIAGE_APPLY", "1")
    _patch_common(monkeypatch)

    issues = [
        _stub_issue("1", "PROJ-A1", "test_one noise"),  # A success
        _stub_issue("2", "PROJ-B1", "Connection refused"),  # B failure
    ]
    monkeypatch.setattr(sentry_triage, "_fetch_unresolved_issues", lambda *_a: issues)

    captured: dict[str, str] = {}

    def capture_tg(msg: str) -> None:
        captured["msg"] = msg

    monkeypatch.setattr(sentry_triage, "_send_telegram_notification", capture_tg)

    def fake_put(url: str, **kwargs):  # noqa: ANN001
        issue_id = url.split("/issues/")[1].rstrip("/")
        if issue_id == "2":
            return _mock_resp(500, "kaboom")
        return _mock_resp(200, "{}")

    with patch.object(sentry_triage.requests, "put", side_effect=fake_put):
        sentry_triage.run_sentry_triage()

    msg = captured.get("msg", "")
    assert "Auto-actioned" in msg
    assert "A=1/1" in msg
    assert "B=0/1" in msg
    assert "(1 failed)" in msg
    # The failure detail line should mention the short id
    assert "PROJ-B1" in msg
    # And the LIVE marker should be present
    assert "[LIVE" in msg


def test_dry_run_digest_marks_no_state_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SENTRY_TRIAGE_APPLY", raising=False)
    _patch_common(monkeypatch)

    # Pair the noise (A) issue with a tier-D issue so the run has something
    # needing attention and a notification fires; otherwise exception-only
    # delivery suppresses the message (see test_all_clear_suppressed below).
    issues = [
        _stub_issue("1", "PROJ-A1", "test_noise"),  # tier A
        _stub_issue("2", "PROJ-D1", "weird thing happened", count=3),  # tier D
    ]
    monkeypatch.setattr(sentry_triage, "_fetch_unresolved_issues", lambda *_a: issues)

    captured: dict[str, str] = {}
    monkeypatch.setattr(
        sentry_triage,
        "_send_telegram_notification",
        lambda m: captured.update(msg=m),
    )

    with patch.object(sentry_triage.requests, "put") as mock_put:
        sentry_triage.run_sentry_triage()

    mock_put.assert_not_called()
    msg = captured["msg"]
    assert "Would auto-action" in msg
    assert "[dry run — no Sentry state changes]" in msg


def test_no_auto_actionable_issues_omits_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """If no A/B/E issues exist, the auto-action block is absent and no PUTs happen."""
    monkeypatch.setenv("SENTRY_TRIAGE_APPLY", "1")
    _patch_common(monkeypatch)

    # Pure tier-D issue (low count, no A/B/E pattern match, fresh).
    d_issue = _stub_issue("1", "PROJ-D1", "weird thing happened", count=3)
    monkeypatch.setattr(sentry_triage, "_fetch_unresolved_issues", lambda *_a: [d_issue])

    captured: dict[str, str] = {}
    monkeypatch.setattr(
        sentry_triage,
        "_send_telegram_notification",
        lambda m: captured.update(msg=m),
    )

    with patch.object(sentry_triage.requests, "put") as mock_put:
        sentry_triage.run_sentry_triage()

    mock_put.assert_not_called()
    msg = captured["msg"]
    assert "Auto-actioned" not in msg
    assert "Would auto-action" not in msg


def test_all_clear_suppressed_when_nothing_needs_attention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exception-only delivery: pure noise/transient/stale auto-actions send NO Telegram.

    Issues that were fully auto-actioned into tiers A/B/E (and no C/D, no
    failures) require no human input, so the daily all-clear ping is suppressed.
    """
    monkeypatch.setenv("SENTRY_TRIAGE_APPLY", "1")
    _patch_common(monkeypatch)

    stale_issue = _stub_issue("3", "PROJ-E1", "ancient regression", count=1)
    stale_issue["lastSeen"] = "2020-01-01T00:00:00Z"  # tier E
    issues = [
        _stub_issue("1", "PROJ-A1", "test_something exploded"),  # tier A
        _stub_issue("2", "PROJ-B1", "Connection refused upstream"),  # tier B
        stale_issue,  # tier E
    ]
    monkeypatch.setattr(sentry_triage, "_fetch_unresolved_issues", lambda *_a: issues)

    sent: list[str] = []
    monkeypatch.setattr(sentry_triage, "_send_telegram_notification", lambda m: sent.append(m))

    with patch.object(sentry_triage.requests, "put") as mock_put:
        mock_put.return_value = _mock_resp(200, "{}")
        result = sentry_triage.run_sentry_triage()

    # Auto-actions still ran (3 PUTs), but no Telegram message was sent.
    assert mock_put.call_count == 3
    assert sent == []
    assert result["status"] == "ok"


def test_actionable_issue_triggers_notification(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Class C (actionable) issue must still produce a Telegram notification."""
    monkeypatch.delenv("SENTRY_TRIAGE_APPLY", raising=False)
    _patch_common(monkeypatch)

    # High event count → tier C (actionable).
    issues = [_stub_issue("1", "PROJ-C1", "NullPointer in checkout", count=5000)]
    monkeypatch.setattr(sentry_triage, "_fetch_unresolved_issues", lambda *_a: issues)

    sent: list[str] = []
    monkeypatch.setattr(sentry_triage, "_send_telegram_notification", lambda m: sent.append(m))

    with patch.object(sentry_triage.requests, "put"):
        sentry_triage.run_sentry_triage()

    assert len(sent) == 1
    assert "Sentry triage" in sent[0]


# ---------------------------------------------------------------------------
# Delta-based notification: a STATIC backlog must stay silent; only genuinely
# new Class C/D issues (or auto-action failures) ping. This is the "still
# getting too many" fix — exception-only was firing daily because the standing
# C/D pile is always non-empty in dry-run.
# ---------------------------------------------------------------------------


def test_first_run_seeds_silently(monkeypatch: pytest.MonkeyPatch) -> None:
    """First run (no prior state) seeds the seen-set and sends NO notification."""
    monkeypatch.delenv("SENTRY_TRIAGE_APPLY", raising=False)
    _patch_common(monkeypatch)
    monkeypatch.setattr(sentry_triage, "_load_seen_ids", lambda: None)  # no state file yet

    saved: list[set[str]] = []
    monkeypatch.setattr(sentry_triage, "_save_seen_ids", lambda ids: saved.append(ids))

    issues = [_stub_issue("1", "PROJ-C1", "NullPointer in checkout", count=5000)]  # tier C
    monkeypatch.setattr(sentry_triage, "_fetch_unresolved_issues", lambda *_a: issues)

    sent: list[str] = []
    monkeypatch.setattr(sentry_triage, "_send_telegram_notification", lambda m: sent.append(m))

    with patch.object(sentry_triage.requests, "put"):
        sentry_triage.run_sentry_triage()

    assert sent == []  # seeded, not announced
    assert saved == [{"PROJ-C1"}]  # current pile persisted for next time


def test_static_backlog_is_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unchanged C/D backlog (already seen last run) sends NO notification."""
    monkeypatch.delenv("SENTRY_TRIAGE_APPLY", raising=False)
    _patch_common(monkeypatch)
    monkeypatch.setattr(sentry_triage, "_load_seen_ids", lambda: {"PROJ-C1", "PROJ-D1"})

    issues = [
        _stub_issue("1", "PROJ-C1", "NullPointer in checkout", count=5000),  # tier C
        _stub_issue("2", "PROJ-D1", "ambiguous slow query", count=2),  # tier D
    ]
    monkeypatch.setattr(sentry_triage, "_fetch_unresolved_issues", lambda *_a: issues)

    sent: list[str] = []
    monkeypatch.setattr(sentry_triage, "_send_telegram_notification", lambda m: sent.append(m))

    with patch.object(sentry_triage.requests, "put"):
        sentry_triage.run_sentry_triage()

    assert sent == []  # nothing new — stay silent


def test_new_issue_since_last_run_notifies(monkeypatch: pytest.MonkeyPatch) -> None:
    """A new C/D short-id not in the seen-set triggers exactly one notification."""
    monkeypatch.delenv("SENTRY_TRIAGE_APPLY", raising=False)
    _patch_common(monkeypatch)
    monkeypatch.setattr(sentry_triage, "_load_seen_ids", lambda: {"PROJ-C1"})  # old pile

    issues = [
        _stub_issue("1", "PROJ-C1", "NullPointer in checkout", count=5000),  # old, seen
        _stub_issue("2", "PROJ-C2", "fresh crash in payments", count=5000),  # NEW
    ]
    monkeypatch.setattr(sentry_triage, "_fetch_unresolved_issues", lambda *_a: issues)

    sent: list[str] = []
    monkeypatch.setattr(sentry_triage, "_send_telegram_notification", lambda m: sent.append(m))

    with patch.object(sentry_triage.requests, "put"):
        sentry_triage.run_sentry_triage()

    assert len(sent) == 1
    assert "(1 new)" in sent[0]  # header advertises the new-issue count


def test_seen_ids_round_trip(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """_save_seen_ids then _load_seen_ids round-trips; missing file returns None."""
    state_file = tmp_path / "sentry_triage_seen.json"
    monkeypatch.setattr(sentry_triage, "_SEEN_STATE_PATH", state_file)

    assert sentry_triage._load_seen_ids() is None  # no file yet → first run
    sentry_triage._save_seen_ids({"PROJ-C1", "PROJ-D9"})
    assert sentry_triage._load_seen_ids() == {"PROJ-C1", "PROJ-D9"}


# ---------------------------------------------------------------------------
# COWORK_ROUTINE fallback: in a cloud clone, load_local_projects() returns []
# because it reads vault/gitignored files absent in the clone. Without a
# fallback, every Class C issue hits the [SKIP] branch instead of filing.
# COWORK_ROUTINE=1 defaults proj_wd to PROJECT_ROOT so filing still happens;
# local runs (no env var) must be unaffected.
# ---------------------------------------------------------------------------


def test_cowork_routine_defaults_working_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    """With COWORK_ROUTINE=1, a missing local-project match still files the issue."""
    monkeypatch.setenv("SENTRY_TRIAGE_APPLY", "1")
    monkeypatch.setenv("COWORK_ROUTINE", "1")
    _patch_common(monkeypatch)
    monkeypatch.setattr(sentry_triage, "load_local_projects", lambda: [])

    issues = [_stub_issue("1", "PROJ-C1", "NullPointer in checkout", count=5000)]  # tier C
    monkeypatch.setattr(sentry_triage, "_fetch_unresolved_issues", lambda *_a: issues)

    mock_file = MagicMock(return_value="https://github.com/org/repo/issues/1")
    monkeypatch.setattr(sentry_triage, "_file_github_issue", mock_file)

    with patch.object(sentry_triage.requests, "put"):
        result = sentry_triage.run_sentry_triage()

    mock_file.assert_called_once()
    call_args = mock_file.call_args.args
    repo_root = call_args[2]
    assert repo_root == sentry_triage.PROJECT_ROOT
    assert "1 GitHub issues filed" in result["summary"]
    findings_text = "\n".join(result["findings"])
    assert "[SKIP] no working directory" not in findings_text


def test_local_run_without_cowork_routine_still_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without COWORK_ROUTINE, a missing local-project match still hits [SKIP] (unchanged)."""
    monkeypatch.setenv("SENTRY_TRIAGE_APPLY", "1")
    monkeypatch.delenv("COWORK_ROUTINE", raising=False)
    _patch_common(monkeypatch)
    monkeypatch.setattr(sentry_triage, "load_local_projects", lambda: [])

    issues = [_stub_issue("1", "PROJ-C1", "NullPointer in checkout", count=5000)]  # tier C
    monkeypatch.setattr(sentry_triage, "_fetch_unresolved_issues", lambda *_a: issues)

    mock_file = MagicMock(return_value="https://github.com/org/repo/issues/1")
    monkeypatch.setattr(sentry_triage, "_file_github_issue", mock_file)

    with patch.object(sentry_triage.requests, "put"):
        result = sentry_triage.run_sentry_triage()

    mock_file.assert_not_called()
    findings_text = "\n".join(result["findings"])
    assert "[SKIP] no working directory for project test-proj" in findings_text


# ---------------------------------------------------------------------------
# _issue_already_filed: strongly-consistent, exact-match, fail-closed dedup
# (issue #2300). The old --search path lagged the GitHub index and failed OPEN,
# so back-to-back runs filed duplicates.
# ---------------------------------------------------------------------------


def _mock_gh_list(titles: list[str], returncode: int = 0) -> MagicMock:
    """Fake `gh issue list --json title` completed-process result."""
    result = MagicMock()
    result.returncode = returncode
    result.stdout = json.dumps([{"title": t} for t in titles])
    result.stderr = ""
    return result


def test_issue_already_filed_exact_match_hit() -> None:
    """An exact-title match in the open-issue listing returns True."""
    title = "[Sentry] NullPointer in checkout flow"
    with patch.object(sentry_triage.subprocess, "run") as mock_run:
        mock_run.return_value = _mock_gh_list(
            ["[Sentry] some other error", title, "[Sentry] yet another"]
        )
        assert sentry_triage._issue_already_filed(title, "/repo") is True


def test_issue_already_filed_near_miss_is_not_substring() -> None:
    """A different-but-overlapping title does NOT count as filed (exact match)."""
    title = "[Sentry] NullPointer in checkout"
    with patch.object(sentry_triage.subprocess, "run") as mock_run:
        # Listing contains a longer title that SHARES the candidate as a prefix.
        mock_run.return_value = _mock_gh_list(["[Sentry] NullPointer in checkout flow after retry"])
        assert sentry_triage._issue_already_filed(title, "/repo") is False


def test_issue_already_filed_normalizes_whitespace() -> None:
    """Whitespace runs are collapsed before comparison (still exact otherwise)."""
    with patch.object(sentry_triage.subprocess, "run") as mock_run:
        mock_run.return_value = _mock_gh_list(["[Sentry] boom   crash"])
        assert sentry_triage._issue_already_filed("[Sentry]  boom crash ", "/repo") is True


@pytest.mark.parametrize(
    "failure",
    [
        {"returncode": 1},  # non-zero exit
        {"exc": subprocess.TimeoutExpired(cmd="gh", timeout=5)},  # timeout
        {"exc": OSError("gh not found")},  # subprocess/exec error
        {"bad_json": True},  # JSON parse failure
    ],
)
def test_issue_already_filed_fails_closed(failure: dict) -> None:
    """gh failure/timeout/exception/bad-JSON returns True (assume filed)."""
    with patch.object(sentry_triage.subprocess, "run") as mock_run:
        if "exc" in failure:
            mock_run.side_effect = failure["exc"]
        elif failure.get("bad_json"):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "not valid json {"
            result.stderr = ""
            mock_run.return_value = result
        else:
            result = _mock_gh_list([], returncode=failure["returncode"])
            mock_run.return_value = result
        assert sentry_triage._issue_already_filed("[Sentry] anything", "/repo") is True


def test_issue_already_filed_does_not_use_search() -> None:
    """Regression guard: the gh command must NOT use the lagging --search index."""
    with patch.object(sentry_triage.subprocess, "run") as mock_run:
        mock_run.return_value = _mock_gh_list([])
        sentry_triage._issue_already_filed("[Sentry] whatever", "/repo")
    cmd = mock_run.call_args.args[0]
    assert "--search" not in cmd
    assert "--json" in cmd
    assert "title" in cmd


# ---------------------------------------------------------------------------
# Ownership scoping (#2331): the org-wide fetch returns issues from EVERY
# project in the yudame org. Only issues from THIS repo's owned Sentry
# project(s) may reach classification/filing. Filter is on the numeric
# project.id (rename-proof), fail-safe = drop-on-ambiguity.
# ---------------------------------------------------------------------------


def _owned_project() -> dict:
    return {"id": sentry_triage._DEFAULT_OWNED_PROJECT_IDS[0], "slug": "ai"}


def test_owned_issue_passes_filter_and_is_classified(monkeypatch: pytest.MonkeyPatch) -> None:
    """An issue from the owned project reaches classification (Class C filing path)."""
    monkeypatch.delenv("SENTRY_TRIAGE_PROJECT_IDS", raising=False)
    monkeypatch.delenv("SENTRY_TRIAGE_APPLY", raising=False)
    _patch_common(monkeypatch)

    # Owned project id + high count → tier C (actionable), which notifies.
    issues = [_stub_issue("1", "PROJ-C1", "NullPointer in checkout", count=5000)]
    monkeypatch.setattr(sentry_triage, "_fetch_unresolved_issues", lambda *_a: issues)

    sent: list[str] = []
    monkeypatch.setattr(sentry_triage, "_send_telegram_notification", lambda m: sent.append(m))

    with patch.object(sentry_triage.requests, "put"):
        result = sentry_triage.run_sentry_triage()

    assert result["status"] == "ok"
    # The owned issue survived the filter and was classified as Class C.
    assert any("Class C" in f for f in result["findings"])
    assert len(sent) == 1


def test_foreign_issue_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """An issue from a foreign project.id is dropped before classification."""
    monkeypatch.delenv("SENTRY_TRIAGE_PROJECT_IDS", raising=False)
    monkeypatch.delenv("SENTRY_TRIAGE_APPLY", raising=False)
    _patch_common(monkeypatch)

    owned = _stub_issue("1", "PROJ-C1", "NullPointer in checkout", count=5000)
    foreign = _stub_issue("2", "PODCAST-1", "OSError: SECONDARY: disk full", count=5000)
    foreign["project"] = {"id": "9999999999999999", "slug": "podcast-episode"}
    monkeypatch.setattr(sentry_triage, "_fetch_unresolved_issues", lambda *_a: [owned, foreign])

    with patch.object(sentry_triage.requests, "put"):
        result = sentry_triage.run_sentry_triage()

    findings_text = "\n".join(result["findings"])
    # Owned issue present; foreign short-id never classified/mentioned.
    assert "PROJ-C1" in findings_text
    assert "PODCAST-1" not in findings_text
    assert "1 project(s)" in result["summary"]


def test_missing_project_id_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-safe: an issue with missing/empty project.id is dropped, not filed."""
    monkeypatch.delenv("SENTRY_TRIAGE_PROJECT_IDS", raising=False)
    monkeypatch.delenv("SENTRY_TRIAGE_APPLY", raising=False)
    _patch_common(monkeypatch)

    owned = _stub_issue("1", "PROJ-C1", "NullPointer in checkout", count=5000)
    no_id = _stub_issue("2", "NOID-1", "mysterious failure", count=5000)
    no_id["project"] = {"slug": "unknown"}  # no id key
    monkeypatch.setattr(sentry_triage, "_fetch_unresolved_issues", lambda *_a: [owned, no_id])

    with patch.object(sentry_triage.requests, "put"):
        result = sentry_triage.run_sentry_triage()

    findings_text = "\n".join(result["findings"])
    assert "PROJ-C1" in findings_text
    assert "NOID-1" not in findings_text


def test_env_override_changes_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """SENTRY_TRIAGE_PROJECT_IDS overrides the default: a previously-foreign id passes."""
    monkeypatch.delenv("SENTRY_TRIAGE_APPLY", raising=False)
    _patch_common(monkeypatch)

    # This id is NOT the default owned id — only the env override admits it.
    foreign_id = "1234567890"
    monkeypatch.setenv("SENTRY_TRIAGE_PROJECT_IDS", foreign_id)

    issue = _stub_issue("1", "OTHER-1", "NullPointer in checkout", count=5000)
    issue["project"] = {"id": foreign_id, "slug": "other-repo"}
    monkeypatch.setattr(sentry_triage, "_fetch_unresolved_issues", lambda *_a: [issue])

    with patch.object(sentry_triage.requests, "put"):
        result = sentry_triage.run_sentry_triage()

    findings_text = "\n".join(result["findings"])
    # Admitted by the override, classified (not scoped out).
    assert "OTHER-1" in findings_text
    assert result["status"] == "ok"


def test_all_foreign_fires_safety_net_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """When the ownership filter drops 100% of fetched issues, a warning fires."""
    monkeypatch.delenv("SENTRY_TRIAGE_PROJECT_IDS", raising=False)
    monkeypatch.delenv("SENTRY_TRIAGE_APPLY", raising=False)
    _patch_common(monkeypatch)

    foreign_a = _stub_issue("1", "PODCAST-1", "boom in episode", count=5000)
    foreign_a["project"] = {"id": "8888", "slug": "podcast-episode"}
    foreign_b = _stub_issue("2", "STRIPE-1", "billing failed", count=5000)
    foreign_b["project"] = {"id": "7777", "slug": "stripe-billing"}
    monkeypatch.setattr(
        sentry_triage, "_fetch_unresolved_issues", lambda *_a: [foreign_a, foreign_b]
    )

    sent: list[str] = []
    monkeypatch.setattr(sentry_triage, "_send_telegram_notification", lambda m: sent.append(m))

    with caplog.at_level("WARNING", logger="reflections.sentry_triage"):
        with patch.object(sentry_triage.requests, "put"):
            result = sentry_triage.run_sentry_triage()

    assert any("dropped ALL 2 issues" in rec.message for rec in caplog.records)
    assert result["status"] == "ok"
    assert sent == []  # nothing owned → nothing to notify


# ---------------------------------------------------------------------------
# Synthetic-noise Class-A coverage (#2331): test-fixture titles emitted into
# our OWN project must classify as noise, not fall through to the
# event_count>=10 → Class C heuristic. Patterns stay NARROW so real errors file.
# ---------------------------------------------------------------------------


def _recent_iso() -> str:
    return (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.mark.parametrize(
    "title",
    [
        "RuntimeError: boom",
        "ValueError: corrupt",
        "RuntimeError: provider down",
    ],
)
def test_synthetic_titles_classify_as_noise(title: str) -> None:
    """Synthetic sentinel titles → Class A even with a high event count."""
    issue = {"title": title, "count": 5000, "lastSeen": _recent_iso()}
    cls, _reason = sentry_triage._classify_issue(issue)
    assert cls == "A"


def test_real_actionable_error_is_not_noise() -> None:
    """A real, specific error from the owned project stays actionable (not Class A)."""
    issue = {
        "title": "ModelException: Model instance parameters invalid",
        "count": 42,
        "lastSeen": _recent_iso(),
    }
    cls, _reason = sentry_triage._classify_issue(issue)
    assert cls != "A"
    # High count + recent + no noise/transient match → Class C (actionable).
    assert cls == "C"


@pytest.mark.parametrize(
    "title",
    [
        "DatabaseError: corrupt index",  # substring ': corrupt' would have swallowed this
        "IntegrityError: corrupted page detected",
        "RuntimeError: boomerang scheduler stuck",  # substring ': boom' near-miss
        "LLM provider down for 3 retries",  # real transient, not synthetic
    ],
)
def test_real_errors_resembling_synthetic_sentinels_are_not_noise(title: str) -> None:
    """Exact-match (not substring) synthetic detection must not swallow real errors
    whose titles merely contain a sentinel fragment. Regression guard for the
    over-broad ': corrupt' / 'provider down' substrings replaced in #2331."""
    issue = {"title": title, "count": 5000, "lastSeen": _recent_iso()}
    cls, _reason = sentry_triage._classify_issue(issue)
    assert cls != "A"
