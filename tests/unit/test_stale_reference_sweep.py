"""Durable guard against reintroducing three classes of stale prose (#2853, #2839).

Every prior attempt to sweep these tokens out of the tree died with the
artifact that carried it: a diff-scoped grep can never see pre-existing text
(#2833's closure criterion), and a filesystem walk that excludes
`docs/plans/completed/` is only correct until the next teardown lands (#1643,
#1924). This module is the first version of that gate meant to outlive the
plan doc that motivated it -- the plan's own Verification rows are archived
into `docs/plans/` at merge, which every sweep below (and this file's own
enumeration) excludes by construction.

Three assertions, all scoped to tracked content:

1. No in-scope file names the reflection by its old, unregistered name.
2. No in-scope file references either of the two deleted granite package
   paths (the renamed-then-deleted `tools/` package and its original name).
3. A literal four-file tuple -- the sites that were rewritten from "nightly"
   to "daily" -- carries no leftover occurrence of "nightly". This class has
   no token in common with (1) or (2): one of the four sites (`models/
   ghost_reconcile.py`, the `Model.clean_indexes()` docstring line) names no
   reflection and no package, so neither of the first two assertions can ever
   reach it. It was found only by scoping this exact anti-criterion to the
   four files during plan revision.

Three trade-offs, deliberate:

- Enumeration walks `git ls-files`, never `os.walk`/`rglob`. An untracked
  file is not repo content, and this machine routinely grows untracked
  Markdown bundles (critique-run artifacts, scratch notes) that legitimately
  carry these exact strings; a filesystem walk turns those into false
  failures for lanes that have nothing to do with this one. The corollary is
  that a brand-new untracked file carrying a stale token is invisible here --
  that is intentional, since any real fix lands in a tracked file anyway.
- Assertion 3's four-file scope permanently retires the ordinary English word
  "nightly" from those four files. A future author with a legitimate reason
  to use it there (a nightly digest in the email bridge is the obvious case)
  should read this docstring and extend or narrow `_NIGHTLY_SCOPED`
  knowingly, rather than deleting or muting the assertion.
- Enumeration is further scoped by suffix to `.py`/`.md`/`.yaml`/`.yml`/
  `.toml`, so a stale token sitting in a tracked file of any other suffix --
  shell scripts, JSON, HTML, plist, or plain text, 146 tracked files outside
  `docs/plans/` in this repo today -- is invisible to every assertion here;
  a stale token in a tracked `.sh` file was confirmed to slip through this
  way. `.yaml`/`.yml` themselves currently match no tracked hits --
  `config/reflections.yaml` is gitignored -- so the reflection registry's
  own name and cadence contract is actually pinned by
  `tests/integration/test_reflections_redis.py`, which asserts both the
  registered name and the 86400s interval, not by anything in this file.
  No live occurrence exists outside `docs/plans/` today and every
  realistic reintroduction path is Python, Markdown, or YAML/TOML, so this
  suffix list is intentionally not widened here.

This file itself is excluded from every enumeration below (belt) in addition
to containing zero literal occurrences of the tokens it asserts against
(suspenders) -- every token compared against tracked content is assembled by
string concatenation rather than spelled out, including in this docstring,
so staging this file can never itself trip the sweeps it exists to guard.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
THIS_FILE = Path(__file__).resolve()

_SCOPED_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".toml"}
_EXCLUDED_DIR = "docs/plans"

# The stale reflection name: hyphenated, distinct from the still-accurate
# module path `scripts/popoto_index_cleanup.py` (underscored).
_STALE_NAME_TOKEN = "popoto" + "-index-cleanup"

# The two deleted granite package path tokens. Neither is the CLI command
# name `valor-granite-loop`, which is correctly mentioned in past tense
# elsewhere in the tree and must never be swept.
_GRANITE_PATH_TOKENS = (
    "granite" + "_interactive_tui_poc",
    "tools/" + "granite_loop",
)

_NIGHTLY_SCOPED = (
    "scripts/popoto_index_cleanup.py",
    "bridge/email_bridge.py",
    "models/ghost_reconcile.py",
    "models/dedup.py",
)
_NIGHTLY_TOKEN = "night" + "ly"


def _tracked_files() -> list[Path]:
    """Return in-scope tracked files: repo-relative, extension-filtered,
    excluding docs/plans and this guard file itself.
    """
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    paths = []
    for rel in result.stdout.split("\0"):
        if not rel:
            continue
        if Path(rel).suffix not in _SCOPED_SUFFIXES:
            continue
        if rel.startswith(_EXCLUDED_DIR + "/") or rel == _EXCLUDED_DIR:
            continue
        abs_path = (REPO_ROOT / rel).resolve()
        if abs_path == THIS_FILE:
            continue
        paths.append(Path(rel))
    return paths


def _find_hits(token: str) -> list[str]:
    """Return "path:line" hits of `token` across in-scope tracked files."""
    hits: list[str] = []
    for rel in _tracked_files():
        abs_path = REPO_ROOT / rel
        try:
            text = abs_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if token in line:
                hits.append(f"{rel}:{lineno}")
    return hits


def test_no_stale_reflection_name():
    """No tracked file outside docs/plans/ names the reflection by its old,
    unregistered name.
    """
    hits = _find_hits(_STALE_NAME_TOKEN)
    assert hits == [], "Stale reflection-name token found at: " + ", ".join(hits)


def test_no_deleted_granite_package_path():
    """No tracked file outside docs/plans/ references either deleted
    granite package path.
    """
    all_hits: list[str] = []
    for token in _GRANITE_PATH_TOKENS:
        all_hits.extend(_find_hits(token))
    assert all_hits == [], "Deleted granite package path token found at: " + ", ".join(all_hits)


def test_no_nightly_cadence_at_scoped_sites():
    """None of the four rewritten sites still call this sweep "nightly" --
    it runs on a rolling 24h interval (`every: 86400s`), not a clock-pinned
    schedule.
    """
    hits: list[str] = []
    for rel_str in _NIGHTLY_SCOPED:
        abs_path = REPO_ROOT / rel_str
        try:
            text = abs_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _NIGHTLY_TOKEN in line:
                hits.append(f"{rel_str}:{lineno}")
    assert hits == [], "Leftover 'nightly' wording found at: " + ", ".join(hits)
