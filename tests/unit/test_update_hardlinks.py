"""Tests for the hardlinks step that propagates standalone scripts to ~/.local/bin.

Specifically validates that ``scripts/sdlc-tool`` lands at ``~/.local/bin/sdlc-tool``
as a real hardlink (same inode), not a copy. Tests use ``tmp_path`` and patch
``Path.home`` so they never touch the real ``~/.local/bin/``.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.update import hardlinks

# Repo root, derived from this test file's location (tests/unit/test_update_hardlinks.py).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILL_ROOTS = (".claude/skills-global", ".claude/skills")


# Directories under a skill root that are legitimately not skills. An explicit
# named allowlist, not a ``_``-prefix rule: an allowlist of one is honest about
# how thin the evidence for a convention is, and it fails loudly when a second
# non-skill directory appears instead of silently absorbing it.
HUSK_GUARD_ALLOWLIST = frozenset({"_shared"})


def _skill_is_live(path: Path) -> bool:
    """True if ``path`` is a live skill, i.e. it holds a ``SKILL.md``.

    Skill liveness is defined by ``SKILL.md`` presence, never by directory
    existence. A directory whose ``SKILL.md`` was deleted but which still holds
    a ``__pycache__`` or a stray reference file is a *husk*: the skill is gone,
    but a bare ``Path.is_dir`` probe still reads True. That blind spot is what let the
    ``do-skills-audit`` husk survive its own rename (#2557, #2523).
    """
    return (path / "SKILL.md").is_file()


def _skill_exists_in_any_root(name: str) -> bool:
    """True if a live skill ``name`` currently exists under either skill root."""
    return any(_skill_is_live(_REPO_ROOT / root / name) for root in _SKILL_ROOTS)


@pytest.fixture
def fake_project(tmp_path):
    """Build a minimal project layout containing scripts/sdlc-tool."""
    project = tmp_path / "ai-project"
    (project / "scripts").mkdir(parents=True)
    (project / ".claude" / "skills").mkdir(parents=True)
    (project / ".claude" / "commands").mkdir(parents=True)
    (project / ".claude" / "agents").mkdir(parents=True)
    (project / ".claude" / "hooks" / "sdlc").mkdir(parents=True)
    # The alias helper only treats a resolved path as tracked source when it is
    # a checkout's .claude/hooks, so the fixture must carry a .git marker.
    (project / ".git").mkdir()

    src = project / "scripts" / "sdlc-tool"
    src.write_text("#!/usr/bin/env bash\necho hello\n")
    src.chmod(0o755)
    return project


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect Path.home() so ~/.local/bin and ~/.claude/ point at tmp_path."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    return home


_FAKE_MANIFEST_TOML = """
[[hook]]
manifest_id = "test_project_hook"
event = "PreToolUse"
matcher = ""
script = "pre_tool_use.py"
timeout = 5
scope = "project"
exit_policy = "suppress"

[[hook]]
manifest_id = "test_global_hook"
event = "PreToolUse"
matcher = "Bash"
script = "sdlc/validate_test.py"
timeout = 10
scope = "global"
exit_policy = "propagate"
"""

# Same as _FAKE_MANIFEST_TOML but with the global entry dropped — used to
# exercise the removal pass (a previously-declared manifest_id disappears).
_FAKE_MANIFEST_TOML_PROJECT_ONLY = """
[[hook]]
manifest_id = "test_project_hook"
event = "PreToolUse"
matcher = ""
script = "pre_tool_use.py"
timeout = 5
scope = "project"
exit_policy = "suppress"
"""


@pytest.fixture
def fake_project_with_hooks(fake_project):
    """Extend ``fake_project`` with a manifest.toml + real hook script files.

    Ships one project-scope entry (``pre_tool_use.py``) and one global-scope
    entry (``sdlc/validate_test.py``) so ``sync_project_hooks``/
    ``sync_user_hooks`` have real declarations + real source files to act on,
    instead of the early-return path a hook-less fake project hits.
    """
    hooks_dir = fake_project / ".claude" / "hooks"
    (hooks_dir / "manifest.toml").write_text(_FAKE_MANIFEST_TOML)
    (hooks_dir / "pre_tool_use.py").write_text("#!/usr/bin/env python3\nprint('{}')\n")
    (hooks_dir / "sdlc" / "validate_test.py").write_text("#!/usr/bin/env python3\nprint('{}')\n")
    (fake_project / ".claude" / "settings.json").write_text("{}\n")
    return fake_project


def test_sync_user_scripts_creates_hardlink(fake_project, fake_home):
    result = hardlinks.sync_user_scripts(fake_project)

    assert result.errors == 0, [a.error for a in result.actions if a.error]
    assert result.created == 1

    src = fake_project / "scripts" / "sdlc-tool"
    dst = fake_home / ".local" / "bin" / "sdlc-tool"
    assert dst.exists()
    # Same inode = real hardlink (not a copy)
    assert os.stat(src).st_ino == os.stat(dst).st_ino


def test_sync_user_scripts_idempotent(fake_project, fake_home):
    """Running twice with no change should be a no-op."""
    first = hardlinks.sync_user_scripts(fake_project)
    second = hardlinks.sync_user_scripts(fake_project)

    assert first.created == 1
    assert second.created == 0
    assert second.skipped == 1
    assert second.errors == 0


def test_sync_user_scripts_replaces_stale_copy(fake_project, fake_home):
    """A non-hardlinked file at the destination should be replaced with a hardlink."""
    dst_dir = fake_home / ".local" / "bin"
    dst_dir.mkdir(parents=True)
    stale = dst_dir / "sdlc-tool"
    stale.write_text("# old version\n")
    stale_inode = os.stat(stale).st_ino

    result = hardlinks.sync_user_scripts(fake_project)
    assert result.errors == 0

    src = fake_project / "scripts" / "sdlc-tool"
    new_inode = os.stat(stale).st_ino
    assert new_inode != stale_inode  # got replaced
    assert new_inode == os.stat(src).st_ino  # now a hardlink to the source


def test_sync_user_scripts_missing_source_records_error(fake_project, fake_home):
    """Deleting the source should surface as an error rather than crashing."""
    (fake_project / "scripts" / "sdlc-tool").unlink()
    result = hardlinks.sync_user_scripts(fake_project)
    assert result.errors == 1
    assert any("Source missing" in (a.error or "") for a in result.actions)


def test_sync_user_editor_settings_creates_defaults(fake_home):
    """Fresh ~/.claude/settings.json gets the baseline env vars and spinnerTipsEnabled."""
    import json

    result = hardlinks.sync_user_editor_settings()
    assert result.errors == 0
    assert result.created == len(hardlinks._USER_ENV_DEFAULTS) + len(
        hardlinks._USER_TOP_LEVEL_DEFAULTS
    )

    settings = json.loads((fake_home / ".claude" / "settings.json").read_text())
    for key, value in hardlinks._USER_ENV_DEFAULTS.items():
        assert settings["env"][key] == value
    for key, value in hardlinks._USER_TOP_LEVEL_DEFAULTS.items():
        assert settings[key] == value


def test_sync_user_editor_settings_preserves_custom_values(fake_home):
    """A value the user already set (env or top-level) must not be overwritten."""
    import json

    settings_path = fake_home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps({"env": {"DISABLE_TELEMETRY": "0"}, "spinnerTipsEnabled": True})
    )

    result = hardlinks.sync_user_editor_settings()
    assert result.errors == 0

    settings = json.loads(settings_path.read_text())
    assert settings["env"]["DISABLE_TELEMETRY"] == "0"
    assert settings["spinnerTipsEnabled"] is True
    # Other defaults still get filled in
    assert settings["env"]["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] == "1"
    assert settings["includeCoAuthoredBy"] is False
    assert settings["effortLevel"] == "high"


def test_sync_user_editor_settings_idempotent(fake_home):
    first = hardlinks.sync_user_editor_settings()
    second = hardlinks.sync_user_editor_settings()

    assert first.created > 0
    assert second.created == 0
    assert second.skipped == 1


def test_sync_claude_dirs_includes_user_scripts(fake_project, fake_home):
    """The top-level sync function must call sync_user_scripts."""
    # sync_claude_dirs reaches into the hook manifest, which expects real hook
    # files. We don't ship those in the plain fake_project fixture — but a
    # missing manifest.toml is tolerated as a logged error (fail-closed), and
    # the failure modes for missing skills/commands dirs are also tolerated.
    # The piece we care about here is that scripts/sdlc-tool gets hardlinked.
    hardlinks.sync_claude_dirs(fake_project)

    dst = fake_home / ".local" / "bin" / "sdlc-tool"
    assert dst.exists()
    src = fake_project / "scripts" / "sdlc-tool"
    assert os.stat(src).st_ino == os.stat(dst).st_ino


def test_sync_claude_dirs_registers_and_hardlinks_hooks(fake_project_with_hooks, fake_home):
    """With a real manifest.toml + hook scripts, hardlink + registration actually happen.

    Regression for the prior test's blind spot: a fake project shipping no
    hook files made ``sync_user_hooks`` early-return, so the hardlink +
    registration path was never exercised. This fixture ships one project-
    scope and one global-scope declaration with real backing scripts.
    """
    import json

    result = hardlinks.sync_claude_dirs(fake_project_with_hooks)
    assert result.errors == 0, [a.error for a in result.actions if a.error]

    # Global-scope script hardlinked into ~/.claude/hooks/.
    src = fake_project_with_hooks / ".claude" / "hooks" / "sdlc" / "validate_test.py"
    dst = fake_home / ".claude" / "hooks" / "sdlc" / "validate_test.py"
    assert dst.exists()
    assert os.stat(src).st_ino == os.stat(dst).st_ino

    # User-scope settings.json carries the global entry, marked by manifest_id.
    user_settings = json.loads((fake_home / ".claude" / "settings.json").read_text())
    pre_tool_use = user_settings["hooks"]["PreToolUse"]
    assert any(
        "# hook:test_global_hook" in h.get("command", "")
        for block in pre_tool_use
        for h in block.get("hooks", [])
    )

    # Project settings.json carries the project-scope entry (no marker needed
    # there — project scope is a full regeneration, not an incremental merge).
    project_settings = json.loads(
        (fake_project_with_hooks / ".claude" / "settings.json").read_text()
    )
    assert project_settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == (
        '"$CLAUDE_PROJECT_DIR"/.claude/hooks/hook_python '
        '"$CLAUDE_PROJECT_DIR"/.claude/hooks/pre_tool_use.py || true'
    )


def test_sync_user_hooks_removal_pass(fake_project_with_hooks, fake_home):
    """A manifest-marked entry no longer declared in the manifest is removed."""
    import json

    from scripts.update.hook_manifest import load_hook_manifest

    manifest_path = fake_project_with_hooks / ".claude" / "hooks" / "manifest.toml"
    manifest = load_hook_manifest(manifest_path)

    hardlinks.sync_user_hooks(fake_project_with_hooks, manifest)

    settings_path = fake_home / ".claude" / "settings.json"
    settings = json.loads(settings_path.read_text())
    assert any(
        "# hook:test_global_hook" in h.get("command", "")
        for block in settings["hooks"]["PreToolUse"]
        for h in block.get("hooks", [])
    )

    # Drop the global entry from the manifest and re-sync.
    manifest_path.write_text(_FAKE_MANIFEST_TOML_PROJECT_ONLY)
    reduced_manifest = load_hook_manifest(manifest_path)
    result = hardlinks.sync_user_hooks(fake_project_with_hooks, reduced_manifest)

    settings = json.loads(settings_path.read_text())
    assert "PreToolUse" not in settings["hooks"], (
        "removed entry's now-empty PreToolUse block/event was not pruned"
    )
    assert result.removed == 1


def test_sync_user_hooks_matcher_update(fake_project_with_hooks, fake_home):
    """An existing manifest-marked entry whose declared matcher changed is updated in place."""
    import json

    from scripts.update.hook_manifest import load_hook_manifest

    manifest_path = fake_project_with_hooks / ".claude" / "hooks" / "manifest.toml"
    manifest = load_hook_manifest(manifest_path)
    hardlinks.sync_user_hooks(fake_project_with_hooks, manifest)

    settings_path = fake_home / ".claude" / "settings.json"
    before = json.loads(settings_path.read_text())
    assert before["hooks"]["PreToolUse"][0]["matcher"] == "Bash"

    # Change the declared matcher and re-run.
    manifest_path.write_text(_FAKE_MANIFEST_TOML.replace('matcher = "Bash"', 'matcher = ""'))
    updated_manifest = load_hook_manifest(manifest_path)
    result = hardlinks.sync_user_hooks(fake_project_with_hooks, updated_manifest)

    after = json.loads(settings_path.read_text())
    assert after["hooks"]["PreToolUse"][0]["matcher"] == ""
    assert result.created == 0  # no new entries, only an in-place update


def test_user_bin_scripts_table_contains_sdlc_tool():
    """Regression guard: ensure the registry isn't empty."""
    paths = [src for src, _ in hardlinks.USER_BIN_SCRIPTS]
    assert "scripts/sdlc-tool" in paths


def test_sync_skills_prunes_intra_dir_orphan(fake_project, fake_home):
    """A file deleted from a surviving source skill dir must be pruned from ~/.claude.

    Regression for the skills-renovation rollout: pass 1 deleted
    do-pr-review/sub-skills/README.md (content folded into SKILL.md), but the
    dir-level stale cleanup only removes whole skill dirs whose source is gone.
    The stale hardlink lingered on fleet machines and could be loaded alongside
    the renovated SKILL.md, contradicting current instructions.
    """
    src_skill = fake_project / ".claude" / "skills-global" / "do-review"
    (src_skill / "sub-skills").mkdir(parents=True)
    (src_skill / "SKILL.md").write_text("# review skill\n")
    (src_skill / "sub-skills" / "keep.md").write_text("keep\n")
    old = src_skill / "sub-skills" / "old.md"
    old.write_text("old guidance\n")

    hardlinks.sync_claude_dirs(fake_project)
    dst_skill = fake_home / ".claude" / "skills" / "do-review"
    assert (dst_skill / "sub-skills" / "old.md").exists()

    # Source file deleted (dir survives) — next sync must prune the dst copy.
    old.unlink()
    result = hardlinks.sync_claude_dirs(fake_project)

    assert not (dst_skill / "sub-skills" / "old.md").exists(), (
        "orphan file lingered after source deletion"
    )
    assert (dst_skill / "sub-skills" / "keep.md").exists()
    assert (dst_skill / "SKILL.md").exists()
    assert result.removed >= 1


def test_sync_skills_prune_removes_emptied_subdir(fake_project, fake_home):
    """When every file in a subdir is deleted at source, the empty dst subdir goes too."""
    src_skill = fake_project / ".claude" / "skills-global" / "do-review"
    (src_skill / "refs").mkdir(parents=True)
    (src_skill / "SKILL.md").write_text("# review skill\n")
    gone = src_skill / "refs" / "only.md"
    gone.write_text("only\n")

    hardlinks.sync_claude_dirs(fake_project)
    gone.unlink()
    (src_skill / "refs").rmdir()
    hardlinks.sync_claude_dirs(fake_project)

    dst_refs = fake_home / ".claude" / "skills" / "do-review" / "refs"
    assert not dst_refs.exists(), "emptied subdir lingered in destination"
    assert (fake_home / ".claude" / "skills" / "do-review" / "SKILL.md").exists()


def test_sync_skills_prune_leaves_foreign_skill_dirs_alone(fake_project, fake_home):
    """A user-level skill dir not backed by this project must never be touched."""
    foreign = fake_home / ".claude" / "skills" / "foreign-skill"
    foreign.mkdir(parents=True)
    (foreign / "SKILL.md").write_text("foreign\n")
    (foreign / "notes.md").write_text("private notes\n")

    hardlinks.sync_claude_dirs(fake_project)

    assert (foreign / "SKILL.md").exists()
    assert (foreign / "notes.md").exists()


_ISSUE_2065_ORPHANS = [
    ("skills", "audit-next-tool"),
    ("skills", "do-design-review"),
    ("skills", "get-telegram-messages"),
    ("skills", "searching-message-history"),
]


def test_renamed_removals_contains_issue_2065_orphans():
    """The four issue-#2065 orphan skill hardlinks must be registered for removal."""
    for pair in _ISSUE_2065_ORPHANS:
        assert pair in hardlinks.RENAMED_REMOVALS, f"{pair} missing from RENAMED_REMOVALS"


# ---------------------------------------------------------------------------
# RENAMED_REMOVALS completeness (issue #2079, Gap 4)
# ---------------------------------------------------------------------------


def test_renamed_removals_entries_are_not_stale():
    """No ``("skills", name)`` removal entry names a skill live in BOTH roots at once.

    Always-on, pure-filesystem invariant with no git dependency — so it provides
    real coverage even under a shallow CI clone where the git-history completeness
    test below skips. A skill present under *both* ``.claude/skills/`` and
    ``.claude/skills-global/`` simultaneously while also being listed for removal
    is a contradiction: the removal sweep would delete a hardlink backed by a live
    source. (A skill live in exactly one root, or absent from both, is fine — that
    is the normal post-move state a removal entry exists to clean up.)
    """
    for kind, name in hardlinks.RENAMED_REMOVALS:
        if kind != "skills":
            continue
        in_global = _skill_is_live(_REPO_ROOT / ".claude" / "skills-global" / name)
        in_project = _skill_is_live(_REPO_ROOT / ".claude" / "skills" / name)
        assert not (in_global and in_project), (
            f"RENAMED_REMOVALS entry ('skills', {name!r}) is stale: the skill is "
            f"live in both .claude/skills-global/ and .claude/skills/ at once"
        )


def _find_husk_dirs(root: Path) -> list[Path]:
    """Directories directly under ``root`` that are husks: not live skills, not allowlisted.

    A missing root yields no husks. The ``is_dir`` calls here filter directory
    entries; skill *liveness* is decided solely by ``_skill_is_live`` (#2557).
    """
    if not root.is_dir():
        return []
    return sorted(
        entry
        for entry in root.iterdir()
        if entry.is_dir() and entry.name not in HUSK_GUARD_ALLOWLIST and not _skill_is_live(entry)
    )


def test_no_husk_directories_in_skill_roots():
    """No skill root holds a husk — a directory with no ``SKILL.md`` (#2557, #2523).

    A husk is what a half-completed skill rename leaves behind: the ``SKILL.md``
    is deleted but ``__pycache__`` or a stray reference file keeps the directory
    alive on disk. Nothing observed that class before, because the only probe
    that could have caught it was itself a directory-existence check.
    """
    husks = [h for root in _SKILL_ROOTS for h in _find_husk_dirs(_REPO_ROOT / root)]
    assert not husks, (
        "husk directories found in the skill roots (a directory with no SKILL.md "
        "is a leftover from an incomplete skill rename/deletion — delete it, or "
        f"add it to HUSK_GUARD_ALLOWLIST if it is an intentional shared resource): "
        f"{[str(h.relative_to(_REPO_ROOT)) for h in husks]}"
    )


def test_find_husk_dirs_edge_cases(tmp_path):
    """Empty root, empty ``SKILL.md``, allowlisted dir, and a real husk."""
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    assert _find_husk_dirs(empty_root) == []
    assert _find_husk_dirs(tmp_path / "does-not-exist") == []

    root = tmp_path / "skills"
    # An EMPTY SKILL.md counts as PRESENT: file existence is the contract here,
    # content validation belongs to the skills audit, not to this guard.
    (root / "empty-skill-md").mkdir(parents=True)
    (root / "empty-skill-md" / "SKILL.md").write_text("")
    (root / "live").mkdir()
    (root / "live" / "SKILL.md").write_text("# live\n")
    (root / "_shared").mkdir()  # allowlisted, no SKILL.md
    (root / "_shared" / "test-quality.md").write_text("x")
    (root / "husk").mkdir()
    (root / "husk" / "leftover.txt").write_text("x")
    (root / "loose-file.md").write_text("not a directory")

    assert [d.name for d in _find_husk_dirs(root)] == ["husk"]


def test_renamed_removals_covers_deleted_skills():
    """Every skill dir ever deleted from a skill root is covered by RENAMED_REMOVALS.

    Git-history completeness check. For each skill root, walk the history of
    deleted ``SKILL.md`` files; each vanished skill name must either appear in
    ``RENAMED_REMOVALS`` as ``("skills", name)`` OR currently exist on disk in
    *any* skill root (a delete-and-re-add within the same root needs no removal
    entry because nothing stale is left behind).

    Skips cleanly — never a silent pass, never a false failure — when git is
    unavailable or the clone is shallow (``git rev-parse --is-shallow-repository``),
    since a truncated history would report spurious or missing deletions. The
    always-on ``test_renamed_removals_entries_are_not_stale`` retains coverage in
    that case. Assertions anchor only on deletions actually present in history;
    the test never asserts a specific deletion count.
    """
    try:
        shallow = subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
        )
    except (OSError, FileNotFoundError):
        pytest.skip("git unavailable — cannot verify RENAMED_REMOVALS completeness")

    if shallow.returncode != 0:
        pytest.skip("git rev-parse failed — cannot verify RENAMED_REMOVALS completeness")
    if shallow.stdout.strip() == "true":
        pytest.skip("shallow clone — deletion history is truncated, skipping completeness check")

    removal_names = {name for kind, name in hardlinks.RENAMED_REMOVALS if kind == "skills"}

    for root in _SKILL_ROOTS:
        proc = subprocess.run(
            [
                "git",
                "log",
                "--diff-filter=D",
                "--name-only",
                "--pretty=format:",
                "--",
                f"{root}/*/SKILL.md",
            ],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            pytest.skip(f"git log failed for {root} — cannot verify completeness")

        prefix = f"{root}/"
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line.startswith(prefix) or not line.endswith("/SKILL.md"):
                continue
            # Extract the skill dir name: <root>/<name>/SKILL.md
            name = line[len(prefix) : -len("/SKILL.md")]
            if "/" in name:
                continue  # nested path, not a top-level skill dir
            assert name in removal_names or _skill_exists_in_any_root(name), (
                f"skill {name!r} was deleted from {root} in git history but is neither "
                f"listed in RENAMED_REMOVALS nor present in any skill root — add "
                f'("skills", "{name}") to RENAMED_REMOVALS'
            )


def test_gap4_in_tree_fixtures_are_covered():
    """The #2096 (do-xref-audit) and #2065 sweep entries pass the Gap-4 invariants.

    Guards the concrete in-tree fixtures the plan calls out: each must be a
    registered ``("skills", name)`` removal (they have no live source) so the
    completeness check accepts them.
    """
    fixtures = [
        "do-xref-audit",
        "do-xref",
        "audit-next-tool",
        "do-design-review",
        "get-telegram-messages",
        "searching-message-history",
    ]
    removal_names = {name for kind, name in hardlinks.RENAMED_REMOVALS if kind == "skills"}
    for name in fixtures:
        assert name in removal_names or _skill_exists_in_any_root(name), (
            f"expected Gap-4 fixture {name!r} to be in RENAMED_REMOVALS or live on disk"
        )


def test_cleanup_renamed_removes_orphaned_skill_hardlinks(fake_project, fake_home):
    """Each issue-#2065 orphan (no live skills-global source) is removed by _cleanup_renamed."""
    # skills-global exists but contains NONE of the orphaned names — they are
    # genuine orphans with no live source backing them.
    (fake_project / ".claude" / "skills-global").mkdir(parents=True)

    user_claude = fake_home / ".claude"
    for _kind, name in _ISSUE_2065_ORPHANS:
        orphan_dir = user_claude / "skills" / name
        orphan_dir.mkdir(parents=True)
        (orphan_dir / "SKILL.md").write_text(f"# {name}\nstale orphan\n")

    result = hardlinks.HardlinkSyncResult()
    hardlinks._cleanup_renamed(user_claude, fake_project, result)

    for _kind, name in _ISSUE_2065_ORPHANS:
        assert not (user_claude / "skills" / name).exists(), (
            f"orphaned skill hardlink {name} was not removed"
        )
    assert result.removed >= len(_ISSUE_2065_ORPHANS)


def test_cleanup_renamed_preserves_live_backed_skill(fake_project, fake_home):
    """Inode guard: a target still hardlinked to a live skills-global source is preserved."""
    name = "audit-next-tool"  # a registered RENAMED_REMOVALS name
    src_skill = fake_project / ".claude" / "skills-global" / name
    src_skill.mkdir(parents=True)
    src_file = src_skill / "SKILL.md"
    src_file.write_text(f"# {name}\nlive source\n")

    user_claude = fake_home / ".claude"
    dst_skill = user_claude / "skills" / name
    dst_skill.mkdir(parents=True)
    # Real hardlink (shared inode) to the live source — proves project-backed.
    os.link(src_file, dst_skill / "SKILL.md")

    result = hardlinks.HardlinkSyncResult()
    hardlinks._cleanup_renamed(user_claude, fake_project, result)

    assert dst_skill.exists(), "live-backed skill was wrongly removed by the sweep"
    assert (dst_skill / "SKILL.md").exists()


# ---------------------------------------------------------------------------
# RENAMED_REMOVALS "hooks" kind (Pre-requisite Bug 3) — removal propagation
# ---------------------------------------------------------------------------


def test_renamed_removals_has_hooks_kind():
    """RENAMED_REMOVALS must carry at least one ('hooks', ...) entry (Verification
    table row: ``grep -c '"hooks"' scripts/update/hardlinks.py`` > 0)."""
    hooks_entries = [pair for pair in hardlinks.RENAMED_REMOVALS if pair[0] == "hooks"]
    assert hooks_entries, "RENAMED_REMOVALS has no 'hooks' kind entry"


def test_cleanup_renamed_sweeps_orphaned_hook_hardlink(fake_project, fake_home):
    """A renamed/removed hook script's stale user-level hardlink (the 'hooks'
    kind of RENAMED_REMOVALS, Pre-requisite Bug 3) is actually swept when it
    has no live project source backing it -- proving removal propagation
    for the hooks kind specifically, not just the pre-existing skills kind.
    """
    hooks_removals = [pair for pair in hardlinks.RENAMED_REMOVALS if pair[0] == "hooks"]
    assert hooks_removals, "no ('hooks', ...) entry registered in RENAMED_REMOVALS"
    _kind, old_name = hooks_removals[0]

    # fake_project's .claude/hooks/sdlc/ dir exists but does NOT contain
    # old_name's basename — a genuine orphan with no live source backing it.
    user_claude = fake_home / ".claude"
    orphan = user_claude / "hooks" / old_name
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text("#!/usr/bin/env python3\nprint('stale orphan')\n")

    result = hardlinks.HardlinkSyncResult()
    hardlinks._cleanup_renamed(user_claude, fake_project, result)

    assert not orphan.exists(), f"orphaned hook hardlink {old_name} was not removed"
    assert result.removed >= 1


def test_cleanup_renamed_preserves_live_backed_hook(fake_project, fake_home):
    """Inode guard: a hook hardlink still backed by a live project source
    (same relative path under .claude/hooks/) is preserved, not swept."""
    hooks_removals = [pair for pair in hardlinks.RENAMED_REMOVALS if pair[0] == "hooks"]
    assert hooks_removals, "no ('hooks', ...) entry registered in RENAMED_REMOVALS"
    _kind, old_name = hooks_removals[0]

    src_file = fake_project / ".claude" / "hooks" / old_name
    src_file.parent.mkdir(parents=True, exist_ok=True)
    src_file.write_text("#!/usr/bin/env python3\nprint('live source')\n")

    user_claude = fake_home / ".claude"
    dst_file = user_claude / "hooks" / old_name
    dst_file.parent.mkdir(parents=True, exist_ok=True)
    os.link(src_file, dst_file)  # real hardlink (shared inode) -> project-backed

    result = hardlinks.HardlinkSyncResult()
    hardlinks._cleanup_renamed(user_claude, fake_project, result)

    assert dst_file.exists(), "live-backed hook hardlink was wrongly removed by the sweep"


def test_sync_commands_recurses_into_namespace_subdirs(fake_project, fake_home):
    """Namespaced commands (e.g. roles/prime-pm-role.md) must hardlink globally.

    Regression origin: PR #1694 moved persona delivery to namespaced slash
    commands living under a .claude/commands/ subdirectory, and a
    top-level-only glob left them unsynced — every session primed in another
    repo's worktree hung on "Unknown command". The runner still resolves
    /roles:prime-*-role commands in OTHER repos' worktrees, so namespace
    recursion stays load-bearing post-#1924.
    """
    src_ns = fake_project / ".claude" / "commands" / "roles"
    src_ns.mkdir(parents=True)
    src_cmd = src_ns / "prime-pm-role.md"
    src_cmd.write_text("---\nname: prime-pm-role\n---\nPrime the PM persona.\n")

    hardlinks._sync_commands(
        fake_project / ".claude" / "commands",
        fake_home / ".claude" / "commands",
        hardlinks.HardlinkSyncResult(),
    )

    dst_cmd = fake_home / ".claude" / "commands" / "roles" / "prime-pm-role.md"
    assert dst_cmd.exists(), "namespaced command was not synced into ~/.claude/commands/roles/"
    assert os.stat(src_cmd).st_ino == os.stat(dst_cmd).st_ino, "synced as copy, not hardlink"


# ---------------------------------------------------------------------------
# generate_project_hooks / sync_project_hooks (Risk 4: empty-diff-on-regen)
# ---------------------------------------------------------------------------


def test_generate_project_hooks_regen_is_empty_diff(tmp_path):
    """Regenerating the real repo's .claude/settings.json hooks block from the
    real manifest twice in a row (on a tmp copy) must be idempotent.

    This is the Risk 4 guardrail: manifest declaration order is load-bearing,
    and this test proves a second regeneration against an unchanged manifest
    produces byte-for-byte the same hooks block. Operates on a tmp copy of
    ``.claude/`` rather than the live repo file, per Test Impact.
    """
    import filecmp
    import shutil

    from scripts.update.hook_manifest import load_hook_manifest

    tmp_claude = tmp_path / ".claude"
    shutil.copytree(_REPO_ROOT / ".claude" / "hooks", tmp_claude / "hooks")
    shutil.copy(_REPO_ROOT / ".claude" / "settings.json", tmp_claude / "settings.json")

    manifest = load_hook_manifest(tmp_claude / "hooks" / "manifest.toml")

    first = hardlinks.sync_project_hooks(tmp_path, manifest)
    assert first.errors == 0

    snapshot = (tmp_claude / "settings.json").read_bytes()

    second = hardlinks.sync_project_hooks(tmp_path, manifest)
    assert second.errors == 0
    assert second.created == 0
    assert second.skipped == 1

    assert (tmp_claude / "settings.json").read_bytes() == snapshot
    assert filecmp.cmp(tmp_claude / "settings.json", tmp_claude / "settings.json")


def test_generate_project_hooks_regen_matches_currently_committed_file(tmp_path):
    """Risk 4's core guardrail: regenerating from the REAL manifest against a
    COPY of the real, currently-committed ``.claude/settings.json`` must
    produce a byte-identical file -- i.e. ``git diff`` on the tracked file
    after a real regeneration is empty.

    Unlike ``test_generate_project_hooks_regen_is_empty_diff`` above (which
    only proves a *second* regeneration is idempotent relative to the
    *first*), this test compares the very first regeneration's output
    against the original bytes copied from the live worktree -- catching
    manifest/generator drift from what's actually committed, not just
    generator self-consistency. Read-only: operates on a tmp copy, never
    writes to the tracked file.
    """
    import shutil

    from scripts.update.hook_manifest import load_hook_manifest

    tmp_claude = tmp_path / ".claude"
    shutil.copytree(_REPO_ROOT / ".claude" / "hooks", tmp_claude / "hooks")
    shutil.copy(_REPO_ROOT / ".claude" / "settings.json", tmp_claude / "settings.json")

    original_bytes = (_REPO_ROOT / ".claude" / "settings.json").read_bytes()

    manifest = load_hook_manifest(tmp_claude / "hooks" / "manifest.toml")
    result = hardlinks.sync_project_hooks(tmp_path, manifest)

    assert result.errors == 0
    assert result.created == 0, (
        "Regenerating from the real manifest changed the committed "
        ".claude/settings.json -- manifest and tracked file have drifted."
    )
    assert result.skipped == 1

    regenerated_bytes = (tmp_claude / "settings.json").read_bytes()
    assert regenerated_bytes == original_bytes


def test_generate_project_hooks_preserves_declaration_order():
    """Entries sharing an (event, matcher) key must stay in manifest order."""
    manifest = [
        hardlinks.HookDeclaration(
            manifest_id="a",
            event="PostToolUse",
            matcher="Write",
            script="a.py",
            timeout=5,
            scope="project",
            exit_policy="propagate",
        ),
        hardlinks.HookDeclaration(
            manifest_id="b",
            event="PostToolUse",
            matcher="Write",
            script="b.py",
            timeout=5,
            scope="project",
            exit_policy="propagate",
        ),
    ]
    hooks = hardlinks.generate_project_hooks(manifest)
    commands = [h["command"] for h in hooks["PostToolUse"][0]["hooks"]]
    assert commands == [
        '"$CLAUDE_PROJECT_DIR"/.claude/hooks/hook_python "$CLAUDE_PROJECT_DIR"/.claude/hooks/a.py',
        '"$CLAUDE_PROJECT_DIR"/.claude/hooks/hook_python "$CLAUDE_PROJECT_DIR"/.claude/hooks/b.py',
    ]


# ---------------------------------------------------------------------------
# Directory-granular deployment of global-scope hooks (issue #2561).
#
# Registration is declaration-granular (the manifest names files that register
# hooks); DEPLOYMENT is directory-granular, because a global script may import
# a shared sibling module that registers no hook and therefore can never be
# declared. PR #2453 narrowed deployment to the declared files and killed every
# global hook with ModuleNotFoundError in every foreign repo.
# ---------------------------------------------------------------------------


def _global_decl(manifest_id: str, script: str):
    return hardlinks.HookDeclaration(
        manifest_id=manifest_id,
        event="PreToolUse",
        matcher="Bash",
        script=script,
        timeout=10,
        scope="global",
        exit_policy="propagate",
    )


def _require_global_interpreter() -> str:
    interpreter = hardlinks.resolve_global_interpreter()
    if interpreter is None:
        pytest.skip("no system python3 resolved on this machine")
    return interpreter


def _declared_global_scripts() -> list[str]:
    from scripts.update.hook_manifest import load_hook_manifest

    manifest = load_hook_manifest(_REPO_ROOT / ".claude" / "hooks" / "manifest.toml")
    return sorted({d.script for d in manifest if d.scope == "global"})


def test_sync_user_hooks_deploys_sdlc_context_helper(fake_home):
    """The undeclared shared helper lands in an empty temp HOME alongside the
    declared global scripts. It has no ``[[hook]]`` entry, so per-declaration
    deployment cannot reach it -- only the directory can."""
    _require_global_interpreter()

    result = hardlinks.sync_user_hooks(_REPO_ROOT)
    assert result.errors == 0, [a.error for a in result.actions if a.error]

    helper = fake_home / ".claude" / "hooks" / "sdlc" / "sdlc_context.py"
    assert helper.exists(), "sdlc_context.py was not deployed to the user hooks tree"
    src = _REPO_ROOT / ".claude" / "hooks" / "sdlc" / "sdlc_context.py"
    assert os.stat(src).st_ino == os.stat(helper).st_ino

    # Declared scripts are a SUBSET of what was deployed -- never an equality on
    # a magic count, which would break the moment a helper is added.
    deployed = {
        str(p.relative_to(fake_home / ".claude" / "hooks"))
        for p in (fake_home / ".claude" / "hooks").rglob("*.py")
    }
    assert set(_declared_global_scripts()) <= deployed


def test_global_hooks_import_smoke(fake_home):
    """Every declared global script must actually IMPORT under the interpreter
    it is registered with.

    Asserts on stderr CONTENT, not exit status: a hook that exits 0 while
    printing ``ModuleNotFoundError`` is exactly the silent failure this guards.

    The child env is built explicitly with all ``CLAUDE_*`` keys stripped.
    ``is_sdlc_context()`` reads ``CLAUDE_SESSION_ID`` and, when set, imports the
    real ``AgentSession`` and issues a live Redis query -- this test runs inside
    a live agent session, so default env inheritance would have a unit test read
    production Redis.
    """
    interpreter = _require_global_interpreter()

    result = hardlinks.sync_user_hooks(_REPO_ROOT)
    assert result.errors == 0, [a.error for a in result.actions if a.error]

    scripts = _declared_global_scripts()
    assert scripts, "expected at least one declared global-scope hook script"

    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}

    for script in scripts:
        dst_file = fake_home / ".claude" / "hooks" / script
        assert dst_file.exists(), f"declared global script not deployed: {script}"
        proc = subprocess.run(
            [interpreter, str(dst_file)],
            input=b"",
            env=env,
            capture_output=True,
            timeout=10,
        )
        stderr = proc.stderr.decode(errors="replace")
        assert "ModuleNotFoundError" not in stderr, f"{script} failed to import:\n{stderr}"
        assert "ImportError" not in stderr, f"{script} failed to import:\n{stderr}"


