#!/usr/bin/env python3
"""Skills audit: validate SKILL.md files against canonical template standards.

Runs 20 deterministic validation rules over every skills root the repo has
(`.claude/skills-global/` and `.claude/skills/`), detects husk directories and
user-level orphans, and optionally syncs against Anthropic's latest published
best practices.

With `--fix`, husk directories that are empty except for build artifacts
(`__pycache__`, `.DS_Store`) are also auto-pruned before rule 19 runs, so a
freshly-pruned husk doesn't reappear as a FAIL in the same invocation. Husks
that still contain real files are left untouched for a human delete-or-restore
decision.

JSON contract (consumed by reflections/audits/skills_audit.py):
  {"summary": {"total_skills": N, "pass": N, "warn": N, "fail": N,
               "description_total_chars": N, "description_budget": N},
   "findings": [{"skill", "rule", "severity", "message", "dir"}, ...]}
Legacy aliases "results" and "skills_audited" are kept for older consumers.

Exit codes:
  0 — all pass (may have warnings)
  1 — at least one FAIL
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def _resolve_repo_root() -> Path:
    """Repo root: prefer cwd when it has skills roots (hardlinked copies run
    from foreign repos audit that repo), else derive from this file's location
    (scripts -> do-skills-audit -> skills[-global] -> .claude -> repo)."""
    cwd = Path.cwd()
    if (cwd / ".claude" / "skills-global").is_dir() or (cwd / ".claude" / "skills").is_dir():
        return cwd
    return Path(__file__).resolve().parents[4]


REPO_ROOT = _resolve_repo_root()
SKILLS_DIR = REPO_ROOT / ".claude" / "skills-global"
PROJECT_SKILLS_DIR = REPO_ROOT / ".claude" / "skills"
USER_SKILLS_DIR = Path.home() / ".claude" / "skills"

MAX_LINES = 500
MAX_DESC_LEN = 1024  # Anthropic hard cap
WARN_DESC_LEN = 200  # doing documentation work beyond this
TARGET_DESC_LEN = 120  # what a pure trigger costs
FLEET_DESC_BUDGET = 4000  # ~2% of context for all descriptions combined
MAX_NAME_LEN = 64

KNOWN_FIELDS = frozenset(
    {
        "name",
        "description",
        "argument-hint",
        "disable-model-invocation",
        "user-invocable",
        "allowed-tools",
        "model",
        "context",
        "agent",
        "hooks",
    }
)

# Classification lists — which skills should have specific frontmatter flags.
# NOTE: setup/prime/sdlc moved to project-only .claude/skills/ (issue #1783, Bucket C).
# Since the audit now iterates both roots, entries may live in either.
INFRA_SKILLS = frozenset({"update", "reclassify", "new-skill", "do-skills-audit"})
BACKGROUND_SKILLS = frozenset(
    {
        # BYOB is exposed as MCP tools only (mcp__byob__browser_*) — there
        # is no SKILL.md for it. Listed here so the audit recognizes it
        # as a legitimate model-only browser surface.
        "byob",
        "telegram",
        "reading-sms-messages",
        "checking-system-logs",
        "google-workspace",
    }
)
FORK_SKILLS = frozenset({"do-build", "do-pr-review", "pthread", "do-design-audit"})

# ---------------------------------------------------------------------------
# Coupling-signal guard (issue #1783, rule_13)
# ---------------------------------------------------------------------------
# Tokens that mark a skill body as coupled to THIS repo's infrastructure. A
# global skill in skills-global/ ships to every machine and runs in every repo,
# so any of these tokens in the body means the skill leaks ai-repo specifics
# unless it defers to the per-repo skill-context seam via the canonical probe
# step. Project-only skills (.claude/skills/) run only in this repo, so the
# guard applies to the "global" root alone.
#
# The set is deliberately limited to EXECUTABLE / IMPORT references — invocations
# that actually *error or silently misfire* in a foreign repo (the plan's exact
# concern): the SDLC stage-marker CLI (sdlc-tool), this repo's Python tool/module
# families (python -m tools.*, reflections.*), the valor-* CLI wrappers, and the
# identity config (config/identity.json) the harness reads at runtime.
#
# It deliberately EXCLUDES weak doc-path / branch-name tokens (docs/features/,
# docs/plans/, session/{slug}). A bare see-also markdown link to docs/features/
# does NOT break execution in another repo — treating it as coupling produced
# false-positives on Bucket A skills the plan declares clean (mermaid-render,
# reclassify, do-discover-paths). Per plan Risk 2 the guard must not fire on them.
COUPLING_SIGNALS: tuple[str, ...] = (
    "sdlc-tool",
    "python -m tools.",
    "reflections.",
    "valor-",
    "config/identity.json",
)

# The canonical probe-step suffix (issue #1783). A leaned body that carries a
# coupling signal MUST contain this exact invariant suffix, proving it defers to
# the per-repo skill-context seam (docs/sdlc/{skill}.md for SDLC skills, or
# .claude/skill-context/{skill}.md otherwise) instead of hard-coding the
# behavior. rule_13 greps for this literal substring.
PROBE_SUFFIX = (
    "exists, read it and honor its declarations; "
    "otherwise use the generic defaults described below."
)

# ---------------------------------------------------------------------------
# Bucket-C coupling guard (issue #2079, rule_21)
# ---------------------------------------------------------------------------
# rule_13 catches EXECUTABLE/IMPORT tokens; rule_21 catches two classes it
# misses: (A) a global skill body invoking a *project-only* skill as a slash
# command (e.g. `/sdlc`, `/setup`), which resolves to nothing on a foreign
# machine because those skills live under .claude/skills/ and never sync, and
# (B) a curated set of internal-infra filenames/env-vars. Project-only skill
# names are derived LIVE from the .claude/skills/ dir listing (never hardcoded),
# so the rule stays repo-agnostic: a foreign repo with no such dir yields an
# empty set and Signal A never fires.

# Signal A: capture the FULL slash-token so exact set-membership decides the
# match — never a substring/prefix. The leading negative lookbehind guards the
# front edge and the greedy [a-z0-9-]* consumes the trailing hyphenated
# remainder, so both edges are hyphen-safe: bare `/do-deploy` captures
# `do-deploy` (a project-only skill → flag) while `/do-deploy-example` captures
# `do-deploy-example` (a global skill → not in the project-only set → no flag).
SLASH_TOKEN_RE = re.compile(r"(?<![\w-])/([a-z0-9][a-z0-9-]*)")

# Signal B: curated internal-infra tokens (repo-specific filenames/env-vars).
# Harmless in foreign repos — they simply never appear. Extend as needed.
BUCKET_C_INFRA_TOKENS: tuple[str, ...] = (
    "sdk_client.py",
    "SDLC_TARGET_REPO",
)

# Same-line escape-hatch markers: a Bucket-C signal is covered when its OWN
# physical line carries conditional framing. Same-line (not whole-file) is the
# deliberate strictness that separates rule_21 from rule_13 — a stray marker
# elsewhere in the doc cannot excuse an unrelated bare `/sdlc`.
CONDITIONAL_MARKERS: tuple[str, ...] = ("in this repo", "this repo's")

TRIGGER_PHRASES = re.compile(
    r"(?i)\b(use when|triggered by|also use when|invoke when|"
    r"use for|handles|use this when)\b"
)

ARGUMENTS_RE = re.compile(r"\$ARGUMENTS|\$\d+|\$\{ARGUMENTS")

# rule_15 asset-rot tokens: skill-conventional subpaths and .claude/ paths with
# a file extension. Placeholder-bearing tokens ({slug}, <name>, $VAR, globs)
# are skipped, as are the two skill-context seam locations, whose references
# are conditional by convention ("If <path> exists, ...").
ASSET_TOKEN_RE = re.compile(
    r"(?<![\w/-])(?:\./)?(?:\.claude|scripts|references|assets)/[\w\-./]+\.[A-Za-z0-9]{1,5}\b"
)
PLACEHOLDER_CHARS = ("{", "<", "*", "$", "…")
SEAM_PREFIXES = (".claude/skill-context/", "docs/sdlc/")

# rule_16 junk patterns — only flagged when git-tracked (untracked build
# artifacts like test-import __pycache__ regenerate and are gitignored).
JUNK_NAMES = frozenset({"README.md", "CHANGELOG.md", ".DS_Store"})
JUNK_SUFFIXES = (".pyc",)

# rule_17 near-duplicate descriptions: Jaccard similarity of content words.
NEAR_DUP_THRESHOLD = 0.5
DESC_STOPWORDS = frozenset(
    "a an and any are as at be by for from has if in into is it not of on or "
    "over should that the this to use used uses when whenever will with you "
    "your skill skills triggered also request requests".split()
)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    skill: str
    rule: int
    severity: str  # PASS, WARN, FAIL
    message: str
    dir: str = ""  # which root the skill came from: global | project | user


@dataclass
class AuditReport:
    skills_audited: int = 0
    results: list[Finding] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=lambda: {"pass": 0, "warn": 0, "fail": 0})

    def add(self, finding: Finding) -> None:
        self.results.append(finding)
        self.summary[finding.severity.lower()] += 1


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter and return (frontmatter_dict, body)."""
    match = re.match(r"^---\n(.*?)\n---\n?(.*)", text, re.DOTALL)
    if not match:
        return {}, text

    try:
        fm = yaml.safe_load(match.group(1))
        if not isinstance(fm, dict):
            fm = {}
    except yaml.YAMLError:
        fm = {}

    return fm, match.group(2).strip()


