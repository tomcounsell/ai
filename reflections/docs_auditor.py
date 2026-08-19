"""
reflections/docs_auditor.py — Unified documentation auditor substrate.

Consolidates five disjointed docs-hygiene pieces into one substrate consumed
by two callers:
  * ``run_docs_auditor`` — daily rotation reflection (Caller A)
  * ``audit()`` — synchronous public API used by the ``/do-docs`` SDLC stage
    (Caller B) via ``python -c "from reflections.docs_auditor import audit; ..."``

Public surface:
  * ``audit(primary_path, *, scope_mode, apply_mode, project_key)`` — main entrypoint
  * ``run_docs_auditor()`` — reflection callable (rotation + Telegram)
  * ``run_docs_branch_sweeper()`` — reflection callable (branch/PR cleanup)
  * ``refresh_docs_in_memory(touched_paths)`` — no-op placeholder for #1249
  * ``STALE_TERMS`` — module-level dict; edit one place to extend

Reflection callables return ``{"status": ..., "findings": [...], "summary": str}``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from config.machine import get_machine_display_name
from config.settings import settings

logger = logging.getLogger("reflections.docs_auditor")

PROJECT_ROOT = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# Module-level configuration
# ---------------------------------------------------------------------------

# Stale-term renames. Edit this dict to extend coverage. Keys are old terms,
# values are the canonical replacement.
STALE_TERMS: dict[str, str] = {
    "SessionLog": "AgentSession",
    "RedisJob": "AgentSession",
    "session_log": "agent_session",
    "redis_job": "agent_session",
}

# Curated vault-relative-path -> (site_page, repo_doc) mapping for the vault<->site/docs
# drift audit. NOT a full-vault walk — only these ~10 canonical narratives are ever
# compared. repo_doc may be None if there is no repo-doc counterpart.
VAULT_SITE_MAPPING: dict[str, tuple[str, str | None]] = {
    "Valor AI System Overview.md": ("site/index.html", None),
    "managed-agents-x-valor-report.md": ("site/research.html", None),
    "valor-x-paperclip-report.md": ("site/research.html", None),
    "ma-x-perplexity-computer-report.md": ("site/research.html", None),
    "openhuman-vs-hermes.md": ("site/research.html", None),
    "Personas/Valor Engels.md": ("site/index.html", None),
    "Personas/HG Wells – Head of Operations.md": ("site/runtime.html", None),
    "Personas/Jules Verne – Head of Engineering.md": ("site/runtime.html", None),
    "Personas/Philip Pullman – Head of Product.md": ("site/runtime.html", None),
}

# Hard caps and tunables.
NEIGHBORHOOD_CAP = 20
VAULT_DRIFT_ISSUE_CAP = 5
LOCK_TTL_SECONDS = 3600
SWEEPER_LOCK_TTL_SECONDS = 1800
STUB_DOC_LINE_THRESHOLD = 5
STALE_BRANCH_AGE_DAYS = 7
STALE_PR_AGE_DAYS = 14

# Per-run cap shared by audit()'s advisory issue-filing loop and the rotation
# withheld-fix filing loop. A separate budget from VAULT_DRIFT_ISSUE_CAP, which
# bounds only _run_vault_drift_detection's own pre-rotation loop — the two are
# deliberately never merged (R5-2), so the true module-wide ceiling for one
# rotation run is ISSUE_FILING_PER_RUN_CAP (advisory) + VAULT_DRIFT_ISSUE_CAP
# (vault-drift) + ISSUE_FILING_PER_RUN_CAP (withheld) = 15 issues, plus at most
# one operational-failure filing.
ISSUE_FILING_PER_RUN_CAP = 5

# Finding categories whose underlying condition can recur after a human closes
# the issue that reported it — a recurring Redis/vault comparison or a run
# outcome, never a durable property of the tree. `_file_issue_if_new` selects
# `states="open"` for these so a closed issue never silences a fresh
# occurrence; every other category is "all", matched once, ever.
_RECURRING_CONDITION_CATEGORIES = frozenset({"vault-drift", "operational-failure"})

# Marker stamped into a docs-audit PR body when the existence invariant withheld
# any fix on the run that opened it. Every docs-audit PR requires a human
# merge (`/do-merge`) — the rotation path opens no code path that lands a
# commit unreviewed — and the sweeper reads this marker to exempt a withheld
# PR from its stale-close at STALE_PR_AGE_DAYS, since closing it would
# discard fixes that already passed the existence invariant. A withheld fix
# means the auditor wanted to write something wrong; that needs a human read
# before the surviving fixes are merged.
#
# This is a *conditional* instance of the "review requirement" option that #2726
# defers — it applies only on the withheld path, leaves clean-run commit/staging
# behavior untouched, and becomes a no-op if #2726 later adopts a wholesale
# stage-and-report or explicit-path-list policy. Recorded on #2726 itself so the
# owner rules with the shipped partial gate in view, not against a blank slate.
#
# NOTE: The marker lives in the PR body with no cross-check, so a human
# `gh pr edit` that rewrites the body loses the sweeper's stale-close
# exemption. A `do-not-close` label would be sturdier, but the label does not
# exist in this repo and `gh pr create --label` fails outright when it is
# missing — a worse failure than the human-only path this guards. No
# automation here runs `gh pr edit`.
WITHHELD_PR_MARKER = "<!-- docs-auditor:fixes-withheld -->"

# Redis key namespace for state/locks/liveness.
REDIS_LAST_RUN_HASH = "docs_audit:last_run"
REDIS_RUNNING_KEY = "docs_audit:running:global"
REDIS_SWEEPER_RUNNING_KEY = "docs_audit:sweeper:running"
REDIS_LAST_COMPLETED_TS_KEY = "docs_audit:last_completed_run_ts"
REDIS_LAST_COMPLETED_SUMMARY_KEY = "docs_audit:last_completed_run_summary"
REDIS_ISSUE_DEDUP_PREFIX = "docs_audit:issues_filed"
REDIS_DAILY_PR_KEY = "docs_audit:prs_today"  # capped at 1 PR per calendar day


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------


def _ok_result(
    status: str,
    files_touched: list[str] | None = None,
    fixes_applied: int = 0,
    issues_filed: int = 0,
    pr_url: str | None = None,
    fixes_withheld: int = 0,
    withheld: list[dict] | None = None,
    extras: dict | None = None,
) -> dict:
    """Build the standard substrate return value.

    ``fixes_withheld`` / ``withheld`` carry fixes the existence invariant rejected.
    ``status`` stays ``"ok"`` for a withheld fix — it is not an error — which is
    exactly why callers must branch on ``fixes_withheld > 0`` rather than trust
    ``status`` alone.
    """
    res: dict = {
        "status": status,
        "files_touched": files_touched or [],
        "fixes_applied": fixes_applied,
        "issues_filed": issues_filed,
        "pr_url": pr_url,
        "fixes_withheld": fixes_withheld,
        "withheld": withheld or [],
    }
    if extras:
        res.update(extras)
    return res


# ---------------------------------------------------------------------------
# Redis helpers
# ---------------------------------------------------------------------------


def _get_redis():
    """Return the shared Popoto Redis connection (lazy import)."""
    from popoto.redis_db import POPOTO_REDIS_DB

    return POPOTO_REDIS_DB


def _acquire_lock(key: str = REDIS_RUNNING_KEY, ttl: int = LOCK_TTL_SECONDS) -> bool:
    """Acquire a SETNX lock. Returns True on success, False if already held."""
    try:
        r = _get_redis()
        return bool(r.set(key, "1", nx=True, ex=ttl))
    except Exception as e:
        logger.warning(f"docs_auditor: lock acquire failed for {key}: {e}")
        return False


def _release_lock(key: str = REDIS_RUNNING_KEY) -> None:
    """Release a previously acquired lock. Best-effort."""
    try:
        _get_redis().delete(key)
    except Exception as e:
        logger.warning(f"docs_auditor: lock release failed for {key}: {e}")


# ---------------------------------------------------------------------------
# Auth probes
# ---------------------------------------------------------------------------


def _check_auth() -> tuple[bool, str]:
    """Probe Anthropic auth. Returns (ok, reason).

    On non-auth network errors, returns (True, "") so transient failures do
    not disable the substrate; only invalid keys do.
    """
    try:
        import anthropic as _anth
    except ImportError:
        return False, "anthropic package is not installed"

    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key or key.lower() in ("none", "null", "false", "0"):
        return False, "ANTHROPIC_API_KEY not set"

    try:
        client = _anth.Anthropic(api_key=key)
        client.models.list()
        return True, ""
    except Exception as e:
        err = str(e).lower()
        if "authentication" in err or "api_key" in err or "auth_token" in err:
            return False, f"ANTHROPIC_API_KEY invalid or expired: {e}"
        logger.warning(f"docs_auditor: auth probe non-auth error: {e} — proceeding")
        return True, ""


def _check_embedding_auth() -> bool:
    """Optional embedding auth probe. Returns True if available, False otherwise.

    Used for graceful degradation: when False, semantic detectors are skipped
    but lexical detectors still run.
    """
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


# ---------------------------------------------------------------------------
# Path slug helpers
# ---------------------------------------------------------------------------


def _path_to_slug(path: str | Path) -> str:
    """Turn a repo-relative path into a stable rotation hash field name."""
    return str(path).replace("/", "_").replace(".", "_")


# ---------------------------------------------------------------------------
# Vault path safety (security-critical — Risk 2)
# ---------------------------------------------------------------------------


def _is_secrets_path(rel_path: str, vault_root: Path) -> bool:
    """Return True if rel_path resolves into a secrets/ path — fail-closed.

    Checks path-COMPONENT equality (not substring), case-insensitive, on BOTH:
      1. The lexical (declared) relative path — because .resolve() can rewrite a
         symlinked "secrets" directory to a real target that does NOT contain the
         "secrets" component, so a resolved-only check could be fooled backwards.
      2. The resolved real path — because a symlink can point INTO a real secrets/
         tree even if its own declared name doesn't say "secrets".
    If resolving `(vault_root / rel_path).resolve().relative_to(vault_root.resolve())`
    raises ValueError (the entry resolves OUTSIDE the vault), treat it as excluded
    (fail-closed) rather than raising or silently including it.

    Component equality (not prefix/substring) means siblings like
    ``secrets-analysis.md`` or ``Secretsandbox/`` are NOT over-matched.
    """
    if any(part.lower() == "secrets" for part in Path(rel_path).parts):
        return True
    try:
        resolved_rel = (vault_root / rel_path).resolve().relative_to(vault_root.resolve())
    except (ValueError, OSError):
        return True  # fail-closed: out-of-vault or unresolvable
    return any(part.lower() == "secrets" for part in resolved_rel.parts)


# ---------------------------------------------------------------------------
# Neighborhood resolution
# ---------------------------------------------------------------------------


def _resolve_neighborhood(
    primary_path: Path,
    repo_root: Path,
    cap: int = NEIGHBORHOOD_CAP,
) -> list[Path]:
    """Expand from primary doc to its neighborhood, capped at ``cap`` files.

    Includes:
      * The primary doc itself
      * Outbound markdown links (from ``[label](path.md)``)
      * Inbound references (other docs linking back)

    Returns a deduplicated list of repo-relative paths, capped at ``cap``.
    """
    neighborhood: list[Path] = [primary_path]
    seen: set[str] = {str(primary_path)}

    full = repo_root / primary_path
    if not full.exists():
        return neighborhood

    try:
        content = full.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return neighborhood

    # Outbound markdown links to .md files
    for m in re.finditer(r"\[[^\]]+\]\(([^)]+\.md)\)", content):
        target = m.group(1).strip()
        # Resolve relative to the primary doc's parent
        target_path = (full.parent / target).resolve()
        try:
            rel = target_path.relative_to(repo_root.resolve())
        except ValueError:
            continue
        rel_str = str(rel)
        if rel_str not in seen:
            seen.add(rel_str)
            neighborhood.append(rel)
            if len(neighborhood) >= cap:
                return neighborhood

    # Inbound references via grep
    try:
        result = subprocess.run(
            ["grep", "-rln", primary_path.name, "docs/"],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(repo_root),
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or "docs/plans/" in line:
                continue
            if line not in seen:
                seen.add(line)
                neighborhood.append(Path(line))
                if len(neighborhood) >= cap:
                    return neighborhood
    except Exception:
        pass

    return neighborhood


def _resolve_pr_changed_files(repo_root: Path) -> list[Path]:
    """Return doc paths changed in the current PR (relative to origin/main).

    Returns an empty list if git is unavailable or the diff is empty.
    """
    try:
        # Determine merge-base with origin/main (or main if no remote).
        base = "origin/main"
        try:
            subprocess.run(
                ["git", "rev-parse", "--verify", base],
                capture_output=True,
                check=True,
                cwd=str(repo_root),
                timeout=settings.timeouts.git_subprocess_s,
            )
        except Exception:
            base = "main"

        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...HEAD"],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(repo_root),
        )
        files = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.endswith(".md") and not line.startswith("docs/plans/"):
                files.append(Path(line))
        return files[:NEIGHBORHOOD_CAP]
    except Exception as e:
        logger.warning(f"docs_auditor: PR-changed-files resolution failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Auto-fix detectors
# ---------------------------------------------------------------------------


def _normalize_prose(text: str | None) -> str:
    """Lowercase, strip backticks, and collapse whitespace — for cue matching only.

    The corpus writes every identifier backticked (``formerly `RedisJob```), which
    is why the pre-#2744 hatch's bare substring tests (``f"formerly {old_term}"``)
    never matched a single live document. Normalizing both the haystack and the
    generated cues makes the hatch see the prose humans actually wrote.

    **Never** use the result to produce output — it is lossy by design. It exists
    solely to answer "does this document record a migration?".
    """
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.replace("`", "").lower())


# Verbs/adjectives that mark a sentence as recording a completed rename. Each fires
# only in combination with the *new* term appearing somewhere in the same document
# (see ``_has_migration_context``), which is what keeps them from exempting prose
# that merely mentions a stale name.
_MIGRATION_CUE_WORDS = (
    "renamed",
    "rename",
    "replaced",
    "replaces",
    "replacing",
    "formerly",
    "earlier",
    "old",
    "alias",
    "superseded",
    "supersedes",
)

# Word-anchored alternation over the cue words. Anchoring is load-bearing, not
# cosmetic: an unanchored substring test fires inside unrelated words ("old" is a
# substring of threshold, holds, placeholder, bold, household), which collapses
# tier 2 of ``_has_migration_context`` into "the document mentions the new term"
# and exempts whole documents by accident.
_MIGRATION_CUE_WORD_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in _MIGRATION_CUE_WORDS) + r")\b"
)


def _migration_cues(old_term: str, new_term: str) -> tuple[str, ...]:
    """Directed migration cues for one ``(old_term, new_term)`` pair, normalized.

    These name *both* terms, so they are conclusive on their own and do not need
    the new term to appear separately. Generated from the pair rather than
    hard-coded, so adding a ``STALE_TERMS`` entry needs no edit here.
    """
    old = _normalize_prose(old_term)
    new = _normalize_prose(new_term)
    return (
        f"renamed to {new}",
        f"replaced by {new}",
        f"now {new}",
        f"formerly {old}",
        f"replaces {old}",
        f"replacing {old}",
        f"earlier {old}",
        f"old {old}",
        f"alias {old}",
        f"alias {new}",
        f"{old} = {new}",
        f"{old} -> {new}",
        f"{old} → {new}",
    )


def _has_migration_context(normalized: str, old_term: str, new_term: str) -> bool:
    """Whether a **whole document** records the ``old_term`` → ``new_term`` migration.

    Two tiers, both evaluated over ``_normalize_prose``'d text:

    1. A *directed* cue naming both (or the specific) terms — ``renamed to X``,
       ``formerly Y``, ``Y = X``, ``Y -> X``, ``Y → X``, ``alias Y`` …
    2. A generic migration cue word (``replacing``, ``earlier``, ``old`` …),
       matched **word-anchored** via ``_MIGRATION_CUE_WORD_RE``, **plus** the new
       term appearing somewhere in the document. Tier 2 is what catches real
       corpus prose whose cue and term sit in different clauses, e.g.
       *"`AgentSession` lands, replacing both the earlier `SessionLog` and
       `RedisJob` models"* — where no directed cue names ``RedisJob`` at all.

    Two things make tier 2 a guard rather than a rubber stamp, and both are
    load-bearing:

    - **The new term must appear.** Prose that merely mentions a stale name
      without ever naming its successor is not a migration record.
    - **The cue words are word-anchored.** A bare ``in`` substring test fires
      inside unrelated words — ``old`` sits inside *threshold*, *holds*,
      *placeholder*, *bold*, *household* — and with that, tier 2 degenerates into
      "the document mentions the new term somewhere". Measured on the live corpus:
      ``docs/guides/summarizer-output-audit.md`` was exempted for ``RedisJob``
      solely because it contains ``summarize_threshold``.
    """
    if any(cue in normalized for cue in _migration_cues(old_term, new_term)):
        return True
    new = _normalize_prose(new_term)
    if new and new in normalized:
        return bool(_MIGRATION_CUE_WORD_RE.search(normalized))
    return False


def _detect_stale_term_fixes(content: str) -> list[tuple[re.Pattern[str], str]]:
    """Detect stale terms from STALE_TERMS dict that lack migration context.

    Matching is **word-anchored** with ``\\b``: a key never matches inside a
    longer run of word characters, so ``session_log`` does not match inside
    ``agent/session_logs.py`` or ``session_log_writer`` (#2711).

    **Paths are never rewritten** (#2744). Word-anchoring alone did not deliver
    that — ``/``, ``.`` and ``-`` are word boundaries, so a key equal to a whole
    path segment used to be rewritten (``models/session_log.py`` →
    ``models/agent_session.py``), a corruption the existence invariant provably
    cannot catch because *both* files exist. Path-token suppression in
    ``_apply_fixes_to_file`` now closes it: a match lying inside a
    ``dir/file.{py,md}``-shaped token is left alone unconditionally.

    **The migration-context hatch is DOCUMENT-scoped, deliberately.** Spike-2 of
    ``docs/plans/docs-auditor-migration-context-and-bare-paths.md`` measured a
    line-scoped (occurrence-scoped) hatch as *strictly worse*: it re-exposed 8
    occurrences that the document scope correctly exempts, because migration
    context in real prose sits in a different sentence from the term it explains.
    Do not "improve" this into a per-occurrence rule.

    Two further gates live at apply time rather than here, because the apply
    loop rewrites the text across iterations and can shift or remove content out
    from under any index computed against ``content``: fence/heading/
    deletion-prose suppression (via ``_build_line_context`` /
    ``_is_documented_deletion``) and path-token suppression. See
    ``_apply_fixes_to_file``.

    Returns fixes on the regex channel — ``(compiled_pattern, replacement)`` —
    so detection and application share one matching semantics. This is
    ``_apply_fixes_to_file``'s only fix channel. The replacement stays a plain
    ``str``; the suppression callable is built at the apply site so the withheld
    record and this channel's contract stay intact.
    """
    normalized = _normalize_prose(content)
    fixes: list[tuple[re.Pattern[str], str]] = []
    for old_term, new_term in STALE_TERMS.items():
        pattern = re.compile(rf"\b{re.escape(old_term)}\b")
        if not pattern.search(content):
            continue
        if not _has_migration_context(normalized, old_term, new_term):
            fixes.append((pattern, new_term))
    return fixes


# Path-shaped reference: ``dir/file.{py,md}`` *or* a bare ``file.{py,md}``. The
# directory segment is optional (`*`, not `+`) so bare filenames enter the existence
# invariant (#2759) — the #2711 corruption shape minus its directory prefix.
_PATH_REF_RE = re.compile(r"(?:[\w.-]+/)*[\w.-]+\.(?:py|md)")

# Basename -> number of owning paths, built once per repo root from the git index.
# Keyed on the resolved ``repo_root`` so distinct checkouts (and distinct test
# ``tmp_path`` roots) never share an index. Cleared at the top of every ``audit()``
# run so a long-lived process does not answer from a stale snapshot.
_BASENAME_INDEX_CACHE: dict[Path, dict[str, int]] = {}


def _repo_basename_index(repo_root: Path) -> dict[str, int]:
    """Map every tracked-or-untracked file's basename to how many paths own it.

    Built from ``git ls-files --cached --others --exclude-standard``: one
    subprocess, ``.gitignore`` respected for free, and files added but not yet
    committed still counted (a doc may reference a file added in the same change).

    On any subprocess failure the index degrades to empty and a warning is logged;
    bare-name resolution then falls back to the doc-relative check alone.
    """
    key = repo_root.resolve()
    cached = _BASENAME_INDEX_CACHE.get(key)
    if cached is not None:
        return cached

    index: dict[str, int] = {}
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=key,
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
        )
        if proc.returncode != 0:
            logger.warning(
                "docs_auditor: git ls-files failed in %s (rc=%s): %s — bare-name "
                "existence falls back to doc-relative resolution only",
                key,
                proc.returncode,
                (proc.stderr or "").strip(),
            )
        else:
            for line in proc.stdout.splitlines():
                name = line.rsplit("/", 1)[-1]
                if name:
                    index[name] = index.get(name, 0) + 1
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(
            "docs_auditor: git ls-files errored in %s: %s — bare-name existence "
            "falls back to doc-relative resolution only",
            key,
            e,
        )

    _BASENAME_INDEX_CACHE[key] = index
    return index


def _absent_new_path_refs(
    original_refs: set[str], candidate: str, repo_root: Path, doc_path: Path
) -> list[str]:
    """Path refs a candidate rewrite would newly introduce that are absent from disk.

    References already present in the original document are never re-validated —
    the invariant constrains only what the auditor *adds*. Because ``original_refs``
    is computed with the same pattern, widening that pattern widens both sides
    symmetrically and costs no new withholds on already-written prose.

    Resolution order:

    - A ref containing ``/`` resolves against ``repo_root`` alone, exactly as before.
    - A **bare** ref (no ``/``, #2759) resolves first against the doc's own directory
      (``repo_root / doc_path.parent / ref``), because a bare name in prose is most
      often a sibling; then against the ``git ls-files`` basename index.

    **Ambiguity is not a withhold.** ``>=1`` owner means the name exists and the fix
    passes; ``>1`` owners is logged at DEBUG and allowed through. The invariant asks
    "does this name denote something real", not "is it unambiguous" — ambiguity never
    produced the #2711 corruption, which was a name existing nowhere. Only ``0``
    owners is absent.
    """
    absent: list[str] = []
    for ref in sorted(set(_PATH_REF_RE.findall(candidate)) - original_refs):
        if "/" in ref:
            if not (repo_root / ref).exists():
                absent.append(ref)
            continue
        if (repo_root / doc_path.parent / ref).exists():
            continue
        owners = _repo_basename_index(repo_root).get(ref, 0)
        if owners == 0:
            absent.append(ref)
        elif owners > 1:
            logger.debug(
                "docs_auditor: bare ref %r in %s resolves to %d paths — ambiguous "
                "but present, allowed through",
                ref,
                doc_path,
                owners,
            )
    return absent


# A file-path-shaped token. Generalizes the single-segment shape to any number of
# directory segments so a stale term matching the *first* segment is suppressed too.
# Byte-identical to ``_PATH_REF_RE`` today, and deliberately kept separate: it answers
# a different question (apply-time "is this match inside a path token?" suppression vs.
# that one's write-path existence oracle) and either may diverge without the other.
_PATH_TOKEN_RE = re.compile(r"(?:[\w.-]+/)*[\w.-]+\.(?:py|md)")


def _match_inside_path_token(text: str, start: int, end: int) -> bool:
    """Whether ``text[start:end]`` lies wholly inside a ``dir/file.{py,md}`` token.

    Scanning is confined to the match's own line, which bounds the cost and makes
    the answer independent of how large the document is.
    """
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    line = text[line_start:line_end]
    rel_start, rel_end = start - line_start, end - line_start
    return any(m.start() <= rel_start and rel_end <= m.end() for m in _PATH_TOKEN_RE.finditer(line))


def _make_stale_term_replacer(replacement: str, suppressed: list[int]) -> Callable[[re.Match], str]:
    """Build the apply-time suppression callable for one stale-term regex fix.

    Context is re-derived from ``match.string`` — the text *currently being
    rewritten* — and ``match.start()``, the live offset into it. That is the whole
    point: ``_apply_fixes_to_file``'s regex loop mutates ``new_text`` across
    iterations, so an index computed against the pre-loop ``content`` is already
    stale for every fix after the first — and it fails *silently*, producing a
    plausible wrong rewrite rather than an error. There is no index here to go
    stale.

    A suppressed match returns ``match.group(0)`` unchanged, but ``subn`` still
    counts it, so each suppression is recorded in ``suppressed`` for the caller to
    subtract from the reported ``applied`` count.

    The callable is local to the apply loop and never travels on the regex channel:
    ``_detect_stale_term_fixes`` keeps returning ``(re.Pattern, str)``, and
    ``_reject`` keeps receiving the replacement *string* so the withheld record
    stays human-readable in the PR body, findings summary, and warning log.
    """
    context_cache: dict[str, tuple[list[str], list[bool], list[str]]] = {}

    def _replace(match: re.Match) -> str:
        text = match.string
        context = context_cache.get(text)
        if context is None:
            in_fence, heading_for_line = _build_line_context(text)
            context = (text.splitlines(), in_fence, heading_for_line)
            context_cache[text] = context
        lines, in_fence, heading_for_line = context
        line_idx = text.count("\n", 0, match.start())
        if _is_documented_deletion(line_idx, lines, in_fence, heading_for_line):
            suppressed.append(1)
            return match.group(0)
        if _match_inside_path_token(text, match.start(), match.end()):
            suppressed.append(1)
            return match.group(0)
        return replacement

    return _replace


def _apply_fixes_to_file(
    path: Path,
    repo_root: Path,
    regex_fixes: list[tuple[re.Pattern[str], str]],
) -> tuple[int, list[dict]]:
    """Apply text replacements to a file, subject to the existence invariant.

    ``regex_fixes`` — ``(pattern, replacement)`` pairs applied via
    ``pattern.subn()`` — are the auditor's single fix channel.

    **Apply-time suppression (#2744):** each fix's plain ``str`` replacement is
    wrapped in a locally-built callable (``_make_stale_term_replacer``) that
    leaves a match untouched when it sits in a fenced code block, under a
    deletion-recording heading, next to deletion prose, or inside a file-path
    token. The loop rewrites ``new_text`` in place across iterations, so an index
    computed against the pre-loop text is stale for every fix after the first;
    this context must be — and is — derived from the live, already-mutated text
    at match time rather than from any index computed at detection time.

    **Existence invariant:** a fix may not introduce a ``file.{py,md}``-shaped
    reference — bare or ``dir/``-prefixed (#2759) — that does not exist under
    ``repo_root``; see ``_absent_new_path_refs``. Violating fixes are rejected
    individually — valid sibling fixes in the same file still apply — logged at
    warning level, and returned for the caller to surface.

    Returns ``(applied_count, withheld)`` where ``withheld`` is a list of
    ``{"doc", "old", "new", "reason"}`` dicts.
    """
    full = repo_root / path
    if not full.exists() or not regex_fixes:
        return 0, []
    try:
        text = full.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.warning(f"docs_auditor: cannot read {path}: {e}")
        return 0, []

    original_refs = set(_PATH_REF_RE.findall(text))
    new_text = text
    applied = 0
    withheld: list[dict] = []

    def _reject(old: str, new: str, absent: list[str]) -> None:
        logger.warning(
            "docs_auditor: withheld fix in %s (%r -> %r) — introduces absent path(s): %s",
            path,
            old,
            new,
            ", ".join(absent),
        )
        withheld.append({"doc": str(path), "old": old, "new": new, "reason": "target-absent"})

    for pattern, new in regex_fixes:
        suppressed: list[int] = []
        candidate, count = pattern.subn(_make_stale_term_replacer(new, suppressed), new_text)
        count -= len(suppressed)
        # An all-suppressed fix leaves the text byte-identical while ``subn``
        # still reports a nonzero raw count. Skipping it keeps ``applied`` honest
        # and avoids a pointless existence-invariant pass over unchanged text.
        if count <= 0 or candidate == new_text:
            continue
        absent = _absent_new_path_refs(original_refs, candidate, repo_root, path)
        if absent:
            _reject(pattern.pattern, new, absent)
            continue
        new_text = candidate
        applied += count

    if new_text != text:
        try:
            full.write_text(new_text, encoding="utf-8")
        except Exception as e:
            logger.warning(f"docs_auditor: cannot write {path}: {e}")
            return 0, withheld
    return applied, withheld


# ---------------------------------------------------------------------------
# File-as-issue detectors
# ---------------------------------------------------------------------------


# Path components that are obvious illustrative stand-ins, not real module names.
# `filename`/`path`/`name` are the link-specific stand-ins the `.md` branch
# surfaces (spike-6); `_is_placeholder_path` is shared by both branches.
_PLACEHOLDER_PATH_COMPONENTS = frozenset(
    {
        "foo",
        "bar",
        "baz",
        "qux",
        "quux",
        "example",
        "your-module",
        "mymodule",
        "sample",
        "filename",
        "path",
        "name",
    }
)

# Heading-keyword stems whose presence means the doc is deliberately recording a
# deletion. Stems, not exact inflections, so "## Dead SDK Path Deletion" and
# "## Hook Cleanup" both match without listing every inflected form.
_DELETION_HEADING_KEYWORDS = ("delet", "remov", "deprecat", "migrat", "cleanup", "obsolete", "retire")

# Word-anchored prose cues that a nearby line is documenting a deletion rather
# than a live reference. Individual words/short phrases, not full sentences —
# real corpus prose reads "deleted (250 lines)" or "no longer needed", not the
# single fixed phrase "deleted module". Compiled once, in the shape of
# `_MIGRATION_CUE_WORD_RE`, so a bare substring test cannot fire inside an
# unrelated longer word.
_DELETION_PROSE_CUE_WORDS = (
    "deleted",
    "removed",
    "no longer",
    "previously",
    "formerly",
    "deprecated",
)
_DELETION_PROSE_CUE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in _DELETION_PROSE_CUE_WORDS) + r")\b"
)

# Word-anchored cues that a match's own line is a *live* claim, not a deletion
# record — cancels the deletion-narrative suppression when present. Keyword-only
# and evaluated only when the caller opts in via `live_claim_veto=True` (see
# `_is_documented_deletion`): the detector's cost for a wrong suppression is a
# missed report, while the write path's cost for a wrong un-suppression is an
# unreviewed rewrite of narrative prose, so only the detector opts in.
_LIVE_CLAIM_VETO_WORDS = (
    "remain",
    "remains",
    "still",
    "defined in",
    "lives in",
    "currently",
    "implemented in",
)
_LIVE_CLAIM_VETO_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in _LIVE_CLAIM_VETO_WORDS) + r")\b"
)


def _is_placeholder_path(path: str) -> bool:
    """Return True if a path is an illustrative placeholder, not a real module path.

    A path is a placeholder when any of its components is a well-known stand-in
    (``foo``, ``bar``, ``example`` ...) or a single lowercase letter directory.
    Empty or single-segment paths return False (the detector regex guarantees a
    ``dir/file.{py,md}`` shape, so this only guards malformed/odd input).
    """
    if not path or "/" not in path:
        return False
    components = path.split("/")
    for i, component in enumerate(components):
        # For the final component, compare the file stem (strip a .py or .md
        # suffix) so ``agent/docs_handler/foo.py`` is caught on its ``foo``
        # stem, and so is ``docs/features/name.md``.
        is_last = i == len(components) - 1
        candidate = component
        if is_last and (candidate.endswith(".py") or candidate.endswith(".md")):
            candidate = candidate[:-3]
        lowered = candidate.lower()
        if lowered in _PLACEHOLDER_PATH_COMPONENTS:
            return True
        # A single lowercase letter directory (e.g. ``a/foo.py``) is illustrative.
        if len(candidate) == 1 and candidate.isalpha() and candidate.islower():
            return True
    return False


def _build_line_context(content: str) -> tuple[list[bool], list[str]]:
    """Single-scan precompute of per-line context for deletion-aware filtering.

    Returns ``(in_fence, heading_for_line)`` where:
    - ``in_fence[i]`` is True if line ``i`` sits inside a fenced ``` code block.
    - ``heading_for_line[i]`` is the text of the nearest preceding Markdown
      heading for line ``i`` (lowercased), or ``""`` if none precedes it.

    No I/O; pure string scan over ``content``.
    """
    lines = content.splitlines()
    in_fence: list[bool] = []
    heading_for_line: list[str] = []
    fence_open = False
    current_heading = ""
    for line in lines:
        stripped = line.lstrip()
        is_fence_marker = stripped.startswith("```")
        # A fence marker line is itself part of the block boundary; treat the
        # marker line as inside the fence so matches on it are suppressed too.
        if is_fence_marker:
            in_fence.append(True)
            fence_open = not fence_open
        else:
            in_fence.append(fence_open)
        # Track nearest preceding heading only outside fenced blocks.
        if not fence_open and not is_fence_marker and stripped.startswith("#"):
            current_heading = stripped.lstrip("#").strip().lower()
        heading_for_line.append(current_heading)
    return in_fence, heading_for_line


def _is_documented_deletion(
    line_idx: int,
    lines: list[str],
    in_fence: list[bool],
    heading_for_line: list[str],
    *,
    live_claim_veto: bool = False,
) -> bool:
    """Return True if a match at ``line_idx`` is an illustrative or documented deletion.

    Three conservative cues (any one suppresses the finding), evaluated in
    order:
    1. The match falls inside a fenced code block (illustrative example) —
       always wins; a fenced block is illustrative no matter what it says.
    2. The nearest preceding heading names a deletion, matched on a stem
       (``delet``, ``remov``, ``deprecat``, ``migrat``, ``cleanup``,
       ``obsolete``, ``retire``) rather than an exact inflection.
    3. The match's line or a line within 2 lines carries a word-anchored
       deletion-prose cue (``deleted``, ``removed``, ``no longer``, ...).

    ``live_claim_veto`` (keyword-only, default ``False``) cancels tiers 2 and 3
    when the match's own line carries a live-claim cue (``remains``, ``still``,
    ``defined in``, ...) — evaluated **after** the fence tier (a fenced block
    stays illustrative regardless) and **before** the heading/prose tiers, so
    a line like *"`fail_stage()` remains defined in `agent/hooks/gone.py`"*
    under a ``## Migration`` heading is still reported. Off by default and
    opt-in only from ``_detect_deleted_target_issues`` — see that function and
    ``_make_stale_term_replacer``, which never sets it, for why: this
    predicate's ``True`` means "suppress" at both call sites, but suppression
    costs differently on each. Widening what the *detector* suppresses costs a
    missed report; widening what the *write path* suppresses costs an
    auditor-authored rewrite of narrative prose on every PR — the exact
    behavior #2739 exists to gate.

    Inline single-backtick code is NOT suppressed — that is how genuine
    references are written.
    """
    if line_idx < len(in_fence) and in_fence[line_idx]:
        return True
    if live_claim_veto and line_idx < len(lines) and _LIVE_CLAIM_VETO_RE.search(lines[line_idx].lower()):
        return False
    if line_idx < len(heading_for_line):
        heading = heading_for_line[line_idx]
        if any(kw in heading for kw in _DELETION_HEADING_KEYWORDS):
            return True
    for adj in (line_idx - 2, line_idx - 1, line_idx, line_idx + 1, line_idx + 2):
        if 0 <= adj < len(lines):
            if _DELETION_PROSE_CUE_RE.search(lines[adj].lower()):
                return True
    return False


# Markdown link syntax: `[label](target)`. Anchor/query stripping and scheme
# detection happen after the match, in `_resolve_md_link_target`.
_MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_URI_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def _in_md_link_scope(doc_path: Path) -> bool:
    """Whether ``doc_path`` is in scope for the ``.md`` broken-link branch.

    Scope is ``docs/`` minus ``docs/plans/completed/`` and ``docs/plans/done/``
    — archived plans deliberately record history and are not a live surface a
    human reviews for broken links. Everything outside ``docs/`` (including
    ``.claude/``) is out of scope for this branch; the ``.py`` branch has no
    such restriction because it runs over whatever neighborhood the caller
    already resolved.
    """
    parts = doc_path.parts
    if not parts or parts[0] != "docs":
        return False
    if len(parts) >= 3 and parts[1] == "plans" and parts[2] in ("completed", "done"):
        return False
    return True


def _resolve_md_link_target(raw_target: str, doc_path: Path, repo_root: Path) -> Path | None:
    """Resolve a markdown link target to a repo-relative ``Path``, or ``None``.

    Anchors and queries are stripped before resolution — ``./gone.md#section``
    resolves as ``gone.md``, so the title (and dedup key) stay stable regardless
    of which section a link points at. Resolution is **doc-relative**: a
    leading ``/`` resolves against the repo root, everything else resolves
    against the containing document's own directory — the frame markdown
    renderers actually use (the #2725 / #2741 regression this branch exists to
    keep fixed). Returns ``None`` for a non-``.md`` target, or one that
    normalizes outside the repo root.
    """
    target = raw_target.split("#", 1)[0].split("?", 1)[0].strip()
    if not target or not target.endswith(".md"):
        return None
    root = repo_root.resolve()
    if target.startswith("/"):
        candidate = (root / target.lstrip("/")).resolve()
    else:
        candidate = (root / doc_path.parent / target).resolve()
    try:
        return candidate.relative_to(root)
    except ValueError:
        return None


def _match_inside_code_span(text: str, start: int, end: int) -> bool:
    """Whether ``text[start:end]`` lies wholly inside an inline `` `code span` ``.

    Scanning is confined to the match's own line, mirroring
    ``_match_inside_path_token``. A markdown link inside a code span is a
    literal illustration of syntax, not a live reference — the deliberate
    asymmetry with the ``.py`` branch, which requires backticks to be a
    reference at all.
    """
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    line = text[line_start:line_end]
    rel_start, rel_end = start - line_start, end - line_start
    return any(
        bm.start() <= rel_start and rel_end <= bm.end() for bm in re.finditer(r"`[^`]*`", line)
    )


def _detect_deleted_target_issues(doc_path: Path, content: str, repo_root: Path) -> list[dict]:
    """File issues for references to deleted targets.

    Two reference shapes, sharing one deletion-narrative hatch
    (``_is_documented_deletion``):

    * Backticked ``.py`` paths, e.g. `` `agent/gone.py` `` — repo-wide, no
      scope restriction beyond what the caller's neighborhood resolved.
    * Markdown-link ``.md`` targets, e.g. ``[label](gone.md)`` — scoped to
      ``docs/`` minus ``docs/plans/completed/`` and ``docs/plans/done/`` (see
      ``_in_md_link_scope``). Resolved **doc-relative**, not repo-root-relative
      (the #2725 / #2741 frame rule): a target that exists at the repo root but
      not doc-relative is still reported. A markdown link inside a code span is
      not a reference — the deliberate asymmetry with the ``.py`` branch, which
      requires backticks to *be* one.

    Both branches suppress the same three classes of false positive:
    placeholder/example paths, matches inside fenced illustrative code blocks,
    and matches under a deletion-recording heading or deletion prose — the
    latter via ``_is_documented_deletion(..., live_claim_veto=True)``, the only
    caller that passes the veto (it is a detector, never the write path). Every
    suppressed match is logged at DEBUG so operators can audit the filter.

    The ``.md`` branch only ever *reports* a broken link; it never rewrites
    the doc, and no auto-repairing replacement is coming back — an auditor
    deleting lines from a human's index file on its own judgment is exactly
    the unreviewed-write class #2739 exists to gate.
    """
    findings: list[dict] = []
    lines = content.splitlines()
    in_fence, heading_for_line = _build_line_context(content)
    # Deliberately narrower than `_PATH_REF_RE`, which was widened to `*` for #2759.
    # The asymmetry is intentional: `_PATH_REF_RE` guards the *write* path, where a
    # bare name that resolves to nothing is a corruption the existence invariant must
    # catch, while this is a *detector* pattern governing what the auditor proposes to
    # a human. Widening it would spend the per-run issue cap on bare names not
    # resolvable to a single path. Ruled unchanged in #2759.
    for m in re.finditer(r"`((?:[\w.-]+/)+[\w.-]+\.py)`", content):
        path = m.group(1)
        if _is_placeholder_path(path):
            logger.debug(
                "docs_auditor: suppressed deleted-target finding for placeholder path %s in %s",
                path,
                doc_path,
            )
            continue
        line_idx = content.count("\n", 0, m.start())
        if _is_documented_deletion(line_idx, lines, in_fence, heading_for_line, live_claim_veto=True):
            logger.debug(
                "docs_auditor: suppressed deleted-target finding for %s in %s "
                "(fenced block or documented deletion)",
                path,
                doc_path,
            )
            continue
        if (repo_root / path).exists():
            continue
        findings.append(
            {
                "title": f"Doc references deleted target: {path} (in {doc_path})",
                "body": f"`{doc_path}` references `{path}` which no longer exists in the repo.",
                "category": "deleted-target",
            }
        )

    if _in_md_link_scope(doc_path):
        for m in _MD_LINK_RE.finditer(content):
            raw_target = m.group(1).strip()
            if not raw_target or raw_target.startswith("#") or _URI_SCHEME_RE.match(raw_target):
                continue
            if _match_inside_code_span(content, m.start(), m.end()):
                continue
            rel = _resolve_md_link_target(raw_target, doc_path, repo_root)
            if rel is None:
                continue
            rel_str = str(rel)
            if _is_placeholder_path(rel_str):
                logger.debug(
                    "docs_auditor: suppressed broken-md-link finding for placeholder path %s in %s",
                    rel_str,
                    doc_path,
                )
                continue
            line_idx = content.count("\n", 0, m.start())
            if _is_documented_deletion(
                line_idx, lines, in_fence, heading_for_line, live_claim_veto=True
            ):
                logger.debug(
                    "docs_auditor: suppressed broken-md-link finding for %s in %s "
                    "(fenced block or documented deletion)",
                    rel_str,
                    doc_path,
                )
                continue
            if (repo_root / rel).exists():
                continue
            findings.append(
                {
                    "title": f"Doc references missing link target: {rel_str} (in {doc_path})",
                    "body": f"`{doc_path}` links to `{rel_str}` which does not exist in the repo.",
                    "category": "broken-md-link",
                }
            )

    return findings


def _detect_stub_doc(doc_path: Path, content: str) -> dict | None:
    """File an issue if a doc has fewer than STUB_DOC_LINE_THRESHOLD content lines."""
    content_lines = [ln for ln in content.splitlines() if ln.strip() and not ln.startswith("#")]
    if len(content_lines) < STUB_DOC_LINE_THRESHOLD:
        body = (
            f"`{doc_path}` has only {len(content_lines)} content lines "
            f"(<{STUB_DOC_LINE_THRESHOLD})."
        )
        return {
            "title": f"Stub doc: {doc_path}",
            "body": body,
            "category": "stub-doc",
        }
    return None


def _detect_orphan_plan_issues(repo_root: Path) -> list[dict]:
    """Find docs/plans/*.md files that lack a tracking issue link."""
    findings: list[dict] = []
    plans_dir = repo_root / "docs" / "plans"
    if not plans_dir.exists():
        return findings
    for plan in sorted(plans_dir.glob("*.md")):
        try:
            text = plan.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if "tracking:" in text or re.search(r"issues/\d+", text):
            continue
        findings.append(
            {
                "title": f"Orphan plan: {plan.relative_to(repo_root)} (no tracking issue)",
                "body": f"`{plan.relative_to(repo_root)}` has no tracking-issue link.",
                "category": "orphan-plan",
            }
        )
    return findings


# ---------------------------------------------------------------------------
# Memory refresh hook (no-op placeholder for #1249)
# ---------------------------------------------------------------------------


def refresh_docs_in_memory(touched_paths: list[str]) -> None:
    """Re-ingest touched docs into the Memory substrate.

    No-op placeholder for #1249. The hook is **stable** — call sites in this
    module will not change when #1249 lands a real implementation. Always
    non-blocking and fire-and-forget; callers wrap invocations in
    ``try/except Exception`` so the auditor never fails because the hook failed.
    """
    logger.debug("refresh_docs_in_memory called with %d path(s) (no-op)", len(touched_paths))


# ---------------------------------------------------------------------------
# Issue filing (gh CLI)
# ---------------------------------------------------------------------------


def _normalize_title(title: str) -> str:
    """Collapse internal whitespace and strip — for exact title comparison."""
    return " ".join(title.split())


def _filing_machine_name() -> str:
    """Human-facing name of the machine filing the issue, for multi-machine triage.

    Matches the ``machine`` field used by the single-machine-ownership system
    (macOS ComputerName via ``scutil``), falling back to the OS hostname. This
    is stamped into every issue body so duplicates fanned across hosts — or a
    host still running this reflection after it was disabled in the synced
    config — name themselves instead of being anonymous.

    Delegates to :func:`config.machine.get_machine_display_name`, the shared
    helper that owns the ComputerName→hostname→"unknown" fallback chain.
    """
    return get_machine_display_name()


def _issue_exists(title: str, repo_root: Path, *, states: str = "all") -> bool:
    """Return True if an issue with this exact title already exists.

    This is the authoritative cross-machine dedup gate: local Redis dedup keys
    are per-machine and invisible across hosts, so two machines would otherwise
    file the same finding. Queries the live tracker via
    ``gh issue list --search`` (REST-backed full-text search) and confirms with
    an exact normalized-title comparison in Python (the title already encodes
    both the path and the doc, making it a natural composite key).

    ``states`` defaults to ``"all"`` — most findings this module files (a
    broken doc reference, an orphan plan, a stub doc) are a claim about the
    tree's current state, and a human who reads one, rules on it, and closes
    it without editing the doc has made a durable decision; matching only
    ``"open"`` issues would re-file that exact finding a month later once the
    per-machine Redis dedup key expires. ``states="open"`` is for the two
    *recurring-condition* categories (see ``_RECURRING_CONDITION_CATEGORIES``)
    whose underlying comparison can genuinely recur after a close: closing one
    of those would otherwise silence the condition forever.

    ``--limit 100`` is not decoration: ``gh issue list`` defaults to 30, and
    under ``--state all`` a repo with more than 30 issues matching the search
    term can push the exact title off the first page, which is a silent
    fail-open that files a duplicate. No label filter — the label a triager
    applied at filing time can be edited later, and the authoritative match
    below is the exact title compare, not the label.

    Fails open: on any `gh` failure, non-zero exit, or malformed output, log a
    WARNING and return False so a genuine finding is never silently dropped —
    the worst case is the duplicate this gate was meant to prevent, which the
    Redis fast-path still suppresses on the next run (for ``states="all"``
    callers; the recurring-condition callers skip that fast-path by design).
    """
    normalized_query = _normalize_title(title)
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--state",
                states,
                "--limit",
                "100",
                "--search",
                title,
                "--json",
                "number,title",
            ],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            check=False,
            cwd=str(repo_root),
        )
        if result.returncode != 0:
            logger.warning(
                "docs_auditor: gh issue list (dedup) failed for '%s' (rc=%d): %s "
                "— falling back to Redis-only dedup",
                title,
                result.returncode,
                result.stderr.strip()[:200],
            )
            return False
        issues = json.loads(result.stdout or "[]")
        for issue in issues:
            if _normalize_title(issue.get("title", "")) == normalized_query:
                return True
        return False
    except Exception as e:
        logger.warning(
            "docs_auditor: gh issue list (dedup) errored for '%s': %s "
            "— falling back to Redis-only dedup",
            title,
            e,
        )
        return False


def _file_issue_if_new(finding: dict, repo_root: Path) -> bool:
    """File a GitHub issue via gh CLI, deduped by title. Returns True if filed.

    Two-tier dedup: a local Redis fast-path (per-machine cache) gates the
    expensive live-tracker query, and `_issue_exists` is the authoritative
    cross-machine gate. Local Redis alone is insufficient because each machine
    keeps its own Redis, so the same finding would be filed once per machine.

    The fast-path is gated on the finding's category. For a recurring-condition
    category (``_RECURRING_CONDITION_CATEGORIES``) the 30-day Redis key would
    otherwise suppress a genuine recurrence of the same condition for up to a
    month after a human closed the issue the first time — the fast-path never
    reaches `_issue_exists`, so its `states="open"` selection would be inert for
    that whole window. Gating only the *read* is sufficient: `dedup_key` has
    exactly one reader (this `exists` check) and two writers (both below), so
    once the read is off for a category the key is never consulted for it
    again — the two `set()` calls stay unconditional as a fail-open cap during
    a `gh` outage (R8-1).
    """
    title = finding.get("title", "").strip()
    if not title:
        return False
    states = "open" if finding.get("category") in _RECURRING_CONDITION_CATEGORIES else "all"
    title_hash = hashlib.sha256(title.encode("utf-8")).hexdigest()[:16]
    dedup_key = f"{REDIS_ISSUE_DEDUP_PREFIX}:{title_hash}"
    redis_client = None
    try:
        redis_client = _get_redis()
        # Fast-path: if this machine already filed it, skip the tracker query
        # entirely — but only for a once-ever category. A recurring-condition
        # category must always reach `_issue_exists(states="open")`, or a
        # closed issue for a condition that has genuinely returned stays
        # silenced by this per-machine cache for up to 30 days.
        if states == "all" and redis_client.exists(dedup_key):
            return False  # already filed
    except Exception:
        redis_client = None  # If Redis is unavailable, attempt to file without dedup

    # Authoritative cross-machine gate: another machine may have already filed this.
    if _issue_exists(title, repo_root, states=states):
        # Record the local fast-path key so subsequent runs skip the tracker query.
        if redis_client is not None:
            try:
                redis_client.set(dedup_key, "1", ex=86400 * 30)
            except (
                Exception
            ):  # swallow-ok: best-effort cache write; tracker already confirmed dedup
                pass
        return False

    # Stamp the filing machine into the body (not the title — title is the dedup
    # key and must stay stable). Lets duplicates fanned across hosts, or a host
    # still running this reflection after it was disabled in synced config, name
    # themselves for triage.
    body = finding.get("body", "")
    body = f"{body}\n\n---\n*Filed by docs-auditor reflection on `{_filing_machine_name()}`.*"

    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "create",
                "--title",
                title,
                "--body",
                body,
                "--label",
                "documentation",
            ],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            check=False,
            cwd=str(repo_root),
        )
        if result.returncode != 0:
            logger.warning(
                f"docs_auditor: gh issue create failed for '{title}' "
                f"(rc={result.returncode}): {result.stderr.strip()[:200]}"
            )
            return False
        # Only set dedup key after successful gh issue create — transient failures retry next run.
        if redis_client is not None:
            try:
                redis_client.set(dedup_key, "1", ex=86400 * 30)
            except Exception:  # swallow-ok: best-effort cache write after successful issue create
                pass
        return True
    except Exception as e:
        logger.warning(f"docs_auditor: gh issue create failed for '{title}': {e}")
        return False


# ---------------------------------------------------------------------------
# Telegram notification (mirrors _send_log_review_telegram pattern)
# ---------------------------------------------------------------------------


def _send_telegram_notification(message: str) -> None:
    """Best-effort Telegram notification. Swallows all subprocess failures."""
    try:
        subprocess.run(
            ["valor-telegram", "send", "--chat", "Eng: Valor", message],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            check=False,
        )
    except FileNotFoundError:
        logger.warning("docs_auditor: valor-telegram not on PATH; skipping Telegram notify")
    except subprocess.TimeoutExpired:
        logger.warning("docs_auditor: valor-telegram send timed out")
    except Exception as e:
        logger.warning(f"docs_auditor: valor-telegram send failed: {e}")


# ---------------------------------------------------------------------------
# Public substrate API
# ---------------------------------------------------------------------------


def audit(
    primary_path: str | Path | None = None,
    *,
    scope_mode: str = "rotation",
    apply_mode: str = "apply",
    project_key: str = "valor",
    repo_root: Path | None = None,
) -> dict:
    """Unified docs-auditor entrypoint. Synchronous.

    **This function never commits.** It writes fixes to the working tree, fires
    the memory-refresh hook on what it wrote, and returns ``files_touched``. The
    tree is left dirty and the **caller owns the commit**, because every write
    the auditor makes has to pass a named review gate before it becomes a
    permanent record: the ``/do-docs`` skill reads the diff for
    ``pr-changed-files``, and the rotation reflection's gate is the pull request
    ``run_docs_auditor`` opens.

    Args:
        primary_path: Repo-relative path to the primary doc to audit. When
            ``scope_mode == "pr-changed-files"`` this is ignored.
        scope_mode: One of ``"rotation"`` (single primary + neighborhood) or
            ``"pr-changed-files"`` (PR diff scope).
        apply_mode: ``"apply"`` writes fixes; ``"dry-run"`` reports only.
        project_key: Used for vault-namespaced rotation keys.
        repo_root: Override repo root (defaults to PROJECT_ROOT).

    Returns:
        Dict with ``status``, ``files_touched``, ``fixes_applied``,
        ``issues_filed``, ``pr_url``.
    """
    # The bare-name existence oracle is a per-*run* snapshot: a long-lived process
    # must not answer from an index built before the last commit (#2759).
    _BASENAME_INDEX_CACHE.clear()

    root = (repo_root or PROJECT_ROOT).resolve()

    # Auth probe (Anthropic required)
    ok, reason = _check_auth()
    if not ok:
        logger.warning(f"docs_auditor: auth disabled: {reason}")
        return _ok_result("disabled", extras={"reason": reason})

    # Optional embedding probe; degrade gracefully if missing
    if not _check_embedding_auth():
        logger.debug("docs_auditor: embedding auth missing — lexical-only mode")

    # Resolve scope
    files: list[Path] = []
    if scope_mode == "pr-changed-files":
        files = _resolve_pr_changed_files(root)
    elif scope_mode == "rotation":
        if primary_path is None:
            return _ok_result("skipped", extras={"reason": "no_primary_path"})
        primary = Path(str(primary_path))
        full = root / primary
        if not full.exists():
            return _ok_result("skipped", extras={"reason": "primary_not_found"})
        files = _resolve_neighborhood(primary, root, cap=NEIGHBORHOOD_CAP)
    else:
        return _ok_result("error", extras={"reason": f"unknown scope_mode: {scope_mode}"})

    if not files:
        return _ok_result("ok", files_touched=[], fixes_applied=0, issues_filed=0)

    # Run detectors per file
    touched: list[str] = []
    total_fixes = 0
    issues_filed = 0
    issue_findings: list[dict] = []
    withheld: list[dict] = []

    for path in files:
        full = root / path
        if not full.exists():
            continue
        try:
            content = full.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        # Auto-fix detectors — anchored stale terms are the only fix channel.
        regex_fixes = _detect_stale_term_fixes(content)

        # Apply-mode writes are markdown-only (#2058). The detector above is
        # markdown-regex based (bare-term renames), so a committed non-.md file
        # that lands in the same PR — e.g. a site/*.html doc page — must never be
        # auto-rewritten inside tags, attributes, or inline <script>. Reporting
        # still runs; only the write-back is guarded.
        if regex_fixes and apply_mode == "apply" and str(path).endswith(".md"):
            applied, rejected = _apply_fixes_to_file(path, root, regex_fixes)
            withheld.extend(rejected)
            if applied > 0:
                total_fixes += applied
                touched.append(str(path))

        # File-as-issue detectors (advisory). Editorial, not auto-fixable — a
        # deleted-target reference has no rename to correct to. These are
        # rotation-only: Caller B (/do-docs, scope=pr-changed-files) runs on
        # every PR's docs stage, so filing advisory issues there re-files the
        # same unfixable findings per-PR, which is the documentation-label
        # duplicate flood. Auto-fix detectors above still run per-PR; only
        # issue-filing is gated to rotation.
        if scope_mode == "rotation":
            issue_findings.extend(_detect_deleted_target_issues(path, content, root))
            stub = _detect_stub_doc(path, content)
            if stub is not None:
                issue_findings.append(stub)

    # Orphan plans (repo-wide, run once)
    if scope_mode == "rotation":
        issue_findings.extend(_detect_orphan_plan_issues(root))

    # File issues (deduped); only when applying in rotation scope.
    # Hard per-run cap prevents flood: rotation allows up to 5.
    per_run_cap = ISSUE_FILING_PER_RUN_CAP if scope_mode == "rotation" else 3
    if apply_mode == "apply" and scope_mode == "rotation":
        for finding in issue_findings:
            if issues_filed >= per_run_cap:
                logger.warning(
                    "docs_auditor: per-run cap (%d) reached for scope=%s — "
                    "%d finding(s) suppressed; re-run to file remaining",
                    per_run_cap,
                    scope_mode,
                    len(issue_findings) - issues_filed,
                )
                break
            if _file_issue_if_new(finding, root):
                issues_filed += 1

    # Caller B (pr-changed-files): fire the memory-refresh hook on the applied
    # set. The hook operates on applied paths and needs no commit — the working
    # tree stays dirty for the /do-docs skill's review gate. Caller A (rotation)
    # handles its own branch/commit/push/hook in run_docs_auditor.
    if scope_mode == "pr-changed-files" and apply_mode == "apply" and touched:
        try:
            refresh_docs_in_memory(touched)
        except Exception as e:
            logger.warning(f"docs_auditor: refresh_docs_in_memory hook failed: {e}")

    return _ok_result(
        "ok",
        files_touched=touched,
        fixes_applied=total_fixes,
        issues_filed=issues_filed,
        fixes_withheld=len(withheld),
        withheld=withheld,
    )


# ---------------------------------------------------------------------------
# Caller A — daily rotation reflection
# ---------------------------------------------------------------------------


def _select_primary_doc(repo_root: Path, project_key: str) -> tuple[Path | None, dict[str, float]]:
    """Pick the least-recently-audited primary doc.

    Returns (selected_path, last_run_map). The map is the parsed Redis hash.
    """
    try:
        r = _get_redis()
        last_run_raw = r.hgetall(REDIS_LAST_RUN_HASH) or {}
    except Exception as e:
        logger.warning(f"docs_auditor: cannot read rotation hash: {e}")
        last_run_raw = {}

    # Decode bytes -> str if necessary
    last_run: dict[str, float] = {}
    for k, v in last_run_raw.items():
        try:
            key = k.decode() if isinstance(k, bytes) else str(k)
            val = float(v.decode() if isinstance(v, bytes) else v)
            last_run[key] = val
        except Exception:
            continue

    # Enumerate candidate docs
    docs_dir = repo_root / "docs" / "features"
    candidates: list[Path] = []
    if docs_dir.exists():
        for md in sorted(docs_dir.glob("*.md")):
            if md.name == "README.md":
                continue
            candidates.append(md.relative_to(repo_root))

    if not candidates:
        return None, last_run

    # Pick oldest / never-run
    def _key(path: Path) -> float:
        return last_run.get(_path_to_slug(path), 0.0)

    candidates.sort(key=_key)
    return candidates[0], last_run


def _git_dirty(repo_root: Path) -> bool:
    """Return True if the working tree is dirty."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(repo_root),
        )
        return bool(result.stdout.strip())
    except Exception as e:
        logger.warning(f"docs_auditor: git status failed: {e}")
        return True  # err on the side of caution


def _git_diff_quiet(repo_root: Path) -> bool:
    """Return True if there are no diffs (zero-diff)."""
    try:
        result = subprocess.run(
            ["git", "diff", "--quiet"],
            capture_output=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(repo_root),
        )
        return result.returncode == 0  # 0 means no diff
    except Exception:
        return False


def _has_open_pr_for_slug(slug: str, repo_root: Path) -> bool:
    """Return True if any open PR already targets a docs-audit branch for this slug."""
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--state", "open", "--json", "headRefName"],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(repo_root),
        )
        if result.returncode != 0:
            return False
        prs = json.loads(result.stdout or "[]")
        prefix = f"docs-audit/{slug}-"
        return any(p.get("headRefName", "").startswith(prefix) for p in prs)
    except Exception as e:
        logger.warning(f"docs_auditor: open-PR check failed: {e}")
        return False


def _daily_pr_cap_reached(repo_root: Path) -> bool:
    """Return True if a docs-audit PR was already created today (calendar day, UTC)."""
    try:
        r = _get_redis()
        today = datetime.now(UTC).strftime("%Y%m%d")
        key = f"{REDIS_DAILY_PR_KEY}:{today}"
        return bool(r.exists(key))
    except Exception as e:
        logger.warning(f"docs_auditor: daily PR cap check failed: {e}")
        return False


def _record_daily_pr(repo_root: Path) -> None:
    """Mark that a PR was created today so the daily cap is enforced."""
    try:
        r = _get_redis()
        today = datetime.now(UTC).strftime("%Y%m%d")
        key = f"{REDIS_DAILY_PR_KEY}:{today}"
        r.set(key, "1", ex=86400 * 2)  # expires after 2 days
    except Exception as e:
        logger.warning(f"docs_auditor: daily PR cap record failed: {e}")


def _current_ref(repo_root: Path) -> str | None:
    """Return the ref the checkout is currently on, or None if it cannot be read."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(repo_root),
        )
        if result.returncode != 0:
            return None
        return (result.stdout or "").strip() or None
    except Exception as e:
        logger.warning(f"docs_auditor: current-ref read failed: {e}")
        return None