def test_sync_user_hooks_deployment_dir_is_derived_not_hardcoded(fake_project, fake_home):
    """A global declaration in a directory other than the one shipping today
    deploys that directory's helpers -- proving the set is derived."""
    _require_global_interpreter()

    hooks_dir = fake_project / ".claude" / "hooks"
    fork_dir = hooks_dir / "forkscope"
    fork_dir.mkdir(parents=True)
    (fork_dir / "guard.py").write_text("#!/usr/bin/env python3\nprint('{}')\n")
    (fork_dir / "shared_helper.py").write_text("VALUE = 1\n")
    (fork_dir / "notes.txt").write_text("not python\n")
    (fork_dir / "__pycache__").mkdir()
    (fork_dir / "__pycache__" / "guard.cpython-39.pyc").write_bytes(b"\x00")

    result = hardlinks.sync_user_hooks(
        fake_project, [_global_decl("fork_guard", "forkscope/guard.py")]
    )
    assert result.errors == 0, [a.error for a in result.actions if a.error]

    dst_dir = fake_home / ".claude" / "hooks" / "forkscope"
    assert (dst_dir / "guard.py").exists()
    assert (dst_dir / "shared_helper.py").exists(), "undeclared sibling helper was not deployed"
    assert not (dst_dir / "notes.txt").exists(), "non-Python file leaked into the user tree"
    assert not (dst_dir / "__pycache__").exists(), "bytecode dir leaked into the user tree"


