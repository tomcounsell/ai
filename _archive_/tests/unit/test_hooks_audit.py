"""Unit tests for reflections/audits/hooks_audit.py.

Covers Pre-requisite Bug 2 (audit only ever inspected the project scope):
these tests prove both the project-scope AND user-scope `settings.json`
are validated with the same checks (Stop `|| true`, command path
existence), and that `.claude/agents/*.md` `hooks:` frontmatter blocks are
surfaced as an informational, non-FAIL-gated declared surface.

All user-scope assertions patch `Path.home()` to a `tmp_path` fixture --
none of these tests ever read or write the live operator
`~/.claude/settings.json`.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from reflections.audits.hooks_audit import (
    _hooks_audit_for_project,
    _scan_agent_hooks,
    _validate_hook_settings,
)


def _write_settings(path: Path, hooks: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hooks": hooks}, indent=2))


class TestValidateHookSettings:
    """Tests for the shared `_validate_hook_settings` scope-agnostic helper."""

    def test_missing_or_true_is_flagged(self, tmp_path):
        settings_path = tmp_path / ".claude" / "settings.json"
        (tmp_path / "stop.py").write_text("# stub\n")  # exists, so only || true is under test
        _write_settings(
            settings_path,
            {
                "Stop": [
                    {
                        "matcher": "",
                        "hooks": [{"type": "command", "command": "python stop.py"}],
                    }
                ]
            },
        )
        findings, issues = _validate_hook_settings(settings_path, tmp_path, "project")
        assert issues == 1
        assert any("FAIL" in f and "|| true" in f for f in findings)
        assert any(f.startswith("[project]") for f in findings)

    def test_or_true_present_is_not_flagged(self, tmp_path):
        settings_path = tmp_path / ".claude" / "settings.json"
        (tmp_path / "stop.py").write_text("# stub\n")
        _write_settings(
            settings_path,
            {
                "Stop": [
                    {
                        "matcher": "",
                        "hooks": [{"type": "command", "command": "python stop.py || true"}],
                    }
                ]
            },
        )
        findings, issues = _validate_hook_settings(settings_path, tmp_path, "project")
        assert issues == 0
        assert not any("FAIL" in f for f in findings)

    def test_missing_command_path_is_flagged(self, tmp_path):
        settings_path = tmp_path / ".claude" / "settings.json"
        _write_settings(
            settings_path,
            {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "python does_not_exist_validator.py || true",
                            }
                        ],
                    }
                ]
            },
        )
        findings, issues = _validate_hook_settings(settings_path, tmp_path, "project")
        assert issues == 1
        assert any("WARN" in f and "does_not_exist_validator.py" in f for f in findings)
        assert any(f.startswith("[project]") for f in findings)

    def test_existing_command_path_is_not_flagged(self, tmp_path):
        settings_path = tmp_path / ".claude" / "settings.json"
        (tmp_path / "real_validator.py").write_text("# stub\n")
        _write_settings(
            settings_path,
            {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {"type": "command", "command": "python real_validator.py || true"}
                        ],
                    }
                ]
            },
        )
        findings, issues = _validate_hook_settings(settings_path, tmp_path, "project")
        assert issues == 0
        assert not any("WARN" in f for f in findings)

    def test_user_scope_missing_or_true_is_flagged(self, tmp_path):
        """Same check, applied to a user-scope settings.json -- proves Bug 2 is fixed:
        a missing || true is no longer invisible just because it's in the user scope."""
        settings_path = tmp_path / "fakehome" / ".claude" / "settings.json"
        script_path = (
            tmp_path / "fakehome" / ".claude" / "hooks" / "sdlc" / "validate_sdlc_on_stop.py"
        )
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text("# stub\n")  # exists, so only || true is under test
        _write_settings(
            settings_path,
            {
                "Stop": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"python {script_path}",
                            }
                        ],
                    }
                ]
            },
        )
        findings, issues = _validate_hook_settings(settings_path, tmp_path / "fakehome", "user")
        assert issues == 1
        assert any("FAIL" in f and "|| true" in f for f in findings)
        assert any(f.startswith("[user]") for f in findings)

    def test_user_scope_missing_command_path_is_flagged(self, tmp_path):
        """Absolute (user-scope-shaped) command paths that don't exist are flagged too."""
        settings_path = tmp_path / "fakehome" / ".claude" / "settings.json"
        missing_script = tmp_path / "fakehome" / ".claude" / "hooks" / "sdlc" / "ghost.py"
        _write_settings(
            settings_path,
            {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {"type": "command", "command": f"python {missing_script} || true"}
                        ],
                    }
                ]
            },
        )
        findings, issues = _validate_hook_settings(settings_path, tmp_path / "fakehome", "user")
        assert issues == 1
        assert any("WARN" in f and "ghost.py" in f for f in findings)
        assert any(f.startswith("[user]") for f in findings)

    def test_nonexistent_settings_file_is_clean(self, tmp_path):
        findings, issues = _validate_hook_settings(
            tmp_path / "nope" / "settings.json", tmp_path, "project"
        )
        assert findings == []
        assert issues == 0

    def test_malformed_json_is_flagged(self, tmp_path):
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text("{not valid json")
        findings, issues = _validate_hook_settings(settings_path, tmp_path, "project")
        assert issues == 1
        assert any("FAIL" in f and "could not parse" in f for f in findings)


