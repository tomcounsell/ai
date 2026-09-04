"""Unit tests for tools.sdlc_session_ensure: ownerless adoption, lane slug, orphans (#2879)."""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from tests.db_claim import subprocess_env

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)


class TestOwnerlessAdoption:
    """WS-F (#2026): adopt an ownerless bridge PM eng session instead of minting
    a competing ``sdlc-local-{N}``.

    A bridge PM session built from raw message text (``"SDLC 1312"``) never gets
    ``issue_url`` stamped, so #1147's ownership check missed and a duplicate
    top-level session was minted. The env short-circuit now adopts the ownerless
    env session (bind run_id + write signal, then stamp ``issue_url`` last).
    """

    def test_ownerless_env_session_is_adopted(self, monkeypatch):
        """Env eng session with issue_url=None → adopt (created False), stamp
        issue_url from the explicit arg, mint nothing, no issue-lookup detour."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.setenv("AGENT_SESSION_ID", "tg_valor_-1003449100931_1192")
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)

        pm_session = MagicMock()
        pm_session.session_id = "tg_valor_-1003449100931_1192"
        pm_session.session_type = "eng"
        pm_session.status = "running"
        pm_session.issue_url = None  # the observed ownerless bridge case

        fsbi = MagicMock()  # divergent-owner detour must NOT run
        mock_as = MagicMock()

        with (
            patch("tools._sdlc_utils.find_session", return_value=pm_session),
            patch("tools._sdlc_utils.find_session_by_issue", fsbi),
            patch("models.agent_session.AgentSession", mock_as),
            patch(
                "tools.sdlc_session_ensure._acquire_run_lock_and_bind",
                return_value=("run_abc123", None),
            ),
        ):
            result = ensure_session(
                issue_number=1312,
                issue_url="https://github.com/tomcounsell/ai/issues/1312",
            )

        assert result["session_id"] == "tg_valor_-1003449100931_1192"
        assert result["created"] is False
        assert result["run_id"] == "run_abc123"
        # issue_url stamped LAST (after a successful bind) and persisted.
        assert pm_session.issue_url == "https://github.com/tomcounsell/ai/issues/1312"
        pm_session.save.assert_called_once()
        # No competitor minted; no divergent-owner detour.
        mock_as.create_local.assert_not_called()
        fsbi.assert_not_called()

    def test_ownerless_variants_none_empty_whitespace_all_adopted(self, monkeypatch):
        """None, "", and whitespace-only issue_url are ALL ownerless → adopt."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)

        for offset, ownerless in enumerate((None, "", "   ")):
            issue_number = 13120 + offset
            monkeypatch.setenv("AGENT_SESSION_ID", f"pm-{issue_number}")

            pm_session = MagicMock()
            pm_session.session_id = f"pm-{issue_number}"
            pm_session.session_type = "eng"
            pm_session.status = "running"
            pm_session.issue_url = ownerless

            mock_as = MagicMock()

            with (
                patch("tools._sdlc_utils.find_session", return_value=pm_session),
                patch("tools._sdlc_utils.find_session_by_issue", MagicMock()),
                patch("models.agent_session.AgentSession", mock_as),
                patch(
                    "tools.sdlc_session_ensure._acquire_run_lock_and_bind",
                    return_value=(f"run-{issue_number}", None),
                ),
            ):
                result = ensure_session(
                    issue_number=issue_number,
                    issue_url=f"https://github.com/tomcounsell/ai/issues/{issue_number}",
                )

            assert result["session_id"] == f"pm-{issue_number}", (
                f"failed to adopt for ownerless issue_url={ownerless!r}"
            )
            assert result["created"] is False
            mock_as.create_local.assert_not_called()

    def test_ownerless_adoption_builds_issue_url_when_arg_absent(self, monkeypatch):
        """No --issue-url arg → stamp is built from the resolved repo slug."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.setenv("AGENT_SESSION_ID", "pm-13199")
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)

        pm_session = MagicMock()
        pm_session.session_id = "pm-13199"
        pm_session.session_type = "eng"
        pm_session.status = "running"
        pm_session.issue_url = None

        mock_as = MagicMock()

        with (
            patch("tools._sdlc_utils.find_session", return_value=pm_session),
            patch("tools._sdlc_utils.find_session_by_issue", MagicMock()),
            patch("models.agent_session.AgentSession", mock_as),
            patch("tools._sdlc_utils._resolve_target_repo", return_value="tomcounsell/ai"),
            patch(
                "tools.sdlc_session_ensure._acquire_run_lock_and_bind",
                return_value=("run-13199", None),
            ),
        ):
            result = ensure_session(issue_number=13199)  # no issue_url

        assert result["created"] is False
        assert pm_session.issue_url == "https://github.com/tomcounsell/ai/issues/13199"

    def test_ownerless_adoption_bind_failure_returns_error_no_mint(self, monkeypatch):
        """Bind fails (foreign ISSUE_LOCKED) → return the error dict verbatim,
        NEVER fall through to a mint, and leave issue_url untouched (no stamp).

        Falling through under a held foreign lock would mint the exact
        ``sdlc-local-{N}`` orphan WS-F prevents (critique blocker #2)."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.setenv("AGENT_SESSION_ID", "pm-1313")
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)

        pm_session = MagicMock()
        pm_session.session_id = "pm-1313"
        pm_session.session_type = "eng"
        pm_session.status = "running"
        pm_session.issue_url = None

        error_dict = {
            "blocked": True,
            "reason": "ISSUE_LOCKED",
            "owner_run_id": "foreign_run",
            "owner_session_id": "foreign_sess",
            "orphaned_lock": False,
        }

        mock_as = MagicMock()

        with (
            patch("tools._sdlc_utils.find_session", return_value=pm_session),
            patch("tools._sdlc_utils.find_session_by_issue", MagicMock()),
            patch("models.agent_session.AgentSession", mock_as),
            patch(
                "tools.sdlc_session_ensure._acquire_run_lock_and_bind",
                return_value=(None, error_dict),
            ),
        ):
            result = ensure_session(issue_number=1313)

        assert result == error_dict
        # No stamp on a bind failure (catches a stamp-first regression).
        assert pm_session.issue_url is None
        pm_session.save.assert_not_called()
        # No competitor minted.
        mock_as.create_local.assert_not_called()

    def test_ownerless_adoption_stamp_failure_returns_adopted_no_mint(self, monkeypatch):
        """Bind succeeds but the issue_url save raises → return the adopted
        session (run already owned); NEVER fall through to a mint."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.setenv("AGENT_SESSION_ID", "pm-1314")
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)

        pm_session = MagicMock()
        pm_session.session_id = "pm-1314"
        pm_session.session_type = "eng"
        pm_session.status = "running"
        pm_session.issue_url = None
        pm_session.save.side_effect = ConnectionError("Redis down during stamp")

        mock_as = MagicMock()

        with (
            patch("tools._sdlc_utils.find_session", return_value=pm_session),
            patch("tools._sdlc_utils.find_session_by_issue", MagicMock()),
            patch("models.agent_session.AgentSession", mock_as),
            patch(
                "tools.sdlc_session_ensure._acquire_run_lock_and_bind",
                return_value=("run-1314", None),
            ),
        ):
            result = ensure_session(
                issue_number=1314,
                issue_url="https://github.com/tomcounsell/ai/issues/1314",
            )

        assert result["session_id"] == "pm-1314"
        assert result["created"] is False
        assert result["run_id"] == "run-1314"
        # Stamp failure did not fall through to a mint under a held lock.
        mock_as.create_local.assert_not_called()

    def test_divergent_owner_not_adopted(self, monkeypatch):
        """Env session owning a DIFFERENT issue is NOT adopted (its issue_url is
        untouched) → existing divergent-owner fall-through preserved (#1671)."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.setenv("AGENT_SESSION_ID", "pm-other")
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)

        env_session = MagicMock()
        env_session.session_id = "pm-other"
        env_session.session_type = "eng"
        env_session.status = "running"
        env_session.issue_url = "https://github.com/tomcounsell/ai/issues/9999"

        issue_session = MagicMock()
        issue_session.session_id = "sdlc-local-1315"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = [issue_session]  # post-save readback
        mock_as.query.get.return_value = issue_session  # post-save readback (primary-key lookup)

        with (
            patch("tools._sdlc_utils.find_session", return_value=env_session),
            patch("tools._sdlc_utils.find_session_by_issue", return_value=issue_session),
            patch("models.agent_session.AgentSession", mock_as),
        ):
            result = ensure_session(issue_number=1315)

        # Divergent env session preferred the issue-scoped session; not adopted.
        assert result["session_id"] == "sdlc-local-1315"
        assert result["created"] is False
        # The divergent env session's issue_url was NOT overwritten.
        assert env_session.issue_url == "https://github.com/tomcounsell/ai/issues/9999"
        env_session.save.assert_not_called()