def test_sync_user_hooks_hooks_root_declaration_deploys_only_that_file(fake_project, fake_home):
    """A declaration at the hooks ROOT must not glob the root -- that would
    sweep every project-scope script into the user tree."""
    _require_global_interpreter()

    hooks_dir = fake_project / ".claude" / "hooks"
    (hooks_dir / "root_global.py").write_text("#!/usr/bin/env python3\nprint('{}')\n")
    (hooks_dir / "project_only.py").write_text("#!/usr/bin/env python3\nprint('{}')\n")

    result = hardlinks.sync_user_hooks(fake_project, [_global_decl("root_hook", "root_global.py")])
    assert result.errors == 0, [a.error for a in result.actions if a.error]

    dst_root = fake_home / ".claude" / "hooks"
    assert (dst_root / "root_global.py").exists()
    assert not (dst_root / "project_only.py").exists(), "hooks root was globbed"


def test_sync_user_hooks_sdlc_context_is_deployed_but_unregistered(fake_home):
    """The helper registers no event, so it must never reach settings.json."""
    import json

    _require_global_interpreter()

    hardlinks.sync_user_hooks(_REPO_ROOT)

    assert (fake_home / ".claude" / "hooks" / "sdlc" / "sdlc_context.py").exists()
    settings_text = (fake_home / ".claude" / "settings.json").read_text()
    assert "sdlc_context.py" not in settings_text, "helper was registered as a hook"
    json.loads(settings_text)  # still valid JSON


