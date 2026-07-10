"""Unit tests for scripts/sync_claude_to_opencode.py (the OpenCode config generator).

Covers the PR #1957 review findings:
- parse_hooks grouping, combined matchers, and loud warnings for dropped hooks
- build_permission reads ONLY the committed settings.json (never settings.local.json)
- Bash prefix specs (`gh pr:*`) emit both the bare and the trailing-arg glob
- split_frontmatter / _as_list edge cases
- selective rewrite: second run writes nothing; a changed source rewrites only
  its own artifact; headers are date-free
- block-decision plumbing: the emitted TS parses validator stdout and maps
  lowercase tool ids back to Claude casing; a real validator subprocess proves
  the {"decision": "block"} contract for a correctly-cased Bash payload
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts.sync_claude_to_opencode import (
    _as_list,
    _claude_bash_to_globs,
    build_permission,
    main,
    parse_hooks,
    split_frontmatter,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMIT_VALIDATOR = REPO_ROOT / ".claude" / "hooks" / "validators" / "validate_commit_message.py"
BAD_COMMIT_COMMAND = 'git commit -m "fix: x\n\nCo-Authored-By: Claude <noreply@anthropic.com>"'

SETTINGS_FIXTURE = {
    "permissions": {
        "allow": [
            "Bash(gh pr:*)",
            "Bash(git add *)",
            "Skill(do-issue)",
            "Write(.claude/hooks/**)",
        ]
    },
    "skillOverrides": {"grill-me": "off"},
    "hooks": {
        "UserPromptSubmit": [
            {
                "matcher": "",
                "hooks": [{"type": "command", "command": "python prompt.py || true"}],
            }
        ],
        "PreToolUse": [
            {
                "matcher": "",
                "hooks": [{"type": "command", "command": "python pre_global.py || true"}],
            },
            {
                "matcher": "Bash",
                "hooks": [{"type": "command", "command": "python check_bash.py"}],
            },
            {
                "matcher": "Write|Edit",
                "hooks": [{"type": "command", "command": "python check_edit.py"}],
            },
        ],
        "PostToolUse": [
            {
                "matcher": "Write",
                "hooks": [{"type": "command", "command": "python post_write.py"}],
            },
            {
                "matcher": "Edit",
                "hooks": [{"type": "command", "command": "python post_edit.py"}],
            },
        ],
        "Stop": [
            {
                "matcher": "",
                "hooks": [{"type": "command", "command": "python stop.py || true"}],
            }
        ],
        "PostCompact": [
            {
                "matcher": "",
                "hooks": [{"type": "command", "command": "python compact.py || true"}],
            }
        ],
    },
}


@pytest.fixture
def claude_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".claude"
    (d / "agents").mkdir(parents=True)
    (d / "commands" / "roles").mkdir(parents=True)
    (d / "hooks").mkdir()
    (d / "settings.json").write_text(json.dumps(SETTINGS_FIXTURE))
    (d / "agents" / "helper.md").write_text(
        "---\ndescription: A helper\nmodel: sonnet\ntools: Read, Grep\n---\nHelper body.\n"
    )
    (d / "agents" / "builder.md").write_text(
        "---\ndescription: Builds things\ntools: ['*']\n---\nBuilder body.\n"
    )
    (d / "commands" / "roles" / "prime-x.md").write_text(
        "---\ndescription: Prime X\n---\nDo $ARGUMENTS.\n"
    )
    (d / "commands" / "roles" / "_shared.md").write_text("shared include, not a command\n")
    (d / "hooks" / "check_bash.py").write_text("# stub validator\n")
    return d


def _snapshot(root: Path) -> dict:
    """Map every file under root to (mtime_ns, bytes) — detects any write, even no-op."""
    return {
        p: (p.stat().st_mtime_ns, p.read_bytes()) for p in sorted(root.rglob("*")) if p.is_file()
    }


# --------------------------------------------------------------------------- #
# parse_hooks
# --------------------------------------------------------------------------- #
class TestParseHooks:
    def test_grouping_against_fixture(self, claude_dir):
        groups = parse_hooks(claude_dir)
        assert [r["cmd"] for r in groups["pre_global"]] == ["python pre_global.py"]
        assert groups["pre_global"][0]["blocking"] is False
        assert [r["cmd"] for r in groups["pre_bash"]] == ["python check_bash.py"]
        assert groups["pre_bash"][0]["blocking"] is True
        assert [r["cmd"] for r in groups["pre_edit"]] == ["python check_edit.py"]
        assert [r["cmd"] for r in groups["post_write"]] == ["python post_write.py"]
        assert [r["cmd"] for r in groups["post_edit"]] == ["python post_edit.py"]
        assert [r["cmd"] for r in groups["session_created"]] == ["python prompt.py"]
        assert [r["cmd"] for r in groups["session_idle"]] == ["python stop.py"]
        assert [r["cmd"] for r in groups["session_compacted"]] == ["python compact.py"]

    def test_combined_matcher_registers_in_both_groups(self, claude_dir):
        settings = json.loads((claude_dir / "settings.json").read_text())
        settings["hooks"]["PreToolUse"].append(
            {"matcher": "Bash|Write", "hooks": [{"type": "command", "command": "python both.py"}]}
        )
        (claude_dir / "settings.json").write_text(json.dumps(settings))
        groups = parse_hooks(claude_dir)
        assert "python both.py" in [r["cmd"] for r in groups["pre_bash"]]
        assert "python both.py" in [r["cmd"] for r in groups["pre_edit"]]

    def test_unmatched_matcher_warns_loudly(self, claude_dir, capsys):
        settings = json.loads((claude_dir / "settings.json").read_text())
        settings["hooks"]["PreToolUse"].append(
            {"matcher": "Task", "hooks": [{"type": "command", "command": "python task_only.py"}]}
        )
        (claude_dir / "settings.json").write_text(json.dumps(settings))
        groups = parse_hooks(claude_dir)
        err = capsys.readouterr().err
        assert "WARNING" in err
        assert "python task_only.py" in err
        all_cmds = [r["cmd"] for recs in groups.values() for r in recs]
        assert "python task_only.py" not in all_cmds

    def test_no_warning_when_all_hooks_matched(self, claude_dir, capsys):
        parse_hooks(claude_dir)
        assert "WARNING" not in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# build_permission
# --------------------------------------------------------------------------- #
class TestBuildPermission:
    def test_reads_committed_settings(self, claude_dir):
        permission = build_permission(claude_dir)
        assert permission["bash"]["git add *"] == "allow"
        assert permission["skill"]["do-issue"] == "allow"
        assert permission["skill"]["grill-me"] == "deny"
        assert permission["bash"]["*"] == "ask"

    def test_ignores_settings_local_json_even_when_present(self, claude_dir):
        (claude_dir / "settings.local.json").write_text(
            json.dumps(
                {
                    "permissions": {"allow": ["Bash(rm -rf /machine-local:*)"]},
                    "skillOverrides": {"frontend-design": "off"},
                }
            )
        )
        permission = build_permission(claude_dir)
        assert not any("rm -rf" in key for key in permission["bash"])
        assert "frontend-design" not in permission["skill"]

    def test_bash_prefix_spec_emits_bare_and_trailing_arg_globs(self, claude_dir):
        permission = build_permission(claude_dir)
        # Claude's `gh pr:*` matches bare `gh pr` too; both keys must exist.
        assert permission["bash"]["gh pr"] == "allow"
        assert permission["bash"]["gh pr *"] == "allow"

    def test_path_scoped_write_grant_is_not_collapsed(self, claude_dir):
        # Write(.claude/hooks/**) must not add anything beyond the template default.
        permission = build_permission(claude_dir)
        assert permission["write"] == "allow"  # template default, not a scoped grant
        assert ".claude/hooks" not in json.dumps(permission)

    def test_missing_settings_returns_template_defaults(self, tmp_path):
        permission = build_permission(tmp_path / "nonexistent")
        assert permission == {
            "edit": "allow",
            "write": "allow",
            "bash": {"*": "ask"},
            "skill": {"*": "allow"},
        }


def test_claude_bash_to_globs():
    assert _claude_bash_to_globs("gh pr:*") == ["gh pr", "gh pr *"]
    assert _claude_bash_to_globs("git add *") == ["git add *"]
    assert _claude_bash_to_globs("ls") == ["ls"]


# --------------------------------------------------------------------------- #
# split_frontmatter / _as_list edge cases
# --------------------------------------------------------------------------- #
class TestSplitFrontmatter:
    def test_valid_frontmatter(self):
        fm, body = split_frontmatter("---\ndescription: hi\n---\nbody\n")
        assert fm == {"description": "hi"}
        assert body == "body\n"

    def test_no_frontmatter(self):
        fm, body = split_frontmatter("just text\n")
        assert fm == {}
        assert body == "just text\n"

    def test_unterminated_frontmatter(self):
        text = "---\ndescription: hi\nno closing delimiter"
        fm, body = split_frontmatter(text)
        assert fm == {}
        assert body == text

    def test_empty_frontmatter_block(self):
        fm, body = split_frontmatter("---\n---\nbody\n")
        assert fm == {}
        assert body == "body\n"


class TestAsList:
    def test_none(self):
        assert _as_list(None) == []

    def test_list_passthrough(self):
        assert _as_list(["Read", "Grep"]) == ["Read", "Grep"]

    def test_comma_string(self):
        assert _as_list("Read, Grep, Bash") == ["Read", "Grep", "Bash"]

    def test_comma_string_with_empties(self):
        assert _as_list("Read,,  ,Grep") == ["Read", "Grep"]

    def test_scalar(self):
        assert _as_list(42) == [42]


# --------------------------------------------------------------------------- #
# Selective rewrite
# --------------------------------------------------------------------------- #
class TestSelectiveRewrite:
    def test_second_run_writes_nothing(self, claude_dir, tmp_path, capsys):
        opencode_dir = tmp_path / ".opencode"
        main(claude_dir=claude_dir, opencode_dir=opencode_dir)
        before = _snapshot(opencode_dir)
        time.sleep(0.01)  # ensure any rewrite would move mtime_ns
        main(claude_dir=claude_dir, opencode_dir=opencode_dir)
        after = _snapshot(opencode_dir)
        assert before == after  # byte-identical AND untouched on disk
        assert "wrote 0 files" in capsys.readouterr().out.splitlines()[-1]

    def test_changed_source_rewrites_only_that_artifact(self, claude_dir, tmp_path):
        opencode_dir = tmp_path / ".opencode"
        main(claude_dir=claude_dir, opencode_dir=opencode_dir)
        before = _snapshot(opencode_dir)
        time.sleep(0.01)
        (claude_dir / "agents" / "helper.md").write_text(
            "---\ndescription: A changed helper\n---\nNew body.\n"
        )
        main(claude_dir=claude_dir, opencode_dir=opencode_dir)
        after = _snapshot(opencode_dir)
        touched = {p for p in after if after[p] != before.get(p)}
        assert touched == {
            opencode_dir / "agents" / "helper.md",
            opencode_dir / "SYNC_MANIFEST.json",
        }
        assert "A changed helper" in (opencode_dir / "agents" / "helper.md").read_text()

    def test_generated_headers_are_date_free(self, claude_dir, tmp_path):
        opencode_dir = tmp_path / ".opencode"
        main(claude_dir=claude_dir, opencode_dir=opencode_dir)
        for artifact in (
            opencode_dir / "agents" / "helper.md",
            opencode_dir / "commands" / "prime-x.md",
            opencode_dir / "opencode.json",
            opencode_dir / "plugins" / "valor-bridge.ts",
        ):
            text = artifact.read_text()
            assert "opencode-sync: generated from" in text
            assert not re.search(r"generated \d{4}-\d{2}-\d{2}", text), artifact

    def test_manifest_records_settings_hash_and_sources(self, claude_dir, tmp_path):
        opencode_dir = tmp_path / ".opencode"
        main(claude_dir=claude_dir, opencode_dir=opencode_dir)
        manifest = json.loads((opencode_dir / "SYNC_MANIFEST.json").read_text())
        assert re.fullmatch(r"[0-9a-f]{64}", manifest["settings"][".claude/settings.json"])
        assert ".claude/agents/helper.md" in manifest["agents"]
        assert ".claude/commands/roles/prime-x.md" in manifest["commands"]
        assert ".claude/hooks/check_bash.py" in manifest["hooks"]
        # exactly one generated_on key, at the top level
        assert isinstance(manifest["generated_on"], str)

    def test_older_generator_manifest_does_not_suppress_refresh(self, claude_dir, tmp_path):
        """A manifest written by an older generator version must not skip-gate
        artifacts — stale template output has to be refreshed."""
        opencode_dir = tmp_path / ".opencode"
        main(claude_dir=claude_dir, opencode_dir=opencode_dir)
        artifact = opencode_dir / "agents" / "helper.md"
        artifact.write_text("stale output from an older template\n")
        manifest_path = opencode_dir / "SYNC_MANIFEST.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["generator_version"] = 0
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        main(claude_dir=claude_dir, opencode_dir=opencode_dir)
        assert "Helper body." in artifact.read_text()

    def test_underscore_prefixed_role_files_are_not_commands(self, claude_dir, tmp_path):
        opencode_dir = tmp_path / ".opencode"
        main(claude_dir=claude_dir, opencode_dir=opencode_dir)
        assert not (opencode_dir / "commands" / "_shared.md").exists()


# --------------------------------------------------------------------------- #
# Block-decision plumbing (Blocker 1)
# --------------------------------------------------------------------------- #
class TestBlockDecisionPlumbing:
    def test_emitted_ts_parses_stdout_and_maps_casing(self, claude_dir, tmp_path):
        opencode_dir = tmp_path / ".opencode"
        main(claude_dir=claude_dir, opencode_dir=opencode_dir)
        ts = (opencode_dir / "plugins" / "valor-bridge.ts").read_text()
        # stdout JSON block-decision protocol
        assert "JSON.parse(out)" in ts
        assert 'decision?.decision === "block"' in ts
        assert "decision.reason" in ts
        # exit-code protocol retained for the PostToolUse plan validators
        assert "code !== 0" in ts
        # lowercase OpenCode tool ids mapped back to Claude casing
        assert 'bash: "Bash"' in ts
        assert 'write: "Write"' in ts
        assert 'edit: "Edit"' in ts
        assert "TOOL_NAME_MAP[tool] ?? tool" in ts

    def test_real_validator_emits_block_decision_for_cased_bash_payload(self):
        """Empirical proof: the plugin's payload shape, with correct casing, makes
        validate_commit_message.py print a {"decision": "block"} JSON to stdout."""
        payload = {"tool_name": "Bash", "tool_input": {"command": BAD_COMMIT_COMMAND}}
        proc = subprocess.run(
            [sys.executable, str(COMMIT_VALIDATOR)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0  # blocks via stdout, NOT via exit code
        decision = json.loads(proc.stdout.strip())
        assert decision["decision"] == "block"
        assert decision["reason"]

    def test_real_validator_noops_on_lowercase_tool_name(self):
        """Documents why the casing map matters: lowercase 'bash' bypasses the validator."""
        payload = {"tool_name": "bash", "tool_input": {"command": BAD_COMMIT_COMMAND}}
        proc = subprocess.run(
            [sys.executable, str(COMMIT_VALIDATOR)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0
        assert proc.stdout.strip() == ""