def _restore_checkout(
    repo_root: Path, starting_ref: str, branch: str, files_touched: list[str]
) -> bool:
    """Return the checkout to ``starting_ref`` and discard only the auditor's own paths.

    Scoped by construction: no ``checkout -f``, no ``reset --hard``, no ``clean``.
    The auditor runs in the shared main checkout where other lanes routinely hold
    uncommitted work, so a whole-tree force-restore would destroy it. Foreign dirt
    *outside* ``files_touched`` is preserved by design.

    ``git checkout HEAD -- <paths>`` is deliberate and the ``HEAD`` is load-bearing:
    the bare ``git checkout -- <paths>`` restores the worktree from the **index**,
    which on the staged-then-commit-failed path still holds the auditor's own
    content. The ``HEAD`` form resets index *and* worktree for those paths only.

    Returns True only when the postcondition holds: HEAD is back on
    ``starting_ref`` **and** no ``files_touched`` path is dirty in either column of
    ``git status --porcelain``.
    """
    ok = True
    try:
        checkout = subprocess.run(
            ["git", "checkout", starting_ref],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(repo_root),
        )
        if checkout.returncode != 0:
            ok = False
            logger.error(
                f"docs_auditor: restore failed to check out {starting_ref}: "
                f"{(checkout.stderr or '').strip()}"
            )

        if files_touched:
            discard = subprocess.run(
                ["git", "checkout", "HEAD", "--", *files_touched],
                capture_output=True,
                text=True,
                timeout=settings.timeouts.git_subprocess_s,
                cwd=str(repo_root),
            )
            if discard.returncode != 0:
                ok = False
                logger.error(
                    "docs_auditor: restore failed to discard auditor paths: "
                    f"{(discard.stderr or '').strip()}"
                )

        # Delete the created branch if it exists.
        exists = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(repo_root),
        )
        if exists.returncode == 0:
            subprocess.run(
                ["git", "branch", "-D", branch],
                capture_output=True,
                text=True,
                timeout=settings.timeouts.git_subprocess_s,
                cwd=str(repo_root),
            )

        # Postcondition (a): back on the starting ref.
        if _current_ref(repo_root) != starting_ref:
            ok = False
            logger.error(f"docs_auditor: restore left the checkout off {starting_ref}")

        # Postcondition (b): no files_touched path dirty in either column. This is
        # deliberately NOT `not _git_dirty(repo_root)` — foreign dirt outside
        # files_touched is expected to survive.
        if files_touched:
            scoped = subprocess.run(
                ["git", "status", "--porcelain", "--", *files_touched],
                capture_output=True,
                text=True,
                timeout=settings.timeouts.git_subprocess_s,
                cwd=str(repo_root),
            )
            if (scoped.stdout or "").strip():
                ok = False
                logger.error(
                    "docs_auditor: restore left auditor paths dirty: "
                    f"{(scoped.stdout or '').strip()}"
                )
    except Exception as e:
        logger.error(f"docs_auditor: restore error: {e}")
        return False
    return ok


