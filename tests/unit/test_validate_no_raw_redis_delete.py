"""Tests for the raw-Redis-on-Popoto-keys PreToolUse validator.

Covers the two scope gates from #2638 (repo scope, executable context) and the
two stale `_POPOTO_CONTEXT` entries from #2641 (the `$SortF` prefix popoto
actually emits, and the models missing from the list).

The validator is a pure text predicate, so nothing here touches Redis. Command
strings that would themselves trip the live hook are assembled from fragments
rather than written literally -- the same false-positive this file exists to
narrow would otherwise block anyone editing it from a Bash call.
"""

from __future__ import annotations

import importlib
import inspect
import json
import pkgutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.db_claim import subprocess_env

REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATORS_DIR = REPO_ROOT / ".claude" / "hooks" / "validators"
DISPATCH_SCRIPT = REPO_ROOT / ".claude" / "hooks" / "dispatch" / "pre_tool_use_bash.py"

sys.path.insert(0, str(VALIDATORS_DIR))

import validate_no_raw_redis_delete as validator  # noqa: E402

# Assembled so this source file does not contain a literal the hook blocks.
DELETE_CALL = "r" + "." + "delete("
HGETALL_CALL = "r" + "." + "hgetall("


def py(snippet: str) -> str:
    """A command that plausibly executes `snippet`."""
    return f'python -c "{snippet}"'


