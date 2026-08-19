"""Unit tests for .claude/hooks/hook_utils/hook_target.py.

The contract under test, in one line: ``None`` means "nothing to validate",
never "go find something to validate". Working-tree state — ``git status``,
mtimes, directory globs — is never an input to target selection (#2682, #2689).

The non-dict top-level payload cases (``null``, ``[1,2]``, ``"str"``, ``42``)
carry their own weight. They parse cleanly as JSON, so swallowing
``json.JSONDecodeError`` does not cover them, and an unguarded ``.get`` on the
result raises ``AttributeError`` out of a hook that gates every ``Write``. The
tests inherited from PR #2688 do not exercise a single one of them.
"""

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / ".claude" / "hooks"
VALIDATORS_DIR = HOOKS_DIR / "validators"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))
if str(VALIDATORS_DIR) not in sys.path:
    sys.path.insert(0, str(VALIDATORS_DIR))

from hook_utils.hook_target import (  # noqa: E402
    in_scope,
    read_hook_input,
    target_from_hook_input,
)

from tests.db_claim import subprocess_env  # noqa: E402

PORTED_VALIDATORS = [
    "validate_no_gos_justification.py",
    "validate_documentation_section.py",
    "validate_test_impact_section.py",
    "validate_verification_section.py",
    "validate_file_contains.py",
]


class TestTargetFromHookInput:
    """Payload in, path or None out. Nothing else is consulted."""

    def test_reads_file_path(self):
        assert target_from_hook_input({"tool_input": {"file_path": "docs/plans/x.md"}}) == (
            "docs/plans/x.md"
        )

    def test_reads_notebook_path(self):
        assert target_from_hook_input({"tool_input": {"notebook_path": "a.ipynb"}}) == "a.ipynb"

    def test_file_path_wins_over_notebook_path(self):
        payload = {"tool_input": {"file_path": "a.md", "notebook_path": "b.ipynb"}}
        assert target_from_hook_input(payload) == "a.md"

    def test_empty_file_path_falls_back_to_notebook_path(self):
        payload = {"tool_input": {"file_path": "", "notebook_path": "b.ipynb"}}
        assert target_from_hook_input(payload) == "b.ipynb"

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"tool_name": "Write"},
            {"tool_input": None},
            {"tool_input": "string"},
            {"tool_input": []},
            {"tool_input": {}},
            {"tool_input": {"file_path": ""}},
            # Both keys present and empty: the `or` chain yields "" here, not
            # None, so the empty-string collapse needs its own guard.
            {"tool_input": {"file_path": "", "notebook_path": ""}},
            {"tool_input": {"notebook_path": ""}},
            {"tool_input": {"other_key": "docs/plans/x.md"}},
        ],
    )
    def test_unresolvable_target_is_none(self, payload):
        assert target_from_hook_input(payload) is None

    @pytest.mark.parametrize("bogus", [123, 1.5, {"a": 1}, ["x"], True, False, None])
    def test_non_string_path_is_rejected(self, bogus):
        """A malformed payload must never hand a non-path downstream."""
        assert target_from_hook_input({"tool_input": {"file_path": bogus}}) is None

    @pytest.mark.parametrize("bogus", [123, {"a": 1}, ["x"], True])
    def test_non_string_notebook_path_is_rejected(self, bogus):
        assert target_from_hook_input({"tool_input": {"notebook_path": bogus}}) is None


class TestNonDictTopLevelPayload:
    """Syntactically valid JSON that is not an object must not raise.

    ``json.loads('null')`` succeeds and yields ``None``; ``.get`` on it is an
    ``AttributeError``. The guard is a separate concern from JSON validity, so
    it gets separate coverage at both levels.
    """

    @pytest.mark.parametrize("parsed", [None, [1, 2], "str", 42, 1.5, True])
    def test_target_from_non_dict_is_none(self, parsed):
        assert target_from_hook_input(parsed) is None

    @pytest.mark.parametrize("raw", ["null", "[1,2]", '"str"', "42", "1.5", "true"])
    def test_read_hook_input_returns_empty_dict(self, raw, monkeypatch):
        monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
        assert read_hook_input() == {}

    @pytest.mark.parametrize("raw", ["null", "[1,2]", '"str"', "42"])
    def test_round_trip_through_both_functions(self, raw, monkeypatch):
        monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
        assert target_from_hook_input(read_hook_input()) is None