def _push_branch_and_pr(
    slug: str, repo_root: Path, files_touched: list[str], withheld: list[dict] | None = None
) -> str | None:
    """Create timestamped branch, push, open PR. Returns PR URL or None on failure.

    ``files_touched`` is the exact set of repo-relative paths the substrate wrote,
    and it is the **only** thing staged: the commit is built from
    ``git add -- <files_touched>``, never a whole-tree sweep. An empty list means
    the auditor wrote nothing, so no branch is created and no commit is run.

    On every exit path the checkout is returned to the ref it started on and the
    auditor's own paths are discarded, scoped to ``files_touched`` — see
    ``_restore_checkout``. A failed restore is reported: the function returns None
    even if the PR was created, so the caller escalates rather than recording a
    clean run over a wedged checkout.

    The daily-cap and open-PR guards do **not** live here. They run in
    ``run_docs_auditor``'s preflight, before the substrate writes anything, so a
    guard can no longer fire after the shared checkout has already been dirtied.

    ``withheld`` is the run's existence-invariant rejections. When non-empty the
    PR body lists them and carries ``WITHHELD_PR_MARKER``, which exempts the PR
    from the sweeper's stale-close so the surviving fixes are not discarded
    before a human reviews them.
    """
    if not files_touched:
        logger.info("docs_auditor: no files touched, skipping branch/commit/PR")
        return None

    starting_ref = _current_ref(repo_root)
    if starting_ref is None:
        logger.error("docs_auditor: cannot read starting ref, refusing to branch")
        return None

    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    branch = f"docs-audit/{slug}-{ts}"
    url: str | None = None
    try:
        subprocess.run(
            ["git", "checkout", "-b", branch],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(repo_root),
            check=True,
        )
        subprocess.run(
            ["git", "add", "--", *files_touched],
            capture_output=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(repo_root),
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", f"Docs: auditor pass for {slug}"],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(repo_root),
            check=True,
        )
        subprocess.run(
            ["git", "push", "-u", "origin", branch],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(repo_root),
            check=True,
        )
        body = "Automated docs auditor pass."
        if withheld:
            rejected = "\n".join(
                f"- `{w.get('doc')}`: `{w.get('old')}` → `{w.get('new')}` "
                f"({w.get('reason', 'unknown')})"
                for w in withheld
            )
            body += (
                f"\n\n{WITHHELD_PR_MARKER}\n"
                f"⚠️ **{len(withheld)} fix(es) withheld** by the existence invariant — "
                "the auditor tried to introduce a path that is absent from the working "
                "tree. Review the surviving fixes before merging.\n\n"
                f"{rejected}"
            )
        pr_result = subprocess.run(
            [
                "gh",
                "pr",
                "create",
                "--title",
                f"Docs auditor: {slug}",
                "--body",
                body,
            ],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(repo_root),
            check=False,
        )
        candidate = (pr_result.stdout or "").strip().splitlines()[-1] if pr_result.stdout else None
        if candidate and candidate.startswith("http"):
            _record_daily_pr(repo_root)
            url = candidate
    except subprocess.CalledProcessError as e:
        logger.warning(f"docs_auditor: branch/push/PR failed: {e}")
    except Exception as e:
        logger.warning(f"docs_auditor: branch/push/PR error: {e}")
    finally:
        # Verified, scoped restore — runs on every exit path.
        restored = _restore_checkout(repo_root, starting_ref, branch, files_touched)

    if not restored:
        logger.error("docs_auditor: shared checkout restore failed, reporting failure")
        return None
    return url