class TestRepoScopeGate:
    """#2638 manifestation 1: an ai-repo rule enforced outside the ai repo.

    The exemption is "inside a different git repository", not "outside this
    one". The Redis these keys live in is machine-global, so an arbitrary
    non-repo cwd is not a reason to stand down.
    """

    def test_blocks_when_cwd_is_this_repo(self):
        cmd = py(f"from models import AgentSession; {DELETE_CALL}'k')")
        assert validator.find_violation(cmd, str(REPO_ROOT)) is not None

    def test_blocks_when_cwd_is_a_worktree_of_this_repo(self, tmp_path):
        """A worktree BLOCKS: its `.git` is a file sitting beside a full checkout.

        That checkout includes the marker itself, and `_guard_applies` checks
        the marker before `.git`, so a worktree must BLOCK rather than stand
        down as if it were a foreign repo. This builds that exact shape instead
        of relying on the marker-before-`.git` ordering being pinned only by
        accident (it otherwise happens to hold in
        `test_blocks_when_cwd_is_this_repo` because `REPO_ROOT` itself always
        carries a `.git`, dir or file).
        """
        worktree = tmp_path / "worktrees" / "some-slug"
        marker_dir = worktree / ".claude" / "hooks" / "validators"
        marker_dir.mkdir(parents=True)
        (marker_dir / "validate_no_raw_redis_delete.py").write_text("# marker\n")
        (worktree / ".git").write_text("gitdir: /elsewhere/.git/worktrees/some-slug\n")
        cmd = py(f"from models import AgentSession; {DELETE_CALL}'k')")
        assert validator.find_violation(cmd, str(worktree)) is not None

    def test_allows_when_cwd_is_another_repo(self, tmp_path):
        """The popoto case: raw Redis there is the library's own test seeding."""
        other = tmp_path / "popoto"
        other.mkdir()
        (other / ".git").mkdir()
        cmd = py(f"import popoto; {DELETE_CALL}'k')")
        assert validator.find_violation(cmd, str(other)) is None

    def test_allows_in_a_subdirectory_of_another_repo(self, tmp_path):
        other = tmp_path / "popoto"
        (other / "tests").mkdir(parents=True)
        (other / ".git").mkdir()
        cmd = py(f"import popoto; {DELETE_CALL}'k')")
        assert validator.find_violation(cmd, str(other / "tests")) is None

    def test_allows_in_a_worktree_of_another_repo(self, tmp_path):
        """A worktree's `.git` is a FILE, and it is still another repo."""
        other = tmp_path / "popoto-wt"
        other.mkdir()
        (other / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n")
        cmd = py(f"import popoto; {DELETE_CALL}'k')")
        assert validator.find_violation(cmd, str(other)) is None

    @pytest.mark.parametrize(
        "cwd",
        [
            "/tmp",
            str(Path.home()),
            "/nonexistent/path/xyz",
            "",
            "\x00not-a-path",
        ],
    )
    def test_a_cwd_in_no_repo_at_all_keeps_the_guard_armed(self, cwd):
        """Fail closed, and mean it.

        `Path.resolve()` is non-strict on macOS, so a missing path resolves
        happily and then finds no marker. Treating "found nothing" as "not our
        problem" let `cd /tmp && python -c '<raw delete>'` reach production
        keys unblocked — the same machine-global Redis, just a cwd with no
        bearing on the popoto rationale. All five of these were measured
        allowing before this case existed.
        """
        cmd = py(f"from models import AgentSession; {DELETE_CALL}'k')")
        assert validator.find_violation(cmd, cwd) is not None

    def test_cwd_defaults_to_armed(self):
        """A caller passing only the command behaves as it did before #2638."""
        cmd = py(f"from models import AgentSession; {DELETE_CALL}'k')")
        assert validator.find_violation(cmd) is not None


class TestExecutableContextGate:
    """#2638 manifestation 2: prose describing the rule tripped the rule."""

    @pytest.mark.parametrize(
        "command",
        [
            # Filing the issue that reported this was blocked by this shape.
            f"gh issue create --title x --body 'blocks {DELETE_CALL}\"k\") on popoto keys'",
            f"git commit -m 'docs: explain why {DELETE_CALL}) on an AgentSession is wrong'",
            f"echo 'never call {DELETE_CALL}) on a Room' >> notes.md",
            f"cat <<'EOF' > doc.md\nAvoid {HGETALL_CALL}) on Memory keys.\nEOF",
            # Prose naming a dotfile that starts with an interpreter name. This
            # pins `.` OUT of the executable-context leading class: with `.` in
            # the class, `.python-version` reads as an invocation and this
            # sentence blocks. `/` alone still catches every real path form,
            # so the narrower class costs nothing. CLAUDE.md discusses
            # `.python-version` next to this rule, so the shape is routine.
            f"git commit -m 'docs: .python-version pins the interpreter; "
            f"never {DELETE_CALL}) an AgentSession'",
        ],
    )
    def test_prose_without_an_interpreter_is_allowed(self, command):
        assert validator.find_violation(command, str(REPO_ROOT)) is None

    @pytest.mark.parametrize(
        "command",
        [
            py(f"from models import AgentSession; {DELETE_CALL}'k')"),
            f"python3 -c \"import popoto; {DELETE_CALL}'k')\"",
            f"uv run python -c \"import popoto; {DELETE_CALL}'k')\"",
            f"./scripts/cleanup.py --popoto  # calls {DELETE_CALL})",
            "redis-cli -n 0 DEL 'AgentSession:abc'",
            # Interpreter invoked BY PATH. This is the house idiom — CLAUDE.md
            # documents the `.venv/bin/valor-*` form — and it is the primary
            # vector, not an edge. The first version of this gate required a
            # whitespace or shell metacharacter before `python` and did not
            # include `/` in the class, so all four of these blocked on main
            # and passed here: a regression the suite was green for, because
            # every row above happens to start its interpreter at a word
            # boundary. The test matrix was narrower than the claim it backed.
            f".venv/bin/python -c \"import popoto; {DELETE_CALL}'k')\"",
            f"/usr/local/bin/python3 -c \"import popoto; {DELETE_CALL}'k')\"",
            f"./.venv/bin/python -c \"import popoto; {DELETE_CALL}'k')\"",
            f"~/src/ai/.venv/bin/python -c \"import popoto; {DELETE_CALL}'k')\"",
        ],
    )
    def test_executable_context_still_blocks(self, command):
        assert validator.find_violation(command, str(REPO_ROOT)) is not None

    def test_grep_for_the_pattern_is_not_a_violation(self):
        cmd = f"grep -rn '{DELETE_CALL}' agent/ | grep AgentSession"
        assert validator.find_violation(cmd, str(REPO_ROOT)) is None


class TestQuotedHeredocBodies:
    """#2736: a quoted-heredoc body is data unless it feeds an interpreter."""

    # The shape that kept blocking doc/comment authoring: a quoted example
    # that names an interpreter AND a blocked call shape, inside a heredoc
    # that only writes a file.
    QUOTED_EXAMPLE = f"Example: .venv/bin/python -c \"import popoto; {DELETE_CALL}'k')\""

    def test_quoted_heredoc_writing_a_doc_is_allowed(self):
        cmd = "cat <<'EOF' > doc.md\n" + self.QUOTED_EXAMPLE + "\nEOF"
        assert validator.find_violation(cmd, str(REPO_ROOT)) is None

    def test_dash_form_quoted_heredoc_is_allowed(self):
        cmd = "cat <<-'EOF' > doc.md\n\t" + self.QUOTED_EXAMPLE + "\n\tEOF"
        assert validator.find_violation(cmd, str(REPO_ROOT)) is None

    def test_unquoted_heredoc_with_command_substitution_still_blocks(self):
        inner = py(f"import popoto; {DELETE_CALL}'k')")
        cmd = "cat <<EOF > doc.md\n$(" + inner + ")\nEOF"
        assert validator.find_violation(cmd, str(REPO_ROOT)) is not None

    def test_quoted_heredoc_feeding_an_interpreter_stays_in_scope(self):
        cmd = "python3 <<'EOF'\nimport popoto\n" + DELETE_CALL + "'k')\nEOF"
        assert validator.find_violation(cmd, str(REPO_ROOT)) is not None

    def test_quoted_heredoc_piped_into_an_interpreter_stays_in_scope(self):
        cmd = "cat <<'PY' | python3\nimport popoto\n" + DELETE_CALL + "'k')\nPY"
        assert validator.find_violation(cmd, str(REPO_ROOT)) is not None

    def test_unterminated_quoted_heredoc_stays_in_scope(self):
        cmd = "cat <<'EOF' > doc.md\n" + py(f"import popoto; {DELETE_CALL}'k')")
        assert validator.find_violation(cmd, str(REPO_ROOT)) is not None

    def test_command_after_a_stripped_heredoc_still_blocks(self):
        """Stripping the body must not eat executable text that follows it."""
        tail = py(f"import popoto; {DELETE_CALL}'k')")
        cmd = "cat <<'EOF' > doc.md\nharmless prose\nEOF\n" + tail
        assert validator.find_violation(cmd, str(REPO_ROOT)) is not None


class TestPopotoContextEntries:
    """#2641: two stale `_POPOTO_CONTEXT` entries made the guard fail open."""

    def test_sorted_field_prefix_is_the_one_popoto_emits(self):
        """`$SortF`, not `$SortedF` -- str.strip takes a character set."""
        assert "SortedField".strip("Field") == "Sort"
        assert r"\$SortF:" in validator._POPOTO_CONTEXT
        assert r"\$SortedF:" not in validator._POPOTO_CONTEXT

    # Every segment of these keys is deliberately free of any `_POPOTO_CONTEXT`
    # entry, so the prefix is the only thing that can satisfy the context gate.
    # Two earlier drafts failed this: one used a real model name, the next used
    # `room-1`, which matches the `Room` entry because the context search is
    # case-insensitive. Both passed while testing nothing. Mutation caught both.
    def _prefix_only(self, key: str) -> None:
        cmd = py(f"{DELETE_CALL}'{key}')")

        # Strip the `$` and the prefix patterns stop matching. If anything
        # still blocks, some other segment of the key is carrying the context
        # signal and this case proves nothing about the prefix.
        without_prefix = cmd.replace("$", "")
        assert validator.find_violation(without_prefix, str(REPO_ROOT)) is None, (
            f"{key!r} carries a context signal besides its prefix; "
            "this case would pass even with the prefix entry removed"
        )

        assert validator.find_violation(cmd, str(REPO_ROOT)) is not None

    def test_blocks_raw_access_on_a_sorted_set_key(self):
        self._prefix_only("$SortF:Widget:tally:zz-1")

    def test_blocks_raw_access_on_a_decaying_sorted_set_key(self):
        self._prefix_only("$DecayingSortF:Widget:tally:zz-1")

    def test_blocks_raw_access_on_a_job_key(self):
        cmd = py(f"{DELETE_CALL}'Job:abc123')")
        assert validator.find_violation(cmd, str(REPO_ROOT)) is not None

    def test_model_list_is_complete(self):
        """Every popoto.Model subclass is named in `_POPOTO_CONTEXT`.

        The list drifted silently twice (`Job` and five others were missing
        while `Room` was present) because nothing made an omission visible.
        This is that check. Extra names in the list are fine -- the context
        gate is a widener, so an extra entry only makes the guard fire more.
        """
        import popoto

        found: dict[str, str] = {}
        for pkg_name in ("models", "agent", "tools", "bridge", "worker", "monitoring"):
            try:
                pkg = importlib.import_module(pkg_name)
            except Exception:
                continue
            module_names = [pkg_name] + [
                f"{pkg_name}.{mi.name}" for mi in pkgutil.iter_modules(getattr(pkg, "__path__", []))
            ]
            for module_name in module_names:
                try:
                    module = importlib.import_module(module_name)
                except Exception:
                    continue
                for obj in vars(module).values():
                    if (
                        inspect.isclass(obj)
                        and issubclass(obj, popoto.Model)
                        and obj is not popoto.Model
                    ):
                        found.setdefault(obj.__name__, module_name)

        assert found, "model sweep found nothing -- the probe is broken, not the list"
        missing = sorted(name for name in found if name not in validator._POPOTO_CONTEXT)
        assert not missing, (
            "Popoto models absent from _POPOTO_CONTEXT in "
            f"{VALIDATORS_DIR / 'validate_no_raw_redis_delete.py'}: {missing}. "
            "A raw command against these keys slips the context gate. "
            "Add each name to the list."
        )


class TestDispatcherIntegration:
    """The gates must hold end to end, not just in the pure predicate."""

    def _dispatch(self, command: str, cwd: str) -> str | None:
        payload = {"tool_name": "Bash", "cwd": cwd, "tool_input": {"command": command}}
        proc = subprocess.run(
            [sys.executable, str(DISPATCH_SCRIPT)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=subprocess_env(project_root=str(REPO_ROOT)),
            timeout=30,
        )
        for line in (proc.stdout or "").splitlines():
            if not line.strip():
                continue
            try:
                decision = json.loads(line)
            except json.JSONDecodeError:
                continue
            if decision.get("decision") == "block":
                return decision.get("reason")
        return None

    def test_dispatcher_passes_cwd_through(self, tmp_path):
        """Registration is what carries the fix; the predicate alone is not enough."""
        other = tmp_path / "popoto"
        other.mkdir()
        (other / ".git").mkdir()
        cmd = py(f"import popoto; {DELETE_CALL}'k')")
        assert self._dispatch(cmd, str(other)) is None

    def test_dispatcher_blocks_a_path_invoked_interpreter(self):
        """The regression the review caught, pinned end to end, not just in the predicate."""
        cmd = f".venv/bin/python -c \"from models import AgentSession; {DELETE_CALL}'k')\""
        reason = self._dispatch(cmd, str(REPO_ROOT))
        assert reason is not None
        assert "Popoto" in reason

    def test_dispatcher_still_blocks_in_this_repo(self):
        cmd = py(f"from models import AgentSession; {DELETE_CALL}'k')")
        reason = self._dispatch(cmd, str(REPO_ROOT))
        assert reason is not None
        assert "Popoto" in reason