def test_sync_user_hooks_deletes_nothing_under_user_hooks(fake_project, fake_home):
    """Deployment is additive: a foreign machine's own file under
    ~/.claude/hooks/ survives a sync. Deletion under that root belongs to
    ``_cleanup_renamed``, behind the alias guard (#2567)."""
    _require_global_interpreter()

    hooks_dir = fake_project / ".claude" / "hooks"
    (hooks_dir / "forkscope").mkdir(parents=True)
    (hooks_dir / "forkscope" / "guard.py").write_text("#!/usr/bin/env python3\nprint('{}')\n")

    stranger_dir = fake_home / ".claude" / "hooks" / "forkscope"
    stranger_dir.mkdir(parents=True)
    stranger = stranger_dir / "operator_own_hook.py"
    stranger.write_text("# hand-added, no source in the repo\n")

    hardlinks.sync_user_hooks(fake_project, [_global_decl("fork_guard", "forkscope/guard.py")])

    assert stranger.exists(), "an unrelated user-tree file was deleted"
    assert stranger.read_text() == "# hand-added, no source in the repo\n"


def test_sync_user_hooks_rerun_is_a_noop(fake_project, fake_home):
    _require_global_interpreter()

    hooks_dir = fake_project / ".claude" / "hooks"
    (hooks_dir / "forkscope").mkdir(parents=True)
    (hooks_dir / "forkscope" / "guard.py").write_text("#!/usr/bin/env python3\nprint('{}')\n")
    (hooks_dir / "forkscope" / "shared_helper.py").write_text("VALUE = 1\n")

    decls = [_global_decl("fork_guard", "forkscope/guard.py")]
    first = hardlinks.sync_user_hooks(fake_project, decls)
    second = hardlinks.sync_user_hooks(fake_project, decls)

    assert first.created > 0
    assert second.created == 0, "re-running the sync created files again"
    assert second.errors == 0