def _write_liveness(
    slug: str,
    status: str,
    pr_url: str | None,
    files_touched: int,
    vault_narratives_compared: int | None = None,
    fixes_withheld: int = 0,
) -> None:
    """Persist liveness signals for PM monitoring (Phase 2).

    ``vault_narratives_compared`` is emitted into the summary only when not None
    (i.e. only from the rotation call site that actually ran the vault drift
    comparison), so "detector ran, found zero drift" is distinguishable from
    "narrative→page mapping is silently empty/broken".

    ``fixes_withheld`` is emitted only when non-zero, mirroring the same pattern.
    This is the only durable, queryable surface the rotation produces — the
    scheduler consumes just ``projects`` from a function reflection's return, so
    without this a withheld run would be byte-identical to a clean one in Redis.
    Both extras are keyword params with defaults, so the positional 4-arg/5-arg
    call contract asserted by ``TestWriteLivenessVaultParam`` is unchanged.
    """
    try:
        r = _get_redis()
        ts = time.time()
        r.set(REDIS_LAST_COMPLETED_TS_KEY, str(ts))
        summary = {
            "slug": slug,
            "pr_url": pr_url,
            "files_touched": files_touched,
            "status": status,
        }
        if vault_narratives_compared is not None:
            summary["vault_narratives_compared"] = vault_narratives_compared
        if fixes_withheld:
            summary["fixes_withheld"] = fixes_withheld
        r.set(REDIS_LAST_COMPLETED_SUMMARY_KEY, json.dumps(summary))
    except Exception as e:
        logger.warning(f"docs_auditor: liveness write failed: {e}")


