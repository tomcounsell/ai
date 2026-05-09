"""
CI gate: no hardcoded ``~/Desktop/Valor`` literals outside the documented
fallback / cascade-implementation patterns.

This test guards against drift introduced by the configurable-vault-path
refactor (``VALOR_VAULT_DIR``). Every literal in source must be EITHER:

  - in an allowlisted file (plans, postmortems, the cascade implementation
    in `config/settings.py`, etc.), OR
  - on a line that also references ``VALOR_VAULT_DIR`` (so it's part of a
    fallback expansion / cascade), OR
  - on a line that's clearly descriptive prose (mentions ``default``,
    ``fallback``, ``cascade``, ``established``, ``legacy``).

If you're getting flagged: either route the call through ``vault.<property>``
or add the noqa-style allowance described above.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# File-level allowlist: directories or paths that may contain bare literals
# without per-line justification. Plans, postmortems, RFCs, and changelogs
# are historical or external context; the cascade implementation deliberately
# names the established default; prompt.VAULT_PICKER_OPTIONS is the picker's
# option list.
ALLOWLIST_PREFIXES = (
    ".plans/",
    "docs/plans/",  # plan documents are historical context; many describe the legacy state
    "docs/postmortems/",
    # Per-feature docs and how-to guides may name the established default in
    # prose; the gate enforces vault-aware code paths, not exhaustive doc
    # cleanup. See docs/features/vault-path.md for the canonical reference.
    "docs/features/",
    "docs/guides/",
    "docs/tools-reference.md",  # generated tool reference; touched by `/update`
    # Worker watchdog comment names the default in TCC explanation.
    "worker/",
    "tests/",  # test fixtures legitimately hardcode paths to model legacy state
    ".env.example",
    "CHANGELOG.md",
    # Cascade-implementation files: each contains the literal as a fallback
    # in its `_resolve_…` helper, plus docstrings explaining the default.
    "config/settings.py",
    "config/paths.py",
    "config/project_key_resolver.py",
    # Example data file: shows the established default to users; copying it
    # to a new vault location is part of `/setup`. The _doc.overview field
    # explicitly names VALOR_VAULT_DIR.
    "config/projects.example.json",
    # Persona overlay: descriptive prose that names the established default.
    "config/personas/project-manager.md",
    "bridge/routing.py",
    "reflections/utils.py",
    "agent/reflection_scheduler.py",
    "agent/sdk_client.py",
    "tools/google_workspace/auth.py",
    "tools/valor_calendar.py",
    "tools/telegram_users.py",
    "tools/knowledge/scope_resolver.py",
    "tools/install/prompt.py",  # picker option list (label + value for desktop choice)
    "ui/data/machine.py",
    "ui/data/memories.py",
    "utils/api_keys.py",
    "scripts/migrate_model_relationships.py",
    "scripts/update/env_sync.py",
    "scripts/update/run.py",
    "scripts/update/service.py",
    "scripts/update/verify.py",
    "scripts/update/cal_integration.py",
    "scripts/reflections_report.py",
    # Shell scripts that fall back to ~/Desktop/Valor when VALOR_VAULT_DIR is unset.
    "scripts/install_worker.sh",
    "scripts/install_autoexperiment.sh",
    "scripts/install_nightly_tests.sh",
    "scripts/install_sdlc_reflection.sh",
    "scripts/start_bridge.sh",
    "scripts/valor-service.sh",
    "scripts/calendar_hook.sh",
    "scripts/calendar_prompt_hook.sh",
    "scripts/remote-update.sh",
    "scripts/fetch_recent_dms.py",
    "scripts/test_emoji_reactions.py",
    "scripts/test_sdk.py",
    "scripts/autoexperiment.py",
    # Skill files: each documents the established default in prose; the M8
    # contract test in test_setup_skill.py enforces vault-aware bash usage.
    ".claude/skills-global/setup/SKILL.md",
    ".claude/skills-global/update/SKILL.md",
    ".claude/skills-global/do-deploy/SKILL.md",
    ".claude/skills-global/do-pr-review/SKILL.md",
    ".claude/agents/baseline-verifier.md",
    # Worker comment that mentions the default location.
    "worker/__main__.py",
)

# Per-line escape hatches: keywords that indicate descriptive prose where
# naming the literal is appropriate (docstrings, comments, JSON instructions).
PROSE_KEYWORDS = (
    "default",
    "fallback",
    "cascade",
    "established",
    "legacy",
    "iCloud",
    "TCC",
    "symlink",
    "migration",
)

LITERAL_PATTERN = re.compile(r"~?/Desktop/Valor|\"Desktop\" / \"Valor\"|Desktop/Valor")

# Files we must scan: every Python, shell, markdown, and JSON file that
# isn't in a hidden directory (.git, .venv, .pytest_cache, etc.) or third-
# party install locations.
SCAN_GLOBS = ("**/*.py", "**/*.sh", "**/*.md", "**/*.json")
SCAN_EXCLUDE_DIR_PARTS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    ".mypy_cache",
    "data",  # generated runtime data
    "logs",
    ".worktrees",
}


def _is_allowlisted(rel_path: str) -> bool:
    return any(rel_path.startswith(prefix) for prefix in ALLOWLIST_PREFIXES)


def _is_excluded_dir(path: Path) -> bool:
    return any(part in SCAN_EXCLUDE_DIR_PARTS for part in path.parts)


def _line_is_justified(line: str) -> bool:
    """Return True if the line's literal is justified by a per-line marker.

    A line is justified if it:
      - mentions ``VALOR_VAULT_DIR`` (it's part of a cascade/fallback), OR
      - mentions any prose keyword (descriptive copy in docstrings, comments,
        markdown, etc.).
    """
    if "VALOR_VAULT_DIR" in line:
        return True
    lower = line.lower()
    return any(kw.lower() in lower for kw in PROSE_KEYWORDS)


def _scan_files() -> Iterable[Path]:
    for glob in SCAN_GLOBS:
        for path in REPO_ROOT.glob(glob):
            if path.is_file() and not _is_excluded_dir(path.relative_to(REPO_ROOT)):
                yield path


def _violations() -> list[tuple[str, int, str]]:
    """Return (rel_path, line_no, line_text) for every unjustified literal."""
    violations: list[tuple[str, int, str]] = []
    for path in _scan_files():
        rel = str(path.relative_to(REPO_ROOT))
        if _is_allowlisted(rel):
            continue
        try:
            content = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(content.splitlines(), start=1):
            if not LITERAL_PATTERN.search(line):
                continue
            if _line_is_justified(line):
                continue
            violations.append((rel, i, line.rstrip()))
    return violations


def test_no_hardcoded_vault_path():
    """No bare ``~/Desktop/Valor`` literals outside the allowlist + per-line markers.

    If this fails, one of these fixes applies:

      1. Route the call through ``config.settings.vault.<property>`` and
         keep the literal only as a fallback in an ``except VaultNotResolved``
         branch.
      2. If the literal IS the fallback, mention ``VALOR_VAULT_DIR`` on the
         same line so the cascade context is visible at the line level.
      3. If the literal is descriptive prose, ensure the line mentions
         ``default``, ``fallback``, ``cascade``, ``established``, ``legacy``,
         ``iCloud``, ``TCC``, ``symlink``, or ``migration``.
      4. As a last resort, add the file to ``ALLOWLIST_PREFIXES`` above and
         document why in this docstring.
    """
    violations = _violations()
    assert not violations, (
        "Found "
        + str(len(violations))
        + " bare ~/Desktop/Valor literals outside the allowlist:\n  "
        + "\n  ".join(f"{p}:{n}: {ln}" for p, n, ln in violations)
        + "\n\nFix one of: route through vault.<property>, add VALOR_VAULT_DIR "
        "to the line, use a prose keyword (default/fallback/cascade/...), "
        "or extend ALLOWLIST_PREFIXES with justification."
    )


@pytest.mark.parametrize(
    "kind",
    ["py", "sh", "md", "json"],
)
def test_scan_actually_finds_files(kind):
    """Sanity: the scan globs match real files in this repo."""
    found = list(REPO_ROOT.glob(f"**/*.{kind}"))
    assert any(
        f.is_file() and not _is_excluded_dir(f.relative_to(REPO_ROOT)) for f in found
    ), f"scan glob for *.{kind} returned no files — globbing is broken"