def test_sync_user_hooks_missing_declared_source_does_not_abort_siblings(fake_project, fake_home):
    """A declared script missing from the repo records an error and continues."""
    _require_global_interpreter()

    hooks_dir = fake_project / ".claude" / "hooks"
    (hooks_dir / "forkscope").mkdir(parents=True)
    (hooks_dir / "forkscope" / "present.py").write_text("#!/usr/bin/env python3\nprint('{}')\n")
    (hooks_dir / "forkscope" / "shared_helper.py").write_text("VALUE = 1\n")

    result = hardlinks.sync_user_hooks(
        fake_project,
        [
            _global_decl("gone", "forkscope/vanished.py"),
            _global_decl("present", "forkscope/present.py"),
        ],
    )

    # Two errors, one per consequence: the source is missing, and because of
    # that the declaration is withheld from registration.
    assert result.errors == 2
    assert any("Source missing" in (a.error or "") for a in result.actions)
    assert any(
        "Not registered" in (a.error or "") and "gone" in (a.error or "") for a in result.actions
    )

    dst_dir = fake_home / ".claude" / "hooks" / "forkscope"
    assert (dst_dir / "present.py").exists(), "sibling sync was aborted by the missing source"
    assert (dst_dir / "shared_helper.py").exists()
    assert not (dst_dir / "vanished.py").exists()

    settings_text = (fake_home / ".claude" / "settings.json").read_text()
    assert "vanished.py" not in settings_text, "registered a script that is not on disk"
    assert "present.py" in settings_text, "the sibling's registration was collateral damage"


def test_sync_user_hooks_empty_global_scope_deploys_nothing_and_still_removes(
    fake_project, fake_home
):
    """Zero global declarations: nothing is deployed, and the registration
    removal pass still sweeps now-undeclared marked entries."""
    import json

    settings_path = fake_home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
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
                                    "command": "/usr/bin/python3 /x/y.py  "
                                    "# hook:no-longer-declared",
                                    "timeout": 5,
                                }
                            ],
                        }
                    ]
                }
            }
        )
    )

    result = hardlinks.sync_user_hooks(fake_project, [])

    assert result.removed == 1
    assert not (fake_home / ".claude" / "hooks").exists(), "nothing should have been deployed"
    assert "PreToolUse" not in json.loads(settings_path.read_text())["hooks"]


# ---------------------------------------------------------------------------
# Legacy ~/.claude/hooks directory-symlink layout (issue #2567)
#
# Under that layout the user hooks root resolves inside a git checkout, so
# every path beneath it is tracked source. A prune pass reading it as a user
# cache deleted 36 tracked files during the #2521 build. These tests pin both
# halves of the cure: detect the alias and refuse to write or delete through
# it, and migrate it away so the next run has a real user tree.
# ---------------------------------------------------------------------------