def _update_rotation_hash(project_key: str, paths: list[str]) -> None:
    """Stamp rotation hash with current timestamp for each touched path."""
    try:
        r = _get_redis()
        ts = time.time()
        mapping = {}
        for p in paths:
            field = _path_to_slug(p)
            mapping[field] = str(ts)
        if mapping:
            r.hset(REDIS_LAST_RUN_HASH, mapping=mapping)
    except Exception as e:
        logger.warning(f"docs_auditor: rotation hash write failed: {e}")


# ---------------------------------------------------------------------------
# Vault<->site drift detection (curated mapping, advisory/report-only)
# ---------------------------------------------------------------------------


def _resolve_vault_root(project_key: str) -> Path | None:
    """Resolve the vault root for ``project_key`` via the tracked scope resolver.

    Reuses ``tools/knowledge/scope_resolver._load_project_mappings()`` (which reads
    ``~/Desktop/Valor/projects.json`` ``knowledge_base``, ``expanduser``+``normpath``)
    rather than reimplementing path resolution or depending on the deleted, untracked
    ``~/.claude/skills/do-xref-audit/`` orphan. Returns None (and logs a warning) if
    the mapping is missing so the audit can continue on repo docs only.
    """
    try:
        from tools.knowledge.scope_resolver import _load_project_mappings

        for kb_path, key in _load_project_mappings():
            if key == project_key:
                return Path(kb_path)
    except Exception as e:
        logger.warning(f"docs_auditor: vault root resolution failed: {e}")
        return None
    logger.warning(
        "docs_auditor: no knowledge_base mapping for project_key=%s — "
        "vault drift detection skipped (audit continues on repo docs)",
        project_key,
    )
    return None


