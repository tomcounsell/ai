"""Tests for the nightly autonomous-fix decision gate (issue #2334, shadow tier).

`decide_fix_or_escalate` is pure, so every branch is reachable here without a
subprocess. `gate_reason` is tested alongside it because the clause ORDER is the
`reason=` token vocabulary — a month of shadow logs is only legible if the first
failing condition is the one reported.

The mode-gating tests drive `main()` end to end and assert the thing this tier
promises above all else: the up-front page fires in BOTH shipped modes with a
byte-identical message. `shadow` adds a classification and a log line and
changes no outbound behavior.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

import nightly_regression_tests as nrt  # noqa: E402


def _flags(**kwargs) -> nrt.RunFlags:
    base = {
        "is_seed_run": False,
        "integrity_warnings": [],
        "dry_run": False,
        "baseline_sha": "cafef00d",
    }
    base.update(kwargs)
    return nrt.RunFlags(**base)


def _caps(max_failures: int = 15) -> nrt.GateCaps:
    return nrt.GateCaps(max_failures=max_failures)


def _clean(nodes: list[str]) -> dict[str, list[str]]:
    return {"newly_broken": list(nodes), "pre_existing": [], "inconclusive": []}


def _decide(classification, new_failures, *, caps=None, flags=None):
    caps = caps or _caps()
    flags = flags if flags is not None else _flags()
    return (
        nrt.decide_fix_or_escalate(classification, new_failures, caps, flags),
        nrt.gate_reason(classification, new_failures, caps, flags),
    )


# --- the positive case the feature exists for --------------------------------


class TestMotivatingCase:
    """#2399's shape must not be disqualified by the feature written for it."""

    def test_eleven_all_newly_broken_nodes_reach_autonomous_fix(self) -> None:
        nodes = [f"tests/unit/test_mod.py::test_{i}" for i in range(11)]
        verdict, reason = _decide(_clean(nodes), nodes)
        assert verdict == "autonomous-fix"
        assert reason == "none"

    def test_motivating_case_is_not_disqualified_by_dispatch_truncation(self) -> None:
        """MAX_DISPATCH_NODES truncates the triage-FILING set, never new_failures.

        Folding it into this gate would make the effective ceiling
        min(NIGHTLY_FIX_MAX_FAILURES, 10), killing every configured value 11..15
        and turning NIGHTLY_FIX_MAX_FAILURES into dead config.
        """
        nodes = [f"tests/unit/test_mod.py::test_{i}" for i in range(11)]
        assert len(nodes) > nrt.MAX_DISPATCH_NODES
        verdict, reason = _decide(_clean(nodes), nodes)
        assert (verdict, reason) == ("autonomous-fix", "none")

    def test_a_single_newly_broken_node_reaches_autonomous_fix(self) -> None:
        verdict, reason = _decide(_clean(["a::t1"]), ["a::t1"])
        assert (verdict, reason) == ("autonomous-fix", "none")


# --- classification-derived escalate branches --------------------------------


class TestClassificationDisqualifiers:
    def test_any_pre_existing_node_escalates(self) -> None:
        classification = {
            "newly_broken": ["a::t1"],
            "pre_existing": ["b::t2"],
            "inconclusive": [],
        }
        assert _decide(classification, ["a::t1", "b::t2"]) == ("escalate", "pre_existing")

    def test_any_inconclusive_node_escalates(self) -> None:
        classification = {
            "newly_broken": ["a::t1"],
            "pre_existing": [],
            "inconclusive": ["b::t2"],
        }
        assert _decide(classification, ["a::t1", "b::t2"]) == ("escalate", "inconclusive")

    def test_pre_existing_is_reported_before_inconclusive(self) -> None:
        """Clause order IS the token vocabulary; both non-empty reports the first."""
        classification = {
            "newly_broken": [],
            "pre_existing": ["b::t2"],
            "inconclusive": ["c::t3"],
        }
        assert _decide(classification, ["b::t2", "c::t3"])[1] == "pre_existing"

    def test_new_failures_not_covered_by_newly_broken_escalates(self) -> None:
        """A node missing from every bucket must never read as eligible."""
        classification = _clean(["a::t1"])
        assert _decide(classification, ["a::t1", "b::t2"]) == ("escalate", "not_all_newly_broken")

    def test_newly_broken_superset_of_new_failures_escalates(self) -> None:
        classification = _clean(["a::t1", "b::t2"])
        assert _decide(classification, ["a::t1"]) == ("escalate", "not_all_newly_broken")

    def test_empty_new_failures_with_empty_classification_is_not_a_fix(self) -> None:
        """Nothing to fix is not the same as "the gate would have fired"."""
        verdict, reason = _decide(nrt.empty_classification(), [])
        # An empty set trivially equals an empty newly_broken bucket, so the
        # clause chain reports `none`; the caller never reaches the gate at all
        # with an empty new_failures set (Data Flow precondition 2).
        assert (verdict, reason) == ("autonomous-fix", "none")

    def test_empty_new_failures_never_reaches_the_gate_from_main(self, tmp_path: Path) -> None:
        """The real guarantee: no verdict line at all when nothing is newly failing."""
        rc, _state, mock_send, log_text = _run_main(tmp_path, confirmed=[], mode="shadow")
        assert rc == 0
        assert "shadow-verdict" not in log_text
        mock_send.assert_not_called()


