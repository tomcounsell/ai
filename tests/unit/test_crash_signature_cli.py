"""
Unit tests for valor-session crash-signatures and crash-policy CLI subcommands.

Tests use mocking for CrashSignature.all_for_project() so no real Redis
connection is required.
"""

import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sig(
    signature_hash="abc123de" * 4,
    human_form="mid_stream|idle_gap[medium]+status_transition[to=failed,dead=false,sig=SIGTERM]",
    signature_class="mid_stream",
    resumable=True,
    escalated=False,
    occurrence_count=5,
    outcome_tallies=None,
    auto_eligible=True,
):
    """Build a minimal mock CrashSignature object."""
    s = MagicMock()
    s.signature_hash = signature_hash
    s.human_form = human_form
    s.signature_class = signature_class
    s.resumable = resumable
    s.escalated = escalated
    s.occurrence_count = occurrence_count
    s.outcome_tallies = outcome_tallies if outcome_tallies is not None else {"continue": 3}
    s.auto_eligible = auto_eligible
    return s


def _args(**kwargs):
    """Build a minimal argparse.Namespace."""
    defaults = {
        "json": False,
        "project": "valor",
        "min_occurrences": 1,
        "min_success_ratio": 0.7,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# crash-signatures
# ---------------------------------------------------------------------------


class TestCrashSignaturesCmd:
    """Tests for cmd_crash_signatures."""

    def _run(self, signatures, **kw):
        """Invoke cmd_crash_signatures with a mocked CrashSignature model."""
        from tools.valor_session import cmd_crash_signatures

        mock_cls = MagicMock()
        mock_cls.all_for_project.return_value = signatures
        with patch.dict(
            "sys.modules", {"models.crash_signature": MagicMock(CrashSignature=mock_cls)}
        ):
            with patch("tools.valor_session._load_env"):
                # patch the import inside the function
                with patch("builtins.__import__", side_effect=_import_with_crash_sig(mock_cls)):
                    return cmd_crash_signatures(_args(**kw))

    def test_empty_library_exits_0(self, capsys):
        """Empty library prints a helpful message and exits 0."""
        from tools.valor_session import cmd_crash_signatures

        mock_cls = MagicMock()
        mock_cls.all_for_project.return_value = []
        with _patch_crash_sig(mock_cls):
            rc = cmd_crash_signatures(_args())
        out = capsys.readouterr().out
        assert rc == 0
        assert "No crash signatures recorded yet." in out

    def test_populated_library_renders(self, capsys):
        """Populated library renders signature lines correctly."""
        from tools.valor_session import cmd_crash_signatures

        sig = _make_sig(
            occurrence_count=5,
            outcome_tallies={"continue": 3, "recovered": 2},
            auto_eligible=True,
        )
        mock_cls = MagicMock()
        mock_cls.all_for_project.return_value = [sig]
        with _patch_crash_sig(mock_cls):
            rc = cmd_crash_signatures(_args())
        out = capsys.readouterr().out
        assert rc == 0
        assert "Crash Signatures (project: valor)" in out
        assert "abc123de" in out
        assert "occurrences: 5" in out
        assert "resumable: yes" in out

    def test_non_resumable_renders_no(self, capsys):
        """Non-resumable signatures show 'resumable: NO'."""
        from tools.valor_session import cmd_crash_signatures

        sig = _make_sig(resumable=False, escalated=True, occurrence_count=2, auto_eligible=False)
        mock_cls = MagicMock()
        mock_cls.all_for_project.return_value = [sig]
        with _patch_crash_sig(mock_cls):
            rc = cmd_crash_signatures(_args())
        out = capsys.readouterr().out
        assert rc == 0
        assert "resumable: NO" in out
        assert "escalated: yes" in out

    def test_json_output_is_valid_json(self, capsys):
        """--json produces parseable JSON list."""
        from tools.valor_session import cmd_crash_signatures

        sig = _make_sig(occurrence_count=3, outcome_tallies={"continue": 2})
        mock_cls = MagicMock()
        mock_cls.all_for_project.return_value = [sig]
        with _patch_crash_sig(mock_cls):
            rc = cmd_crash_signatures(_args(json=True))
        out = capsys.readouterr().out
        assert rc == 0
        parsed = json.loads(out)
        assert isinstance(parsed, list)
        assert len(parsed) == 1
        item = parsed[0]
        assert "signature_hash" in item
        assert "human_form" in item
        assert "occurrence_count" in item
        assert item["occurrence_count"] == 3

    def test_json_empty_library(self, capsys):
        """--json with empty library returns empty list."""
        from tools.valor_session import cmd_crash_signatures

        mock_cls = MagicMock()
        mock_cls.all_for_project.return_value = []
        with _patch_crash_sig(mock_cls):
            rc = cmd_crash_signatures(_args(json=True))
        out = capsys.readouterr().out
        assert rc == 0
        parsed = json.loads(out)
        assert parsed == []

    def test_min_occurrences_filters(self, capsys):
        """--min-occurrences filters out low-count signatures."""
        from tools.valor_session import cmd_crash_signatures

        sig_low = _make_sig(signature_hash="aaaa" * 8, occurrence_count=1)
        sig_high = _make_sig(signature_hash="bbbb" * 8, occurrence_count=5)
        mock_cls = MagicMock()
        mock_cls.all_for_project.return_value = [sig_low, sig_high]
        with _patch_crash_sig(mock_cls):
            rc = cmd_crash_signatures(_args(min_occurrences=3))
        out = capsys.readouterr().out
        assert rc == 0
        assert "bbbb" in out
        assert "aaaa" not in out

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
    """Tests for cmd_crash_policy."""

    def test_empty_library_exits_0(self, capsys):
        """Empty library prints cold-library message and exits 0."""
        from tools.valor_session import cmd_crash_policy

        mock_cls = MagicMock()
        mock_cls.all_for_project.return_value = []
        with _patch_crash_sig(mock_cls):
            rc = cmd_crash_policy(_args())
        out = capsys.readouterr().out
        assert rc == 0
        assert "No auto-resume policy entries" in out
        assert "cold" in out

    def test_populated_policy_renders(self, capsys):
        """Populated library renders policy entries."""
        from tools.valor_session import cmd_crash_policy

        sig = _make_sig(
            occurrence_count=5,
            outcome_tallies={"continue": 1, "recovered": 2},
            resumable=True,
        )
        mock_cls = MagicMock()
        mock_cls.all_for_project.return_value = [sig]
        with _patch_crash_sig(mock_cls):
            rc = cmd_crash_policy(_args())
        out = capsys.readouterr().out
        assert rc == 0
        assert "Auto-Resume Policy (project: valor)" in out
        assert "Signature:" in out
        assert "Confidence:" in out
        assert "Auto-eligible:" in out

    def test_non_resumable_shows_no_entry(self, capsys):
        """Non-resumable signatures render as no-entry lines."""
        from tools.valor_session import cmd_crash_policy

        sig = _make_sig(resumable=False, escalated=True, occurrence_count=2)
        mock_cls = MagicMock()
        mock_cls.all_for_project.return_value = [sig]
        with _patch_crash_sig(mock_cls):
            rc = cmd_crash_policy(_args())
        out = capsys.readouterr().out
        assert rc == 0
        assert "NON_RESUMABLE" in out

    def test_json_output_valid(self, capsys):
        """--json produces valid JSON list with expected keys."""
        from tools.valor_session import cmd_crash_policy

        sig = _make_sig(
            occurrence_count=4,
            outcome_tallies={"continue": 3},
            resumable=True,
        )
        mock_cls = MagicMock()
        mock_cls.all_for_project.return_value = [sig]
        with _patch_crash_sig(mock_cls):
            rc = cmd_crash_policy(_args(json=True))
        out = capsys.readouterr().out
        assert rc == 0
        parsed = json.loads(out)
        assert isinstance(parsed, list)
        item = parsed[0]
        assert "signature_hash" in item
        assert "confidence" in item
        assert "auto_eligible" in item
        assert "attempts" in item
        assert "recovered" in item

    def test_json_empty_library(self, capsys):
        """--json with empty library returns empty list."""
        from tools.valor_session import cmd_crash_policy

        mock_cls = MagicMock()
        mock_cls.all_for_project.return_value = []
        with _patch_crash_sig(mock_cls):
            rc = cmd_crash_policy(_args(json=True))
        out = capsys.readouterr().out
        assert rc == 0
        parsed = json.loads(out)
        assert parsed == []

    def test_auto_eligible_yes_when_thresholds_met(self, capsys):
        """Signature meeting both thresholds shows Auto-eligible: YES."""
        from tools.valor_session import cmd_crash_policy

        sig = _make_sig(
            occurrence_count=5,
            outcome_tallies={"recovered": 3},  # 3/3 = 100% > 70%
            resumable=True,
        )
        mock_cls = MagicMock()
        mock_cls.all_for_project.return_value = [sig]
        with _patch_crash_sig(mock_cls):
            rc = cmd_crash_policy(_args(min_occurrences=3, min_success_ratio=0.7))
        out = capsys.readouterr().out
        assert rc == 0
        assert "Auto-eligible: YES" in out

    def test_auto_eligible_no_when_low_confidence(self, capsys):
        """Signature below confidence threshold shows Auto-eligible: NO."""
        from tools.valor_session import cmd_crash_policy

        # 1 out of 3 = 33% < 70%
        sig = _make_sig(
            occurrence_count=5,
            outcome_tallies={"recovered": 1, "abandoned": 2},
            resumable=True,
        )
        mock_cls = MagicMock()
        mock_cls.all_for_project.return_value = [sig]
        with _patch_crash_sig(mock_cls):
            rc = cmd_crash_policy(_args(min_occurrences=3, min_success_ratio=0.7))
        out = capsys.readouterr().out
        assert rc == 0
        assert "Auto-eligible: NO" in out

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

        mock_cls = MagicMock()
        mock_cls.all_for_project.return_value = []
        with _patch_crash_sig(mock_cls):
            with patch("sys.argv", ["valor-session", "crash-signatures", "--json"]):
                rc = vs.main()
        assert rc == 0
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed == []

    def test_crash_policy_list_dispatches(self, capsys):
        """crash-policy list dispatches to cmd_crash_policy."""
        import tools.valor_session as vs

        mock_cls = MagicMock()
        mock_cls.all_for_project.return_value = []
        with _patch_crash_sig(mock_cls):
            with patch("sys.argv", ["valor-session", "crash-policy", "list", "--json"]):
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


@contextmanager
def _patch_crash_sig(mock_cls):
    """Context manager that patches the models.crash_signature import."""
    mock_module = MagicMock()
    mock_module.CrashSignature = mock_cls
    with patch.dict("sys.modules", {"models.crash_signature": mock_module}):
        with patch("tools.valor_session._load_env"):
            yield


def _import_raising_for_crash_sig(name, *args, **kwargs):
    """Side-effect for builtins.__import__ that raises ImportError for crash_signature."""
    if "crash_signature" in name:
        raise ImportError("mocked unavailable")
    return __import__(name, *args, **kwargs)


def _import_with_crash_sig(mock_cls):
    """Side-effect for builtins.__import__ that injects mock for crash_signature."""
    import importlib

    def _side_effect(name, *args, **kwargs):
        if name == "models.crash_signature":
            mock_mod = MagicMock()
            mock_mod.CrashSignature = mock_cls
            return mock_mod
        return importlib.import_module(name)

    return _side_effect