class TestLaneSlugMintedAtLaneStart:
    """``ensure_session`` is the single minter of the lane slug (#2735).

    It is the one component that runs on every lane-start path before any
    plan or any stage exists, so it is where the lane's identity is created
    and recorded on the ``PipelineLedger`` -- once, conditional-on-empty.
    """

    _TEST_REPO = "test-owner/test-repo"
    _ISSUE = 927360

    def _cleanup(self):
        from agent.pipeline_ledger import PipelineLedger

        for record in PipelineLedger.query.filter(ledger_key=f"{self._TEST_REPO}:{self._ISSUE}"):
            record.delete()

    def setup_method(self):
        self._cleanup()

    def teardown_method(self):
        self._cleanup()

    def test_ensure_session_records_the_lane_slug(self, monkeypatch):
        """After ensure_session(N) on a clean Redis a ledger exists for N with
        a non-empty slug -- the mint has a write target."""
        from agent.pipeline_ledger import PipelineLedger
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.setenv("GH_REPO", self._TEST_REPO)

        mock_new_session = MagicMock()
        mock_new_session.session_id = f"sdlc-local-{self._ISSUE}"
        mock_as = MagicMock()
        mock_as.query.filter.side_effect = [[]]  # existing_by_id lookup (none)
        mock_as.query.get.return_value = mock_new_session  # post-save readback (primary-key lookup)
        mock_as.create_local.return_value = mock_new_session

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
        ):
            ensure_session(issue_number=self._ISSUE)

        ledger = PipelineLedger.get(self._TEST_REPO, self._ISSUE)
        assert ledger is not None
        assert ledger.slug == f"sdlc-{self._ISSUE}"

    def test_invalid_issue_number_mints_nothing(self, monkeypatch):
        """The validity guard runs first: no ledger for a bogus issue number."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.setenv("GH_REPO", self._TEST_REPO)
        with patch("tools.sdlc_session_ensure.resolve_lane_slug") as mock_resolve:
            assert ensure_session(issue_number=0) == {}
            assert ensure_session(issue_number=-1) == {}
        mock_resolve.assert_not_called()

    def test_slug_resolution_failure_never_fails_the_ensure(self, monkeypatch):
        """A Redis or git failure inside identity resolution must not convert a
        successful ensure into ``return {}``."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.setenv("GH_REPO", self._TEST_REPO)

        mock_new_session = MagicMock()
        mock_new_session.session_id = f"sdlc-local-{self._ISSUE}"
        mock_as = MagicMock()
        mock_as.query.filter.side_effect = [[]]  # existing_by_id lookup (none)
        mock_as.query.get.return_value = mock_new_session  # post-save readback (primary-key lookup)
        mock_as.create_local.return_value = mock_new_session

        with (
            patch(
                "tools.sdlc_session_ensure.resolve_lane_slug",
                side_effect=RuntimeError("redis down"),
            ),
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
        ):
            result = ensure_session(issue_number=self._ISSUE)

        assert result["session_id"] == f"sdlc-local-{self._ISSUE}"
        assert result["created"] is True