# ---------------------------------------------------------------------------
# Individual rule checks
# ---------------------------------------------------------------------------


def rule_01_line_count(skill_name: str, lines: list[str]) -> Finding:
    """SKILL.md must be <= 500 lines."""
    count = len(lines)
    if count > MAX_LINES:
        return Finding(skill_name, 1, "FAIL", f"Line count {count} exceeds {MAX_LINES}")
    return Finding(skill_name, 1, "PASS", f"Line count ({count} lines)")


def rule_02_frontmatter_exists(skill_name: str, fm: dict) -> Finding:
    """Must have YAML frontmatter."""
    if not fm:
        return Finding(skill_name, 2, "FAIL", "No YAML frontmatter found")
    return Finding(skill_name, 2, "PASS", "Frontmatter present")


def rule_03_name_field(skill_name: str, fm: dict, dir_name: str) -> Finding:
    """Name must be present, lowercase, hyphens/numbers only, max 64 chars, match dir."""
    name = fm.get("name")
    if not name:
        return Finding(skill_name, 3, "FAIL", "Missing 'name' field")
    name = str(name)
    if len(name) > MAX_NAME_LEN:
        return Finding(skill_name, 3, "FAIL", f"Name '{name}' exceeds {MAX_NAME_LEN} chars")
    if not re.match(r"^[a-z0-9][a-z0-9-]*$", name):
        return Finding(
            skill_name,
            3,
            "FAIL",
            f"Name '{name}' must be lowercase letters, numbers, and hyphens only",
        )
    if name != dir_name:
        return Finding(
            skill_name,
            3,
            "FAIL",
            f"Name '{name}' does not match directory name '{dir_name}'",
        )
    return Finding(skill_name, 3, "PASS", f"Name '{name}' valid")