def _make_checkout(root: Path, worktree: bool = False) -> Path:
    """A directory that reads as a git checkout with a .claude/hooks tree.

    ``worktree=True`` writes ``.git`` as the gitdir-pointer FILE a linked
    worktree carries, which is the form the alias helper's ``exists()`` probe
    exists for and the layout an agent actually runs in.
    """
    (root / ".claude" / "hooks" / "sdlc").mkdir(parents=True, exist_ok=True)
    if worktree:
        (root / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n")
    else:
        (root / ".git").mkdir(exist_ok=True)
    return root


def _hooks_symlink(fake_home: Path, target: Path) -> Path:
    """Point ~/.claude/hooks at ``target`` the way the legacy layout does."""
    user_claude = fake_home / ".claude"
    user_claude.mkdir(parents=True, exist_ok=True)
    (user_claude / "hooks").symlink_to(target, target_is_directory=True)
    return user_claude


def test_repo_aliased_helper_flags_directory_symlink(fake_project, fake_home):
    _hooks_symlink(fake_home, fake_project / ".claude" / "hooks")

    aliased = hardlinks.user_hooks_root_is_repo_aliased(fake_project, fake_home / ".claude")

    assert aliased == (fake_project / ".claude" / "hooks").resolve()


def test_repo_aliased_helper_flags_alias_to_a_foreign_checkout(fake_project, tmp_path, fake_home):
    """The alias need not point at ``project_dir``. An agent running in a
    worktree while ~/.claude/hooks points at the main checkout is the same
    hazard, and the resolved target is what the caller must be told about."""
    other = _make_checkout(tmp_path / "other-checkout")
    _hooks_symlink(fake_home, other / ".claude" / "hooks")

    aliased = hardlinks.user_hooks_root_is_repo_aliased(fake_project, fake_home / ".claude")

    assert aliased == (other / ".claude" / "hooks").resolve()


def test_repo_aliased_helper_clears_a_real_user_directory(fake_project, fake_home):
    real = fake_home / ".claude" / "hooks" / "sdlc"
    real.mkdir(parents=True)
    (real / "sdlc_context.py").write_text("# real user tree\n")

    assert hardlinks.user_hooks_root_is_repo_aliased(fake_project, fake_home / ".claude") is None


def test_repo_aliased_helper_clears_an_absent_directory(fake_project, fake_home):
    assert hardlinks.user_hooks_root_is_repo_aliased(fake_project, fake_home / ".claude") is None


def test_cleanup_renamed_never_deletes_through_a_hooks_symlink(fake_project, tmp_path, fake_home):
    """The #2521 deletion, reproduced in miniature and then blocked.

    ~/.claude/hooks points at a checkout that is NOT ``project_dir``, so the
    inode guard finds no live source for the renamed name and clears it for
    removal. The file it would unlink is tracked source in the other checkout.
    """
    hooks_removals = [pair for pair in hardlinks.RENAMED_REMOVALS if pair[0] == "hooks"]
    assert hooks_removals, "no ('hooks', ...) entry registered in RENAMED_REMOVALS"
    _kind, old_name = hooks_removals[0]

    other = _make_checkout(tmp_path / "other-checkout")
    tracked = other / ".claude" / "hooks" / old_name
    tracked.parent.mkdir(parents=True, exist_ok=True)
    tracked.write_text("#!/usr/bin/env python3\nprint('tracked source')\n")
    user_claude = _hooks_symlink(fake_home, other / ".claude" / "hooks")

    result = hardlinks.HardlinkSyncResult()
    hardlinks._cleanup_renamed(user_claude, fake_project, result)

    assert tracked.is_file(), "the sweep deleted tracked source through the hooks symlink"
    assert tracked.read_text() == "#!/usr/bin/env python3\nprint('tracked source')\n"
    assert any(a.action == "skipped" and "aliased" in (a.error or "") for a in result.actions)


def test_sync_user_hooks_never_writes_into_an_aliased_foreign_checkout(
    fake_project_with_hooks, tmp_path, fake_home
):
    """An alias to a checkout that is NOT ``project_dir`` is the sharp case:
    the inodes differ, so writing through the link would unlink that checkout's
    tracked file and relink it to this project's copy. The link is removed
    first, so every write lands in the new real directory and nothing under the
    foreign checkout changes."""
    _require_global_interpreter()

    other = _make_checkout(tmp_path / "other-checkout")
    tracked = other / ".claude" / "hooks" / "sdlc" / "validate_test.py"
    tracked.parent.mkdir(parents=True, exist_ok=True)
    tracked.write_text("#!/usr/bin/env python3\nprint('foreign copy')\n")
    foreign_inode = os.stat(tracked).st_ino
    hooks_root = _hooks_symlink(fake_home, other / ".claude" / "hooks") / "hooks"

    result = hardlinks.sync_user_hooks(fake_project_with_hooks)

    assert result.errors == 0, [a.error for a in result.actions if a.error]
    assert tracked.read_text() == "#!/usr/bin/env python3\nprint('foreign copy')\n"
    assert os.stat(tracked).st_ino == foreign_inode, "relinked the foreign checkout's file"

    assert not hooks_root.is_symlink(), "the alias survived"
    deployed = hooks_root / "sdlc" / "validate_test.py"
    assert deployed.is_file()
    assert (
        os.stat(deployed).st_ino
        == os.stat(
            fake_project_with_hooks / ".claude" / "hooks" / "sdlc" / "validate_test.py"
        ).st_ino
    )


def test_sync_claude_dirs_migrates_the_hooks_symlink_and_keeps_the_helper(fake_home):
    """Migration mirrors the skills precedent: unlink the symlink (never its
    target) and rebuild a real directory of hardlinks. The rebuilt directory
    must carry ``sdlc/sdlc_context.py`` -- a real directory holding only the
    declared scripts is the #2561 breakage, and shipping this migration without
    it would convert a working machine into a broken one.
    """
    _require_global_interpreter()

    user_claude = _hooks_symlink(fake_home, _REPO_ROOT / ".claude" / "hooks")

    result = hardlinks.sync_claude_dirs(_REPO_ROOT)

    hooks_root = user_claude / "hooks"
    assert not hooks_root.is_symlink(), "the legacy symlink survived the migration"
    assert hooks_root.is_dir()
    assert any(
        a.action == "removed" and "dir-symlink" in (a.error or "") for a in result.actions
    ), "the migration was not reported"

    helper = hooks_root / "sdlc" / "sdlc_context.py"
    src = _REPO_ROOT / ".claude" / "hooks" / "sdlc" / "sdlc_context.py"
    assert helper.is_file(), "migrated to a real directory missing sdlc_context.py (#2561)"
    assert os.stat(helper).st_ino == os.stat(src).st_ino
    assert src.is_file(), "unlinking the symlink removed its target"


def test_hooks_symlink_survives_a_manifest_that_fails_to_load(fake_home, tmp_path):
    """The migration must never outlive its rebuild.

    ``sync_claude_dirs`` catches ``HookManifestError`` and downgrades it to
    ``hook_manifest = None``, which skips ``sync_user_hooks`` entirely. Were the
    unlink to happen earlier, that path would leave ``~/.claude/hooks`` absent
    while ``~/.claude/settings.json`` still registers global hooks against it.
    ``/usr/bin/python3`` on a missing script exits 2, the PreToolUse deny code,
    and both ``propagate`` and ``deny-only`` pass that straight through, so
    every Bash call in every repo would be denied -- including the ``/update``
    that would repair it. Keeping unlink and rebuild adjacent inside
    ``sync_user_hooks`` is what closes that window.
    """
    checkout = _make_checkout(tmp_path / "checkout")
    (checkout / ".claude" / "hooks" / "manifest.toml").write_text("this is not valid toml {{{\n")
    (checkout / ".claude" / "skills-global").mkdir(parents=True)
    (checkout / ".claude" / "commands").mkdir(parents=True)
    (checkout / ".claude" / "agents").mkdir(parents=True)
    (checkout / "scripts").mkdir(parents=True)
    hooks_root = _hooks_symlink(fake_home, checkout / ".claude" / "hooks") / "hooks"

    result = hardlinks.sync_claude_dirs(checkout)

    assert any("manifest" in (a.error or "").lower() for a in result.actions), (
        "the manifest was expected to fail loading"
    )
    assert hooks_root.is_symlink(), "the migration ran without its rebuild"
    assert (hooks_root / "sdlc").is_dir(), "global hook scripts became unreachable"


def test_parent_symlink_alias_is_detected_and_never_unlinked(fake_project_with_hooks, fake_home):
    """The helper's second branch: ~/.claude/hooks is a real directory that
    still resolves into the repo because a PARENT carries the symlink. There is
    no link at the hooks root to remove, and unlinking a real directory would
    raise, so the alias stands and only registration runs."""
    _require_global_interpreter()

    # ~/.claude itself is the symlink, into the project's .claude/.
    (fake_home / ".claude").symlink_to(
        fake_project_with_hooks / ".claude", target_is_directory=True
    )
    hooks_root = fake_home / ".claude" / "hooks"
    assert not hooks_root.is_symlink(), "fixture must exercise the non-symlink branch"

    aliased = hardlinks.user_hooks_root_is_repo_aliased(
        fake_project_with_hooks, fake_home / ".claude"
    )
    assert aliased == (fake_project_with_hooks / ".claude" / "hooks").resolve()

    result = hardlinks.sync_user_hooks(fake_project_with_hooks)

    assert result.errors == 0, [a.error for a in result.actions if a.error]
    assert hooks_root.is_dir(), "the aliased directory was removed"
    assert (fake_project_with_hooks / ".claude" / "hooks" / "sdlc" / "validate_test.py").is_file()
    assert any(
        a.action == "skipped" and "parent aliases" in (a.error or "") for a in result.actions
    )
    # Registration still runs -- the scripts are reachable through the alias.
    import json

    settings = json.loads((fake_home / ".claude" / "settings.json").read_text())
    assert "PreToolUse" in settings.get("hooks", {})


def test_cleanup_renamed_reports_no_skip_when_there_is_nothing_to_prune(fake_project, fake_home):
    """The alias guard must not claim it declined a prune on a machine that had
    no stale hook file in the first place."""
    _hooks_symlink(fake_home, fake_project / ".claude" / "hooks")

    result = hardlinks.HardlinkSyncResult()
    hardlinks._cleanup_renamed(fake_home / ".claude", fake_project, result)

    assert not [a for a in result.actions if a.dst == "~/.claude/hooks"], (
        "reported a declined prune with nothing to decline"
    )


def test_a_symlink_to_a_plain_user_directory_is_not_an_alias(fake_project, fake_home, tmp_path):
    """A deliberate ``~/.claude/hooks -> ~/dotfiles/claude-hooks`` arrangement
    holds no tracked source. It is a real user tree, just not where it appears,
    and migrating it away would destroy a setup this code has no business
    touching. Detection and destruction share one definition, so returning
    ``None`` here is exactly what stops the migration."""
    _require_global_interpreter()

    dotfiles = tmp_path / "dotfiles" / "claude-hooks"
    dotfiles.mkdir(parents=True)
    hooks_root = _hooks_symlink(fake_home, dotfiles) / "hooks"

    assert hardlinks.user_hooks_root_is_repo_aliased(fake_project, fake_home / ".claude") is None

    hardlinks.sync_user_hooks(fake_project)

    assert hooks_root.is_symlink(), "detached a symlink that pointed at no tracked source"
    assert hooks_root.resolve() == dotfiles.resolve()


def test_parent_symlink_to_a_foreign_checkout_is_an_alias(fake_project, tmp_path, fake_home):
    """The alias can sit on ``~/.claude`` AND point somewhere other than
    ``project_dir``: an agent in a worktree while ``~/.claude`` points at the
    main checkout. Scoping the answer to ``project_dir`` would return ``None``
    here and let ``_cleanup_renamed`` delete that checkout's tracked source."""
    hooks_removals = [pair for pair in hardlinks.RENAMED_REMOVALS if pair[0] == "hooks"]
    _kind, old_name = hooks_removals[0]

    other = _make_checkout(tmp_path / "other-checkout")
    tracked = other / ".claude" / "hooks" / old_name
    tracked.parent.mkdir(parents=True, exist_ok=True)
    tracked.write_text("#!/usr/bin/env python3\nprint('tracked source')\n")
    (fake_home / ".claude").symlink_to(other / ".claude", target_is_directory=True)

    aliased = hardlinks.user_hooks_root_is_repo_aliased(fake_project, fake_home / ".claude")
    assert aliased == (other / ".claude" / "hooks").resolve()

    result = hardlinks.HardlinkSyncResult()
    hardlinks._cleanup_renamed(fake_home / ".claude", fake_project, result)

    assert tracked.is_file(), "deleted a foreign checkout's tracked source through a parent alias"


def test_a_declaration_that_fails_to_deploy_is_never_registered(fake_project_with_hooks, fake_home):
    """Registering a script that is not on disk is the migration wedge one level
    down. The declared global hook is ``exit_policy = "propagate"``, so its
    command carries no guard, and ``/usr/bin/python3`` on a missing script exits
    2, the PreToolUse deny code. Every Bash call in every repo would be denied,
    including the ``/update`` that would repair it."""
    _require_global_interpreter()

    # Remove the source so deployment cannot succeed. The declaration stands.
    (fake_project_with_hooks / ".claude" / "hooks" / "sdlc" / "validate_test.py").unlink()
    settings = fake_home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text("{}\n")

    result = hardlinks.sync_user_hooks(fake_project_with_hooks)

    assert result.errors >= 1, "a failed deployment must be loud"
    assert any("Not registered" in (a.error or "") for a in result.actions)

    import json

    # The settings file must already exist, or _merge_hook_settings never writes
    # and the assertion below passes for a reason unrelated to registration.
    settings_path = fake_home / ".claude" / "settings.json"
    settings_text = settings_path.read_text()
    assert "validate_test.py" not in settings_text, (
        "registered a blocking hook whose script is not on disk"
    )
    json.loads(settings_text)  # still valid JSON


def test_a_failed_deployment_deregisters_a_previously_working_hook(
    fake_project_with_hooks, fake_home
):
    """The safe direction is deregistration, not a stale entry. A machine that
    registered the hook on an earlier run and then loses the deploy must end up
    with the hook absent rather than denying every Bash call."""
    _require_global_interpreter()

    first = hardlinks.sync_user_hooks(fake_project_with_hooks)
    assert first.errors == 0, [a.error for a in first.actions if a.error]
    settings_path = fake_home / ".claude" / "settings.json"
    assert "validate_test.py" in settings_path.read_text(), "setup did not register the hook"

    (fake_project_with_hooks / ".claude" / "hooks" / "sdlc" / "validate_test.py").unlink()
    (fake_home / ".claude" / "hooks" / "sdlc" / "validate_test.py").unlink()

    hardlinks.sync_user_hooks(fake_project_with_hooks)

    assert "validate_test.py" not in settings_path.read_text(), (
        "left a registration pointing at a script that is no longer on disk"
    )


def test_a_skills_migration_alone_does_not_read_as_a_hooks_migration(fake_project, fake_home):
    """Round 2's exact failure, as a regression test. ``/update`` keys its
    report off ``hooks_were_migrated``. With a skills dir-symlink present and no
    hooks alias, the skills migration fires and the hooks one does not, and the
    predicate must say so. Both emitters are exercised rather than asserted
    about as string literals, so the constant and its consumer cannot drift.
    """
    _require_global_interpreter()

    user_claude = fake_home / ".claude"
    user_claude.mkdir(parents=True, exist_ok=True)
    (fake_project / ".claude" / "skills-global").mkdir(parents=True, exist_ok=True)
    (user_claude / "skills").symlink_to(
        fake_project / ".claude" / "skills-global", target_is_directory=True
    )

    result = hardlinks.sync_claude_dirs(fake_project)

    assert not (user_claude / "skills").is_symlink(), "the skills migration did not run"
    assert not hardlinks.hooks_were_migrated(result), (
        "a skills migration was reported as a hooks migration"
    )


def test_the_hooks_migration_reads_as_migrated(fake_home):
    """The predicate's positive direction, against the real emitter."""
    _require_global_interpreter()

    _hooks_symlink(fake_home, _REPO_ROOT / ".claude" / "hooks")

    assert hardlinks.hooks_were_migrated(hardlinks.sync_claude_dirs(_REPO_ROOT))


def test_a_worktree_gitdir_file_still_reads_as_a_checkout(fake_project, fake_home, tmp_path):
    """A linked worktree's ``.git`` is a FILE holding a gitdir pointer, and a
    worktree is where an agent actually runs. A ``.git`` probe that required a
    directory would return ``None`` here and let the guards write and delete
    through the alias."""
    other = _make_checkout(tmp_path / "wt-checkout", worktree=True)
    assert (other / ".git").is_file()

    _hooks_symlink(fake_home, other / ".claude" / "hooks")

    assert (
        hardlinks.user_hooks_root_is_repo_aliased(fake_project, fake_home / ".claude")
        == (other / ".claude" / "hooks").resolve()
    )


def test_a_link_failure_withholds_the_registration(fake_project_with_hooks, fake_home, monkeypatch):
    """The failure the round-2 blocker was reproduced with: ``os.link`` raising
    (EXDEV across filesystems, ENOSPC on a full disk). ``_ensure_hardlink``
    catches OSError and creates ``dst_file.parent`` before the link raises, so
    the settings file genuinely exists and the write path is reached."""
    _require_global_interpreter()

    def _refuse(src, dst):
        raise OSError(18, "Cross-device link")

    monkeypatch.setattr(hardlinks.os, "link", _refuse)
    # Seed the settings file: with nothing deployed there is nothing to merge,
    # so an absent file would make the assertion below pass for the wrong reason.
    settings = fake_home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text("{}\n")

    result = hardlinks.sync_user_hooks(fake_project_with_hooks)

    assert result.errors >= 1
    assert any("Not registered" in (a.error or "") for a in result.actions)
    assert not (fake_home / ".claude" / "hooks" / "sdlc" / "validate_test.py").is_file()

    settings_text = (fake_home / ".claude" / "settings.json").read_text()
    assert "validate_test.py" not in settings_text, (
        "registered a blocking hook that os.link never wrote"
    )
    # The filter and the sweep leave the same settings file, so the file alone
    # cannot tell them apart. The action log can: a Deregistered action here
    # means the entry was written and then cleaned up, which is the window an
    # interrupt turns into a wedge.
    assert not [a for a in result.actions if "Deregistered dead hook" in (a.error or "")], (
        "the registration was written and then swept, rather than never written"
    )


def test_parent_alias_withholds_a_declaration_the_aliased_checkout_lacks(
    fake_project_with_hooks, tmp_path, fake_home
):
    """The parent-alias branch declines to deploy on the grounds that the
    scripts are already in place. That is a claim about the ALIASED checkout,
    not this one. A worktree branch declaring a global hook the main checkout
    does not carry yet leaves the script nowhere, so the registration must be
    withheld rather than assumed."""
    _require_global_interpreter()

    # The aliased checkout carries no sdlc/validate_test.py.
    other = _make_checkout(tmp_path / "main-checkout")
    (other / ".claude" / "settings.json").write_text("{}\n")
    (fake_home / ".claude").symlink_to(other / ".claude", target_is_directory=True)

    result = hardlinks.sync_user_hooks(fake_project_with_hooks)

    assert any(
        a.action == "skipped" and "parent aliases" in (a.error or "") for a in result.actions
    )
    assert any("Not registered" in (a.error or "") for a in result.actions), (
        "registered a hook the aliased checkout does not carry"
    )
    assert result.errors >= 1, "a withheld registration must not report a clean run"
    assert "validate_test.py" not in (fake_home / ".claude" / "settings.json").read_text()


def test_migration_deregisters_an_unmarked_entry_it_severed(fake_home):
    """Under the alias, ~/.claude/hooks/ exposed the WHOLE repo hooks tree.
    Unlinking it severs every non-declared path beneath, and
    ``_merge_hook_settings`` never touches unmarked entries by design. A real
    fleet machine carried exactly such an entry, an unmarked PreToolUse/Bash
    command under ``validators/``. Left registered it is a blocking command
    pointing at nothing, and a python interpreter on a missing file exits 2,
    the deny code. The migration closes its own door."""
    import json

    _require_global_interpreter()

    user_claude = _hooks_symlink(fake_home, _REPO_ROOT / ".claude" / "hooks")
    severed = user_claude / "hooks" / "validators" / "legacy_guard.py"
    (user_claude / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {"type": "command", "command": f"python {severed}", "timeout": 5}
                            ],
                        }
                    ]
                }
            }
        )
    )

    hardlinks.sync_claude_dirs(_REPO_ROOT)

    assert not severed.is_file(), "fixture assumed the migration severs validators/"
    settings_text = (user_claude / "settings.json").read_text()
    assert "legacy_guard.py" not in settings_text, (
        "left a blocking command registered against a severed path"
    )
    json.loads(settings_text)


