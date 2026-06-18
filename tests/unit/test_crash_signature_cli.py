"""
Unit tests for valor-session crash-signatures and crash-policy CLI subcommands.

These tests build REAL CrashSignature records (via get_or_create_by_hash +
upsert_occurrence + record_outcome) so the CLI handlers exercise the real read
path: the nested ``outcome_tallies_json`` shape loaded through ``_load_tallies()``,
``policy_confidence()``, ``occurrence_count_int``, and ``is_auto_eligible()``.

The previous version of these tests mocked a fabricated flat ``outcome_tallies``
attribute, which masked the bug where the CLI read a nonexistent attribute and
always rendered zeros. Building real records makes the tests fail against the
unfixed CLI and pass after the fix.

The autouse ``redis_test_db`` conftest fixture isolates Popoto to a per-worker
test db, so these records never touch production data. Records are deleted in a
finally block for hygiene.
"""

import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from models.crash_signature import CrashSignature

# Project key used to namespace all test signatures.
_TEST_PROJECT = "test-crash-cli"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_real_sig(
    signature_hash: str,
    *,
    human_form: str = "mid_stream|idle_gap[medium]+status_transition[to=failed,dead=false]",
    signature_class: str = "mid_stream",
    resumable: bool = True,
    occurrences: int = 0,
    recovered: int = 0,
    failed: int = 0,
    escalated: bool = False,
    project_key: str = _TEST_PROJECT,
) -> CrashSignature:
    """Build a REAL CrashSignature record with real nested outcome tallies.

    Mirrors how tests/integration/test_crash_auto_resume.py warms the library:
    get_or_create_by_hash -> upsert_occurrence (occurrence_count) ->
    record_outcome (per-strategy nested tally).
    """
    record = CrashSignature.get_or_create_by_hash(
        signature_hash,
        human_form=human_form,
        signature_class=signature_class,
        resumable=resumable,
    )
    record.project_key = project_key
    if escalated:
        record.escalated = True
    record.save()

    for i in range(occurrences):
        record.upsert_occurrence(
            f"sess-{signature_hash[:6]}-{i}",
            terminal_status="failed",
            has_uuid=True,
            project_key=project_key,
        )
    for _ in range(recovered):
        record.record_outcome("auto_resume", recovered=True)
    for _ in range(failed):
        record.record_outcome("auto_resume", recovered=False)
    return record


def _cleanup(*hashes: str) -> None:
    for h in hashes:
        rec = CrashSignature.get_by_hash(h)
        if rec is not None:
            try:
                rec.delete()
            except Exception:
                pass