def _make_orphan_session(
    session_id,
    age_seconds,
    heartbeat=None,
    session_type="eng",
    last_activity_seconds=None,
    issue_number=None,
):
    """Build a MagicMock AgentSession with orphan-relevant fields.

    ``age_seconds`` sets ``created_at`` (creation age). By default the session's
    last-activity timestamps (``updated_at``/``started_at``) mirror
    ``created_at`` — i.e. a session that was created and never advanced a stage,
    which is the genuinely-dead-orphan shape.

    Pass ``last_activity_seconds`` to model a LIVE pipeline that was created long
    ago but recently refreshed ``updated_at`` via a stage_states write (#1676):
    ``created_at`` stays at ``age_seconds`` while ``updated_at`` is set to the
    fresher ``last_activity_seconds``.

    ``issue_number`` defaults to None so a session with no resolvable
    issue-lock payload exercises the idle-time fallback path (issue #2305
    defect 1) exactly like the pre-existing tests below expect. Pass an
    explicit issue number to exercise the lock-payload-authoritative path.
    """
    s = MagicMock()
    s.session_id = session_id
    s.session_type = session_type
    s.status = "running"
    s.last_heartbeat_at = heartbeat
    s.created_at = datetime.now(UTC) - timedelta(seconds=age_seconds)
    activity_age = age_seconds if last_activity_seconds is None else last_activity_seconds
    s.updated_at = datetime.now(UTC) - timedelta(seconds=activity_age)
    s.started_at = s.updated_at
    s.issue_url = None
    s.issue_number = issue_number
    return s