def rule_04_description_trigger(skill_name: str, fm: dict) -> Finding:
    """Description must be present and trigger-oriented."""
    desc = fm.get("description")
    if not desc:
        return Finding(skill_name, 4, "FAIL", "Missing 'description' field")
    desc = str(desc)
    if not TRIGGER_PHRASES.search(desc):
        return Finding(
            skill_name,
            4,
            "WARN",
            "Description missing trigger phrase (e.g. 'Use when...', 'Triggered by...')",
        )
    return Finding(skill_name, 4, "PASS", "Description has trigger phrase")


def rule_05_description_length(skill_name: str, fm: dict) -> Finding:
    """Description target <=120 chars; WARN above 200 (doing documentation
    work); the Anthropic hard cap is 1024."""
    desc = fm.get("description", "")
    length = len(str(desc))
    if length > WARN_DESC_LEN:
        return Finding(
            skill_name,
            5,
            "WARN",
            f"Description length {length} exceeds {WARN_DESC_LEN} "
            f"(target <={TARGET_DESC_LEN}; hard cap {MAX_DESC_LEN})",
        )
    return Finding(skill_name, 5, "PASS", f"Description length ({length} chars)")


def rule_06_infra_classification(skill_name: str, fm: dict) -> Finding:
    """Infrastructure skills must have disable-model-invocation: true."""
    is_infra = skill_name in INFRA_SKILLS
    has_flag = fm.get("disable-model-invocation") is True
    if is_infra and not has_flag:
        return Finding(
            skill_name,
            6,
            "WARN",
            "Infrastructure skill missing 'disable-model-invocation: true'",
        )
    if has_flag and not is_infra:
        # Not an error — just informational, skill may intentionally disable
        pass
    return Finding(skill_name, 6, "PASS", "Infrastructure classification correct")


def rule_07_background_classification(skill_name: str, fm: dict) -> Finding:
    """Background reference skills must have user-invocable: false."""
    is_bg = skill_name in BACKGROUND_SKILLS
    has_flag = fm.get("user-invocable") is False
    if is_bg and not has_flag:
        return Finding(
            skill_name,
            7,
            "WARN",
            "Background reference skill missing 'user-invocable: false'",
        )
    return Finding(skill_name, 7, "PASS", "Background classification correct")


def rule_08_fork_classification(skill_name: str, fm: dict) -> Finding:
    """Fork skills should have context: fork."""
    is_fork = skill_name in FORK_SKILLS
    has_flag = fm.get("context") == "fork"
    if is_fork and not has_flag:
        return Finding(
            skill_name,
            8,
            "WARN",
            "Fork skill missing 'context: fork'",
        )
    return Finding(skill_name, 8, "PASS", "Fork classification correct")


def rule_09_sub_file_links(skill_name: str, body: str, skill_dir: Path) -> Finding:
    """Markdown links to files must point to existing files in the skill directory."""
    # Match [text](file.md) style links, excluding URLs
    link_re = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
    broken = []
    for _text, href in link_re.findall(body):
        # Skip URLs, anchors, and code blocks
        if href.startswith(("http://", "https://", "#", "mailto:")):
            continue
        # Skip template variables like {review_url}
        if re.search(r"\{[a-zA-Z_]+\}", href):
            continue
        # Strip anchor from path
        path_part = href.split("#")[0]
        if not path_part:
            continue
        target = skill_dir / path_part
        if not target.exists():
            broken.append(href)
    if broken:
        return Finding(
            skill_name,
            9,
            "FAIL",
            f"Broken sub-file links: {', '.join(broken)}",
        )
    return Finding(skill_name, 9, "PASS", "All sub-file links valid")


def rule_10_duplicate_descriptions(
    descriptions: dict[str, str],
) -> list[Finding]:
    """No two skills should have identical descriptions."""
    findings = []
    seen: dict[str, str] = {}  # desc -> first skill
    for skill_name, desc in sorted(descriptions.items()):
        normalized = desc.strip().lower()
        if normalized in seen:
            findings.append(
                Finding(
                    skill_name,
                    10,
                    "WARN",
                    f"Duplicate description with '{seen[normalized]}'",
                )
            )
        else:
            seen[normalized] = skill_name
    return findings


def rule_11_known_fields(skill_name: str, fm: dict) -> Finding:
    """Frontmatter should only contain recognized fields."""
    unknown = set(fm.keys()) - KNOWN_FIELDS
    if unknown:
        return Finding(
            skill_name,
            11,
            "WARN",
            f"Unknown frontmatter fields: {', '.join(sorted(unknown))}",
        )
    return Finding(skill_name, 11, "PASS", "All frontmatter fields recognized")


def rule_12_argument_hint(skill_name: str, fm: dict, body: str) -> Finding:
    """If body uses $ARGUMENTS/$0/$1, argument-hint should be set."""
    uses_args = bool(ARGUMENTS_RE.search(body))
    has_hint = "argument-hint" in fm
    if uses_args and not has_hint:
        return Finding(
            skill_name,
            12,
            "WARN",
            "Uses $ARGUMENTS but missing 'argument-hint' field",
        )
    return Finding(skill_name, 12, "PASS", "Argument hint check passed")