class TestScanAgentHooks:
    """Tests for the `.claude/agents/*.md` `hooks:` frontmatter scan (informational)."""

    def test_agent_with_hooks_block_is_surfaced(self, tmp_path):
        agents_dir = tmp_path / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "builder.md").write_text(
            "---\n"
            "name: builder\n"
            "description: test builder\n"
            "hooks:\n"
            "  PostToolUse:\n"
            "    - matcher: 'Write|Edit'\n"
            "      hooks:\n"
            "        - type: command\n"
            "          command: python format_file.py || true\n"
            "tools: ['*']\n"
            "---\n"
            "# Builder\n"
        )
        findings = _scan_agent_hooks(tmp_path)
        assert len(findings) == 1
        assert findings[0].startswith("[agent-hooks] INFO:")
        assert "builder.md" in findings[0]
        assert "PostToolUse" in findings[0]

    def test_agent_without_hooks_block_is_not_surfaced(self, tmp_path):
        agents_dir = tmp_path / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "validator.md").write_text(
            "---\nname: validator\ndescription: test validator\n---\n# Validator\n"
        )
        findings = _scan_agent_hooks(tmp_path)
        assert findings == []

    def test_no_agents_dir_returns_empty(self, tmp_path):
        assert _scan_agent_hooks(tmp_path) == []

    def test_malformed_frontmatter_is_skipped_not_raised(self, tmp_path):
        agents_dir = tmp_path / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "broken.md").write_text("---\nhooks: [unterminated\n---\nbody\n")
        # Must not raise -- malformed frontmatter is an informational surface,
        # never allowed to fail the audit.
        findings = _scan_agent_hooks(tmp_path)
        assert findings == []


class TestHooksAuditForProjectBothScopes:
    """End-to-end per-project body: proves both scopes render in one report."""

    def test_both_scope_fail_findings_render_together(self, tmp_path):
        repo_root = tmp_path / "repo"
        fakehome = tmp_path / "fakehome"
        repo_root.mkdir()
        (repo_root / "stop.py").write_text("# stub\n")  # exists, so only || true is under test

        # Project scope: Stop hook missing || true.
        _write_settings(
            repo_root / ".claude" / "settings.json",
            {
                "Stop": [
                    {
                        "matcher": "",
                        "hooks": [{"type": "command", "command": "python stop.py"}],
                    }
                ]
            },
        )

        # User scope: Stop hook missing || true (Pre-requisite Bug 1 shape).
        user_script = fakehome / ".claude" / "hooks" / "sdlc" / "validate_sdlc_on_stop.py"
        user_script.parent.mkdir(parents=True, exist_ok=True)
        user_script.write_text("# stub\n")  # exists, so only || true is under test
        _write_settings(
            fakehome / ".claude" / "settings.json",
            {
                "Stop": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"python {user_script}",
                            }
                        ],
                    }
                ]
            },
        )

        with patch("reflections.audits.hooks_audit.Path.home", return_value=fakehome):
            result = _hooks_audit_for_project({"working_directory": str(repo_root)})

        assert result["status"] == "ok"
        findings = result["findings"]
        project_fails = [f for f in findings if f.startswith("[project] FAIL")]
        user_fails = [f for f in findings if f.startswith("[user] FAIL")]
        assert len(project_fails) == 1
        assert len(user_fails) == 1
        assert "2 settings issues" in result["summary"]

    def test_agent_hooks_surfaced_alongside_scope_findings(self, tmp_path):
        repo_root = tmp_path / "repo"
        fakehome = tmp_path / "fakehome"
        repo_root.mkdir()
        _write_settings(repo_root / ".claude" / "settings.json", {})

        agents_dir = repo_root / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "builder.md").write_text(
            "---\n"
            "name: builder\n"
            "hooks:\n"
            "  PostToolUse:\n"
            "    - matcher: 'Write|Edit'\n"
            "      hooks:\n"
            "        - type: command\n"
            "          command: python format_file.py || true\n"
            "---\n"
            "# Builder\n"
        )

        with patch("reflections.audits.hooks_audit.Path.home", return_value=fakehome):
            result = _hooks_audit_for_project({"working_directory": str(repo_root)})

        assert any(f.startswith("[agent-hooks] INFO:") for f in result["findings"])


