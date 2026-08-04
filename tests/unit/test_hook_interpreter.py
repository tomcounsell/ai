"""Regression tests for the SDLC hook interpreter contract (issue #2503).

Two prior fixes to the "hooks run under a bare ``python`` that a stripped
``/bin/sh`` cannot resolve" bug BOTH failed for the same reason: nobody ever
EXECUTED a generated hook command under a stripped environment. These tests are
the standing guard against that recurrence, so several of them run REAL
commands (a ``/bin/sh``-invoked shim, an ``env -i`` interpreter probe) rather
than asserting on strings alone.

Groups:
  (a) No generated command starts with a bare ``python`` token, in either
      scope -- exempting ``migrations.py``'s legacy match literal explicitly.
  (b) An ALWAYS-ON AST walk over every declared global-scope script for
      pre-3.10 union annotations without ``from __future__ import annotations``,
      and for 3.11+ runtime symbols. Parses source, so it never skips.
  (c) A real ``env -i`` execution of each global script under the minimum
      interpreter with ``{}`` on stdin. ``skipif``-guarded ONLY on that
      interpreter being present and executable.
  (d) Double-``/update`` idempotence: generator + migration run twice yields no
      duplicate hook entries in either settings.json.
  (e) The shim's worktree-precedence branch: from a worktree (with or without
      its own ``.venv``) the shim resolves the MAIN checkout's interpreter.
      Not skipped on interpreter presence -- stubs remove that dependency.

TEST ISOLATION: never touches production Redis or the live
``~/.claude/settings.json``. ``tmp_path`` and a monkeypatched ``Path.home``
throughout.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from scripts.update import hardlinks, migrations
from scripts.update.hardlinks import (
    GLOBAL_INTERPRETER_CANDIDATES,
    MIN_GLOBAL_PYTHON,
    PROJECT_HOOK_INTERPRETER,
    _extract_manifest_id,
    resolve_global_interpreter,
)
from scripts.update.hook_manifest import load_hook_manifest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS_DIR = _REPO_ROOT / ".claude" / "hooks"
_MANIFEST = _HOOKS_DIR / "manifest.toml"

# The four globally-deployed scripts: the 3 declared global hooks plus
# sdlc_context.py (a sibling-imported context injector). Paths relative to
# ``.claude/hooks/``. sdlc_context.py is not a declared hook but ships to
# ~/.claude/hooks/ alongside them and must meet the same 3.9 floor (spike-2).
_EXTRA_GLOBAL_SCRIPTS = ("sdlc/sdlc_context.py",)


def _global_script_rel_paths() -> list[str]:
    manifest = load_hook_manifest(_MANIFEST)
    scripts = [d.script for d in manifest if d.scope == "global"]
    scripts.extend(_EXTRA_GLOBAL_SCRIPTS)
    # De-dupe while preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for s in scripts:
        if s not in seen:
            seen.add(s)
            ordered.append(s)
    return ordered


def _first_token(command: str) -> str:
    return command.split()[0] if command.split() else ""


def _starts_with_bare_python(command: str) -> bool:
    """True iff the command's leading interpreter token is a bare ``python``.

    A bare token has no ``/`` (not an absolute path) and no ``$`` (not the
    ``$CLAUDE_PROJECT_DIR`` shim expansion). ``/usr/bin/python3`` and
    ``"$CLAUDE_PROJECT_DIR"/.claude/hooks/hook_python`` are both fine.
    """
    tok = _first_token(command).strip('"')
    return "/" not in tok and "$" not in tok and tok.startswith("python")


# ---------------------------------------------------------------------------
# (a) No generated command starts with a bare ``python`` token
# ---------------------------------------------------------------------------


def test_project_scope_commands_have_no_bare_python():
    manifest = load_hook_manifest(_MANIFEST)
    hooks = hardlinks.generate_project_hooks(manifest)
    commands = [
        h["command"]
        for event_blocks in hooks.values()
        for block in event_blocks
        for h in block["hooks"]
    ]
    assert commands, "expected generated project hook commands"
    offenders = [c for c in commands if _starts_with_bare_python(c)]
    assert not offenders, f"project-scope commands begin with a bare python: {offenders}"


def test_project_interpreter_token_is_the_shim():
    assert not _starts_with_bare_python(PROJECT_HOOK_INTERPRETER)
    assert PROJECT_HOOK_INTERPRETER.endswith("/.claude/hooks/hook_python")


def test_global_scope_commands_have_no_bare_python(tmp_path, monkeypatch):
    """Generate the real user-scope settings.json in an isolated fake HOME and
    assert no command begins with a bare ``python``."""
    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setenv("HOME", str(fake_home))

    if resolve_global_interpreter() is None:
        pytest.skip("no system python3 resolved on this machine to generate global commands")

    result = hardlinks.sync_user_hooks(_REPO_ROOT)
    assert result.errors == 0, [a.error for a in result.actions if a.error]

    settings = json.loads((fake_home / ".claude" / "settings.json").read_text())
    commands = [
        h.get("command", "")
        for event_blocks in settings.get("hooks", {}).values()
        for block in event_blocks
        for h in block.get("hooks", [])
    ]
    assert commands, "expected generated global hook commands"
    offenders = [c for c in commands if _starts_with_bare_python(c)]
    assert not offenders, f"global-scope commands begin with a bare python: {offenders}"


def test_global_interpreter_candidates_are_absolute():
    for cand in GLOBAL_INTERPRETER_CANDIDATES:
        assert cand.startswith("/"), f"global candidate is not an absolute path: {cand}"


def test_merge_hook_settings_runs_removal_pass_with_no_interpreter(tmp_path):
    """``_merge_hook_settings`` must survive ``interpreter=None``.

    ``sync_user_hooks`` returns early on a total probe failure only when there
    are global declarations to register; with an empty global scope it calls
    through with ``interpreter=None`` on purpose, so the removal + block-pruning
    passes still sweep now-undeclared marked entries. Guarding the add/update
    loop from *outside* the loop turned that deliberate path into an
    AssertionError out of ``/update``'s hardlink step (issue #2503 review).
    """
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python /x/y.py  # hook:no-longer-declared",
                                    "timeout": 5,
                                },
                                {
                                    "type": "command",
                                    "command": "python /opt/my-own-tool/guard.py",
                                    "timeout": 5,
                                },
                            ],
                        }
                    ]
                }
            }
        )
    )

    result = hardlinks.HardlinkSyncResult()
    hardlinks._merge_hook_settings(settings_path, tmp_path / "hooks", [], None, result)

    assert result.errors == 0, [a.error for a in result.actions if a.error]
    written = settings_path.read_text()
    assert "hook:no-longer-declared" not in written, "removal pass did not run"
    assert "/opt/my-own-tool/guard.py" in written, "unmarked foreign hook was clobbered"


def test_legacy_fork_literal_keeps_its_bare_python():
    """The ONE deliberate exemption: ``migrations._legacy_fork_command_prefix``
    is a MATCH KEY against bytes already on disk in each machine's settings.json
    and must keep its bare ``python`` forever (issue #2503). If this ever stops
    being a bare-python literal, the migration silently stops matching real
    machines -- so the ban test exempts it here, explicitly and by name."""
    literal = migrations._legacy_fork_command_prefix("sdlc/validate_sdlc_on_stop.py")
    assert literal.startswith("python "), (
        "the legacy match literal must remain bare `python ` -- it matches "
        "pre-manifest bytes on disk, not generated output"
    )


# ---------------------------------------------------------------------------
# (b) Always-on AST walk: no pre-3.10 union annotations without __future__,
#     no 3.11+ runtime symbols. Parses source -- never skipped.
# ---------------------------------------------------------------------------

# Runtime symbols that only exist on 3.10/3.11+ and that ``from __future__
# import annotations`` does NOT rescue (they are used at runtime, not in an
# annotation position). Spike-2 confirmed zero matches today; this pins it.
_FORBIDDEN_ATTRIBUTES = {
    ("datetime", "UTC"),  # 3.11
    ("typing", "Self"),  # 3.11
    ("asyncio", "timeout"),  # 3.11
}
_FORBIDDEN_NAMES = {"ExceptionGroup"}  # 3.11 builtin
_FORBIDDEN_IMPORT_MODULES = {"tomllib"}  # 3.11 stdlib


def _has_future_annotations(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            if any(alias.name == "annotations" for alias in node.names):
                return True
    return False


def _union_binops_in_annotations(tree: ast.Module) -> list[int]:
    """Return line numbers of ``X | Y`` BinOps appearing in annotation position.

    PEP 604 union syntax in an annotation is evaluated at import time on <3.10
    unless ``from __future__ import annotations`` is present -- exactly the
    crash class that silently disabled the commit-message guard (issue #2503).
    """
    offending: list[int] = []

    def _scan_annotation(annotation: ast.expr | None) -> None:
        if annotation is None:
            return
        for sub in ast.walk(annotation):
            if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.BitOr):
                offending.append(sub.lineno)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for arg in (
                *node.args.args,
                *node.args.posonlyargs,
                *node.args.kwonlyargs,
                node.args.vararg,
                node.args.kwarg,
            ):
                if arg is not None:
                    _scan_annotation(arg.annotation)
            _scan_annotation(node.returns)
        elif isinstance(node, ast.AnnAssign):
            _scan_annotation(node.annotation)

    return offending


def _forbidden_runtime_symbols(tree: ast.Module) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if (node.value.id, node.attr) in _FORBIDDEN_ATTRIBUTES:
                hits.append(f"{node.value.id}.{node.attr} (line {node.lineno})")
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            hits.append(f"{node.id} (line {node.lineno})")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _FORBIDDEN_IMPORT_MODULES:
                    hits.append(f"import {alias.name} (line {node.lineno})")
        elif isinstance(node, ast.ImportFrom):
            if node.module in _FORBIDDEN_IMPORT_MODULES:
                hits.append(f"from {node.module} (line {node.lineno})")
    return hits


@pytest.mark.parametrize("rel_script", _global_script_rel_paths())
def test_global_script_parses_free_of_pre310_syntax(rel_script):
    path = _HOOKS_DIR / rel_script
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))

    union_lines = _union_binops_in_annotations(tree)
    if union_lines and not _has_future_annotations(tree):
        pytest.fail(
            f"{rel_script} uses PEP 604 union annotations at lines {union_lines} "
            f"without `from __future__ import annotations` -- crashes at import "
            f"under Python {MIN_GLOBAL_PYTHON[0]}.{MIN_GLOBAL_PYTHON[1]} (issue #2503)"
        )

    forbidden = _forbidden_runtime_symbols(tree)
    assert not forbidden, (
        f"{rel_script} uses 3.11+ runtime symbols not available under "
        f"Python {MIN_GLOBAL_PYTHON[0]}.{MIN_GLOBAL_PYTHON[1]}: {forbidden}"
    )


def test_commit_message_guard_has_future_import():
    """The one file spike-2 found affected must carry the fix."""
    source = (_HOOKS_DIR / "sdlc" / "validate_commit_message_sdlc.py").read_text()
    tree = ast.parse(source)
    assert _has_future_annotations(tree)


# ---------------------------------------------------------------------------
# (c) Real ``env -i`` execution under the minimum interpreter
# ---------------------------------------------------------------------------


def _min_interpreter() -> str | None:
    """Path to an executable interpreter at exactly the ``MIN_GLOBAL_PYTHON``
    floor, else None. Probes by EXECUTION (a Command Line Tools stub exits
    non-zero), preferring ``/usr/bin/python3`` -- the worst case a global hook
    lands on."""
    want = f"{MIN_GLOBAL_PYTHON[0]}.{MIN_GLOBAL_PYTHON[1]}"
    for cand in ("/usr/bin/python3", *GLOBAL_INTERPRETER_CANDIDATES):
        try:
            proc = subprocess.run(
                [cand, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
                env={},
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0 and proc.stdout.strip() == want:
            return cand
    return None


_MIN_PY = _min_interpreter()


@pytest.mark.skipif(
    _MIN_PY is None,
    reason=f"no executable Python {MIN_GLOBAL_PYTHON[0]}.{MIN_GLOBAL_PYTHON[1]} present",
)
@pytest.mark.parametrize("rel_script", _global_script_rel_paths())
def test_global_script_runs_clean_under_min_interpreter(rel_script):
    """Each globally-deployed script exits 0 under the minimum interpreter with
    ``{}`` on stdin and a stripped environment -- the empty-payload path a
    malformed hook event actually delivers. No ``command not found`` on stderr."""
    path = _HOOKS_DIR / rel_script
    proc = subprocess.run(
        [_MIN_PY, str(path)],
        input="{}",
        env={},
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(_REPO_ROOT),
    )
    assert proc.returncode == 0, (
        f"{rel_script} exited {proc.returncode} under {_MIN_PY}\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "command not found" not in proc.stderr, (
        f"{rel_script} emitted 'command not found' under a stripped env: {proc.stderr}"
    )


# ---------------------------------------------------------------------------
# (d) Double-``/update`` idempotence
# ---------------------------------------------------------------------------


def _all_commands(settings: dict) -> list[str]:
    return [
        h.get("command", "")
        for event_blocks in settings.get("hooks", {}).values()
        for block in event_blocks
        for h in block.get("hooks", [])
    ]


def test_double_update_project_scope_is_idempotent(tmp_path):
    """Regenerating project settings.json twice yields no duplicate commands."""
    import shutil

    tmp_claude = tmp_path / ".claude"
    shutil.copytree(_HOOKS_DIR, tmp_claude / "hooks")
    shutil.copy(_REPO_ROOT / ".claude" / "settings.json", tmp_claude / "settings.json")

    manifest = load_hook_manifest(tmp_claude / "hooks" / "manifest.toml")
    hardlinks.sync_project_hooks(tmp_path, manifest)
    first = json.loads((tmp_claude / "settings.json").read_text())
    hardlinks.sync_project_hooks(tmp_path, manifest)
    second = json.loads((tmp_claude / "settings.json").read_text())

    # Idempotence = the second /update introduces no new entries. (Note: a
    # command string may legitimately repeat -- validate_features_readme_sort.py
    # is declared twice under different matchers -- so entry-count stability, not
    # set-uniqueness, is the correct duplicate measure here.)
    assert first == second
    assert len(_all_commands(first)) == len(_all_commands(second)), (
        "second project /update grew the hooks block -- duplicates introduced"
    )


def test_double_update_user_scope_and_migration_is_idempotent(tmp_path, monkeypatch):
    """Running sync_user_hooks + both hook migrations twice against a fake HOME
    yields zero duplicate hook entries in ~/.claude/settings.json."""
    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setenv("HOME", str(fake_home))

    if resolve_global_interpreter() is None:
        pytest.skip("no system python3 resolved on this machine")

    def _one_update_cycle() -> None:
        hardlinks.sync_user_hooks(_REPO_ROOT)
        migrations._migrate_hook_registration_manifest_ids(_REPO_ROOT)
        migrations._migrate_sweep_legacy_unmarked_global_hooks(_REPO_ROOT)

    _one_update_cycle()
    first = json.loads((fake_home / ".claude" / "settings.json").read_text())
    _one_update_cycle()
    second = json.loads((fake_home / ".claude" / "settings.json").read_text())

    assert first == second, "second /update cycle changed user settings.json"
    # Global scope carries one entry per manifest_id -- Risk 6's duplicate is a
    # legacy + marked copy for the same id, so assert manifest_ids are unique.
    marked = [mid for c in _all_commands(second) if (mid := _extract_manifest_id(c))]
    assert marked, "expected at least one marked global entry"
    assert len(marked) == len(set(marked)), f"duplicate manifest_id entries: {marked}"
    assert len(_all_commands(first)) == len(_all_commands(second)), (
        "second user /update cycle grew the hooks block -- duplicates introduced"
    )


# ---------------------------------------------------------------------------
# (e) Shim worktree-precedence -- the standing guard for Success Criterion 2.
#     Not skipped on interpreter presence: the stubs remove that dependency.
# ---------------------------------------------------------------------------

_SHIM = _HOOKS_DIR / "hook_python"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )


def _write_python_stub(venv_bin_python: Path, sentinel: str) -> None:
    venv_bin_python.parent.mkdir(parents=True, exist_ok=True)
    venv_bin_python.write_text(f"#!/bin/sh\necho {sentinel}\n")
    venv_bin_python.chmod(0o755)


def _run_shim(project_dir: Path) -> subprocess.CompletedProcess:
    """Invoke the real shim through /bin/sh, exactly as Claude Code does, with
    ``CLAUDE_PROJECT_DIR`` pointing at ``project_dir`` and a stripped env."""
    return subprocess.run(
        ["/bin/sh", "-c", f"{_SHIM} -V"],
        env={"CLAUDE_PROJECT_DIR": str(project_dir), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        cwd=str(project_dir),
        timeout=30,
    )


@pytest.fixture
def main_and_worktree(tmp_path):
    """A ``git init``-ed main checkout with a ``.venv/bin/python`` stub echoing
    ``MAIN``, plus a ``git worktree add``-ed child with NO local ``.venv``."""
    main = tmp_path / "main"
    main.mkdir()
    _write_python_stub(main / ".venv" / "bin" / "python", "MAIN")

    _git(main, "init")
    (main / "seed.txt").write_text("seed\n")
    _git(main, "add", "seed.txt")
    _git(main, "commit", "-m", "seed")

    worktree = tmp_path / "wt"
    _git(main, "worktree", "add", str(worktree))
    return main, worktree


def test_shim_from_worktree_without_venv_resolves_main(main_and_worktree):
    """Case 1: a worktree with no local ``.venv`` -> the git-common-dir recovery
    branch resolves the MAIN checkout's interpreter."""
    _main, worktree = main_and_worktree
    proc = _run_shim(worktree)
    assert proc.returncode == 0, proc.stderr
    assert "MAIN" in proc.stdout, f"expected MAIN sentinel, got: {proc.stdout!r}"


def test_shim_from_worktree_with_own_venv_still_resolves_main(main_and_worktree):
    """Case 2 (load-bearing): a worktree WITH its own ``.venv`` still resolves
    the MAIN checkout's interpreter, not the local one. Pins the main-first
    precedence decision -- this branch is live on 9 of 57 real worktrees today
    (spike-1). Under a local-first order this would resolve LOCAL."""
    _main, worktree = main_and_worktree
    _write_python_stub(worktree / ".venv" / "bin" / "python", "LOCAL")

    proc = _run_shim(worktree)
    assert proc.returncode == 0, proc.stderr
    assert "MAIN" in proc.stdout, (
        f"precedence violated: worktree with its own venv resolved to {proc.stdout!r}, "
        "expected the MAIN checkout"
    )
    assert "LOCAL" not in proc.stdout


def test_shim_from_main_checkout_resolves_main(main_and_worktree):
    """Case 3: from the main checkout itself, the two paths coincide -> MAIN."""
    main, _worktree = main_and_worktree
    proc = _run_shim(main)
    assert proc.returncode == 0, proc.stderr
    assert "MAIN" in proc.stdout, f"expected MAIN sentinel, got: {proc.stdout!r}"


def test_shim_fail_open_names_directory_empty_stdout_exit_0(tmp_path):
    """The fail-open branch: a non-git, non-venv directory -> the shim writes
    its failure message (naming the searched directory) to STDERR, keeps STDOUT
    EMPTY (hook stdout is parsed as JSON), and exits 0. The stderr literal is
    the same one the hooks_audit test asserts, so the two cannot drift."""
    non_git = tmp_path / "plain"
    non_git.mkdir()

    proc = _run_shim(non_git)

    assert proc.returncode == 0, f"fail-open must exit 0, got {proc.returncode}"
    assert proc.stdout == "", f"stdout must stay empty, got: {proc.stdout!r}"
    assert "hook_python: no repo venv interpreter found" in proc.stderr
    assert str(non_git) in proc.stderr, (
        f"fail-open message must name the searched directory, got: {proc.stderr!r}"
    )


def test_shim_fail_open_writes_hooks_log_in_log_hook_error_format(tmp_path):
    """The fail-open branch also APPENDS to ``<root>/logs/hooks.log`` in
    ``log_hook_error``'s exact format, creating ``logs/`` if absent.

    No hook Python runs on this branch, so the shim is the only thing that can
    emit the record that makes hooks_audit's dedicated shim finding reachable.
    The append must not disturb the load-bearing invariants (empty stdout,
    exit 0), and the logged message must be the same string sent to stderr.
    """
    non_git = tmp_path / "plain"
    non_git.mkdir()  # note: no logs/ subdirectory -- the shim must create it

    proc = _run_shim(non_git)

    assert proc.returncode == 0, f"fail-open must exit 0, got {proc.returncode}"
    assert proc.stdout == "", f"stdout must stay empty, got: {proc.stdout!r}"

    log_path = non_git / "logs" / "hooks.log"
    assert log_path.exists(), "fail-open branch wrote no logs/hooks.log record"
    lines = log_path.read_text().splitlines()
    assert len(lines) == 1, f"expected exactly one record, got: {lines!r}"

    expected_msg = f"hook_python: no repo venv interpreter found under {non_git}"
    ts_re = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"
    pattern = rf"^{ts_re} - hook_python - ERROR - {re.escape(expected_msg)}$"
    assert re.match(pattern, lines[0]), (
        f"record does not match log_hook_error's format: {lines[0]!r}"
    )
    assert expected_msg == proc.stderr.strip(), (
        "logged message must be byte-identical to the stderr line"
    )

    # A second invocation appends rather than truncating.
    _run_shim(non_git)
    assert len(log_path.read_text().splitlines()) == 2
