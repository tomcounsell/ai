"""Tests for the in-process PreToolUse/Bash dispatcher
(.claude/hooks/dispatch/pre_tool_use_bash.py).

Covers the Failure Path Test Strategy items from
docs/plans/hook-registration-manifest-dispatcher.md:
- A raising validator does not skip the rest (Risk 2).
- The crash is logged via log_hook_error (not silently swallowed).
- A fail-closed validator (merge-guard) raising still results in a block.
- Empty/whitespace stdin JSON is handled gracefully (allow, no crash).
"""

import importlib
import json
import sys
from pathlib import Path

import pytest

_DISPATCH_DIR = str(Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "dispatch")
_HOOKS_DIR = str(Path(__file__).resolve().parents[2] / ".claude" / "hooks")
_VALIDATORS_DIR = str(Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "validators")

for _p in (_DISPATCH_DIR, _HOOKS_DIR, _VALIDATORS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture
def dispatcher():
    """Import (or re-import) the dispatcher module fresh for each test."""
    import pre_tool_use_bash as mod

    return importlib.reload(mod)


@pytest.fixture
def bash_hook_input():
    return {
        "tool_name": "Bash",
        "tool_input": {"command": "echo hello world"},
        "cwd": "/tmp",
    }


class TestEmptyInvalidInput:
    def test_empty_stdin_allows_no_crash(self, dispatcher, capsys):
        assert dispatcher.read_stdin.__call__ is not None
        result = dispatcher.dispatch({})
        assert result is None

    def test_whitespace_stdin_parses_to_empty_dict(self, monkeypatch, dispatcher):
        monkeypatch.setattr(sys, "stdin", _FakeStdin("   \n\t  "))
        hook_input = dispatcher.read_stdin()
        assert hook_input == {}

    def test_malformed_json_stdin_parses_to_empty_dict(self, monkeypatch, dispatcher):
        monkeypatch.setattr(sys, "stdin", _FakeStdin("{not valid json"))
        hook_input = dispatcher.read_stdin()
        assert hook_input == {}

    def test_main_with_empty_stdin_prints_nothing(self, monkeypatch, dispatcher, capsys):
        monkeypatch.setattr(sys, "stdin", _FakeStdin(""))
        dispatcher.main()
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_non_bash_tool_allows(self, dispatcher):
        result = dispatcher.dispatch({"tool_name": "Read", "tool_input": {}})
        assert result is None

    def test_no_command_allows(self, dispatcher):
        result = dispatcher.dispatch({"tool_name": "Bash", "tool_input": {}})
        assert result is None


class _FakeStdin:
    def __init__(self, text: str):
        self._text = text

    def read(self) -> str:
        return self._text


class TestRaisingValidatorDoesNotSkipRest:
    def test_fail_open_validator_raises_but_next_validators_still_run(
        self, monkeypatch, dispatcher, bash_hook_input
    ):
        """A raising fail-open validator must not prevent later validators
        from running. We inject a raise into the first validator
        (validate_commit_message) and a real block into a later one
        (validate_no_raw_redis_delete), and assert the LATER block still
        fires -- proving evaluation continued past the crash.
        """
        calls: list[str] = []

        def _raising(_command, _cwd):
            calls.append("commit_message")
            raise RuntimeError("boom")

        def _second_ok(_command, _cwd):
            calls.append("no_inline_timeout")
            return None

        def _third_block(_command, _cwd):
            calls.append("merge_guard")
            return None

        def _fourth_blocks(_command, _cwd):
            calls.append("no_raw_redis_delete")
            return "BLOCKED: raw redis delete"

        monkeypatch.setattr(dispatcher, "_run_commit_message", _raising)
        monkeypatch.setattr(dispatcher, "_run_no_inline_timeout", _second_ok)
        monkeypatch.setattr(dispatcher, "_run_merge_guard", _third_block)
        monkeypatch.setattr(dispatcher, "_run_no_raw_redis_delete", _fourth_blocks)
        monkeypatch.setattr(
            dispatcher,
            "_VALIDATORS",
            [
                ("validate_commit_message", _raising, False),
                ("validate_no_inline_timeout", _second_ok, False),
                ("validate_merge_guard", _third_block, True),
                ("validate_no_raw_redis_delete", _fourth_blocks, False),
            ],
        )

        result = dispatcher.dispatch(bash_hook_input)

        assert result == "BLOCKED: raw redis delete"
        # All four ran despite the first one raising.
        assert calls == [
            "commit_message",
            "no_inline_timeout",
            "merge_guard",
            "no_raw_redis_delete",
        ]

    def test_crash_is_logged_via_log_hook_error(self, monkeypatch, dispatcher, bash_hook_input):
        logged: list[tuple[str, str]] = []

        def _fake_log_hook_error(hook_name: str, error: str) -> None:
            logged.append((hook_name, error))

        def _raising(_command, _cwd):
            raise ValueError("kaboom")

        monkeypatch.setattr(dispatcher, "log_hook_error", _fake_log_hook_error)
        monkeypatch.setattr(
            dispatcher,
            "_VALIDATORS",
            [("validate_commit_message", _raising, False)],
        )
        monkeypatch.setattr(dispatcher, "_run_design_system_sync", lambda _hi: None)

        result = dispatcher.dispatch(bash_hook_input)

        assert result is None
        assert len(logged) == 1
        hook_name, error = logged[0]
        assert hook_name == "dispatch.pre_tool_use_bash.validate_commit_message"
        assert "kaboom" in error


class TestMergeGuardFailClosed:
    def test_merge_guard_raising_still_blocks(self, monkeypatch, dispatcher, bash_hook_input):
        """Per Risk 2 / spike-1: an exception raised while evaluating the
        merge-guard predicate must produce a BLOCK decision, not a silent
        fail-open allow.
        """

        def _raising(_command, _cwd):
            raise RuntimeError("merge predicate exploded")

        monkeypatch.setattr(
            dispatcher,
            "_VALIDATORS",
            [("validate_merge_guard", _raising, True)],
        )
        monkeypatch.setattr(dispatcher, "_run_design_system_sync", lambda _hi: None)

        result = dispatcher.dispatch(bash_hook_input)

        assert result is not None
        assert "Merge blocked" in result

    def test_merge_guard_raising_is_logged(self, monkeypatch, dispatcher, bash_hook_input):
        logged: list[tuple[str, str]] = []

        def _fake_log_hook_error(hook_name: str, error: str) -> None:
            logged.append((hook_name, error))

        def _raising(_command, _cwd):
            raise RuntimeError("merge predicate exploded")

        monkeypatch.setattr(dispatcher, "log_hook_error", _fake_log_hook_error)
        monkeypatch.setattr(
            dispatcher,
            "_VALIDATORS",
            [("validate_merge_guard", _raising, True)],
        )
        monkeypatch.setattr(dispatcher, "_run_design_system_sync", lambda _hi: None)

        dispatcher.dispatch(bash_hook_input)

        assert any("validate_merge_guard" in name for name, _ in logged)


class TestFirstBlockWins:
    def test_first_block_short_circuits_remaining_validators(
        self, monkeypatch, dispatcher, bash_hook_input
    ):
        calls: list[str] = []

        def _first_blocks(_command, _cwd):
            calls.append("first")
            return "BLOCKED: first reason"

        def _second_should_not_run(_command, _cwd):
            calls.append("second")
            return "BLOCKED: second reason"

        monkeypatch.setattr(
            dispatcher,
            "_VALIDATORS",
            [
                ("validate_commit_message", _first_blocks, False),
                ("validate_no_inline_timeout", _second_should_not_run, False),
            ],
        )

        result = dispatcher.dispatch(bash_hook_input)

        assert result == "BLOCKED: first reason"
        assert calls == ["first"]

    def test_all_pass_allows(self, monkeypatch, dispatcher, bash_hook_input):
        monkeypatch.setattr(dispatcher, "_VALIDATORS", [])
        monkeypatch.setattr(dispatcher, "_run_design_system_sync", lambda _hi: None)

        result = dispatcher.dispatch(bash_hook_input)

        assert result is None


class TestMainEmitsSingleDecision:
    def test_main_prints_one_block_json_line(self, monkeypatch, dispatcher, capsys):
        monkeypatch.setattr(
            sys,
            "stdin",
            _FakeStdin(
                json.dumps(
                    {
                        "tool_name": "Bash",
                        "tool_input": {"command": "git commit -m ''"},
                        "cwd": "/tmp",
                    }
                )
            ),
        )
        monkeypatch.setattr(dispatcher, "dispatch", lambda _hi: "BLOCKED: test reason")

        dispatcher.main()

        captured = capsys.readouterr()
        lines = [ln for ln in captured.out.splitlines() if ln.strip()]
        assert len(lines) == 1
        decision = json.loads(lines[0])
        assert decision == {"decision": "block", "reason": "BLOCKED: test reason"}

    def test_main_allows_prints_nothing(self, monkeypatch, dispatcher, capsys):
        monkeypatch.setattr(
            sys,
            "stdin",
            _FakeStdin(
                json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": "/tmp"})
            ),
        )
        monkeypatch.setattr(dispatcher, "dispatch", lambda _hi: None)

        dispatcher.main()

        captured = capsys.readouterr()
        assert captured.out == ""


class TestDesignSystemSyncOutOfProcess:
    def test_block_from_subprocess_stdout_is_surfaced(
        self, monkeypatch, dispatcher, bash_hook_input
    ):
        class _FakeCompletedProcess:
            stdout = json.dumps({"decision": "block", "reason": "drift detected"}) + "\n"
            stderr = ""

        def _fake_run(*_args, **_kwargs):
            return _FakeCompletedProcess()

        monkeypatch.setattr(dispatcher.subprocess, "run", _fake_run)
        monkeypatch.setattr(dispatcher, "_VALIDATORS", [])

        result = dispatcher.dispatch(bash_hook_input)

        assert result == "drift detected"

    def test_subprocess_failure_fails_open(self, monkeypatch, dispatcher, bash_hook_input):
        def _fake_run(*_args, **_kwargs):
            raise OSError("no interpreter")

        monkeypatch.setattr(dispatcher.subprocess, "run", _fake_run)
        monkeypatch.setattr(dispatcher, "_VALIDATORS", [])

        result = dispatcher.dispatch(bash_hook_input)

        assert result is None


class TestManifestOrderConsistency:
    """Integration guard: the manifest's PreToolUse/Bash matcher group must be
    collapsed behind the single in-process dispatcher, and the dispatcher's
    hardcoded ``_VALIDATORS`` order (plus the out-of-process
    design-system-sync tail) is the sole source of validator precedence.

    Before the dispatcher was wired into the manifest, this test compared two
    independently-declared orderings (manifest entries vs. the dispatcher's
    hardcoded list) to catch drift between them. Now that the manifest
    declares exactly one entry for this matcher group -- the dispatcher
    itself -- there is nothing left to compare orderings against; instead
    this guards the new invariant: exactly one ``PreToolUse``/``Bash``
    project-scope manifest entry, pointing at the dispatcher script. Ordering
    is now owned entirely by ``dispatch.pre_tool_use_bash._VALIDATORS`` (see
    that module's own tests for its first-block-wins behavior).
    """

    def test_manifest_declares_single_dispatcher_entry_for_bash(self):
        from scripts.update.hook_manifest import load_hook_manifest

        repo_root = Path(__file__).resolve().parents[2]
        manifest = load_hook_manifest(repo_root / ".claude" / "hooks" / "manifest.toml")

        bash_decls = [
            decl
            for decl in manifest
            if decl.event == "PreToolUse" and decl.matcher == "Bash" and decl.scope == "project"
        ]

        assert len(bash_decls) == 1, (
            "PreToolUse/Bash (project scope) must be collapsed behind a single "
            f"dispatcher entry, found {len(bash_decls)}: "
            f"{[d.manifest_id for d in bash_decls]}"
        )
        assert bash_decls[0].script == "dispatch/pre_tool_use_bash.py"


class TestSharedCheckoutValidatorRegistered:
    """Registration guard for issue #2448: the shared-checkout guardrail
    must be present in ``_VALIDATORS`` (fail-open, right after its worktree
    sibling), so a future edit that silently drops the registration fails
    CI instead of shipping a no-op guardrail."""

    def test_shared_checkout_validator_is_registered(self, dispatcher):
        names = [name for name, _fn, _fail_closed in dispatcher._VALIDATORS]

        assert "validate_no_destructive_git_in_shared_checkout" in names
        assert "validate_no_destructive_git_in_worktree" in names
        worktree_idx = names.index("validate_no_destructive_git_in_worktree")
        shared_checkout_idx = names.index("validate_no_destructive_git_in_shared_checkout")
        assert shared_checkout_idx == worktree_idx + 1, (
            "shared-checkout guard must be declared immediately after its worktree sibling"
        )

        entry = dispatcher._VALIDATORS[shared_checkout_idx]
        assert entry[2] is False, "shared-checkout guard must be fail-open"


class TestRedisFlushValidatorRegistered:
    """Registration guard for issue #2645: validate_no_redis_flush must be
    present in ``_VALIDATORS`` (fail-open, immediately after
    validate_no_broad_process_kill, the last of the original 8 in-process
    predicates), so a future edit that silently drops the registration fails
    CI instead of shipping a validator that blocks nothing (#2435)."""

    def test_redis_flush_validator_is_registered_after_broad_process_kill(self, dispatcher):
        names = [name for name, _fn, _fail_closed in dispatcher._VALIDATORS]

        assert "validate_no_redis_flush" in names
        assert "validate_no_broad_process_kill" in names
        broad_kill_idx = names.index("validate_no_broad_process_kill")
        redis_flush_idx = names.index("validate_no_redis_flush")
        assert redis_flush_idx == broad_kill_idx + 1, (
            "redis-flush guard must be declared immediately after validate_no_broad_process_kill"
        )

        entry = dispatcher._VALIDATORS[redis_flush_idx]
        assert entry[2] is False, "redis-flush guard must be fail-open"

    def test_redis_flush_validator_blocks_through_dispatch(self, dispatcher, bash_hook_input):
        """First-block-wins integration: a real flush command reaching
        dispatch() through the actual _VALIDATORS list (not a monkeypatched
        stand-in) must be blocked, proving registration rather than just the
        predicate's own logic."""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "redis.Redis().flushdb()"},
            "cwd": "/tmp",
        }

        result = dispatcher.dispatch(hook_input)

        assert result is not None
        assert "REDIS_PRODUCTION_FLUSH_OK=1" in result