def _is_markitdown_sidecar(full_path: Path) -> bool:
    """Return True if the file's YAML frontmatter marks it a markitdown sidecar.

    Reads only the first ~15 lines. Best-effort: on any read error, returns False
    (the caller's own read will then surface the error and skip the entry).
    """
    try:
        with open(full_path, encoding="utf-8") as fh:
            head = [next(fh, "") for _ in range(15)]
    except Exception:
        return False
    return any("generated_by: markitdown" in line for line in head)


def _git_commit_ts(path: str, repo_root: Path) -> int:
    """Return the unix timestamp of the last git commit touching ``path``.

    Returns 0 when the path has no commit history yet or git fails — so a mapped
    page that does not exist in git yet reads as older than any vault file and
    surfaces as drift (signalling it needs to be authored).
    """
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "--", path],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(repo_root),
            check=False,
        )
        out = (result.stdout or "").strip()
        return int(out) if out else 0
    except Exception as e:
        logger.warning(f"docs_auditor: git log for {path} failed: {e}")
        return 0


def _detect_vault_site_drift(vault_root: Path, repo_root: Path) -> tuple[list[dict], int]:
    """Compare each curated vault narrative against its mapped site page / repo doc.

    A coarse changed-since heuristic (advisory/report-only): if the vault file's
    filesystem mtime is newer than the mapped target's last git-commit timestamp,
    the narrative has drifted from the target and a finding is emitted. Never
    blocking, never auto-rewriting.

    Returns ``(issue_findings, vault_narratives_compared)``. Only narratives that
    are successfully read (not secrets-guarded, present, not a markitdown sidecar)
    count toward ``vault_narratives_compared`` so a silently-broken mapping is
    distinguishable from genuine zero-drift.
    """
    findings: list[dict] = []
    compared = 0

    for vault_rel_path, (site_page, repo_doc) in VAULT_SITE_MAPPING.items():
        # 1. Security guard: never read (or let into a log/issue) a secrets/ path.
        if _is_secrets_path(vault_rel_path, vault_root):
            logger.warning("docs_auditor: skipped secrets-guarded mapping entry (not read)")
            continue

        full_path = vault_root / vault_rel_path

        # 2. Must resolve and read cleanly.
        try:
            if not full_path.exists():
                logger.warning(
                    "docs_auditor: vault narrative '%s' not found — skipped", vault_rel_path
                )
                continue
        except OSError as e:
            logger.warning(f"docs_auditor: cannot stat vault narrative '{vault_rel_path}': {e}")
            continue

        # 3. Skip markitdown sidecars (defensive — none of the curated entries are).
        if _is_markitdown_sidecar(full_path):
            logger.warning(
                "docs_auditor: vault narrative '%s' is a markitdown sidecar — skipped",
                vault_rel_path,
            )
            continue

        # 4. Successfully read this narrative.
        try:
            vault_mtime = full_path.stat().st_mtime
        except (OSError, UnicodeDecodeError) as e:
            logger.warning(f"docs_auditor: cannot read vault narrative '{vault_rel_path}': {e}")
            continue
        compared += 1

        # 5/6/7. Compare vault mtime against the site page's last commit timestamp.
        site_ts = _git_commit_ts(site_page, repo_root)
        if vault_mtime > site_ts:
            findings.append(
                {
                    "title": (
                        f"docs-auditor: vault narrative '{vault_rel_path}' "
                        f"has drifted from {site_page}"
                    ),
                    "body": (
                        f"The vault narrative `{vault_rel_path}` was modified more recently "
                        f"(mtime {vault_mtime:.0f}) than `{site_page}` was last committed "
                        f"(commit ts {site_ts}). Review and reconcile the site page against "
                        f"the canonical vault source."
                    ),
                    "category": "vault-drift",
                }
            )

        # 8. Same comparison against the optional repo-doc counterpart.
        if repo_doc is not None:
            repo_ts = _git_commit_ts(repo_doc, repo_root)
            if vault_mtime > repo_ts:
                findings.append(
                    {
                        "title": (
                            f"docs-auditor: vault narrative '{vault_rel_path}' "
                            f"has drifted from {repo_doc}"
                        ),
                        "body": (
                            f"The vault narrative `{vault_rel_path}` was modified more recently "
                            f"(mtime {vault_mtime:.0f}) than `{repo_doc}` was last committed "
                            f"(commit ts {repo_ts}). Review and reconcile the repo doc against "
                            f"the canonical vault source."
                        ),
                        "category": "vault-drift",
                    }
                )

    return findings, compared