# --- run-shape and data disqualifiers ----------------------------------------


class TestRunShapeDisqualifiers:
    """Four run-shape/data refusals plus the cap. Deliberately NOT a truncation clause."""

    def test_seed_run_escalates(self) -> None:
        """A re-baseline declares state; it does not discover a regression."""
        nodes = ["a::t1"]
        assert _decide(_clean(nodes), nodes, flags=_flags(is_seed_run=True)) == (
            "escalate",
            "seed_run",
        )

    def test_integrity_warnings_escalate(self) -> None:
        """An untrusted confirmed set is no basis for a verdict."""
        nodes = ["a::t1"]
        assert _decide(
            _clean(nodes), nodes, flags=_flags(integrity_warnings=["total shrank 12%"])
        ) == ("escalate", "integrity_warnings")

    def test_dry_run_escalates(self) -> None:
        """--dry-run stays a pure preview."""
        nodes = ["a::t1"]
        assert _decide(_clean(nodes), nodes, flags=_flags(dry_run=True)) == (
            "escalate",
            "dry_run",
        )

    def test_no_baseline_sha_escalates(self) -> None:
        """First run after deploy, or state from an older schema."""
        nodes = ["a::t1"]
        assert _decide(_clean(nodes), nodes, flags=_flags(baseline_sha="")) == (
            "escalate",
            "no_baseline_sha",
        )

    def test_over_max_failures_escalates(self) -> None:
        nodes = [f"a::t{i}" for i in range(16)]
        assert _decide(_clean(nodes), nodes, caps=_caps(15)) == (
            "escalate",
            "over_max_failures",
        )

    def test_exactly_at_the_cap_is_allowed(self) -> None:
        """The ceiling is inclusive; an off-by-one here silently kills the top value."""
        nodes = [f"a::t{i}" for i in range(15)]
        assert _decide(_clean(nodes), nodes, caps=_caps(15)) == ("autonomous-fix", "none")

    def test_run_shape_clauses_short_circuit_in_declared_order(self) -> None:
        """seed_run < integrity_warnings < dry_run < no_baseline_sha < over_max_failures."""
        nodes = [f"a::t{i}" for i in range(99)]
        flags = _flags(
            is_seed_run=True,
            integrity_warnings=["w"],
            dry_run=True,
            baseline_sha="",
        )
        assert nrt.gate_reason(_clean(nodes), nodes, _caps(15), flags) == "seed_run"

        flags = _flags(integrity_warnings=["w"], dry_run=True, baseline_sha="")
        assert nrt.gate_reason(_clean(nodes), nodes, _caps(15), flags) == "integrity_warnings"

        flags = _flags(dry_run=True, baseline_sha="")
        assert nrt.gate_reason(_clean(nodes), nodes, _caps(15), flags) == "dry_run"

        flags = _flags(baseline_sha="")
        assert nrt.gate_reason(_clean(nodes), nodes, _caps(15), flags) == "no_baseline_sha"

        assert nrt.gate_reason(_clean(nodes), nodes, _caps(15), _flags()) == "over_max_failures"

    def test_run_shape_clauses_precede_the_classification_clauses(self) -> None:
        """A refused night reports its precondition, not a bucket it never computed."""
        classification = {"newly_broken": [], "pre_existing": ["a::t1"], "inconclusive": []}
        assert (
            nrt.gate_reason(classification, ["a::t1"], _caps(), _flags(is_seed_run=True))
            == "seed_run"
        )

    def test_gate_has_no_dispatch_truncation_clause(self) -> None:
        """A run whose dispatch set was truncated is still gate-eligible."""
        nodes = [f"a::t{i}" for i in range(nrt.MAX_DISPATCH_NODES + 3)]
        assert len(nodes) <= nrt.NIGHTLY_FIX_MAX_FAILURES
        assert _decide(_clean(nodes), nodes) == ("autonomous-fix", "none")