def rule_13_coupling_signals(
    skill_name: str, body: str, sub_file_text: str = ""
) -> Finding:
    """Global skill bodies with ai-repo coupling MUST defer to the skill-context seam.

    A skill under skills-global/ ships to every machine and runs in every repo.
    If its body — OR any of its bundled `*.md` sub-files, which hardlink to every
    machine too — contains any token from COUPLING_SIGNALS (the EXECUTABLE/IMPORT
    set: sdlc-tool, python -m tools.*, reflections.*, valor-*, config/identity.json)
    that actually errors or silently misfires in a foreign repo, it leaks this
    repo's specifics into every repo, UNLESS it carries the canonical probe step
    (PROBE_SUFFIX), which makes the body defer to the per-repo skill-context seam
    (docs/sdlc/{skill}.md for SDLC skills, or .claude/skill-context/{skill}.md).

    Signals are scanned across the union of SKILL.md body + sub-file text, but
    probe coverage is read from `body` (SKILL.md) ONLY — a probe buried in a
    sub-file does not certify the skill defers.

    The signal set is intentionally executable-only: weak doc-path/branch-name
    mentions (docs/features/, docs/plans/, session/{slug}) are NOT coupling — a
    see-also markdown link does not break execution elsewhere — and including
    them false-positived Bucket A skills the plan declares clean (plan Risk 2).

    Emits severity FAIL (not WARN) for a genuine violation: main() returns a
    non-zero exit code only when summary["fail"] > 0, so a WARN would never trip
    the red-state exit this regression guard depends on. A clean or properly
    probed body returns PASS. Deterministic on empty/garbage input — never raises.

    The caller (audit_skill) applies this rule to the "global" root only —
    project-only skills run solely in this repo and may couple freely.
    """
    body = body or ""
    scan = body + "\n" + (sub_file_text or "")
    matched = [sig for sig in COUPLING_SIGNALS if sig in scan]
    if not matched:
        return Finding(skill_name, 13, "PASS", "No ai-repo coupling signals in body")
    if PROBE_SUFFIX in body:
        return Finding(
            skill_name,
            13,
            "PASS",
            f"Coupling signals present but body defers via probe step ({', '.join(matched)})",
        )
    return Finding(
        skill_name,
        13,
        "FAIL",
        f"Coupling signals without skill-context probe step: {', '.join(matched)}",
    )


def _iter_non_fenced_lines(text: str):
    """Yield each physical line of `text` that is NOT inside a ``` fenced block.

    A coupling token inside a code fence is a usage demonstration (it cannot
    carry same-line prose framing), not a behavioral-coupling claim, so rule_21
    skips fenced lines. The fence delimiter lines themselves are skipped too.
    """
    in_fence = False
    for line in (text or "").split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            yield line


def _bucket_c_line_signals(line: str, project_only: set[str]) -> list[str]:
    """Return the Bucket-C signals present on a single line (Signal A + B)."""
    signals: list[str] = []
    for m in SLASH_TOKEN_RE.finditer(line):
        token = m.group(1)
        if token in project_only:
            signals.append(f"/{token}")
    for infra in BUCKET_C_INFRA_TOKENS:
        if infra in line:
            signals.append(infra)
    return signals


def _line_has_conditional_cover(line: str) -> bool:
    """True when `line` carries same-line conditional/probe framing."""
    low = line.lower()
    if PROBE_SUFFIX.lower() in low:
        return True
    return any(marker in low for marker in CONDITIONAL_MARKERS)


def rule_21_bucket_c_coupling(
    skill_name: str,
    body: str,
    project_only_names: set[str] | frozenset[str] | None,
    sub_file_text: str = "",
) -> Finding:
    """Global skill bodies must not invoke project-only skills or internal-infra tokens.

    rule_13 catches EXECUTABLE/IMPORT references; rule_21 catches two classes it
    misses, both of which leak this repo's specifics to every machine:

    - Signal A — a slash-invocation whose FULL token exactly names a project-only
      skill (`.claude/skills/`, derived live and passed in as `project_only_names`).
      Those skills never sync, so `/sdlc` on a foreign machine resolves to nothing.
      Matched via full-token capture + exact set-membership (never substring), so
      `/do-deploy-example` — a legit global skill — is not confused for `/do-deploy`.
    - Signal B — a curated internal-infra token (`sdk_client.py`, `SDLC_TARGET_REPO`).

    Signals are scanned across the union of SKILL.md `body` + `sub_file_text`,
    frontmatter already stripped, with fenced code blocks skipped. A signal is
    covered when its OWN physical line carries conditional framing (`in this
    repo`, `this repo's`, or the canonical PROBE_SUFFIX). Same-line (not
    whole-file) coverage is the deliberate strictness that separates rule_21 from
    rule_13: a probe elsewhere in the doc cannot excuse an unrelated bare `/sdlc`.

    Emits FAIL (not WARN) for an uncovered signal so main() red-states. Returns
    PASS on empty/None/whitespace-only input and never raises. The caller applies
    this to the "global" root only, skips project-only skills, and self-exempts
    the `do-skills-audit` skill (whose rule inventory documents these very tokens).
    """
    project_only = set(project_only_names or ())
    uncovered: list[str] = []
    for source in ((body or ""), (sub_file_text or "")):
        for line in _iter_non_fenced_lines(source):
            signals = _bucket_c_line_signals(line, project_only)
            if signals and not _line_has_conditional_cover(line):
                uncovered.extend(signals)
    if uncovered:
        return Finding(
            skill_name,
            21,
            "FAIL",
            "Bucket-C coupling without same-line conditional cover: "
            + ", ".join(sorted(set(uncovered))),
        )
    return Finding(skill_name, 21, "PASS", "No uncovered Bucket-C coupling signals")


def _project_only_skill_names() -> set[str]:
    """Project-only skill names (.claude/skills/) that are NOT also global skills.

    Derived live from the filesystem so the Bucket-C rule stays repo-agnostic: a
    foreign repo with no .claude/skills/ yields an empty set and Signal A never
    fires. Names present under BOTH roots are excluded — a global skill of the
    same name is legitimately invocable, so it must not be flagged.
    """
    if not PROJECT_SKILLS_DIR.is_dir():
        return set()
    project = {
        d.name
        for d in PROJECT_SKILLS_DIR.iterdir()
        if d.is_dir() and (d / "SKILL.md").exists()
    }
    if SKILLS_DIR.is_dir():
        global_names = {
            d.name
            for d in SKILLS_DIR.iterdir()
            if d.is_dir() and (d / "SKILL.md").exists()
        }
        project -= global_names
    return project