def _run_vault_drift_detection(project_key: str) -> int:
    """Resolve the vault, run the drift detector, and file capped advisory issues.

    Runs beside the ``docs/features/*.md`` rotation (not through
    ``_select_primary_doc``), unconditionally per rotation run. Its own failures
    (vault unresolvable, git-log errors, file read errors) never crash the caller —
    the whole block is wrapped so ``run_docs_auditor`` continues on the repo-doc
    rotation. Vault-drift ``gh issue create`` volume is bounded by
    ``VAULT_DRIFT_ISSUE_CAP``, checked before every filing. Returns the count of
    narratives actually compared (0 when the vault is unresolvable/empty), which is
    threaded into the liveness payload.
    """
    try:
        vault_root = _resolve_vault_root(project_key)
        if vault_root is None:
            return 0
        findings, compared = _detect_vault_site_drift(vault_root, PROJECT_ROOT)
        issues_filed = 0
        for finding in findings:
            if issues_filed >= VAULT_DRIFT_ISSUE_CAP:
                logger.warning(
                    "docs_auditor: vault-drift issue cap (%d) reached — %d finding(s) suppressed",
                    VAULT_DRIFT_ISSUE_CAP,
                    len(findings) - issues_filed,
                )
                break
            if _file_issue_if_new(finding, PROJECT_ROOT):
                issues_filed += 1
        return compared
    except Exception as e:
        logger.warning(f"docs_auditor: vault drift detection failed: {e}")
        return 0


def run_docs_auditor() -> dict:
    """Daily rotation reflection callable.

    Sequence:
      1. Auth probe (cheap, no side effects)
      2. SETNX lock acquire (global)
      3. Dirty-tree guard
      4. Rotation pick, then the daily-cap and open-PR guards (pre-write)
      5. Run substrate
      6. Zero-diff gate
      7. (If diff) push branch + PR
      8. Memory refresh hook (fire-and-forget)
      9. Telegram notification
      10. Update rotation hash
      11. Liveness signal
      12. Lock release (try/finally)
    """
    findings: list[str] = []

    # 1. Auth probe
    ok, reason = _check_auth()
    if not ok:
        return {
            "status": "disabled",
            "findings": [f"Docs auditor disabled: {reason}"],
            "summary": f"docs-auditor disabled: {reason}",
        }

    # 2. Lock
    if not _acquire_lock(REDIS_RUNNING_KEY, LOCK_TTL_SECONDS):
        return {
            "status": "skipped",
            "findings": ["docs-auditor already running, skipped"],
            "summary": "docs-auditor skipped: locked",
        }

    project_key = os.environ.get("VALOR_PROJECT_KEY", "valor").strip() or "valor"

    try:
        # 3. Dirty-tree guard. Deliberately files nothing: `_git_dirty` tests the
        # whole shared main checkout, where concurrent lanes routinely hold
        # uncommitted work as a matter of routine, so a filing guard would mint
        # issues blaming the auditor for a peer's dirt. The escalation belongs on
        # the failure path below, which knows it caused the dirt — this guard,
        # which cannot know, stays quiet (Q4 item 5).
        if _git_dirty(PROJECT_ROOT):
            _write_liveness("(dirty)", "skipped", None, 0)
            return {
                "status": "skipped",
                "findings": ["docs-auditor skipped: working tree dirty"],
                "summary": "docs-auditor skipped: dirty_tree",
            }

        # 3b. Vault<->site drift detection — runs beside the repo-doc rotation,
        # unconditionally, NOT gated behind the _select_primary_doc pick. Its own
        # failures never crash the rotation (wrapped internally).
        vault_narratives_compared = _run_vault_drift_detection(project_key)

        # 4. Rotation pick
        primary, _last_run = _select_primary_doc(PROJECT_ROOT, project_key)
        if primary is None:
            _write_liveness("(no-candidates)", "skipped", None, 0)
            return {
                "status": "skipped",
                "findings": ["No candidate docs found"],
                "summary": "docs-auditor skipped: no candidates",
            }

        slug = _path_to_slug(primary)

        # 4b. PR guards — hoisted here from _push_branch_and_pr so they fire
        # strictly BEFORE the substrate writes to the shared main checkout. They
        # sit after the dirty-tree guard, after _run_vault_drift_detection (which
        # its own comment declares runs unconditionally), and after `slug` is
        # computed, which _has_open_pr_for_slug needs. A fired guard performs no
        # working-tree write and no git operation — but it MUST still stamp the
        # rotation hash for the picked doc, exactly as the zero-diff path does.
        # Without the stamp, _select_primary_doc re-picks the same
        # least-recently-audited doc on every subsequent run for as long as the
        # guard fires, and a withheld PR (which the sweeper never closes) would
        # pin the rotation on one doc permanently while reporting "skipped".
        guard_reason: str | None = None
        if _daily_pr_cap_reached(PROJECT_ROOT):
            guard_reason = "daily PR cap reached"
        elif _has_open_pr_for_slug(slug, PROJECT_ROOT):
            guard_reason = f"open PR already exists for {slug}"
        if guard_reason is not None:
            logger.info(f"docs_auditor: {guard_reason}, skipping before any write")
            _update_rotation_hash(project_key, [str(primary)])
            _write_liveness(slug, "skipped", None, 0, fixes_withheld=0)
            return {
                "status": "skipped",
                "findings": [f"docs-auditor skipped: {guard_reason}"],
                "summary": f"docs-auditor skipped ({slug}): {guard_reason}",
            }

        # 5. Substrate
        result = audit(
            primary_path=primary,
            scope_mode="rotation",
            apply_mode="apply",
            project_key=project_key,
            repo_root=PROJECT_ROOT,
        )

        files_touched: list[str] = result.get("files_touched", [])
        # Existence-invariant rejections. This is the one caller with no human
        # review before the PR opens — every rotation PR still requires a human
        # merge, but the withheld count must reach every surface this function
        # produces so the human reviewing it sees it, not just a log line:
        # findings, summary, Telegram, the PR body, and the Redis liveness
        # summary, which is the only durable queryable one.
        # Telegram has two mutually exclusive senders, and a run can also reach
        # neither. Three cases: files were touched — step 9 sends the pass
        # summary; nothing was touched but fixes were withheld — the zero-diff
        # early return sends the withheld alert, the loudest case and one step 9
        # can never reach; nothing was touched and nothing was withheld — a clean
        # zero-diff run, which stays silent.
        withheld: list[dict] = result.get("withheld", [])
        fixes_withheld: int = result.get("fixes_withheld", 0)
        withheld_note = (
            f"; {fixes_withheld} fix(es) withheld (target-absent)" if fixes_withheld else ""
        )

        # Q5 (B4): file one issue per withheld entry so a human is pointed at the
        # specific substitution the existence invariant rejected, not just a log
        # line. Deduped per-defect by `_file_issue_if_new`'s title-based gate.
        # Bounded at the module's shared per-run cap (NEW-4 / R3-3) — a withheld
        # flood must not spend a different budget than the advisory loop's.
        if withheld:
            for i, w in enumerate(withheld):
                if i >= ISSUE_FILING_PER_RUN_CAP:
                    logger.warning(
                        "docs_auditor: withheld-fix per-run cap (%d) reached — "
                        "%d finding(s) suppressed",
                        ISSUE_FILING_PER_RUN_CAP,
                        len(withheld) - ISSUE_FILING_PER_RUN_CAP,
                    )
                    break
                # `old` is a regex source (rf"\b{re.escape(old_term)}\b"), not the
                # substitution term itself — unwrap it before it becomes the
                # dedup key (R5-3), or the title carries a literal `\b` into
                # `gh issue list --search`.
                term = re.sub(
                    r"\\(.)",
                    r"\1",
                    w["old"].removeprefix(r"\b").removesuffix(r"\b"),
                )
                _file_issue_if_new(
                    {
                        "title": (
                            f"docs-auditor: withheld fix in {w.get('doc')} "
                            f"({term} -> {w.get('new')})"
                        ),
                        "body": (
                            f"The docs auditor tried to rewrite `{term}` to "
                            f"`{w.get('new')}` in `{w.get('doc')}`, but the rewrite "
                            "would have introduced a path that does not exist in "
                            f"the working tree ({w.get('reason', 'target-absent')}), "
                            "so it was withheld and the file was left unchanged."
                        ),
                        "category": "withheld-fix",
                    },
                    PROJECT_ROOT,
                )

        # 6. Zero-diff gate
        if not files_touched or _git_diff_quiet(PROJECT_ROOT):
            _update_rotation_hash(project_key, [str(primary)])
            _write_liveness(slug, "skipped", None, 0, fixes_withheld=fixes_withheld)
            if fixes_withheld:
                _send_telegram_notification(
                    f"docs-auditor pass for {slug}: zero-diff, no PR"
                    f"\n⚠️ {fixes_withheld} fix(es) withheld — target path absent; "
                    "nothing was written and no PR was opened to review them"
                )
            return {
                "status": "skipped",
                "findings": [f"docs-auditor: zero-diff for {primary}{withheld_note}"],
                "summary": f"docs-auditor: zero-diff ({slug}){withheld_note}",
            }

        # 7. Memory refresh hook (fire-and-forget) — fired after commit
        # 8. Push branch + PR. The guards moved to the preflight, so a None here
        # unambiguously means the branch/commit/push/PR or the restore failed —
        # never "a guard declined". That routes to status="error": no success
        # Telegram, no rotation-hash stamp (the doc was written but not audited to
        # completion, so re-picking it next run is correct), and no liveness "ok".
        pr_url = _push_branch_and_pr(slug, PROJECT_ROOT, files_touched, withheld=withheld)

        if pr_url is None:
            findings.append(
                f"docs-auditor: rotation wrote {len(files_touched)} file(s) for {slug} "
                "but produced no PR"
            )
            # R5-1: a wedged shared checkout must escalate through a real channel
            # before this returns, not just log a warning nobody reads — the
            # `status="error"` alone reaches nobody, since
            # agent/reflection_scheduler.py:639-640 reads only `projects` from
            # this dict. Slug-keyed only, no run id or date, so a failure that
            # repeats every run files once. Deliberately silent on which step
            # failed and on whether the scoped restore succeeded — neither fact
            # reaches this branch (`_push_branch_and_pr` returns `str | None`),
            # and a body that asserts a restore outcome it never observed is
            # worse than one that omits it.
            _file_issue_if_new(
                {
                    "title": f"docs-auditor: rotation failed to produce a PR for {slug}",
                    "body": (
                        f"Rotation wrote {len(files_touched)} file(s) for `{slug}` but the "
                        "branch/push/PR sequence did not produce a PR URL.\n\n"
                        f"Files touched: {', '.join(files_touched)}\n\n"
                        "If the shared checkout is left dirty, clean it up with:\n\n"
                        "```\n"
                        'git -C "${AI_REPO_ROOT:-$HOME/src/ai}" status --porcelain -- docs .claude\n'
                        "```\n\n"
                        "See the `docs_auditor: branch/push/PR …` warning in the reflection "
                        "log for the step that failed."
                    ),
                    "category": "operational-failure",
                },
                PROJECT_ROOT,
            )
            return {
                "status": "error",
                "findings": findings,
                "summary": (
                    f"docs-auditor error ({slug}): {len(files_touched)} file(s) written, "
                    f"no PR created{withheld_note}"
                ),
            }

        try:
            refresh_docs_in_memory(files_touched)
        except Exception as e:
            logger.warning(f"docs_auditor: refresh_docs_in_memory hook failed: {e}")

        # 9. Telegram notification. Every rotation PR requires a human merge —
        # `/do-merge` — and is closed unmerged at STALE_PR_AGE_DAYS if nobody
        # acts, which is the intended "nobody cared" outcome, not a failure mode.
        msg = (
            f"docs-auditor pass for {slug}: "
            f"{len(files_touched)} files, {result.get('fixes_applied', 0)} fixes"
            + (
                f"\n⚠️ {fixes_withheld} fix(es) withheld — target path absent; see the "
                "filed issue(s) for details"
                if fixes_withheld
                else ""
            )
            + f"\nPR: {pr_url}"
            + f"\nReview required — closed unmerged after {STALE_PR_AGE_DAYS} days if unreviewed."
        )
        _send_telegram_notification(msg)

        # 10. Update rotation hash for all touched files
        _update_rotation_hash(project_key, files_touched)

        # 11. Liveness signal (threads the vault-drift compared count — the only
        # call site that ran the vault comparison; the other 3 stay 4-arg — plus
        # the withheld count, so Redis distinguishes a withheld run from a clean one).
        _write_liveness(
            slug,
            "ok",
            pr_url,
            len(files_touched),
            vault_narratives_compared,
            fixes_withheld=fixes_withheld,
        )

        findings.append(
            f"Touched {len(files_touched)} files; {result.get('fixes_applied', 0)} fixes applied"
        )
        if fixes_withheld:
            findings.append(
                f"{fixes_withheld} fix(es) withheld by the existence invariant "
                "(target-absent); see the filed issue(s) for details"
            )
            findings.extend(
                f"withheld: {w.get('doc')} {w.get('old')!r} -> {w.get('new')!r}" for w in withheld
            )
        findings.append(f"PR: {pr_url}")

        return {
            "status": "ok",
            "findings": findings,
            "summary": (
                f"docs-auditor: {len(files_touched)} files touched, "
                f"{result.get('fixes_applied', 0)} fixes{withheld_note}, "
                f"PR={pr_url}"
            ),
        }

    except Exception as e:
        logger.warning(f"docs_auditor: unexpected error: {e}")
        return {
            "status": "error",
            "findings": [f"docs-auditor error: {e}"],
            "summary": f"docs-auditor error: {e}",
        }
    finally:
        # 12. Lock release
        _release_lock(REDIS_RUNNING_KEY)