def test_the_sweep_leaves_a_live_hand_added_hook_alone(fake_project_with_hooks, fake_home):
    """The sweep tests the referenced file's absence, never a path prefix. A
    hand-added user hook whose script is really on disk stays registered."""
    import json

    _require_global_interpreter()

    user_claude = fake_home / ".claude"
    mine = user_claude / "hooks" / "mine" / "guard.py"
    mine.parent.mkdir(parents=True)
    mine.write_text("#!/usr/bin/env python3\nprint('{}')\n")
    (user_claude / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {"type": "command", "command": f"python {mine}", "timeout": 5}
                            ],
                        }
                    ]
                }
            }
        )
    )

    hardlinks.sync_user_hooks(fake_project_with_hooks)

    assert mine.is_file(), "deleted a hand-added user hook's script"
    assert "mine/guard.py" in (user_claude / "settings.json").read_text(), (
        "deregistered a hand-added hook whose script is on disk"
    )


@pytest.mark.parametrize("form", ["~/.claude/hooks", "$HOME/.claude/hooks"])
def test_the_sweep_expands_tilde_and_home_before_comparing(
    fake_project_with_hooks, fake_home, form
):
    """``shlex.split`` leaves ``~/`` and ``$HOME/`` literal, but the ``/bin/sh``
    that runs the hook expands both. An unexpanded compare would miss exactly
    the entries a hand-written command is most likely to use, and a dead
    blocking command is a Bash deny for every repo."""
    import json

    _require_global_interpreter()

    user_claude = fake_home / ".claude"
    user_claude.mkdir(parents=True, exist_ok=True)
    (user_claude / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"python {form}/validators/gone.py",
                                    "timeout": 5,
                                }
                            ],
                        }
                    ]
                }
            }
        )
    )

    hardlinks.sync_user_hooks(fake_project_with_hooks)

    assert "gone.py" not in (user_claude / "settings.json").read_text(), (
        f"the sweep did not expand {form}"
    )


def test_an_unparseable_hook_command_is_reported_not_raised(fake_project_with_hooks, fake_home):
    """A bare apostrophe in a hand-written command makes ``shlex.split`` raise.
    ``/update`` has no guard around this call, so an exception here breaks the
    repair tool. Skipping is the conservative read: this sweep only removes, so
    a command it cannot parse keeps its registration."""
    import json

    _require_global_interpreter()

    user_claude = fake_home / ".claude"
    user_claude.mkdir(parents=True, exist_ok=True)
    (user_claude / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {"type": "command", "command": "echo don't", "timeout": 5},
                                {"type": "command", "command": None, "timeout": 5},
                            ],
                        }
                    ]
                }
            }
        )
    )

    result = hardlinks.sync_user_hooks(fake_project_with_hooks)

    assert any("Unparseable hook command" in (a.error or "") for a in result.actions)
    assert "don't" in (user_claude / "settings.json").read_text(), (
        "removed an entry the sweep could not parse"
    )