class TestReadHookInput:
    """stdin in, dict out. Never raises, whatever arrives."""

    def test_parses_a_real_payload(self, monkeypatch):
        payload = {"tool_name": "Write", "tool_input": {"file_path": "docs/plans/x.md"}}
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        assert read_hook_input() == payload

    @pytest.mark.parametrize("raw", ["", "   ", "\n", "\t\n "])
    def test_empty_stdin_is_an_empty_dict(self, raw, monkeypatch):
        monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
        assert read_hook_input() == {}

    @pytest.mark.parametrize("raw", ["not json", "{", "{'single': 'quotes'}", "{}}", "<html>"])
    def test_malformed_stdin_is_an_empty_dict(self, raw, monkeypatch):
        monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
        assert read_hook_input() == {}

    def test_unreadable_stdin_is_an_empty_dict(self, monkeypatch):
        """An OSError out of the stream is swallowed, not propagated."""

        class ExplodingStream:
            def read(self):
                raise OSError("stdin is gone")

        monkeypatch.setattr(sys, "stdin", ExplodingStream())
        assert read_hook_input() == {}

    def test_empty_json_object_is_an_empty_dict(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
        assert read_hook_input() == {}


class TestNoValidatorRaisesOnAHostilePayload:
    """End to end: the harness contract is an exit code, never a traceback."""

    @pytest.mark.parametrize("validator", PORTED_VALIDATORS)
    @pytest.mark.parametrize("stdin", ["null", "[1,2]", '"str"', "42", "", "not json", "{}"])
    def test_exits_zero_without_a_traceback(self, validator, stdin, tmp_path):
        proc = subprocess.run(
            [sys.executable, str(VALIDATORS_DIR / validator)],
            input=stdin,
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            timeout=30,
            env=subprocess_env(),
        )
        assert proc.returncode == 0, proc.stderr
        assert "Traceback" not in proc.stderr
        assert "AttributeError" not in proc.stderr

    @pytest.mark.parametrize("validator", PORTED_VALIDATORS)
    def test_closed_stdin_exits_zero(self, validator, tmp_path):
        proc = subprocess.run(
            [sys.executable, str(VALIDATORS_DIR / validator)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            timeout=30,
            env=subprocess_env(),
        )
        assert proc.returncode == 0, proc.stderr
        assert "Traceback" not in proc.stderr


class TestInScope:
    """The anchored scope predicate shared by all five validators.

    Anchored means two things, and both are regressions the family has actually
    shipped. The directory must match on a *path segment* — an unanchored
    ``"docs/plans" in path`` fails closed on ``docs/plans_archive/old.md``. And
    the test must work on the path **as given**, absolute included: rewriting an
    absolute payload path to a cwd-relative form before a ``startswith`` puts
    the payload's cwd (often absent) and the hook process's cwd (often another
    lane's checkout) back into target selection.
    """

    @pytest.mark.parametrize(
        "path,expected",
        [
            # In scope: relative, nested, absolute, and Windows separators.
            ("docs/plans/a.md", True),
            ("docs/plans/completed/a.md", True),
            ("/Users/x/src/ai/.worktrees/slug/docs/plans/a.md", True),
            ("/private/tmp/pytest-1/docs/plans/a.md", True),
            ("docs\\plans\\a.md", True),
            ("C:\\repo\\docs\\plans\\a.md", True),
            # Out of scope: sibling and prefix lookalikes.
            ("docs/plans_archive/old.md", False),
            ("docs/plansomething/a.md", False),
            ("/repo/docs/plans_archive/old.md", False),
            ("docs/plansible.md", False),
            # Out of scope: wrong extension, wrong directory, bare filename.
            ("docs/plans/helper.py", False),
            ("docs/features/a.md", False),
            ("tools/foo.py", False),
            ("a.md", False),
            ("", False),
        ],
    )
    def test_scope(self, path, expected):
        assert in_scope(path, "docs/plans", ".md") is expected

    def test_an_absolute_path_needs_no_cwd_to_be_in_scope(self):
        """The regression that made a payload without a `cwd` key fail open."""
        assert in_scope("/anywhere/at/all/docs/plans/mine.md", "docs/plans", ".md") is True

    @pytest.mark.parametrize("extension", [".md", "md"])
    def test_extension_dot_is_optional(self, extension):
        assert in_scope("docs/plans/a.md", "docs/plans", extension) is True

    @pytest.mark.parametrize("directory", ["docs/plans", "docs/plans/", "/docs/plans/"])
    def test_directory_slashes_are_tolerated(self, directory):
        assert in_scope("docs/plans/a.md", directory, ".md") is True

    def test_an_empty_extension_matches_any_suffix(self):
        assert in_scope("docs/plans/helper.py", "docs/plans", "") is True

    @pytest.mark.parametrize("directory", ["", "/"])
    def test_an_empty_directory_is_never_in_scope(self, directory):
        """A missing scope must fail closed to "not mine", not match everything."""
        assert in_scope("docs/plans/a.md", directory, ".md") is False


class TestWorkingTreeStateIsNeverAnInput:
    """The anti-regression: the deleted guessers must not come back."""

    def test_module_does_not_shell_out(self):
        source = (HOOKS_DIR / "hook_utils" / "hook_target.py").read_text()
        for banned in ("import subprocess", "--porcelain", "st_mtime", ".glob(", ".rglob("):
            assert banned not in source, f"hook_target.py reintroduced {banned}"

    @pytest.mark.parametrize("validator", PORTED_VALIDATORS)
    def test_no_validator_selects_a_target_from_the_working_tree(self, validator):
        source = (VALIDATORS_DIR / validator).read_text()
        for banned in ("find_newest_plan_file", "find_newest_file", "porcelain", "newest_mtime"):
            assert banned not in source, f"{validator} still references {banned}"

    @pytest.mark.parametrize("validator", PORTED_VALIDATORS)
    def test_no_validator_resolves_its_target_against_a_cwd(self, validator):
        """A cwd is process/payload state, so it is not an input to target selection.

        Rewriting the payload's absolute path relative to ``payload["cwd"]``
        reintroduces the whole defect: absent the key the path silently falls
        out of scope, and once relative it resolves against whatever checkout
        the hook process started in.
        """
        source = (VALIDATORS_DIR / validator).read_text()
        for banned in ('get("cwd")', "normalize_target", "os.chdir", "os.getcwd"):
            assert banned not in source, f"{validator} resolves its target against a cwd: {banned}"
