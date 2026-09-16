"""The commit-guard self-check's disposition in `/update`'s summary (#3259).

The behavioral self-check is the only signal that asks whether the deployed
guard actually blocks. Its two non-passing outcomes were both mis-dispositioned
and neither was testable, because the logic was inlined in `run_update`:

* FAILED (the guard is deployed and WRONG) rendered as a generic warning, so a
  machine with an unprotected `main` reported "COMPLETED with N warning(s)".
* SKIPPED (the fixture could not be built, so nothing was learned) had no
  reader at all and rendered as nothing.

A proof that can silently stop running is not a proof.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from update import hardlinks  # noqa: E402
from update.run import UpdateResult, report_hardlink_actions  # noqa: E402


def _result(*actions):
    result = UpdateResult()
    sync = hardlinks.HardlinkSyncResult()
    for action in actions:
        sync.actions.append(action)
        if action.action == "error":
            sync.errors += 1
        elif action.action == "skipped":
            sync.skipped += 1
    result.hardlink_result = sync
    return result


def _action(kind, detail):
    return hardlinks.LinkAction("", "~/.claude/hooks/sdlc/x.py", kind, detail)


class TestSelfCheckFailureIsAHardError:
    def test_failed_self_check_fails_the_run(self):
        detail = f"{hardlinks.SELF_CHECK_DETAIL} FAILED: the deployed guard ALLOWED a commit"
        result = _result(_action("error", detail))

        report_hardlink_actions(result, False)

        assert result.errors == [detail], "a wrong guard must fail the run, not warn"
        assert result.warnings == []

    def test_failure_text_survives_into_the_error(self):
        """The generic 'Hardlink step failed: <path>' text told an operator
        nothing about why the fleet was unprotected."""
        detail = f"{hardlinks.SELF_CHECK_DETAIL} FAILED: the deployed guard BLOCKED a git status"
        result = _result(_action("error", detail))

        report_hardlink_actions(result, False)

        assert "BLOCKED" in result.errors[0]
        assert "Hardlink step failed" not in result.errors[0]


class TestOrdinaryHardlinkErrorStaysAWarning:
    def test_a_file_that_did_not_link_does_not_fail_the_run(self):
        """Escalation must be exact: one file failing to link is not evidence
        that the guard is wrong."""
        result = _result(_action("error", "Permission denied"))

        report_hardlink_actions(result, False)

        assert result.errors == []
        assert len(result.warnings) == 1
        assert "Hardlink step failed" in result.warnings[0]


class TestSkippedSelfCheckIsNotSilent:
    def test_skipped_self_check_warns(self):
        detail = f"{hardlinks.SELF_CHECK_DETAIL} skipped: fixture could not be built (no git)"
        result = _result(_action("skipped", detail))

        report_hardlink_actions(result, False)

        assert result.warnings == [detail], "a skipped behavioral proof must still be said"
        assert result.errors == [], "a skip is not a failure; it must not brick /update"

    def test_unrelated_skip_is_not_reported(self):
        result = _result(_action("skipped", "already up to date"))

        report_hardlink_actions(result, False)

        assert result.warnings == []
        assert result.errors == []


class TestNoHardlinkResult:
    def test_absent_result_is_not_an_error(self):
        result = UpdateResult()
        result.hardlink_result = None

        report_hardlink_actions(result, False)

        assert result.errors == []
        assert result.warnings == []
