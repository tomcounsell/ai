"""Unit tests for the self-healing SDLC run-identity helper (issue #2144).

Covers ``tools._sdlc_run_identity`` in isolation:
- ``classify_refusal`` recognition of run-identity refusal reasons
- ``reestablish_run_id`` across all ``ensure_session`` outcomes
  (supervised-inherit, env-corroborated reuse, fresh mint on a free lock,
  terminal-guard decline, foreign ISSUE_LOCKED, fail-open)
- the two CLI-wiring helpers ``heal_missing_run_id`` / ``maybe_heal_after_write``
- the visibility sink (``log_run_identity_event`` writes one JSON line)
- CLI-level self-heal wiring in ``sdlc_stage_marker.main``

Everything is mocked; no Redis or live lease is required.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import tools._sdlc_run_identity as rid


class TestClassifyRefusal:
    def test_recognizes_run_id_required(self):
        assert rid.classify_refusal({"error": "RUN_ID_REQUIRED"}) == "RUN_ID_REQUIRED"

    def test_recognizes_lease_absent_reason(self):
        assert rid.classify_refusal({"reason": "LEASE_ABSENT"}) == "LEASE_ABSENT"

    def test_recognizes_lowercase_sentinels(self):
        assert rid.classify_refusal({"error": "lease_absent"}) == "lease_absent"
        assert rid.classify_refusal({"reason": "issue_locked"}) == "issue_locked"

    def test_recognizes_issue_locked(self):
        assert rid.classify_refusal({"reason": "ISSUE_LOCKED"}) == "ISSUE_LOCKED"

    def test_ignores_non_identity_reason(self):
        assert rid.classify_refusal({"reason": "TARGET_REPO_MISSING"}) is None
        assert rid.classify_refusal({"stage": "BUILD", "status": "completed"}) is None

    def test_ignores_non_dict_and_none(self):
        assert rid.classify_refusal(None) is None
        assert rid.classify_refusal("LEASE_ABSENT") is None
        assert rid.classify_refusal([]) is None


class TestReestablishRunId:
    def test_no_issue_number_returns_none(self):
        assert rid.reestablish_run_id(None) is None
        assert rid.reestablish_run_id(0) is None

    def test_supervised_inherit_returns_live_run_id(self):
        status = SimpleNamespace(live=True, run_id="sup-run-1")
        with (
            patch("agent.supervised_run.supervised_run_status", return_value=status),
            patch("agent.supervised_run.read_supervised_run_signal", return_value=None),
        ):
            assert rid.reestablish_run_id(2144, working_dir="/tmp") == "sup-run-1"

    def test_env_corroborated_reuse_from_signal(self):
        status = SimpleNamespace(live=False, run_id=None)
        with (
            patch("agent.supervised_run.supervised_run_status", return_value=status),
            patch(
                "agent.supervised_run.read_supervised_run_signal",
                return_value={"run_id": "env-run-2"},
            ),
            patch(
                "tools.sdlc_session_ensure.ensure_session",
                return_value={"run_id": "env-run-2", "created": False},
            ) as ens,
        ):
            out = rid.reestablish_run_id(2144, working_dir="/tmp")
        assert out == "env-run-2"
        # reuse candidate must be routed through ensure_session
        assert ens.call_args.kwargs["reuse_run_id"] == "env-run-2"

    def test_active_run_id_used_when_no_signal(self):
        status = SimpleNamespace(live=False, run_id=None)
        with (
            patch("agent.supervised_run.supervised_run_status", return_value=status),
            patch("agent.supervised_run.read_supervised_run_signal", return_value=None),
            patch.object(rid, "_active_run_id_for_issue", return_value="mirror-run-3"),
            patch(
                "tools.sdlc_session_ensure.ensure_session",
                return_value={"run_id": "mirror-run-3"},
            ) as ens,
        ):
            out = rid.reestablish_run_id(2144, working_dir="/tmp")
        assert out == "mirror-run-3"
        assert ens.call_args.kwargs["reuse_run_id"] == "mirror-run-3"

    def test_fresh_mint_on_free_lock_returned_when_not_terminal(self):
        status = SimpleNamespace(live=False, run_id=None)
        with (
            patch("agent.supervised_run.supervised_run_status", return_value=status),
            patch("agent.supervised_run.read_supervised_run_signal", return_value=None),
            patch.object(rid, "_active_run_id_for_issue", return_value=None),
            patch(
                "tools.sdlc_session_ensure.ensure_session",
                return_value={"run_id": "fresh-run-4", "created": True},
            ),
            patch.object(rid, "_pipeline_is_terminal", return_value=False),
        ):
            out = rid.reestablish_run_id(2144, prior_run_id=None, working_dir="/tmp")
        assert out == "fresh-run-4"

    def test_terminal_guard_declines_fresh_mint_and_releases(self):
        status = SimpleNamespace(live=False, run_id=None)
        with (
            patch("agent.supervised_run.supervised_run_status", return_value=status),
            patch("agent.supervised_run.read_supervised_run_signal", return_value=None),
            patch.object(rid, "_active_run_id_for_issue", return_value=None),
            patch(
                "tools.sdlc_session_ensure.ensure_session",
                return_value={"run_id": "fresh-run-5", "created": True},
            ),
            patch.object(rid, "_pipeline_is_terminal", return_value=True),
            patch("models.session_lifecycle.release_issue_lock") as release,
        ):
            out = rid.reestablish_run_id(2144, prior_run_id=None, working_dir="/tmp")
        assert out is None
        release.assert_called_once_with(2144, "fresh-run-5")

    def test_supervised_run_active_fallthrough_inherits(self):
        status = SimpleNamespace(live=False, run_id=None)
        with (
            patch("agent.supervised_run.supervised_run_status", return_value=status),
            patch("agent.supervised_run.read_supervised_run_signal", return_value=None),
            patch.object(rid, "_active_run_id_for_issue", return_value=None),
            patch(
                "tools.sdlc_session_ensure.ensure_session",
                return_value={"reason": "SUPERVISED_RUN_ACTIVE", "run_id": "owner-6"},
            ),
        ):
            out = rid.reestablish_run_id(2144, working_dir="/tmp")
        assert out == "owner-6"

    def test_foreign_issue_locked_returns_none(self):
        status = SimpleNamespace(live=False, run_id=None)
        with (
            patch("agent.supervised_run.supervised_run_status", return_value=status),
            patch("agent.supervised_run.read_supervised_run_signal", return_value=None),
            patch.object(rid, "_active_run_id_for_issue", return_value=None),
            patch(
                "tools.sdlc_session_ensure.ensure_session",
                return_value={"reason": "ISSUE_LOCKED"},
            ),
        ):
            assert rid.reestablish_run_id(2144, working_dir="/tmp") is None

    def test_fail_open_on_exception(self):
        with patch(
            "agent.supervised_run.supervised_run_status",
            side_effect=RuntimeError("redis down"),
        ):
            # supervised check is guarded; force a harder failure downstream too
            with patch(
                "agent.supervised_run.read_supervised_run_signal",
                side_effect=RuntimeError("redis down"),
            ):
                # ensure_session import itself raising must not propagate
                with patch(
                    "tools.sdlc_session_ensure.ensure_session",
                    side_effect=RuntimeError("redis down"),
                ):
                    assert rid.reestablish_run_id(2144, working_dir="/tmp") is None


class TestCliWiringHelpers:
    def test_heal_missing_run_id_no_issue_returns_none(self):
        assert rid.heal_missing_run_id(None, "stage_marker") is None

    def test_heal_missing_run_id_delegates(self):
        with patch.object(rid, "heal_run_identity", return_value="healed-7") as h:
            out = rid.heal_missing_run_id(2144, "stage_marker")
        assert out == "healed-7"
        assert h.call_args.args[0] == 2144
        assert h.call_args.args[1] is None  # no prior run_id
        assert h.call_args.args[3] == "RUN_ID_REQUIRED"

    def test_maybe_heal_after_write_ignores_non_refusal(self):
        with patch.object(rid, "heal_run_identity") as h:
            out = rid.maybe_heal_after_write({"stage": "BUILD"}, "run-x", 2144, "stage_marker")
        assert out is None
        h.assert_not_called()

    def test_maybe_heal_after_write_no_issue_returns_none(self):
        assert rid.maybe_heal_after_write({"reason": "LEASE_ABSENT"}, "run-x", None, "s") is None

    def test_maybe_heal_after_write_returns_different_healed_id(self):
        with patch.object(rid, "heal_run_identity", return_value="new-8"):
            out = rid.maybe_heal_after_write(
                {"reason": "LEASE_ABSENT"}, "old-run", 2144, "stage_marker"
            )
        assert out == "new-8"

    def test_maybe_heal_after_write_none_when_healed_equals_prior(self):
        # A heal that re-echoes the same (still-dead) id is not a retry trigger.
        with patch.object(rid, "heal_run_identity", return_value="same-run"):
            out = rid.maybe_heal_after_write(
                {"reason": "LEASE_ABSENT"}, "same-run", 2144, "stage_marker"
            )
        assert out is None


class TestVisibilitySink:
    def test_log_event_writes_one_json_line(self, tmp_path):
        log_file = tmp_path / "logs" / "sdlc_run_identity.log"
        with (
            patch.object(rid, "_log_path", return_value=log_file),
            patch.object(rid, "_record_refusal_redis"),
        ):
            rid.log_run_identity_event(2144, "stage_marker", "LEASE_ABSENT", True, "old", "new")
        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 1
        payload = json.loads(lines[0])
        assert payload["issue"] == 2144
        assert payload["subcommand"] == "stage_marker"
        assert payload["reason"] == "LEASE_ABSENT"
        assert payload["healed"] is True
        assert payload["old_run_id"] == "old"
        assert payload["new_run_id"] == "new"

    def test_log_event_fail_open_on_io_error(self):
        # A log path that cannot be created must never raise into the caller.
        with (
            patch.object(rid, "_log_path", side_effect=RuntimeError("no fs")),
            patch.object(rid, "_record_refusal_redis"),
        ):
            rid.log_run_identity_event(2144, "s", "LEASE_ABSENT", False, None, None)

    def test_redis_recorder_fail_open(self):
        # No Redis available in unit env — must swallow the error.
        rid._record_refusal_redis(2144, "stage_marker", "LEASE_ABSENT", False)


class TestStageMarkerCliSelfHeal:
    def _run_main(self, argv):
        import tools.sdlc_stage_marker as sm

        with patch("sys.argv", argv):
            try:
                sm.main()
            except SystemExit as e:
                return e.code
        return None

    def test_no_run_id_but_healable_writes_and_exits_0(self, capsys):
        import tools.sdlc_stage_marker as sm

        with (
            patch.object(sm, "heal_missing_run_id", return_value="healed-run"),
            patch.object(
                sm, "write_marker", return_value=({"stage": "BUILD", "status": "completed"}, 0)
            ) as wm,
        ):
            code = self._run_main(
                [
                    "sdlc-tool",
                    "--stage",
                    "BUILD",
                    "--status",
                    "completed",
                    "--issue-number",
                    "2144",
                ]
            )
        assert code == 0
        # write ran under the healed id
        assert wm.call_args.kwargs["run_id"] == "healed-run"
        out = json.loads(capsys.readouterr().out)
        assert out["stage"] == "BUILD"

    def test_no_run_id_unhealable_keeps_run_id_required(self, capsys):
        import tools.sdlc_stage_marker as sm

        with (
            patch.object(sm, "heal_missing_run_id", return_value=None),
            patch.object(sm, "write_marker") as wm,
        ):
            code = self._run_main(
                [
                    "sdlc-tool",
                    "--stage",
                    "BUILD",
                    "--status",
                    "completed",
                    "--issue-number",
                    "2144",
                ]
            )
        assert code == 2
        wm.assert_not_called()
        assert json.loads(capsys.readouterr().out) == {"error": "RUN_ID_REQUIRED"}

    def test_stale_run_id_lease_absent_heals_and_retries(self, capsys):
        import tools.sdlc_stage_marker as sm

        # First write refuses LEASE_ABSENT; heal yields a new id; retry succeeds.
        writes = [
            ({"error": "lease_absent", "reason": "LEASE_ABSENT"}, 1),
            ({"stage": "BUILD", "status": "completed"}, 0),
        ]
        with (
            patch.object(sm, "write_marker", side_effect=writes) as wm,
            patch.object(sm, "maybe_heal_after_write", return_value="healed-run"),
        ):
            code = self._run_main(
                [
                    "sdlc-tool",
                    "--stage",
                    "BUILD",
                    "--status",
                    "completed",
                    "--issue-number",
                    "2144",
                    "--run-id",
                    "stale-run",
                ]
            )
        assert code == 0
        assert wm.call_count == 2
        assert wm.call_args_list[1].kwargs["run_id"] == "healed-run"
        assert json.loads(capsys.readouterr().out)["status"] == "completed"

    def test_stale_run_id_unhealable_refusal_stands(self, capsys):
        import tools.sdlc_stage_marker as sm

        with (
            patch.object(
                sm,
                "write_marker",
                return_value=({"error": "issue_locked", "reason": "ISSUE_LOCKED"}, 1),
            ) as wm,
            patch.object(sm, "maybe_heal_after_write", return_value=None),
        ):
            code = self._run_main(
                [
                    "sdlc-tool",
                    "--stage",
                    "BUILD",
                    "--status",
                    "completed",
                    "--issue-number",
                    "2144",
                    "--run-id",
                    "stale-run",
                ]
            )
        assert code == 1
        # only the initial write, no successful retry
        assert wm.call_count == 1


class TestDispatchCliSelfHeal:
    def _run_main(self, argv):
        import tools.sdlc_dispatch as d

        with patch("sys.argv", argv):
            try:
                d.main()
            except SystemExit as e:
                return e.code
        return None

    def test_no_run_id_but_healable_records_and_exits_0(self, capsys):
        import tools.sdlc_dispatch as d

        with (
            patch.object(d, "heal_missing_run_id", return_value="healed-run"),
            patch.object(d, "_cli_record", return_value={"recorded": True}) as rec,
        ):
            code = self._run_main(
                ["sdlc-tool", "record", "--skill", "/do-build", "--issue-number", "2144"]
            )
        assert code == 0
        # record ran under the healed id (args namespace carries the healed run_id)
        assert rec.call_args.args[0].run_id == "healed-run"

    def test_no_run_id_unhealable_keeps_run_id_required(self, capsys):
        import tools.sdlc_dispatch as d

        with (
            patch.object(d, "heal_missing_run_id", return_value=None),
            patch.object(d, "_cli_record") as rec,
        ):
            code = self._run_main(
                ["sdlc-tool", "record", "--skill", "/do-build", "--issue-number", "2144"]
            )
        assert code == 2
        rec.assert_not_called()
        assert json.loads(capsys.readouterr().out) == {"error": "RUN_ID_REQUIRED"}

    def test_stale_run_id_lease_absent_heals_and_retries(self, capsys):
        import tools.sdlc_dispatch as d

        outcomes = [{"reason": "LEASE_ABSENT"}, {"recorded": True}]
        with (
            patch.object(d, "_cli_record", side_effect=outcomes) as rec,
            patch.object(d, "maybe_heal_after_write", return_value="healed-run"),
        ):
            code = self._run_main(
                [
                    "sdlc-tool",
                    "record",
                    "--skill",
                    "/do-build",
                    "--issue-number",
                    "2144",
                    "--run-id",
                    "stale-run",
                ]
            )
        assert code == 0
        assert rec.call_count == 2
        assert rec.call_args_list[1].args[0].run_id == "healed-run"


class TestMetaSetCliSelfHeal:
    def _run_main(self, argv):
        import tools.sdlc_meta_set as m

        with patch("sys.argv", argv):
            try:
                m.main()
            except SystemExit as e:
                return e.code
        return None

    def test_no_run_id_but_healable_writes_and_exits_0(self, capsys):
        import tools.sdlc_meta_set as m

        with (
            patch.object(m, "heal_missing_run_id", return_value="healed-run"),
            patch.object(
                m, "write_meta", return_value={"key": "plan_revising", "value": True}
            ) as wm,
        ):
            code = self._run_main(
                [
                    "sdlc-tool",
                    "--key",
                    "plan_revising",
                    "--value",
                    "true",
                    "--issue-number",
                    "2144",
                ]
            )
        assert code == 0
        assert wm.call_args.kwargs["run_id"] == "healed-run"

    def test_no_run_id_unhealable_keeps_run_id_required(self, capsys):
        import tools.sdlc_meta_set as m

        with (
            patch.object(m, "heal_missing_run_id", return_value=None),
            patch.object(m, "write_meta") as wm,
        ):
            code = self._run_main(
                [
                    "sdlc-tool",
                    "--key",
                    "plan_revising",
                    "--value",
                    "true",
                    "--issue-number",
                    "2144",
                ]
            )
        assert code == 2
        wm.assert_not_called()
        assert json.loads(capsys.readouterr().out) == {"error": "RUN_ID_REQUIRED"}

    def test_stale_run_id_lease_absent_heals_and_retries(self, capsys):
        import tools.sdlc_meta_set as m

        outcomes = [{"reason": "LEASE_ABSENT"}, {"key": "plan_revising", "value": True}]
        with (
            patch.object(m, "write_meta", side_effect=outcomes) as wm,
            patch.object(m, "maybe_heal_after_write", return_value="healed-run"),
        ):
            code = self._run_main(
                [
                    "sdlc-tool",
                    "--key",
                    "plan_revising",
                    "--value",
                    "true",
                    "--issue-number",
                    "2144",
                    "--run-id",
                    "stale-run",
                ]
            )
        assert code == 0
        assert wm.call_count == 2
        assert wm.call_args_list[1].kwargs["run_id"] == "healed-run"


class TestVerdictCliSelfHeal:
    def _run_main(self, argv):
        import tools.sdlc_verdict as v

        with patch("sys.argv", argv):
            try:
                v.main()
            except SystemExit as e:
                return e.code
        return None

    def test_no_run_id_but_healable_records_and_exits_0(self, capsys):
        import tools.sdlc_verdict as v

        with (
            patch.object(v, "heal_missing_run_id", return_value="healed-run"),
            patch.object(
                v, "_cli_record", return_value={"stage": "CRITIQUE", "verdict": "READY TO BUILD"}
            ) as rec,
        ):
            code = self._run_main(
                [
                    "sdlc-tool",
                    "record",
                    "--stage",
                    "CRITIQUE",
                    "--verdict",
                    "READY TO BUILD",
                    "--issue-number",
                    "2144",
                ]
            )
        assert code == 0
        assert rec.call_args.args[0].run_id == "healed-run"

    def test_ownership_error_lease_absent_heals_and_retries(self, capsys):
        import tools.sdlc_verdict as v

        outcomes = [
            v.OwnershipError("LEASE_ABSENT: no live lease"),
            {"stage": "CRITIQUE", "verdict": "READY TO BUILD"},
        ]
        with (
            patch.object(v, "_cli_record", side_effect=outcomes) as rec,
            patch.object(v, "maybe_heal_after_write", return_value="healed-run"),
        ):
            code = self._run_main(
                [
                    "sdlc-tool",
                    "record",
                    "--stage",
                    "CRITIQUE",
                    "--verdict",
                    "READY TO BUILD",
                    "--issue-number",
                    "2144",
                    "--run-id",
                    "stale-run",
                ]
            )
        assert code == 0
        assert rec.call_count == 2
        assert rec.call_args_list[1].args[0].run_id == "healed-run"

    def test_ownership_error_unhealable_refusal_stands(self, capsys):
        import tools.sdlc_verdict as v

        with (
            patch.object(
                v, "_cli_record", side_effect=v.OwnershipError("ISSUE_LOCKED: foreign holder")
            ) as rec,
            patch.object(v, "maybe_heal_after_write", return_value=None),
        ):
            code = self._run_main(
                [
                    "sdlc-tool",
                    "record",
                    "--stage",
                    "CRITIQUE",
                    "--verdict",
                    "READY TO BUILD",
                    "--issue-number",
                    "2144",
                    "--run-id",
                    "stale-run",
                ]
            )
        assert code == 1
        assert rec.call_count == 1