# --- mode resolution ---------------------------------------------------------


class TestResolveFixMode:
    @pytest.fixture(autouse=True)
    def _reset_warn_latch(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "nightly.log")
        monkeypatch.setattr(nrt, "_FIX_MODE_WARNED", False)

    @pytest.mark.parametrize("raw", ["off", "OFF", " off ", "shadow", "SHADOW"])
    def test_recognized_values_normalize(self, raw: str) -> None:
        assert nrt.resolve_fix_mode(raw) == raw.strip().lower()

    @pytest.mark.parametrize("raw", ["active", "", "on", "true", "Shadow-mode"])
    def test_unrecognized_value_is_treated_as_off(self, raw: str, tmp_path: Path) -> None:
        """Fail toward the detector's pre-feature behavior, and say so once."""
        assert nrt.resolve_fix_mode(raw) == "off"
        assert "unrecognized NIGHTLY_FIX_MODE" in (tmp_path / "nightly.log").read_text()

    def test_warning_is_emitted_once_per_process(self, tmp_path: Path) -> None:
        nrt.resolve_fix_mode("active")
        nrt.resolve_fix_mode("active")
        log_text = (tmp_path / "nightly.log").read_text()
        assert log_text.count("unrecognized NIGHTLY_FIX_MODE") == 1

    def test_default_reads_the_module_constant(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(nrt, "NIGHTLY_FIX_MODE", "off")
        assert nrt.resolve_fix_mode() == "off"
        monkeypatch.setattr(nrt, "NIGHTLY_FIX_MODE", "shadow")
        assert nrt.resolve_fix_mode() == "shadow"


# --- verdict log format ------------------------------------------------------


class TestVerdictLogFormat:
    @pytest.fixture(autouse=True)
    def _log(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "nightly.log")

    def test_autonomous_fix_run_logs_reason_none(self, tmp_path: Path) -> None:
        nodes = [f"a::t{i}" for i in range(11)]
        with patch.object(nrt, "classify_against_baseline", return_value=_clean(nodes)):
            nrt.log_shadow_verdict(nodes, _caps(), _flags())
        log_text = (tmp_path / "nightly.log").read_text()
        assert "nightly-fix shadow-verdict: autonomous-fix reason=none nodes=11" in log_text
        assert (
            "nightly-fix shadow-buckets: newly_broken=11 pre_existing=0 inconclusive=0 "
            "not_newly_broken=" in log_text
        )

    def test_escalate_run_logs_the_first_failing_reason(self, tmp_path: Path) -> None:
        classification = {
            "newly_broken": ["a::t1"],
            "pre_existing": [],
            "inconclusive": ["b::t2"],
        }
        with patch.object(nrt, "classify_against_baseline", return_value=classification):
            nrt.log_shadow_verdict(["a::t1", "b::t2"], _caps(), _flags())
        log_text = (tmp_path / "nightly.log").read_text()
        assert "nightly-fix shadow-verdict: escalate reason=inconclusive nodes=2" in log_text
        assert (
            "nightly-fix shadow-buckets: newly_broken=1 pre_existing=0 inconclusive=1 "
            "not_newly_broken=b::t2" in log_text
        )

    def test_classifier_is_called_with_the_baseline_sha(self, tmp_path: Path) -> None:
        with patch.object(
            nrt, "classify_against_baseline", return_value=_clean(["a::t1"])
        ) as classify:
            nrt.log_shadow_verdict(["a::t1"], _caps(), _flags(baseline_sha="feedface"))
        classify.assert_called_once_with(["a::t1"], "feedface")


# --- mode gating, driven through main() --------------------------------------


def _run_result(confirmed: list[str], total: int = 11) -> dict:
    return {
        "passed": total - len(confirmed),
        "failed": len(confirmed),
        "error": 0,
        "skipped": 0,
        "total": total,
        "failing_parallel": list(confirmed),
        "run_at": "2026-09-02T00:00:00+00:00",
    }


def _run_main(
    tmp_path: Path,
    *,
    confirmed: list[str],
    mode: str,
    prev_extra: dict | None = None,
    classification: dict[str, list[str]] | None = None,
):
    """Drive main() with everything below the alert branch stubbed out.

    Returns ``(rc, persisted_state_or_None, mock_send, log_text)``.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    log_file = tmp_path / "nightly.log"
    prev = {
        "collection": nrt.COLLECTION_PATHS,
        "failing_tests": [],
        "dispatched_nodes": [],
        "head_commit": "baselinesha",
    }
    prev.update(prev_extra or {})

    serial_report = tmp_path / "serial.json"
    serial_report.write_text(json.dumps({"tests": []}))

    last_run = tmp_path / "last_run.json"
    last_run.write_text(json.dumps(prev))

    raw_report = {"summary": {"total": 11}, "tests": []}
    classification = classification if classification is not None else _clean(confirmed)

    with (
        patch.object(nrt, "LOG_FILE", log_file),
        patch.object(nrt, "LOCK_FILE", tmp_path / "nightly.lock"),
        patch.object(nrt, "LAST_RUN_FILE", last_run),
        patch.object(nrt, "PYTEST_SERIAL_JSON_TMP", str(serial_report)),
        patch.object(nrt, "NIGHTLY_FIX_MODE", mode),
        patch.object(nrt, "MIN_EXPECTED_COLLECTED", 0),
        patch("sys.argv", ["nightly_regression_tests.py"]),
        patch.object(nrt, "load_env_or_die", return_value=(42, None)),
        patch.object(nrt, "run_tests", return_value=(raw_report, _run_result(confirmed), 0)),
        patch.object(nrt, "reconfirm_serial", return_value=(list(confirmed), [], True)),
        patch.object(nrt, "summarize_failures", return_value="mocked summary"),
        patch.object(nrt, "maybe_dispatch_triage_session", return_value="sess-1"),
        patch.object(nrt, "run_ttft_gate", return_value=None),
        patch.object(nrt, "_get_head_commit", return_value="headsha"),
        patch.object(nrt, "classify_against_baseline", return_value=classification) as classify,
        patch.object(nrt, "send_telegram") as mock_send,
    ):
        rc = nrt.main()

    state = json.loads(last_run.read_text())
    log_text = log_file.read_text() if log_file.exists() else ""
    mock_send.classify = classify
    return rc, state, mock_send, log_text


class TestModeGating:
    """The up-front page fires in BOTH shipped modes, byte-identically."""

    NODES = ["tests/unit/test_a.py::test_one", "tests/unit/test_b.py::test_two"]

    def test_mode_gating_off_pages_and_does_no_classification(self, tmp_path: Path) -> None:
        rc, _state, mock_send, log_text = _run_main(tmp_path, confirmed=self.NODES, mode="off")
        assert rc == 0
        mock_send.assert_called_once()
        mock_send.classify.assert_not_called()
        assert "shadow-verdict" not in log_text
        assert "shadow-buckets" not in log_text

    def test_mode_gating_shadow_pages_and_logs_the_verdict(self, tmp_path: Path) -> None:
        rc, _state, mock_send, log_text = _run_main(tmp_path, confirmed=self.NODES, mode="shadow")
        assert rc == 0
        mock_send.assert_called_once()
        mock_send.classify.assert_called_once_with(sorted(self.NODES), "baselinesha")
        assert "nightly-fix shadow-verdict: autonomous-fix reason=none nodes=2" in log_text

    def test_mode_gating_alert_text_is_byte_identical_across_modes(self, tmp_path: Path) -> None:
        """The behavioral assertion the report-path blocker would have failed.

        If the classifier had reused PYTEST_SERIAL_JSON_TMP, the shadow-mode
        alert would be summarized from a report in which every newly-broken
        node passed.
        """
        _rc, _s, send_off, _l = _run_main(tmp_path / "off", confirmed=self.NODES, mode="off")
        _rc, _s, send_shadow, _l = _run_main(
            tmp_path / "shadow", confirmed=self.NODES, mode="shadow"
        )
        off_msg = send_off.call_args.args[0]
        shadow_msg = send_shadow.call_args.args[0]
        assert off_msg == shadow_msg
        assert "newly-confirmed failure(s)" in off_msg

    def test_mode_gating_unrecognized_mode_behaves_like_off(self, tmp_path: Path) -> None:
        with patch.object(nrt, "_FIX_MODE_WARNED", False):
            _rc, _s, mock_send, log_text = _run_main(tmp_path, confirmed=self.NODES, mode="active")
        mock_send.assert_called_once()
        mock_send.classify.assert_not_called()
        assert "shadow-verdict" not in log_text

    def test_mode_gating_shadow_escalate_still_pages(self, tmp_path: Path) -> None:
        """An escalate verdict changes nothing outbound in this tier."""
        classification = {
            "newly_broken": [],
            "pre_existing": list(self.NODES),
            "inconclusive": [],
        }
        _rc, _s, mock_send, log_text = _run_main(
            tmp_path,
            confirmed=self.NODES,
            mode="shadow",
            classification=classification,
        )
        mock_send.assert_called_once()
        assert "nightly-fix shadow-verdict: escalate reason=pre_existing nodes=2" in log_text