class TestKillOrphans:
    """Tests for the --kill-orphans zombie-cleanup CLI path."""

    def test_dry_run_lists_without_modifying(self):
        from tools.sdlc_session_ensure import ORPHAN_AGE_SECONDS, _kill_orphans

        orphan = _make_orphan_session("sdlc-local-9991", ORPHAN_AGE_SECONDS + 60)
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [orphan]
        mock_as.query.get.return_value = orphan  # post-save readback (primary-key lookup)

        with patch("models.agent_session.AgentSession", mock_as):
            result = _kill_orphans(dry_run=True)

        assert result["count"] == 1
        assert result["killed"] is False
        assert result["orphans"][0]["session_id"] == "sdlc-local-9991"

    def test_real_run_finalizes_orphans_via_finalize_session(self):
        from tools.sdlc_session_ensure import ORPHAN_AGE_SECONDS, _kill_orphans

        orphan = _make_orphan_session("sdlc-local-9992", ORPHAN_AGE_SECONDS + 60)
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [orphan]
        mock_as.query.get.return_value = orphan  # post-save readback (primary-key lookup)

        finalize_mock = MagicMock()
        with (
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.finalize_session", finalize_mock),
        ):
            result = _kill_orphans(dry_run=False)

        assert result["count"] == 1
        assert result["killed"] is True
        assert result["failures"] == 0
        assert result["results"][0] == {
            "session_id": "sdlc-local-9992",
            "result": "killed",
        }
        # Verify finalize_session was called with correct args (not transition_status).
        finalize_mock.assert_called_once()
        _args, kwargs = finalize_mock.call_args
        assert kwargs["reason"] == "zombie sdlc-local session cleanup"
        assert kwargs["skip_auto_tag"] is True
        assert kwargs["skip_checkpoint"] is True
        assert kwargs["skip_parent"] is True

    def test_finalize_session_failure_does_not_crash(self):
        from tools.sdlc_session_ensure import ORPHAN_AGE_SECONDS, _kill_orphans

        orphan = _make_orphan_session("sdlc-local-9993", ORPHAN_AGE_SECONDS + 60)
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [orphan]
        mock_as.query.get.return_value = orphan  # post-save readback (primary-key lookup)

        with (
            patch("models.agent_session.AgentSession", mock_as),
            patch(
                "models.session_lifecycle.finalize_session",
                side_effect=RuntimeError("boom"),
            ),
        ):
            result = _kill_orphans(dry_run=False)

        assert result["count"] == 1
        assert result["failures"] == 1
        assert result["results"][0]["result"] == "failed"
        assert "boom" in result["results"][0]["error"]

    def test_newer_than_threshold_not_listed(self):
        from tools.sdlc_session_ensure import _kill_orphans

        fresh = _make_orphan_session("sdlc-local-9994", 60)  # 1 minute old
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [fresh]
        mock_as.query.get.return_value = fresh  # post-save readback (primary-key lookup)

        with patch("models.agent_session.AgentSession", mock_as):
            result = _kill_orphans(dry_run=True)

        assert result["count"] == 0
        assert result["orphans"] == []

    def test_session_with_heartbeat_never_listed(self):
        from tools.sdlc_session_ensure import ORPHAN_AGE_SECONDS, _kill_orphans

        old_but_alive = _make_orphan_session(
            "sdlc-local-9995",
            age_seconds=ORPHAN_AGE_SECONDS + 3600,
            heartbeat=datetime.now(UTC),
        )
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [old_but_alive]
        mock_as.query.get.return_value = old_but_alive  # post-save readback (primary-key lookup)

        with patch("models.agent_session.AgentSession", mock_as):
            result = _kill_orphans(dry_run=True)

        assert result["count"] == 0

    def test_boundary_at_threshold_is_listed(self):
        from tools.sdlc_session_ensure import ORPHAN_AGE_SECONDS, _kill_orphans

        at_boundary = _make_orphan_session("sdlc-local-9996", ORPHAN_AGE_SECONDS)
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [at_boundary]
        mock_as.query.get.return_value = at_boundary  # post-save readback (primary-key lookup)

        with patch("models.agent_session.AgentSession", mock_as):
            result = _kill_orphans(dry_run=True)

        # At-threshold means age >= threshold is True.
        assert result["count"] == 1

    def test_boundary_one_second_under_not_listed(self):
        from tools.sdlc_session_ensure import ORPHAN_AGE_SECONDS, _kill_orphans

        under = _make_orphan_session("sdlc-local-9997", ORPHAN_AGE_SECONDS - 1)
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [under]
        mock_as.query.get.return_value = under  # post-save readback (primary-key lookup)

        with patch("models.agent_session.AgentSession", mock_as):
            result = _kill_orphans(dry_run=True)

        assert result["count"] == 0

    def test_boundary_one_second_over_is_listed(self):
        from tools.sdlc_session_ensure import ORPHAN_AGE_SECONDS, _kill_orphans

        over = _make_orphan_session("sdlc-local-9998", ORPHAN_AGE_SECONDS + 1)
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [over]
        mock_as.query.get.return_value = over  # post-save readback (primary-key lookup)

        with patch("models.agent_session.AgentSession", mock_as):
            result = _kill_orphans(dry_run=True)

        assert result["count"] == 1

    def test_non_sdlc_local_session_never_listed(self):
        from tools.sdlc_session_ensure import ORPHAN_AGE_SECONDS, _kill_orphans

        # A bridge session matching all other zombie criteria must be skipped.
        bridge = _make_orphan_session("tg_valor_-1003449100931_691", ORPHAN_AGE_SECONDS + 3600)
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [bridge]
        mock_as.query.get.return_value = bridge  # post-save readback (primary-key lookup)

        with patch("models.agent_session.AgentSession", mock_as):
            result = _kill_orphans(dry_run=True)

        assert result["count"] == 0

    def test_live_local_pipeline_with_fresh_updated_at_not_listed(self):
        """#1676: a worker-less sdlc-local-N PM session with last_heartbeat_at=None
        but a FRESH updated_at (advanced a stage recently) must NOT be reaped.

        This is the core defect: on a skills-only machine no worker writes a
        heartbeat, so a live /do-sdlc pipeline matched the old zombie criteria
        after 10 minutes and --kill-orphans destroyed its stage_states mid-run.
        The fix exempts it because every stage_states write refreshes updated_at.
        """
        from tools.sdlc_session_ensure import ORPHAN_AGE_SECONDS, _kill_orphans

        # Created 1 hour ago (well past threshold), but advanced a stage 30s ago.
        live = _make_orphan_session(
            "sdlc-local-1676",
            age_seconds=ORPHAN_AGE_SECONDS + 3600,
            heartbeat=None,
            last_activity_seconds=30,
        )
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [live]
        mock_as.query.get.return_value = live  # post-save readback (primary-key lookup)

        with patch("models.agent_session.AgentSession", mock_as):
            result = _kill_orphans(dry_run=True)

        assert result["count"] == 0
        assert result["orphans"] == []

    def test_stale_local_pipeline_no_heartbeat_still_listed(self):
        """#1676: a worker-less sdlc-local-N PM session with last_heartbeat_at=None
        AND a stale updated_at (no stage advanced for the full window) is still a
        genuine zombie and MUST be reaped — preserving original dead-orphan
        behavior for sessions that truly stalled.
        """
        from tools.sdlc_session_ensure import ORPHAN_AGE_SECONDS, _kill_orphans

        # Created AND last-active well past the threshold.
        stale = _make_orphan_session(
            "sdlc-local-1677",
            age_seconds=ORPHAN_AGE_SECONDS + 3600,
            heartbeat=None,
            last_activity_seconds=ORPHAN_AGE_SECONDS + 600,
        )
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [stale]
        mock_as.query.get.return_value = stale  # post-save readback (primary-key lookup)

        with patch("models.agent_session.AgentSession", mock_as):
            result = _kill_orphans(dry_run=True)

        assert result["count"] == 1
        assert result["orphans"][0]["session_id"] == "sdlc-local-1677"

    def test_fresh_updated_at_exempts_even_at_creation_boundary(self):
        """#1676: updated_at just under the threshold exempts a session whose
        created_at is exactly at the threshold — last activity, not creation, is
        the liveness clock.
        """
        from tools.sdlc_session_ensure import ORPHAN_AGE_SECONDS, _kill_orphans

        s = _make_orphan_session(
            "sdlc-local-1678",
            age_seconds=ORPHAN_AGE_SECONDS,
            heartbeat=None,
            last_activity_seconds=ORPHAN_AGE_SECONDS - 1,
        )
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [s]
        mock_as.query.get.return_value = s  # post-save readback (primary-key lookup)

        with patch("models.agent_session.AgentSession", mock_as):
            result = _kill_orphans(dry_run=True)

        assert result["count"] == 0

    def test_falls_back_to_started_at_when_updated_at_missing(self):
        """#1676: when updated_at is None, _last_activity_at falls back to
        started_at. A fresh started_at exempts an old-created_at session.
        """
        from tools.sdlc_session_ensure import ORPHAN_AGE_SECONDS, _kill_orphans

        s = _make_orphan_session(
            "sdlc-local-1679",
            age_seconds=ORPHAN_AGE_SECONDS + 3600,
            heartbeat=None,
        )
        s.updated_at = None
        s.started_at = datetime.now(UTC) - timedelta(seconds=30)
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [s]
        mock_as.query.get.return_value = s  # post-save readback (primary-key lookup)

        with patch("models.agent_session.AgentSession", mock_as):
            result = _kill_orphans(dry_run=True)

        assert result["count"] == 0

    def test_falls_back_to_created_at_when_no_activity_timestamps(self):
        """#1676: when both updated_at and started_at are None, the reaper falls
        back to created_at — an old, never-advanced session is still a zombie.
        """
        from tools.sdlc_session_ensure import ORPHAN_AGE_SECONDS, _kill_orphans

        s = _make_orphan_session(
            "sdlc-local-1680",
            age_seconds=ORPHAN_AGE_SECONDS + 3600,
            heartbeat=None,
        )
        s.updated_at = None
        s.started_at = None
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [s]
        mock_as.query.get.return_value = s  # post-save readback (primary-key lookup)

        with patch("models.agent_session.AgentSession", mock_as):
            result = _kill_orphans(dry_run=True)

        assert result["count"] == 1
        assert result["orphans"][0]["session_id"] == "sdlc-local-1680"

    def test_hollow_session_with_dead_locked_owner_is_reapable(self):
        """A hollow sdlc-local session whose issue-lock payload names a dead
        pid is reapable REGARDLESS of idle time (issue #2305 defect 1): the
        lock-payload-authoritative predicate is consulted first and a dead
        owner overrides `updated_at` freshness."""
        from tools.sdlc_session_ensure import _kill_orphans

        # Fresh updated_at (would have been exempt under the old updated_at
        # heuristic) but the recorded lock owner pid is dead.
        hollow = _make_orphan_session(
            "sdlc-local-2305",
            age_seconds=3600,
            heartbeat=None,
            last_activity_seconds=5,
            issue_number=2305,
        )
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [hollow]
        mock_as.query.get.return_value = hollow  # post-save readback (primary-key lookup)

        dead_payload = {
            "run_id": "dead-run",
            "session_id": "sdlc-local-2305",
            "pid": 424242,
            "hostname": "this-host",
            "create_time": 1000.0,
        }

        with (
            patch("models.agent_session.AgentSession", mock_as),
            patch(
                "tools.sdlc_session_ensure._issue_lock_payload_for_session",
                return_value=dead_payload,
            ),
            patch("socket.gethostname", return_value="this-host"),
            patch(
                "agent.session_health._psutil_process_for_pid",
                return_value=None,
            ),
        ):
            result = _kill_orphans(dry_run=True)

        assert result["count"] == 1
        assert result["orphans"][0]["session_id"] == "sdlc-local-2305"

    def test_hollow_session_with_live_locked_owner_is_exempt(self):
        """A sdlc-local session whose issue-lock payload names a LIVE owner
        (matching pid + create_time) is exempt from reaping even when it is
        old and idle -- the lock payload is authoritative, not `updated_at`
        (issue #2305 defect 1)."""
        from tools.sdlc_session_ensure import _kill_orphans

        live = _make_orphan_session(
            "sdlc-local-2306",
            age_seconds=3600 * 24,  # very old
            heartbeat=None,
            last_activity_seconds=3600 * 24,  # and idle for just as long
            issue_number=2306,
        )
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [live]
        mock_as.query.get.return_value = live  # post-save readback (primary-key lookup)

        live_payload = {
            "run_id": "live-run",
            "session_id": "sdlc-local-2306",
            "pid": 4242,
            "hostname": "this-host",
            "create_time": 1000.0,
        }
        live_proc = MagicMock()
        live_proc.create_time.return_value = 1000.0

        with (
            patch("models.agent_session.AgentSession", mock_as),
            patch(
                "tools.sdlc_session_ensure._issue_lock_payload_for_session",
                return_value=live_payload,
            ),
            patch("socket.gethostname", return_value="this-host"),
            patch(
                "agent.session_health._psutil_process_for_pid",
                return_value=live_proc,
            ),
        ):
            result = _kill_orphans(dry_run=True)

        assert result["count"] == 0
        assert result["orphans"] == []

    def test_cli_dry_run_exits_zero_with_valid_json(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.sdlc_session_ensure",
                "--kill-orphans",
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=subprocess_env(project_root=REPO_ROOT, PYTHONDONTWRITEBYTECODE="1"),
        )
        assert result.returncode == 0
        # stdout must be parseable JSON
        payload = json.loads(result.stdout)
        assert "count" in payload
        assert payload["killed"] is False

    def test_cli_rejects_issue_number_with_kill_orphans(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.sdlc_session_ensure",
                "--kill-orphans",
                "--issue-number",
                "1",
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=subprocess_env(project_root=REPO_ROOT),
        )
        # argparse .error() exits 2
        assert result.returncode != 0
        assert "mutually exclusive" in result.stderr.lower()