# ---------------------------------------------------------------------------
# Branch sweeper reflection
# ---------------------------------------------------------------------------


def run_docs_branch_sweeper() -> dict:
    """Sweep stale ``docs-audit/*`` branches and PRs.

    Conservative: only touches ``docs-audit/*`` branches, never any other
    prefix. Every ``docs-audit/*`` PR requires a human merge; this sweeper only
    closes ones nobody reviewed within ``STALE_PR_AGE_DAYS`` — except a PR
    carrying ``WITHHELD_PR_MARKER``, which it never closes or deletes, because
    the withheld fixes it holds already have their own escalation issue and
    closing the PR would discard the surviving fixes that passed the existence
    invariant.
    """
    if not _acquire_lock(REDIS_SWEEPER_RUNNING_KEY, SWEEPER_LOCK_TTL_SECONDS):
        return {
            "status": "ok",
            "findings": ["sweeper already running, skipped"],
            "summary": "do-docs-branch-sweeper skipped: locked",
        }

    findings: list[str] = []
    branches_deleted = 0
    prs_closed = 0

    try:
        # List remote branches under docs-audit/
        try:
            res = subprocess.run(
                ["git", "ls-remote", "--heads", "origin", "docs-audit/*"],
                capture_output=True,
                text=True,
                timeout=settings.timeouts.git_subprocess_s,
                cwd=str(PROJECT_ROOT),
            )
        except Exception as e:
            return {
                "status": "error",
                "findings": [f"sweeper ls-remote failed: {e}"],
                "summary": f"do-docs-branch-sweeper error: {e}",
            }

        for line in res.stdout.splitlines():
            parts = line.strip().split("\t")
            if len(parts) != 2:
                continue
            ref = parts[1]
            if not ref.startswith("refs/heads/docs-audit/"):
                continue
            branch = ref[len("refs/heads/") :]

            # Query PR state for this branch
            try:
                pr_res = subprocess.run(
                    [
                        "gh",
                        "pr",
                        "list",
                        "--head",
                        branch,
                        "--state",
                        "all",
                        "--json",
                        "number,state,createdAt,body",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=settings.timeouts.git_subprocess_s,
                    cwd=str(PROJECT_ROOT),
                )
                prs = json.loads(pr_res.stdout) if pr_res.stdout.strip() else []
            except Exception as e:
                logger.warning(f"sweeper: gh pr list failed for {branch}: {e}")
                continue

            now = datetime.now(UTC)

            # Branches with all PRs already closed/merged: delete if branch is old enough
            open_prs = [p for p in prs if p.get("state", "").upper() == "OPEN"]
            closed_prs = [p for p in prs if p.get("state", "").upper() != "OPEN"]
            if prs and not open_prs:
                # All PRs closed — delete branch if oldest closed PR is stale
                newest_close = max((p.get("createdAt", "") for p in closed_prs), default="")
                if newest_close:
                    try:
                        age_days = (
                            now - datetime.fromisoformat(newest_close.replace("Z", "+00:00"))
                        ).days
                        if age_days >= STALE_BRANCH_AGE_DAYS:
                            subprocess.run(
                                ["git", "push", "origin", "--delete", branch],
                                capture_output=True,
                                timeout=settings.timeouts.git_subprocess_s,
                                cwd=str(PROJECT_ROOT),
                                check=False,
                            )
                            branches_deleted += 1
                            findings.append(
                                f"Deleted branch with closed PR: {branch} ({age_days}d)"
                            )
                    except Exception as e:
                        logger.warning(
                            f"sweeper: closed-PR branch cleanup failed for {branch}: {e}"
                        )
                continue

            if not prs:
                # No PR ever; check branch age via creation time of the latest commit
                try:
                    commit_res = subprocess.run(
                        ["git", "log", "-1", "--format=%cI", f"origin/{branch}"],
                        capture_output=True,
                        text=True,
                        timeout=settings.timeouts.git_subprocess_s,
                        cwd=str(PROJECT_ROOT),
                    )
                    commit_ts = commit_res.stdout.strip()
                    if not commit_ts:
                        continue
                    age_days = (now - datetime.fromisoformat(commit_ts)).days
                    if age_days >= STALE_BRANCH_AGE_DAYS:
                        subprocess.run(
                            ["git", "push", "origin", "--delete", branch],
                            capture_output=True,
                            timeout=settings.timeouts.git_subprocess_s,
                            cwd=str(PROJECT_ROOT),
                            check=False,
                        )
                        branches_deleted += 1
                        findings.append(f"Deleted stale branch: {branch} ({age_days}d)")
                except Exception as e:
                    logger.warning(f"sweeper: branch-age check failed for {branch}: {e}")
                continue

            for pr in open_prs:
                state = pr.get("state", "").upper()
                if state not in ("OPEN",):
                    continue
                created_at = pr.get("createdAt", "")
                if not created_at:
                    continue
                try:
                    age_days = (
                        now - datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    ).days
                except Exception:
                    continue
                pr_num = pr.get("number")
                if not pr_num:
                    continue

                # A withheld PR is exempt from stale-close: closing it would
                # discard fixes that already passed the existence invariant.
                # The escalation issue is distinct from `_file_issue_if_new`'s
                # per-defect withheld-fix titles, since a same-title filing is
                # a guaranteed no-op — this names the PR, not the substitution.
                if WITHHELD_PR_MARKER in (pr.get("body") or ""):
                    _file_issue_if_new(
                        {
                            "title": f"docs-auditor: withheld PR #{pr_num} still unreviewed",
                            "body": (
                                f"PR #{pr_num} (branch `{branch}`) is {age_days} day(s) old "
                                "and carries withheld fixes that need a human review before "
                                "merge. The sweeper will not close it or delete its branch."
                            ),
                            "category": "withheld-pr",
                        },
                        PROJECT_ROOT,
                    )
                    continue

                if age_days >= STALE_PR_AGE_DAYS:
                    try:
                        subprocess.run(
                            ["gh", "pr", "close", "--delete-branch", str(pr_num)],
                            capture_output=True,
                            timeout=settings.timeouts.git_subprocess_s,
                            cwd=str(PROJECT_ROOT),
                            check=False,
                        )
                        prs_closed += 1
                        findings.append(f"Closed stale PR #{pr_num} (branch={branch}, {age_days}d)")
                    except Exception as e:
                        logger.warning(f"sweeper: gh pr close failed for #{pr_num}: {e}")

        summary = (
            f"do-docs-branch-sweeper: {branches_deleted} branches deleted, "
            f"{prs_closed} PRs closed"
        )
        logger.info(summary)
        return {"status": "ok", "findings": findings, "summary": summary}

    finally:
        _release_lock(REDIS_SWEEPER_RUNNING_KEY)


# ---------------------------------------------------------------------------
# CLI entrypoint (one-shot for /do-docs)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Allow `python -m reflections.docs_auditor` to print a JSON result for the
    # ``/do-docs`` skill bash block. Args via env: SCOPE_MODE, APPLY_MODE.
    #
    # This runs no git of its own and **leaves a dirty working tree**: the fixes
    # `audit()` applied are unstaged on exit, and reviewing and committing them
    # is the caller's job.
    scope = os.environ.get("DOCS_AUDIT_SCOPE", "pr-changed-files")
    apply = os.environ.get("DOCS_AUDIT_APPLY", "apply")
    project = os.environ.get("VALOR_PROJECT_KEY", "valor")
    out = audit(
        primary_path=None,
        scope_mode=scope,
        apply_mode=apply,
        project_key=project,
    )
    print(json.dumps(out))
