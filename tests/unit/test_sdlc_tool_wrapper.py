"""Tests for the scripts/sdlc-tool bash wrapper and the loud-failure
exit-code semantic on tools/sdlc_verdict.py and tools/sdlc_dispatch.py.

These tests cover the build of issue #1175:

1. Wrapper-level errors (bad subcommand, bad AI_REPO_ROOT) exit 2.
2. Dispatch through the wrapper succeeds from a foreign cwd that ships
   its own ``tools/`` package — proving the cwd-shadowing bug is fixed.
3. ``tools.sdlc_verdict`` and ``tools.sdlc_dispatch`` ``main()`` exit 1
   (was 0) when their inner CLI handler raises — proving the loud
   semantic actually reaches operators.
4. Skill-markdown parity: zero ``python -m tools.sdlc_*`` invocations
   remain in the include set (skills, post_compact.py, persona).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "scripts" / "sdlc-tool"

# Which paths the parity sweep is allowed to scan. Anything outside this
# include set is ignored (tests, archived docs/plans/**, the wrapper itself,
# tools/** which IS the underlying module, etc.).
PARITY_INCLUDE_GLOBS = (
    ".claude/skills/**/SKILL.md",
    ".claude/skills/**/sub-skills/**/*.md",
    ".claude/hooks/post_compact.py",
    "config/personas/project-manager.md",
)

# Explicit excludes for safety. Anything matching these is never flagged
# even if a glob above accidentally captures it.
PARITY_EXCLUDE_PARTS = (
    "scripts/sdlc-tool",
    "tools",
    "tests",
    "docs/plans",
    "docs/features",
    "CLAUDE.md",
)


def _iter_include_paths():
    for pattern in PARITY_INCLUDE_GLOBS:
        for path in REPO_ROOT.glob(pattern):
            if not path.is_file():
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            if any(part in rel for part in PARITY_EXCLUDE_PARTS):
                continue
            yield path


class TestWrapperShellSemantics:
    """Wrapper-level errors that don't reach the underlying Python module."""

    def test_wrapper_exists_and_is_executable(self):
        assert WRAPPER.exists(), f"wrapper missing at {WRAPPER}"
        assert os.access(WRAPPER, os.X_OK), f"wrapper not executable: {WRAPPER}"

    def test_wrapper_no_args_exits_2(self):
        result = subprocess.run([str(WRAPPER)], capture_output=True, text=True)
        assert result.returncode == 2
        assert "Usage" in result.stderr
        assert "stage-query" in result.stderr  # subcommand list visible

    def test_wrapper_unknown_subcommand_exits_2(self):
        result = subprocess.run([str(WRAPPER), "make-coffee"], capture_output=True, text=True)
        assert result.returncode == 2
        assert "unknown subcommand" in result.stderr
        assert "make-coffee" in result.stderr

    def test_wrapper_help_flag_exits_0(self):
        result = subprocess.run([str(WRAPPER), "--help"], capture_output=True, text=True)
        assert result.returncode == 0

    def test_wrapper_missing_ai_repo_root_exits_2(self, tmp_path):
        env = os.environ.copy()
        env["AI_REPO_ROOT"] = str(tmp_path / "does-not-exist")
        result = subprocess.run(
            [str(WRAPPER), "stage-query", "--issue-number", "0"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 2
        assert "AI_REPO_ROOT" in result.stderr
        assert "does not exist" in result.stderr

    def test_wrapper_ai_repo_root_without_tools_exits_2(self, tmp_path):
        # Directory exists but has no tools/ subdir
        env = os.environ.copy()
        env["AI_REPO_ROOT"] = str(tmp_path)
        result = subprocess.run(
            [str(WRAPPER), "stage-query", "--issue-number", "0"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 2
        assert "tools/" in result.stderr


class TestWrapperDispatch:
    """The wrapper actually reaches the underlying tools.sdlc_* module."""

    def test_dispatch_from_foreign_cwd_with_own_tools_succeeds(self, tmp_path):
        """Reproducer for the original bug: foreign cwd shadows tools/.

        When /sdlc runs from a target-repo cwd whose ``tools/`` package
        does NOT contain ``sdlc_stage_query``, bare ``python -m tools.sdlc_*``
        fails with ModuleNotFoundError. The wrapper must not.
        """
        # Plant a fake tools/ in the foreign cwd that would shadow ours.
        fake_tools = tmp_path / "tools"
        fake_tools.mkdir()
        (fake_tools / "__init__.py").write_text("")
        # Deliberately do NOT create sdlc_stage_query.py here.

        env = os.environ.copy()
        env["AI_REPO_ROOT"] = str(REPO_ROOT)
        result = subprocess.run(
            [str(WRAPPER), "stage-query", "--issue-number", "999999"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"wrapper failed from foreign cwd: stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        # stage-query always emits parseable JSON
        payload = json.loads(result.stdout.strip())
        assert isinstance(payload, dict)


class TestVerdictAndDispatchLoudExit:
    """tools.sdlc_verdict and tools.sdlc_dispatch must exit 1 on raise.

    This is the load-bearing piece of the fix: without these exit codes,
    removing ``|| true`` from skill markdown is cosmetic.
    """

    def test_verdict_record_exit_1_on_inner_raise(self, monkeypatch):
        """Force the inner CLI handler to raise and assert exit 1.

        We patch ``tools._sdlc_utils.find_session`` (re-exported into
        ``tools.sdlc_verdict`` as ``_find_session``) so the patch survives
        the argparse ``set_defaults(func=_cli_record)`` capture — the
        ``args.func`` reference still resolves into our patched helper.
        """
        harness = (
            "import sys\n"
            "from tools import sdlc_verdict\n"
            "def boom(*a, **kw): raise RuntimeError('redis down')\n"
            "sdlc_verdict._find_session = boom\n"
            "sys.argv = ['sdlc_verdict', 'record', '--stage', 'CRITIQUE', "
            "'--verdict', 'NEEDS REVISION', '--session-id', '__nope__']\n"
            "sdlc_verdict.main()\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", harness],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1, (
            f"sdlc_verdict.main() should exit 1 on inner raise, got "
            f"{result.returncode}; stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        # stdout still emits {} so JSON parsers don't break
        assert result.stdout.strip() == "{}"
        # stderr surfaces the error so the operator sees it
        assert "redis down" in result.stderr or "RuntimeError" in result.stderr

    def test_verdict_get_exit_0_on_empty_match(self):
        """Empty result (no session matched) is NOT a failure — exit 0."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.sdlc_verdict",
                "get",
                "--stage",
                "CRITIQUE",
                "--session-id",
                "__definitely_not_a_real_session__",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert json.loads(result.stdout.strip()) == {}

    def test_dispatch_record_exit_1_on_inner_raise(self):
        """Force the inner CLI handler to raise and assert exit 1.

        Same monkeypatch pattern as the verdict test.
        """
        harness = (
            "import sys\n"
            "from tools import sdlc_dispatch\n"
            "def boom(*a, **kw): raise RuntimeError('redis down')\n"
            "sdlc_dispatch._find_session = boom\n"
            "sys.argv = ['sdlc_dispatch', 'record', '--skill', '/do-build', "
            "'--session-id', '__nope__']\n"
            "sdlc_dispatch.main()\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", harness],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1, (
            f"sdlc_dispatch.main() should exit 1 on inner raise, got "
            f"{result.returncode}; stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        assert result.stdout.strip() == "{}"
        assert "redis down" in result.stderr or "RuntimeError" in result.stderr

    def test_stage_marker_stays_silent_on_failure(self):
        """Best-effort tools (stage-marker) keep exit 0 on failure.

        Stage marker hasn't been changed — still meant to be silent.
        We assert the file does NOT contain a ``sys.exit(1)`` in its
        exception handler, which would imply somebody made it loud
        by accident.
        """
        src = (REPO_ROOT / "tools" / "sdlc_stage_marker.py").read_text()
        assert "sys.exit(1)" not in src or "best-effort" in src.lower(), (
            "tools/sdlc_stage_marker.py looks loud — verify it's still best-effort."
        )


class TestSkillMarkdownParity:
    """No bare ``python -m tools.sdlc_*`` calls remain in the include set."""

    def test_no_bare_invocations_in_skills_hooks_personas(self):
        offenders: list[tuple[str, int, str]] = []
        pattern = re.compile(r"python\s+-m\s+tools\.sdlc_")
        for path in _iter_include_paths():
            for lineno, line in enumerate(path.read_text().splitlines(), start=1):
                if pattern.search(line):
                    offenders.append(
                        (path.relative_to(REPO_ROOT).as_posix(), lineno, line.rstrip())
                    )
        assert not offenders, (
            "These files still call `python -m tools.sdlc_*` — they must use "
            "`sdlc-tool <subcommand>` instead:\n"
            + "\n".join(f"  {p}:{n}: {ln}" for p, n, ln in offenders)
        )

    def test_verdict_and_dispatch_calls_are_not_silenced(self):
        """Load-bearing recorders must surface failures — no 2>/dev/null || true."""
        offenders: list[tuple[str, int, str]] = []
        # Match: `sdlc-tool verdict ...|| true` or `sdlc-tool verdict ... 2>/dev/null`
        load_bearing = re.compile(r"sdlc-tool\s+(verdict|dispatch)\b[^\n]*")
        silencing = re.compile(r"(2>/dev/null|\|\|\s*true)")
        for path in _iter_include_paths():
            for lineno, line in enumerate(path.read_text().splitlines(), start=1):
                if load_bearing.search(line) and silencing.search(line):
                    offenders.append(
                        (path.relative_to(REPO_ROOT).as_posix(), lineno, line.rstrip())
                    )
        assert not offenders, (
            "Verdict/dispatch invocations must NOT silence failures — drop "
            "2>/dev/null and || true:\n" + "\n".join(f"  {p}:{n}: {ln}" for p, n, ln in offenders)
        )

    def test_pm_bash_allowlist_includes_sdlc_tool(self):
        """PM session must be allowed to call sdlc-tool."""
        src = (REPO_ROOT / "agent" / "hooks" / "pre_tool_use.py").read_text()
        assert '"sdlc-tool stage-query"' in src
        assert '"sdlc-tool verdict"' in src
        assert '"sdlc-tool dispatch"' in src