def _gather_sub_file_text(skill_dir: Path) -> str:
    """Concatenated text of every `*.md` sub-file (excluding SKILL.md).

    Non-.md files (especially `.py`) are excluded so this repo's own
    audit_skills.py token literals (COUPLING_SIGNALS, BUCKET_C_INFRA_TOKENS) are
    never scanned as leaks. Used only for coupling signal DETECTION; probe/
    conditional coverage is read from SKILL.md, not from sub-files.
    """
    parts: list[str] = []
    for p in sorted(skill_dir.rglob("*.md")):
        if p.name == "SKILL.md" or "__pycache__" in p.parts:
            continue
        try:
            parts.append(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return "\n".join(parts)


def rule_14_fleet_description_budget(descriptions: dict[str, str]) -> Finding:
    """All skill descriptions ship in every session's context. The fleet total
    must stay within FLEET_DESC_BUDGET chars (~2% of context)."""
    total = sum(len(d) for d in descriptions.values())
    if total > FLEET_DESC_BUDGET:
        return Finding(
            "(fleet)",
            14,
            "WARN",
            f"Fleet description total {total} chars exceeds budget {FLEET_DESC_BUDGET} "
            f"({len(descriptions)} skills; per-skill target <={TARGET_DESC_LEN})",
        )
    return Finding(
        "(fleet)",
        14,
        "PASS",
        f"Fleet description total {total}/{FLEET_DESC_BUDGET} chars",
    )


def rule_15_asset_rot(skill_name: str, body: str, skill_dir: Path) -> Finding:
    """Path-like tokens in the body must resolve. Skills rot as repos move:
    a body citing scripts/foo.py or .claude/x/y.md that no longer exists will
    error or silently misfire at invocation time.

    Unambiguous self-references (.claude/skills*/<this-skill>/...) missing ->
    FAIL. Everything else missing -> WARN: bare scripts/references/assets
    tokens may be cross-skill mentions, create-this-file instructions, or
    foreign-repo generics — triage material, never auto-filed as issues.
    Seam paths and placeholder tokens are skipped (conditional by convention).
    """
    body = body or ""
    missing_self: list[str] = []
    missing_other: list[str] = []
    self_prefixes = (
        f".claude/skills-global/{skill_name}/",
        f".claude/skills/{skill_name}/",
    )
    for token in sorted(set(ASSET_TOKEN_RE.findall(body))):
        if any(ch in token for ch in PLACEHOLDER_CHARS):
            continue
        normalized = token[2:] if token.startswith("./") else token
        if any(normalized.startswith(p) for p in SEAM_PREFIXES):
            continue  # conditional by convention: "If <seam-path> exists, ..."
        if (
            (skill_dir / normalized).exists()
            or (REPO_ROOT / normalized).exists()
            or (Path.home() / normalized).exists()
        ):
            continue
        if normalized.startswith(self_prefixes):
            missing_self.append(token)
        else:
            missing_other.append(token)
    if missing_self:
        return Finding(
            skill_name,
            15,
            "FAIL",
            f"Body references missing own assets: {', '.join(missing_self)}",
        )
    if missing_other:
        return Finding(
            skill_name,
            15,
            "WARN",
            f"Body references unresolvable paths: {', '.join(missing_other)}",
        )
    return Finding(skill_name, 15, "PASS", "All referenced assets resolve")


def rule_16_junk_files(skill_name: str, skill_dir: Path, tracked_files: set[str] | None) -> Finding:
    """No auxiliary/junk files in skill directories (per Anthropic guidance:
    no README.md, CHANGELOG.md, etc.). Only git-TRACKED junk is flagged —
    untracked build artifacts (test-import __pycache__) are gitignored noise."""
    if tracked_files is None:
        return Finding(skill_name, 16, "PASS", "Junk check skipped (git unavailable)")
    try:
        prefix = str(skill_dir.relative_to(REPO_ROOT))
    except ValueError:
        return Finding(skill_name, 16, "PASS", "Junk check skipped (outside repo)")
    junk: list[str] = []
    for rel in tracked_files:
        if not rel.startswith(prefix + "/"):
            continue
        name = rel.rsplit("/", 1)[-1]
        if name in JUNK_NAMES or name.endswith(JUNK_SUFFIXES) or "__pycache__" in rel:
            junk.append(rel[len(prefix) + 1 :])
    if junk:
        return Finding(
            skill_name,
            16,
            "WARN",
            f"Tracked junk files in skill dir: {', '.join(sorted(junk))}",
        )
    return Finding(skill_name, 16, "PASS", "No junk files tracked in skill dir")


def _desc_word_set(desc: str) -> frozenset[str]:
    words = re.findall(r"[a-z][a-z0-9'-]+", desc.lower())
    return frozenset(w for w in words if w not in DESC_STOPWORDS)


def rule_17_near_duplicate_descriptions(descriptions: dict[str, str]) -> list[Finding]:
    """Two skills whose descriptions share most content words have colliding
    trigger surfaces — the model can't reliably pick between them. Catches
    what rule 10's exact-match misses."""
    findings: list[Finding] = []
    items = sorted(descriptions.items())
    word_sets = {name: _desc_word_set(desc) for name, desc in items}
    for i, (name_a, _) in enumerate(items):
        set_a = word_sets[name_a]
        if not set_a:
            continue
        for name_b, _ in items[i + 1 :]:
            set_b = word_sets[name_b]
            if not set_b:
                continue
            jaccard = len(set_a & set_b) / len(set_a | set_b)
            if jaccard >= NEAR_DUP_THRESHOLD:
                findings.append(
                    Finding(
                        name_b,
                        17,
                        "WARN",
                        f"Trigger surface overlaps '{name_a}' "
                        f"(similarity {jaccard:.0%}) — merge candidates or sharpen descriptions",
                    )
                )
    return findings


def rule_18_unreferenced_sub_files(skill_name: str, body: str, skill_dir: Path) -> Finding:
    """Every file bundled in a skill dir should be referenced by SKILL.md or by
    a sibling file (scripts reading references/, etc.). Unreferenced files are
    dead weight that still syncs to every machine."""
    sub_files = [
        p
        for p in skill_dir.rglob("*")
        if p.is_file()
        and p.name != "SKILL.md"
        and "__pycache__" not in p.parts
        and p.name != ".DS_Store"
    ]
    if not sub_files:
        return Finding(skill_name, 18, "PASS", "No sub-files")
    # Corpus: SKILL.md body plus every text sub-file's content (a file counts
    # as referenced when any OTHER file mentions its name).
    texts: dict[str, str] = {"SKILL.md": body or ""}
    for p in sub_files:
        if p.suffix in {".py", ".sh", ".md", ".json", ".yaml", ".yml", ".txt"}:
            try:
                texts[str(p.relative_to(skill_dir))] = p.read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                continue
    unreferenced = []
    for p in sub_files:
        rel = str(p.relative_to(skill_dir))
        referenced = any(p.name in text for owner, text in texts.items() if owner != rel)
        if not referenced:
            unreferenced.append(rel)
    if unreferenced:
        return Finding(
            skill_name,
            18,
            "WARN",
            "Sub-files never referenced by SKILL.md or siblings: "
            + ", ".join(sorted(unreferenced)),
        )
    return Finding(skill_name, 18, "PASS", "All sub-files referenced")


def rule_19_husk_directories(skills_dir: Path, dir_label: str) -> list[Finding]:
    """A directory in a skills root without SKILL.md is not a skill — it is a
    leftover from a move/rename (metadata husks, orphaned sub-files). Dirs
    starting with '_' (shared assets) are exempt."""
    findings: list[Finding] = []
    if not skills_dir.is_dir():
        return findings
    for d in sorted(skills_dir.iterdir()):
        if not d.is_dir() or d.name.startswith(("_", ".")):
            continue
        if (d / "SKILL.md").exists():
            continue
        contents = [
            str(p.relative_to(d))
            for p in d.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.name != ".DS_Store"
        ]
        detail = f" (contains: {', '.join(sorted(contents)[:5])})" if contents else " (empty)"
        findings.append(
            Finding(
                d.name,
                19,
                "FAIL",
                f"Husk directory: no SKILL.md{detail} — delete or restore",
                dir=dir_label,
            )
        )
    return findings


def _is_empty_husk(d: Path) -> bool:
    """Same "empty" predicate as rule_19_husk_directories: a directory with no
    SKILL.md and no files besides build artifacts (__pycache__, .DS_Store)."""
    contents = [
        p
        for p in d.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.name != ".DS_Store"
    ]
    return not contents


def prune_husk_directories(skills_dir: Path, dir_label: str) -> list[str]:
    """Auto-fix companion to rule_19_husk_directories: actually remove husks.

    Uses the identical "empty" predicate as rule 19 — a directory lacking
    SKILL.md whose only contents (if any) are build artifacts (__pycache__,
    .DS_Store). Only those genuinely-empty husks are pruned; a husk that still
    holds real orphaned files is left on disk for a human delete-or-restore
    decision (and will still surface as a rule 19 FAIL).

    Returns a list of human-readable descriptions of husks actually removed,
    suitable for reporting as "Fixed: ..." findings. Never raises — a failed
    rmtree on one husk is logged and skipped so the sweep continues.
    """
    removed: list[str] = []
    if not skills_dir.is_dir():
        return removed
    for d in sorted(skills_dir.iterdir()):
        if not d.is_dir() or d.name.startswith(("_", ".")):
            continue
        if (d / "SKILL.md").exists():
            continue
        if not _is_empty_husk(d):
            continue
        # TOCTOU guard: re-check immediately before the irreversible delete —
        # a file could have landed in the directory since the scan above.
        if not _is_empty_husk(d):
            continue
        resolved = d.resolve()
        logger.warning("Pruning empty husk directory: %s", resolved)
        try:
            shutil.rmtree(d)
        except OSError:
            continue
        removed.append(f"Removed husk directory: {d.name} ({dir_label})")
    return removed


def rule_20_user_level_orphans(repo_skill_dirs: dict[str, Path]) -> list[Finding]:
    """Skills in ~/.claude/skills/ should be hardlink-synced from a repo source.
    A user-level skill with no repo source is an orphan (stale leftover or
    unsynced personal skill); one whose SKILL.md diverged from its repo source
    is a broken sync."""
    findings: list[Finding] = []
    if not USER_SKILLS_DIR.is_dir():
        return findings
    for d in sorted(USER_SKILLS_DIR.iterdir()):
        user_md = d / "SKILL.md"
        if not d.is_dir() or d.name.startswith(("_", ".")) or not user_md.exists():
            continue
        repo_dir = repo_skill_dirs.get(d.name)
        if repo_dir is None:
            findings.append(
                Finding(
                    d.name,
                    20,
                    "WARN",
                    "User-level skill has no source in this repo "
                    "(stale orphan, or personal/foreign skill — disposition needed)",
                    dir="user",
                )
            )
            continue
        repo_md = repo_dir / "SKILL.md"
        try:
            same = user_md.stat().st_ino == repo_md.stat().st_ino or (
                user_md.read_bytes() == repo_md.read_bytes()
            )
        except OSError:
            continue
        if not same:
            findings.append(
                Finding(
                    d.name,
                    20,
                    "WARN",
                    "User-level copy diverged from repo source (sync broken — re-run /update)",
                    dir="user",
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Fix helpers
# ---------------------------------------------------------------------------


def apply_fixes(skill_path: Path, fm: dict, text: str, dir_name: str) -> list[str]:
    """Apply trivial auto-fixes. Returns list of descriptions of what was fixed."""
    fixes = []
    modified = False

    # Fix 1: Add missing name field
    if "name" not in fm:
        fm["name"] = dir_name
        modified = True
        fixes.append(f"Added name: {dir_name}")

    # Fix 2: Trim trailing whitespace in frontmatter string values
    for key, value in fm.items():
        if isinstance(value, str) and value != value.strip():
            fm[key] = value.strip()
            modified = True
            fixes.append(f"Trimmed whitespace in '{key}'")

    if modified:
        # Rebuild the file
        fm_text = yaml.dump(fm, default_flow_style=False, allow_unicode=True).strip()
        # Preserve body
        match = re.match(r"^---\n.*?\n---\n?(.*)", text, re.DOTALL)
        body = match.group(1) if match else text
        new_text = f"---\n{fm_text}\n---\n{body}"
        skill_path.write_text(new_text, encoding="utf-8")

    # Fix 3: Remove untracked build artifacts from the skill dir
    skill_dir = skill_path.parent
    for junk in list(skill_dir.rglob("__pycache__")) + list(skill_dir.rglob(".DS_Store")):
        try:
            if junk.is_dir():
                shutil.rmtree(junk)
            else:
                junk.unlink()
            fixes.append(f"Removed {junk.relative_to(skill_dir)}")
        except OSError:
            continue

    return fixes


# ---------------------------------------------------------------------------
# Main audit logic
# ---------------------------------------------------------------------------


def discover_skills(skills_dir: Path, single: str | None = None) -> list[Path]:
    """Find all SKILL.md files."""
    if single:
        target = skills_dir / single / "SKILL.md"
        if target.exists():
            return [target]
        return []
    return sorted(skills_dir.glob("*/SKILL.md"))


def _git_tracked_files() -> set[str] | None:
    """Repo-relative tracked paths, or None when git is unavailable."""
    try:
        proc = subprocess.run(
            ["git", "ls-files"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(REPO_ROOT),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return set(proc.stdout.splitlines())


def audit_skill(
    skill_path: Path,
    report: AuditReport,
    do_fix: bool = False,
    dir_label: str = "",
    tracked_files: set[str] | None = None,
) -> dict[str, str]:
    """Audit a single skill. Returns {skill_name: description} for cross-skill checks."""
    skill_dir = skill_path.parent
    dir_name = skill_dir.name
    text = skill_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    fm, body = parse_frontmatter(text)

    # Apply fixes first if requested
    if do_fix:
        fixes = apply_fixes(skill_path, fm, text, dir_name)
        for fix in fixes:
            report.add(Finding(dir_name, 0, "PASS", f"Fixed: {fix}", dir=dir_label))
        # Re-read after fixes
        if fixes:
            text = skill_path.read_text(encoding="utf-8")
            lines = text.splitlines()
            fm, body = parse_frontmatter(text)

    # rules 13 + 21 guard the sync boundary: they apply to skills that ship to
    # every machine ("global" root, or unlabeled for direct/test invocations).
    # Project-only skills run solely in this repo and may couple freely.
    #
    # do-skills-audit self-exempts from the sub-file scan (and from rule_21
    # entirely): its own rule-inventory docs describe these coupling signals, so
    # scanning it against them would self-trip a FAIL on the docs that explain
    # the rule. It is this repo's own tooling, never shipped for foreign semantics.
    is_auditor = dir_name == "do-skills-audit"
    sub_file_text = "" if is_auditor else _gather_sub_file_text(skill_dir)
    if dir_label == "project":
        coupling = Finding(dir_name, 13, "PASS", "Project-only skill; local coupling allowed")
        bucket_c = Finding(dir_name, 21, "PASS", "Project-only skill; local coupling allowed")
    else:
        coupling = rule_13_coupling_signals(dir_name, body, sub_file_text)
        if is_auditor:
            bucket_c = Finding(
                dir_name, 21, "PASS", "Auditor skill self-exempt (documents these signals)"
            )
        else:
            bucket_c = rule_21_bucket_c_coupling(
                dir_name, body, _project_only_skill_names(), sub_file_text
            )

    per_skill = [
        rule_01_line_count(dir_name, lines),
        rule_02_frontmatter_exists(dir_name, fm),
        rule_03_name_field(dir_name, fm, dir_name),
        rule_04_description_trigger(dir_name, fm),
        rule_05_description_length(dir_name, fm),
        rule_06_infra_classification(dir_name, fm),
        rule_07_background_classification(dir_name, fm),
        rule_08_fork_classification(dir_name, fm),
        rule_09_sub_file_links(dir_name, body, skill_dir),
        rule_11_known_fields(dir_name, fm),
        rule_12_argument_hint(dir_name, fm, body),
        coupling,
        bucket_c,
        rule_15_asset_rot(dir_name, body, skill_dir),
        rule_16_junk_files(dir_name, skill_dir, tracked_files),
        rule_18_unreferenced_sub_files(dir_name, body, skill_dir),
    ]
    for f in per_skill:
        f.dir = f.dir or dir_label
        report.add(f)

    desc = str(fm.get("description", ""))
    return {dir_name: desc} if desc else {}


def run_sync(args: argparse.Namespace, report_json: dict | None = None) -> str | None:
    """Run the best practices sync script if available."""
    sync_script = Path(__file__).parent / "sync_best_practices.py"
    if not sync_script.exists():
        return "⚠️  sync_best_practices.py not found — skipping best practices sync"

    cmd = [sys.executable, str(sync_script)]
    if args.apply:
        cmd.append("--apply")
    if args.update_skills:
        cmd.append("--update-skills")
    if args.force_refresh:
        cmd.append("--force-refresh")
    if args.json:
        cmd.append("--json")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(REPO_ROOT),
        )
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return "⚠️  Best practices sync timed out after 60s"
    except Exception as e:
        return f"⚠️  Best practices sync failed: {e}"


def format_human(report: AuditReport, sync_output: str | None = None) -> str:
    """Format report as human-readable table."""
    lines = [
        "Skills Audit Report",
        "═══════════════════",
        "",
    ]

    # Group by skill
    by_skill: dict[str, list[Finding]] = {}
    for f in report.results:
        by_skill.setdefault(f.skill, []).append(f)

    for skill in sorted(by_skill):
        findings = by_skill[skill]
        # Only show non-PASS findings in compact mode, or all if few
        non_pass = [f for f in findings if f.severity != "PASS"]
        if non_pass:
            for f in non_pass:
                icon = "⚠️ " if f.severity == "WARN" else "❌"
                lines.append(
                    f"  {icon} {f.severity:<4}  {f.skill:<30}  Rule {f.rule:>2}: {f.message}"
                )
        else:
            lines.append(f"  ✅ PASS  {skill:<30}  All {len(findings)} rules passed")

    lines.append("")
    lines.append(
        f"Summary: {report.skills_audited} skills audited | "
        f"{report.summary['pass']} PASS | "
        f"{report.summary['warn']} WARN | "
        f"{report.summary['fail']} FAIL"
    )

    if sync_output:
        lines.append("")
        lines.append(sync_output)

    return "\n".join(lines)


def format_json(report: AuditReport, sync_output: str | None = None, desc_total: int = 0) -> str:
    """Format report as JSON (contract documented in the module docstring)."""
    findings = [asdict(f) for f in report.results]
    data = {
        "skills_audited": report.skills_audited,  # legacy alias
        "findings": findings,
        "results": findings,  # legacy alias
        "summary": {
            **report.summary,
            "total_skills": report.skills_audited,
            "description_total_chars": desc_total,
            "description_budget": FLEET_DESC_BUDGET,
        },
    }
    if sync_output:
        data["best_practices_sync"] = sync_output
    return json.dumps(data, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Claude Code skills")
    parser.add_argument("--fix", action="store_true", help="Auto-fix trivial issues")
    parser.add_argument("--json", action="store_true", help="JSON output only")
    parser.add_argument("--skill", type=str, help="Audit a single skill by name")
    parser.add_argument("--no-sync", action="store_true", help="Skip best practices sync")
    parser.add_argument("--apply", action="store_true", help="Apply best practices updates")
    parser.add_argument(
        "--update-skills",
        action="store_true",
        help="Update existing skills to match best practices",
    )
    parser.add_argument("--force-refresh", action="store_true", help="Bypass doc cache")
    args = parser.parse_args()

    report = AuditReport()

    # Roots: the global fleet plus this repo's project-only skills. A foreign
    # repo typically has only .claude/skills/ — missing roots are skipped.
    roots: list[tuple[str, Path]] = []
    if SKILLS_DIR.is_dir():
        roots.append(("global", SKILLS_DIR))
    if PROJECT_SKILLS_DIR.is_dir():
        roots.append(("project", PROJECT_SKILLS_DIR))

    skill_paths: list[tuple[str, Path]] = []
    for label, root in roots:
        found = discover_skills(root, args.skill)
        skill_paths.extend((label, p) for p in found)
        if args.skill and found:
            break  # single-skill mode: first root wins

    if not skill_paths:
        print(f"No skills found{f' matching {args.skill!r}' if args.skill else ''}")
        return 1

    report.skills_audited = len(skill_paths)
    tracked_files = _git_tracked_files()

    # Audit each skill, collecting descriptions for cross-skill checks
    all_descriptions: dict[str, str] = {}
    repo_skill_dirs: dict[str, Path] = {}
    for label, sp in skill_paths:
        descs = audit_skill(
            sp, report, do_fix=args.fix, dir_label=label, tracked_files=tracked_files
        )
        all_descriptions.update(descs)
        repo_skill_dirs.setdefault(sp.parent.name, sp.parent)

    # Fleet-level rules (full-fleet runs only)
    if not args.skill:
        for f in rule_10_duplicate_descriptions(all_descriptions):
            report.add(f)
        report.add(rule_14_fleet_description_budget(all_descriptions))
        for f in rule_17_near_duplicate_descriptions(all_descriptions):
            report.add(f)
        if args.fix:
            for label, root in roots:
                for desc in prune_husk_directories(root, label):
                    report.add(Finding(root.name, 0, "PASS", f"Fixed: {desc}", dir=label))
        for label, root in roots:
            for f in rule_19_husk_directories(root, label):
                report.add(f)
        for f in rule_20_user_level_orphans(repo_skill_dirs):
            report.add(f)

    # Best practices sync
    sync_output = None
    if not args.no_sync:
        sync_output = run_sync(args)

    # Output
    desc_total = sum(len(d) for d in all_descriptions.values())
    if args.json:
        print(format_json(report, sync_output, desc_total=desc_total))
    else:
        print(format_human(report, sync_output))

    return 1 if report.summary["fail"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