def _args(**kwargs):
    """Build a minimal argparse.Namespace."""
    defaults = {
        "json": False,
        "project": _TEST_PROJECT,
        "min_occurrences": 1,
        "min_success_ratio": 0.7,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@contextmanager
def _no_env():
    """Patch _load_env so the CLI handler does not touch the real .env."""
    with patch("tools.valor_session._load_env"):
        yield


# ---------------------------------------------------------------------------
# crash-signatures
# ---------------------------------------------------------------------------


class TestCrashSignaturesCmd:
    """Tests for cmd_crash_signatures against real CrashSignature records."""

    def test_empty_library_exits_0(self, capsys):
        """Empty library prints a helpful message and exits 0."""
        from tools.valor_session import cmd_crash_signatures

        with _no_env():
            rc = cmd_crash_signatures(_args())
        out = capsys.readouterr().out
        assert rc == 0
        assert "No crash signatures recorded yet." in out

    def test_populated_library_renders_real_tallies(self, capsys):
        """Populated library renders real occurrence count and real outcome tallies.

        3 occurrences, 2 recovered + 1 failed = 3 attempts, 2 recovered, ~66.7%.
        This FAILS against the unfixed CLI (which read a nonexistent attribute and
        rendered attempts=0/recovered=0) and PASSES after Blocker 2's fix.
        """
        h = "abc123de" * 4
        _make_real_sig(h, occurrences=3, recovered=2, failed=1)
        try:
            from tools.valor_session import cmd_crash_signatures

            with _no_env():
                rc = cmd_crash_signatures(_args())
            out = capsys.readouterr().out
            assert rc == 0
            assert f"Crash Signatures (project: {_TEST_PROJECT})" in out
            assert "abc123de" in out
            assert "occurrences: 3" in out
            assert "resumable: yes" in out
            # Real outcome stats must be rendered (not zeros).
            assert "attempts=3" in out
            assert "recovered=2" in out
            assert "66.7%" in out
        finally:
            _cleanup(h)

    def test_non_resumable_renders_no(self, capsys):
        """Non-resumable signatures show 'resumable: NO' and 'escalated: yes'."""
        h = "dead" * 8
        _make_real_sig(
            h,
            resumable=False,
            signature_class="NON_RESUMABLE_DETERMINISTIC",
            occurrences=2,
            escalated=True,
        )
        try:
            from tools.valor_session import cmd_crash_signatures

            with _no_env():
                rc = cmd_crash_signatures(_args())
            out = capsys.readouterr().out
            assert rc == 0
            assert "resumable: NO" in out
            assert "escalated: yes" in out
        finally:
            _cleanup(h)

    def test_json_output_reflects_real_data(self, capsys):
        """--json produces parseable JSON with real attempts/recovered/confidence."""
        h = "feed" * 8
        _make_real_sig(h, occurrences=3, recovered=3)
        try:
            from tools.valor_session import cmd_crash_signatures

            with _no_env():
                rc = cmd_crash_signatures(_args(json=True))
            out = capsys.readouterr().out
            assert rc == 0
            parsed = json.loads(out)
            assert isinstance(parsed, list)
            assert len(parsed) == 1
            item = parsed[0]
            assert item["occurrence_count"] == 3
            assert item["attempts"] == 3
            assert item["recovered"] == 3
            assert item["policy_confidence"] == 1.0
            assert item["strategy"] == "auto_resume"
        finally:
            _cleanup(h)

    def test_json_empty_library(self, capsys):
        """--json with empty library returns empty list."""
        from tools.valor_session import cmd_crash_signatures

        with _no_env():
            rc = cmd_crash_signatures(_args(json=True))
        out = capsys.readouterr().out
        assert rc == 0
        assert json.loads(out) == []

    def test_min_occurrences_filters(self, capsys):
        """--min-occurrences filters out low-count signatures (real occurrence_count)."""
        h_low = "aaaa" * 8
        h_high = "bbbb" * 8
        _make_real_sig(h_low, occurrences=1)
        _make_real_sig(h_high, occurrences=5)
        try:
            from tools.valor_session import cmd_crash_signatures

            with _no_env():
                rc = cmd_crash_signatures(_args(min_occurrences=3))
            out = capsys.readouterr().out
            assert rc == 0
            assert "bbbb" in out
            assert "aaaa" not in out
        finally:
            _cleanup(h_low, h_high)

    def test_import_error_returns_1(self, capsys):
        """If crash_signature module is unavailable, exit 1 with error."""
        from tools.valor_session import cmd_crash_signatures

        with patch("tools.valor_session._load_env"):
            with patch("builtins.__import__", side_effect=_import_raising_for_crash_sig):
                rc = cmd_crash_signatures(_args())
        err = capsys.readouterr().err
        assert rc == 1
        assert "crash_signature library not available" in err


# ---------------------------------------------------------------------------
# crash-policy list
# ---------------------------------------------------------------------------


class TestCrashPolicyCmd:
    """Tests for cmd_crash_policy against real CrashSignature records."""

    def test_empty_library_exits_0(self, capsys):
        """Empty library prints cold-library message and exits 0."""
        from tools.valor_session import cmd_crash_policy

        with _no_env():
            rc = cmd_crash_policy(_args())
        out = capsys.readouterr().out
        assert rc == 0
        assert "No auto-resume policy entries" in out
        assert "cold" in out

    def test_populated_policy_renders(self, capsys):
        """Populated library renders policy entries with real confidence."""
        h = "c0de" * 8
        _make_real_sig(h, occurrences=5, recovered=2, failed=1)
        try:
            from tools.valor_session import cmd_crash_policy

            with _no_env():
                rc = cmd_crash_policy(_args())
            out = capsys.readouterr().out
            assert rc == 0
            assert f"Auto-Resume Policy (project: {_TEST_PROJECT})" in out
            assert "Signature:" in out
            assert "Confidence:" in out
            assert "Auto-eligible:" in out
            # Real recovered/attempts rendered, not zeros.
            assert "(2/3 recovered)" in out
        finally:
            _cleanup(h)

    def test_non_resumable_shows_no_entry(self, capsys):
        """Non-resumable signatures render as no-entry lines."""
        h = "beef" * 8
        _make_real_sig(
            h,
            resumable=False,
            signature_class="NON_RESUMABLE_DETERMINISTIC",
            occurrences=2,
            escalated=True,
        )
        try:
            from tools.valor_session import cmd_crash_policy

            with _no_env():
                rc = cmd_crash_policy(_args())
            out = capsys.readouterr().out
            assert rc == 0
            assert "NON_RESUMABLE" in out
        finally:
            _cleanup(h)

    def test_json_output_valid(self, capsys):
        """--json produces valid JSON list with real keys and values."""
        h = "1234" * 8
        _make_real_sig(h, occurrences=4, recovered=3)
        try:
            from tools.valor_session import cmd_crash_policy

            with _no_env():
                rc = cmd_crash_policy(_args(json=True))
            out = capsys.readouterr().out
            assert rc == 0
            parsed = json.loads(out)
            assert isinstance(parsed, list)
            item = parsed[0]
            assert item["signature_hash"] == h
            assert item["confidence"] == 1.0
            assert item["attempts"] == 3
            assert item["recovered"] == 3
            assert "auto_eligible" in item
        finally:
            _cleanup(h)

    def test_json_empty_library(self, capsys):
        """--json with empty library returns empty list."""
        from tools.valor_session import cmd_crash_policy

        with _no_env():
            rc = cmd_crash_policy(_args(json=True))
        out = capsys.readouterr().out
        assert rc == 0
        assert json.loads(out) == []

    def test_auto_eligible_yes_when_thresholds_met(self, capsys):
        """Signature meeting both thresholds shows Auto-eligible: YES.

        5 occurrences, 3/3 recovered = 100% > 70%, occurrences >= 3.
        """
        h = "9999" * 8
        _make_real_sig(h, occurrences=5, recovered=3)
        try:
            from tools.valor_session import cmd_crash_policy

            with _no_env():
                rc = cmd_crash_policy(_args(min_occurrences=3, min_success_ratio=0.7))
            out = capsys.readouterr().out
            assert rc == 0
            assert "Auto-eligible: YES" in out
        finally:
            _cleanup(h)

    def test_auto_eligible_no_when_low_confidence(self, capsys):
        """Signature below confidence threshold shows Auto-eligible: NO.

        1 recovered out of 3 attempts = 33% < 70%.
        """
        h = "7777" * 8
        _make_real_sig(h, occurrences=5, recovered=1, failed=2)
        try:
            from tools.valor_session import cmd_crash_policy

            with _no_env():
                rc = cmd_crash_policy(_args(min_occurrences=3, min_success_ratio=0.7))
            out = capsys.readouterr().out
            assert rc == 0
            assert "Auto-eligible: NO" in out
        finally:
            _cleanup(h)

    def test_import_error_returns_1(self, capsys):
        """If crash_signature module is unavailable, exit 1 with error."""
        from tools.valor_session import cmd_crash_policy

        with patch("tools.valor_session._load_env"):
            with patch("builtins.__import__", side_effect=_import_raising_for_crash_sig):
                rc = cmd_crash_policy(_args())
        err = capsys.readouterr().err
        assert rc == 1
        assert "crash_signature library not available" in err


# ---------------------------------------------------------------------------
# CLI integration: subcommand registration
# ---------------------------------------------------------------------------


class TestSubcommandRegistration:
    """Verify the subcommands are wired into the argparse parser."""

    def test_crash_signatures_dispatches(self, capsys):
        """crash-signatures subcommand is wired into main() dispatch."""
        import tools.valor_session as vs

        with _no_env():
            with patch(
                "sys.argv",
                ["valor-session", "crash-signatures", "--project", _TEST_PROJECT, "--json"],
            ):
                rc = vs.main()
        assert rc == 0
        out = capsys.readouterr().out
        assert json.loads(out) == []

    def test_crash_policy_list_dispatches(self, capsys):
        """crash-policy list dispatches to cmd_crash_policy."""
        import tools.valor_session as vs

        with _no_env():
            with patch(
                "sys.argv",
                ["valor-session", "crash-policy", "list", "--project", _TEST_PROJECT, "--json"],
            ):
                rc = vs.main()
        assert rc == 0

    def test_crash_policy_no_action_exits_nonzero(self, capsys):
        """crash-policy without sub-action exits non-zero."""
        import tools.valor_session as vs

        with patch("sys.argv", ["valor-session", "crash-policy"]):
            rc = vs.main()
        assert rc == 1


# ---------------------------------------------------------------------------
# Patch helpers
# ---------------------------------------------------------------------------


def _import_raising_for_crash_sig(name, *args, **kwargs):
    """Side-effect for builtins.__import__ that raises ImportError for crash_signature."""
    if "crash_signature" in name:
        raise ImportError("mocked unavailable")
    return __import__(name, *args, **kwargs)