# --------------------------------------------------------------------------- #
# exit_policy -> generated command string (issue #2527)
#
# Every prior hook test called the enforcement function directly and asserted
# on SystemExit, so the deployed command string was invisible to CI and the
# blanket `|| true` swallowed every deny for as long as the manifest existed.
# These tests assert on the generated string and run it through a real shell.
# --------------------------------------------------------------------------- #
def _decl(exit_policy: str) -> hardlinks.HookDeclaration:
    return hardlinks.HookDeclaration(
        manifest_id="probe",
        event="PreToolUse",
        matcher="",
        script="probe.py",
        timeout=5,
        scope="project",
        exit_policy=exit_policy,
    )


def _generated_command(exit_policy: str) -> str:
    return hardlinks._build_hook_command(
        _decl(exit_policy), "/hooks", interpreter="python3", embed_marker=False
    )


def test_exit_policy_generates_distinct_suffixes():
    assert _generated_command("propagate") == "python3 /hooks/probe.py"
    assert _generated_command("suppress") == "python3 /hooks/probe.py || true"
    deny_only = _generated_command("deny-only")
    assert deny_only.startswith("python3 /hooks/probe.py; ")
    assert "|| true" not in deny_only


@pytest.mark.parametrize(
    ("exit_policy", "hook_rc", "expected"),
    [
        # deny-only: a deliberate exit 2 blocks; every other exit fails open.
        ("deny-only", 0, 0),
        ("deny-only", 1, 0),
        ("deny-only", 2, 2),
        ("deny-only", 3, 0),
        # suppress: nothing reaches the harness, exit 2 included.
        ("suppress", 2, 0),
        ("suppress", 1, 0),
        # propagate: everything reaches the harness.
        ("propagate", 2, 2),
        ("propagate", 1, 1),
    ],
)
def test_generated_command_exit_code_through_a_real_shell(tmp_path, exit_policy, hook_rc, expected):
    """Run the GENERATED command string in `sh` and assert the shell's exit code.

    This is the check that was missing: `sh -c 'exit 2'` returns 2 but
    `sh -c 'exit 2' || true` returns 0, so a registered `sys.exit(2)` deny
    reported success and Claude Code allowed the tool call.
    """
    script = tmp_path / "probe.py"
    script.write_text(f"import sys; sys.exit({hook_rc})\n")
    command = hardlinks._build_hook_command(
        _decl(exit_policy), str(tmp_path), interpreter="python3", embed_marker=False
    )
    result = subprocess.run(command, shell=True, capture_output=True)
    assert result.returncode == expected, (
        f"exit_policy={exit_policy!r} with a hook exiting {hook_rc} produced "
        f"shell exit {result.returncode}, expected {expected}. Command: {command}"
    )


def test_real_manifest_keeps_pre_tool_use_deny_alive():
    """`pre_tool_use` must stay deny-only: both its `sys.exit(2)` denies
    (`_enforce_tool_budget`, `_enforce_foreground_subagents`) depend on it."""
    from scripts.update.hook_manifest import load_hook_manifest

    decl = next(d for d in load_hook_manifest() if d.manifest_id == "pre_tool_use")
    assert decl.exit_policy == "deny-only"

    hooks = hardlinks.generate_project_hooks(load_hook_manifest())
    command = next(
        h["command"]
        for block in hooks["PreToolUse"]
        for h in block["hooks"]
        if "pre_tool_use.py" in h["command"]
    )
    assert "|| true" not in command, (
        "pre_tool_use.py is registered with the blanket `|| true` guard again — "
        "its deny cannot block (issue #2527)."
    )


# --------------------------------------------------------------------------- #
# Where #2579's deployment filter and #2527's exit_policy meet.
#
# `deny-only` passes exit 2 through by design, and `python3 <missing script>`
# exits 2. So a `deny-only` declaration registered against a script that failed
# to deploy denies every matching tool call in every repo, including the
# `/update` that would repair it -- the same wedge #2579 closed for
# `propagate`, reached by a different route.
#
# `_register_deployed_only` filters on disk presence before any command string
# is built, so it is exit_policy-independent and the two compose by
# construction. These tests pin that, because the property is invisible in the
# current manifest: nothing declares global + deny-only today, so a refactor
# that moved the filter after command generation would break nothing visible.
# --------------------------------------------------------------------------- #
_FAKE_MANIFEST_TOML_GLOBAL_DENY_ONLY = """
[[hook]]
manifest_id = "test_global_deny_only"
event = "PreToolUse"
matcher = "Bash"
script = "sdlc/validate_test.py"
timeout = 10
scope = "global"
exit_policy = "deny-only"
"""


def test_deny_only_declaration_that_fails_to_deploy_is_never_registered(
    fake_project_with_hooks, fake_home
):
    """A `deny-only` hook whose script is missing must not reach settings.json."""
    _require_global_interpreter()

    hooks_dir = fake_project_with_hooks / ".claude" / "hooks"
    (hooks_dir / "manifest.toml").write_text(_FAKE_MANIFEST_TOML_GLOBAL_DENY_ONLY)
    (hooks_dir / "sdlc" / "validate_test.py").unlink()

    settings_path = fake_home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text("{}\n")

    result = hardlinks.sync_user_hooks(fake_project_with_hooks)

    assert result.errors >= 1, "a failed deployment must be loud"
    assert any("Not registered" in (a.error or "") for a in result.actions)

    settings_text = settings_path.read_text()
    assert "validate_test.py" not in settings_text, (
        "registered a deny-only hook whose script is not on disk -- a missing "
        "script exits 2, which this policy passes through as a PreToolUse DENY"
    )
    json.loads(settings_text)

    # The filter and the sweep leave the same settings file behind, so the file
    # alone cannot tell them apart and an assertion on it passes either way.
    # The action log discriminates: a Deregistered action means the entry WAS
    # written and then cleaned up. Under deny-only that window is the wedge --
    # an interrupt between the write and the sweep leaves every matching tool
    # call denied by a hook pointing at a missing script.
    assert not [a for a in result.actions if "Deregistered dead hook" in (a.error or "")], (
        "the deny-only registration was written and then swept, rather than never written"
    )


def test_deny_only_guard_would_deny_on_a_missing_script():
    """The premise of the test above, stated as an executable fact.

    Without this, the filter test could pass for the wrong reason (e.g. if
    `python3` on a missing file ever stopped exiting 2, the wedge would be
    theoretical and the filter untested against a real hazard).
    """
    interpreter = hardlinks.resolve_global_interpreter()
    if interpreter is None:
        pytest.skip("no system python3 resolved on this machine")

    decl = hardlinks.HookDeclaration(
        manifest_id="probe",
        event="PreToolUse",
        matcher="Bash",
        script="definitely_not_here.py",
        timeout=5,
        scope="global",
        exit_policy="deny-only",
    )
    command = hardlinks._build_hook_command(
        decl, "/nonexistent-hooks-root", interpreter=interpreter, embed_marker=False
    )
    assert subprocess.run(command, shell=True, capture_output=True).returncode == 2

    # The same missing script under `suppress` is inert -- which is why the
    # deployment filter, not the policy, is what makes `deny-only` safe.
    suppressed = hardlinks._build_hook_command(
        replace(decl, exit_policy="suppress"),
        "/nonexistent-hooks-root",
        interpreter=interpreter,
        embed_marker=False,
    )
    assert subprocess.run(suppressed, shell=True, capture_output=True).returncode == 0


def test_an_unresolvable_hooks_root_fails_closed(fake_project, fake_home, monkeypatch):
    """``user_hooks_root_is_repo_aliased`` must return a truthy value when it
    cannot resolve the path.

    ``None`` is read by every caller as "real user tree, safe to write and
    delete", and that is the one answer an unresolvable path cannot support.
    ``_cleanup_renamed`` acts on that answer by unlinking, so failing open here
    is a deletion decision made on a reading the code just admitted it could
    not take.
    """
    hooks_root = fake_home / ".claude" / "hooks"
    hooks_root.mkdir(parents=True)

    real_resolve = Path.resolve

    def _refuse(self, *args, **kwargs):
        if self == hooks_root:
            raise OSError(40, "Too many levels of symbolic links")
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", _refuse)

    # Asserting the exact path, not merely non-None: callers use the returned
    # Path, so a non-None sentinel would pass a truthiness check and then be
    # misused downstream.
    assert (
        hardlinks.user_hooks_root_is_repo_aliased(fake_project, fake_home / ".claude") == hooks_root
    ), "an unresolvable hooks root was reported as a safe real user tree"


def test_a_non_string_command_neither_crashes_the_sweep_nor_is_removed(
    fake_project_with_hooks, fake_home
):
    """A ``"command": null`` or numeric entry is malformed, not empty.

    What this pins, precisely: removing either type guard -- the sweep's
    ``isinstance`` or ``_extract_manifest_id``'s -- raises out of an unguarded
    ``/update``, and the malformed entries survive the pass untouched.

    What it does NOT pin, stated here rather than only in the PR that added it:
    rewriting the sweep's guard to ``str(entry.get("command") or "")`` keeps
    this green. Python's ``repr`` escaping is robust enough that a coerced list
    or dict never yields a token matching the hooks root, so coercion happens to
    be outcome-equivalent for realistic values. The guard is pinned against
    removal, not against that particular rewrite. Skipping is still the right
    shape -- coercing reports an entry as inspected when it was not -- but that
    argument rests on intent, not on this assertion.
    """
    import json

    _require_global_interpreter()

    user_claude = fake_home / ".claude"
    user_claude.mkdir(parents=True, exist_ok=True)
    (user_claude / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {"type": "command", "command": None, "timeout": 5},
                                {"type": "command", "command": 42, "timeout": 5},
                            ],
                        }
                    ]
                }
            }
        )
    )

    result = hardlinks.sync_user_hooks(fake_project_with_hooks)

    assert result.errors == 0, [a.error for a in result.actions if a.error]
    settings = json.loads((user_claude / "settings.json").read_text())
    commands = [e["command"] for b in settings["hooks"]["PreToolUse"] for e in b["hooks"]]
    assert None in commands and 42 in commands, "the sweep did not leave malformed entries alone"