class TestRealRepoBothScopesIntegration:
    """Cross-cutting Phase B integration gate (Step 8b): scan BOTH the real,
    live-repo project-scope ``.claude/settings.json`` AND a manifest-generated
    fake user-scope ``settings.json`` (built in an isolated fake HOME, never
    the live operator file) and assert every registered command's script path
    exists on disk and every Stop-event hook command carries ``|| true``.

    This is the kind of check no individual builder (manifest, generators,
    audit) could write in isolation: it only fails if the manifest, the two
    generators, and the audit's path-resolution logic all agree with each
    other simultaneously.
    """

    def test_real_project_scope_is_clean(self):
        repo_root = Path(__file__).resolve().parents[2]

        findings, issues = _validate_hook_settings(
            repo_root / ".claude" / "settings.json", repo_root, "project"
        )

        assert issues == 0, findings
        assert not any("FAIL" in f for f in findings)
        assert not any("WARN: Hook script not found" in f for f in findings)

    def test_manifest_generated_user_scope_is_clean(self, tmp_path):
        from scripts.update import hardlinks
        from scripts.update.hook_manifest import load_hook_manifest

        repo_root = Path(__file__).resolve().parents[2]
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()

        with patch("scripts.update.hardlinks.Path.home", return_value=fake_home):
            manifest = load_hook_manifest(repo_root / ".claude" / "hooks" / "manifest.toml")
            result = hardlinks.sync_user_hooks(repo_root, manifest)

        assert result.errors == 0

        user_settings_path = fake_home / ".claude" / "settings.json"
        findings, issues = _validate_hook_settings(user_settings_path, fake_home, "user")

        assert issues == 0, findings
        assert not any("FAIL" in f for f in findings)
        assert not any("WARN: Hook script not found" in f for f in findings)


class TestShimFailOpenSignal:
    """Issue #2503, Risk 5: the ``hook_python`` shim's fail-open branch produces
    its OWN dedicated finding, not folded into the aggregate hook-error count.

    A machine whose main checkout is relocated/renamed (or its ``.venv``
    removed) silently disables every project hook -- the shim exits 0 without
    running any Python. It writes its own ``logs/hooks.log`` record on that
    branch (nothing else can, since no hook Python runs), and the audit
    surfaces it as a distinguishable finding rather than ordinary hook-error
    noise. The dedicated-finding test below drives the REAL shim end to end so
    it proves that whole path, not a substring match on a fixture.
    """

    def _recent_ts(self) -> str:
        from utils.utc import utc_now

        return utc_now().strftime("%Y-%m-%d %H:%M:%S")

    def test_shim_fail_open_gets_dedicated_finding(self, tmp_path):
        import subprocess

        from reflections.audits.hooks_audit import _hooks_audit_for_project

        repo_root = tmp_path / "repo"
        fakehome = tmp_path / "fakehome"
        repo_root.mkdir()
        _write_settings(repo_root / ".claude" / "settings.json", {})

        # Drive the real shim against a non-git, no-venv root so IT writes
        # logs/hooks.log -- the audit then reads the shim's own output.
        shim = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "hook_python"
        proc = subprocess.run(
            ["/bin/sh", "-c", f"{shim} -V"],
            env={"CLAUDE_PROJECT_DIR": str(repo_root), "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        assert (repo_root / "logs" / "hooks.log").exists(), (
            f"shim wrote no hooks.log record; stderr: {proc.stderr!r}"
        )

        with patch("reflections.audits.hooks_audit.Path.home", return_value=fakehome):
            result = _hooks_audit_for_project({"working_directory": str(repo_root)})

        shim_findings = [f for f in result["findings"] if "hook_python shim failing open" in f]
        assert len(shim_findings) == 1, (
            f"expected exactly one dedicated shim finding, got: {result['findings']}"
        )

    def test_no_shim_finding_without_marker(self, tmp_path):
        """An ordinary hook error (no shim marker) yields the aggregate count
        line but NOT the dedicated shim finding."""
        from reflections.audits.hooks_audit import _hooks_audit_for_project

        repo_root = tmp_path / "repo"
        fakehome = tmp_path / "fakehome"
        repo_root.mkdir()
        _write_settings(repo_root / ".claude" / "settings.json", {})

        hooks_log = repo_root / "logs" / "hooks.log"
        hooks_log.parent.mkdir(parents=True, exist_ok=True)
        ts = self._recent_ts()
        hooks_log.write_text(f"{ts} ERROR - some_validator.py - boom\n")

        with patch("reflections.audits.hooks_audit.Path.home", return_value=fakehome):
            result = _hooks_audit_for_project({"working_directory": str(repo_root)})

        assert not any("hook_python shim failing open" in f for f in result["findings"])
