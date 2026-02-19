#!/usr/bin/env python3
"""
docs_auditor.py — Standalone documentation audit module.

Analyzes all .md files in docs/ (excluding docs/plans/) against the actual
codebase. Uses the Anthropic API (Haiku for initial analysis, Sonnet for
uncertain cases) to produce KEEP / UPDATE / DELETE verdicts, then applies them.

Integrated into daydream.py as a weekly maintenance step.

Usage:
    python scripts/docs_auditor.py [--dry-run]
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Ensure project root is importable
_project_root = str(Path(__file__).parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

try:
    import anthropic as _anthropic_module
except ImportError:
    _anthropic_module = None  # type: ignore[assignment]

logger = logging.getLogger("docs_auditor")

# ---------------------------------------------------------------------------
# Model constants
# ---------------------------------------------------------------------------
MODEL_HAIKU = "claude-haiku-4-5-20251001"
MODEL_SONNET = "claude-sonnet-4-6"

# Confidence keywords that suggest the Haiku answer needs escalation
_UNCERTAIN_PHRASES = [
    "uncertain",
    "unclear",
    "cannot determine",
    "not sure",
    "may have",
    "might be",
    "possibly",
    "ambiguous",
]

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class Verdict:
    """Result of auditing a single documentation file."""

    action: str  # "KEEP", "UPDATE", or "DELETE"
    rationale: str
    corrections: list[str] = field(default_factory=list)
    low_confidence: bool = False


@dataclass
class AuditSummary:
    """Aggregate results for a full audit run."""

    skipped: bool = False
    skip_reason: str = ""
    kept: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    relocated: list[str] = field(default_factory=list)
    renamed: list[str] = field(default_factory=list)
    verdicts: dict[str, Verdict] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        if self.skipped:
            return f"Docs audit skipped: {self.skip_reason}"
        lines = [
            "=== Docs Audit Summary ===",
            f"KEPT     ({len(self.kept)}): {', '.join(self.kept) or 'none'}",
            f"UPDATED  ({len(self.updated)}): {', '.join(self.updated) or 'none'}",
            f"DELETED  ({len(self.deleted)}): {', '.join(self.deleted) or 'none'}",
            f"RELOCATED({len(self.relocated)}): {', '.join(self.relocated) or 'none'}",
            f"RENAMED  ({len(self.renamed)}): {', '.join(self.renamed) or 'none'}",
        ]
        if self.errors:
            lines.append(f"ERRORS   ({len(self.errors)}): {'; '.join(self.errors)}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Reference extraction helpers
# ---------------------------------------------------------------------------

# Patterns for extracting candidate references from markdown text
_FILE_PATH_RE = re.compile(
    r"(?<!\w)"  # not preceded by a word char
    r"((?:[a-zA-Z0-9_.-]+/)+[a-zA-Z0-9_.\-]+)"  # path segments with /
    r"(?!\w)"
)
_ENV_VAR_RE = re.compile(r"\b([A-Z][A-Z0-9_]{2,})\b")
_PYTHON_IMPORT_RE = re.compile(r"(?:^|\s)(?:from|import)\s+([\w.]+)", re.MULTILINE)
_BACKTICK_RE = re.compile(r"`([^`\n]+)`")
_CODE_BLOCK_RE = re.compile(r"```(?:\w*\n)?(.*?)```", re.DOTALL)


def _extract_references(content: str) -> dict[str, list[str]]:
    """Extract candidate references from markdown content.

    Returns a dict keyed by reference type, each value a deduplicated list
    of candidate strings extracted from the document.
    """
    refs: dict[str, list[str]] = {
        "file_paths": [],
        "env_vars": [],
        "backtick_tokens": [],
        "python_imports": [],
    }

    # Extract from backtick spans (most reliable signal)
    backtick_hits = _BACKTICK_RE.findall(content)
    refs["backtick_tokens"] = list(dict.fromkeys(backtick_hits))

    # Extract file-path-shaped strings everywhere
    for m in _FILE_PATH_RE.finditer(content):
        candidate = m.group(1)
        # Skip pure version strings like "1.0.0" or date-like "2024-01-01"
        if re.match(r"^\d+[\.\-]\d+", candidate):
            continue
        refs["file_paths"].append(candidate)
    refs["file_paths"] = list(dict.fromkeys(refs["file_paths"]))

    # ENV vars (ALL_CAPS identifiers 3+ chars)
    # Filter to things that look like env vars (not plain words)
    env_candidates = _ENV_VAR_RE.findall(content)
    refs["env_vars"] = list(
        dict.fromkeys(
            v
            for v in env_candidates
            if "_" in v or len(v) >= 5  # likely an env var / constant
        )
    )

    # Python import targets mentioned in code blocks
    code_blocks = _CODE_BLOCK_RE.findall(content)
    for block in code_blocks:
        for m in _PYTHON_IMPORT_RE.finditer(block):
            refs["python_imports"].append(m.group(1).split(".")[0])
    refs["python_imports"] = list(dict.fromkeys(refs["python_imports"]))

    return refs


def _verify_references(
    refs: dict[str, list[str]],
    repo_root: Path,
) -> dict[str, dict[str, bool]]:
    """Verify each reference against the filesystem.

    Returns a dict keyed by reference type → {candidate: exists_bool}.
    """
    results: dict[str, dict[str, bool]] = {}

    # --- File paths ---
    file_results: dict[str, bool] = {}
    for candidate in refs.get("file_paths", []):
        full = repo_root / candidate
        file_results[candidate] = full.exists()
    results["file_paths"] = file_results

    # --- ENV vars — check .env.example and grep codebase ---
    env_results: dict[str, bool] = {}
    env_example = repo_root / ".env.example"
    env_example_text = env_example.read_text() if env_example.exists() else ""
    for var in refs.get("env_vars", []):
        if var in env_example_text:
            env_results[var] = True
            continue
        # grep codebase for the var name
        try:
            result = subprocess.run(
                ["grep", "-r", "--include=*.py", "-l", var, str(repo_root)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            env_results[var] = bool(result.stdout.strip())
        except Exception:
            env_results[var] = False
    results["env_vars"] = env_results

    # --- Python imports — check if module exists in repo or pyproject ---
    import_results: dict[str, bool] = {}
    pyproject_text = ""
    pyproject = repo_root / "pyproject.toml"
    if pyproject.exists():
        pyproject_text = pyproject.read_text()

    for mod in refs.get("python_imports", []):
        # Check if it's a known stdlib / installed package (presence in pyproject)
        if mod in pyproject_text:
            import_results[mod] = True
            continue
        # Check if there's a local module directory
        mod_path = repo_root / mod.replace(".", "/")
        if mod_path.exists() or (mod_path.parent / f"{mod}.py").exists():
            import_results[mod] = True
            continue
        import_results[mod] = False
    results["python_imports"] = import_results

    # --- Backtick tokens — heuristic: check if they look like file paths ---
    backtick_results: dict[str, bool] = {}
    for token in refs.get("backtick_tokens", []):
        # Only verify tokens that look like paths or commands
        if "/" in token or token.endswith(".py") or token.endswith(".sh"):
            full = repo_root / token.lstrip("/")
            backtick_results[token] = full.exists()
        else:
            # Don't fail on non-path tokens (class names, commands, etc.)
            backtick_results[token] = True  # assume OK unless path-shaped
    results["backtick_tokens"] = backtick_results

    return results


def _build_verification_report(
    path: Path,
    content: str,
    refs: dict[str, list[str]],
    verification: dict[str, dict[str, bool]],
) -> str:
    """Build a human-readable verification report for the LLM prompt."""
    lines = [
        f"File: {path}",
        "",
        "## Verification Results",
        "",
    ]

    all_ok = True
    broken: list[str] = []

    for ref_type, results in verification.items():
        if not results:
            continue
        lines.append(f"### {ref_type.replace('_', ' ').title()}")
        for candidate, exists in results.items():
            status = "OK" if exists else "MISSING"
            lines.append(f"  {status}: {candidate}")
            if not exists:
                all_ok = False
                broken.append(f"{ref_type}/{candidate}")
        lines.append("")

    if all_ok:
        lines.append("**All references verified** — no broken references found.")
    else:
        lines.append(f"**Broken references ({len(broken)})**: {', '.join(broken)}")

    lines.append("")
    lines.append("## Document Content (first 2000 chars)")
    lines.append(content[:2000])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# DocsAuditor
# ---------------------------------------------------------------------------


class DocsAuditor:
    """Audit documentation files against the actual codebase.

    Note on relocation: ``run()`` detects non-canonical doc locations and records
    them in ``AuditSummary.relocated``, but does *not* move any files.  Relocation
    detection here is advisory only — the ``/do-docs-audit`` skill (SKILL.md Step 6)
    handles actual physical relocation via Claude Code after the audit report is
    produced.
    """

    STATE_FILE = Path("data/daydream_state.json")
    AUDIT_FREQUENCY_DAYS = 7
    INDEX_FILES = [
        "docs/README.md",
        "docs/features/README.md",
        "CLAUDE.md",
    ]

    def __init__(self, repo_root: Path, dry_run: bool = False) -> None:
        self.repo_root = repo_root.resolve()
        self.dry_run = dry_run
        self._client: Any = None  # lazy-init anthropic client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enumerate_docs(self) -> list[Path]:
        """Find all .md files in docs/ except docs/plans/.

        Returns paths relative to repo_root.
        """
        docs_dir = self.repo_root / "docs"
        if not docs_dir.exists():
            return []

        result: list[Path] = []
        for md_file in sorted(docs_dir.rglob("*.md")):
            rel = md_file.relative_to(self.repo_root)
            # Exclude docs/plans/
            if str(rel).startswith("docs/plans/"):
                continue
            result.append(rel)
        return result

    def analyze_doc(self, path: Path) -> Verdict:
        """Use Anthropic API to analyze a doc and return a verdict.

        1. Read the doc content
        2. Extract reference candidates
        3. Verify each reference against the filesystem
        4. Call Haiku with verification results to get verdict
        5. Escalate to Sonnet if Haiku returns a low-confidence result
        """
        full_path = self.repo_root / path
        try:
            content = full_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return Verdict(
                action="KEEP",
                rationale=f"Could not read file: {e}",
                low_confidence=True,
            )

        # Extract and verify references
        refs = _extract_references(content)
        verification = _verify_references(refs, self.repo_root)
        report = _build_verification_report(path, content, refs, verification)

        # Call LLM
        verdict = self._call_llm_for_verdict(report, MODEL_HAIKU)
        if verdict.low_confidence:
            logger.info(
                "Haiku returned low-confidence verdict for %s, escalating to Sonnet",
                path,
            )
            verdict = self._call_llm_for_verdict(report, MODEL_SONNET)

        return verdict

    def execute_verdict(self, path: Path, verdict: Verdict) -> None:
        """Apply a verdict: delete file, apply corrections, or skip.

        In dry-run mode, prints the verdict without making changes.
        """
        full_path = self.repo_root / path

        if verdict.action == "DELETE":
            if self.dry_run:
                print(f"[DRY RUN] DELETE {path}: {verdict.rationale}")
            else:
                logger.info("Deleting %s: %s", path, verdict.rationale)
                full_path.unlink(missing_ok=True)

        elif verdict.action == "UPDATE":
            if self.dry_run:
                print(f"[DRY RUN] UPDATE {path}: {verdict.rationale}")
                for i, correction in enumerate(verdict.corrections, 1):
                    print(f"  Correction {i}: {correction}")
            else:
                logger.info("Updating %s: %s", path, verdict.rationale)
                self._apply_corrections(path, verdict.corrections)

        else:  # KEEP
            if self.dry_run:
                print(f"[DRY RUN] KEEP {path}: {verdict.rationale}")

    def sweep_index_files(self, deleted: list[Path]) -> None:
        """Remove broken links from index files after deletions.

        Searches docs/README.md, docs/features/README.md, and CLAUDE.md
        for references to deleted files and removes those lines/rows.
        """
        if not deleted or self.dry_run:
            if self.dry_run and deleted:
                print(
                    f"[DRY RUN] Would sweep index files for {len(deleted)} deleted docs"
                )
            return

        deleted_names = {p.name for p in deleted}
        deleted_stems = {p.stem for p in deleted}

        for index_rel in self.INDEX_FILES:
            index_path = self.repo_root / index_rel
            if not index_path.exists():
                continue

            original = index_path.read_text(encoding="utf-8")
            lines = original.splitlines(keepends=True)
            new_lines: list[str] = []
            changed = False

            for line in lines:
                # Check if this line references any deleted file
                should_remove = False
                for name in deleted_names | deleted_stems:
                    if name in line and ("[" in line or "(" in line):
                        should_remove = True
                        break
                if should_remove:
                    logger.info(
                        "Removing broken link from %s: %s", index_rel, line.rstrip()
                    )
                    changed = True
                else:
                    new_lines.append(line)

            if changed:
                index_path.write_text("".join(new_lines), encoding="utf-8")
                logger.info("Swept index file: %s", index_rel)

    def commit_results(self, summary: AuditSummary) -> None:
        """git add and commit with detailed summary of all verdicts.

        Skipped in dry-run mode.
        """
        if self.dry_run:
            print("[DRY RUN] Skipping git commit")
            return

        if not summary.updated and not summary.deleted:
            logger.info("No changes to commit")
            return

        # Stage changes
        try:
            subprocess.run(
                ["git", "add", "-A", "docs/", "CLAUDE.md"],
                cwd=self.repo_root,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            logger.error("git add failed: %s", e)
            return

        # Build commit message
        kept_count = len(summary.kept)
        updated_count = len(summary.updated)
        deleted_count = len(summary.deleted)

        body_lines = [
            f"Kept: {kept_count} | Updated: {updated_count} | Deleted: {deleted_count}",
            "",
        ]

        for doc_path, verdict in summary.verdicts.items():
            body_lines.append(f"- {verdict.action} {doc_path}: {verdict.rationale}")

        commit_msg = (
            "Docs audit: remove stale, correct outdated references\n\n"
            + "\n".join(body_lines)
        )

        try:
            subprocess.run(
                ["git", "commit", "-m", commit_msg],
                cwd=self.repo_root,
                check=True,
                capture_output=True,
            )
            logger.info("Committed audit results")
        except subprocess.CalledProcessError as e:
            logger.error("git commit failed: %s", e)

    def run(self) -> AuditSummary:
        """Main entry point: enumerate → frequency gate → analyze → execute → sweep → commit.

        Returns AuditSummary with full results.
        """
        # --- Weekly frequency gate ---
        if self._should_skip():
            state = self._load_state()
            last = state.get("last_audit_date", "never")
            summary = AuditSummary(skipped=True, skip_reason=f"last run: {last}")
            logger.info("Skipping docs audit: %s", summary.skip_reason)
            return summary

        summary = AuditSummary()
        docs = self.enumerate_docs()

        if not docs:
            logger.info("No documentation files found to audit")
            return summary

        logger.info("Auditing %d documentation files", len(docs))

        for path in docs:
            try:
                verdict = self.analyze_doc(path)
                summary.verdicts[str(path)] = verdict

                if verdict.action == "DELETE":
                    summary.deleted.append(str(path))
                elif verdict.action == "UPDATE":
                    summary.updated.append(str(path))
                else:
                    summary.kept.append(str(path))

                self.execute_verdict(path, verdict)

            except Exception as e:
                msg = f"Error auditing {path}: {e}"
                logger.error(msg)
                summary.errors.append(msg)
                summary.kept.append(str(path))  # safe default: keep on error

        # Normalize filenames to lowercase-with-hyphens
        for path in docs:
            if str(path) not in summary.deleted:
                normalized = self._normalize_filename(path)
                if normalized is not None:
                    rename_note = f"{path} → {normalized}"
                    if self.rename_doc(path, normalized):
                        summary.renamed.append(rename_note)
                        logger.info("Normalized filename: %s", rename_note)

        # Check and record docs that are in non-canonical locations.
        # NOTE: This is advisory only — no files are moved here.  The
        # /do-docs-audit skill (SKILL.md Step 6) reads summary.relocated and
        # performs the actual relocation via Claude Code.
        for path in docs:
            if str(path) not in summary.deleted:
                suggested = self._check_doc_location(path)
                if suggested is not None:
                    relocation_note = f"{path} -> {suggested}"
                    summary.relocated.append(relocation_note)
                    logger.info("Doc in non-canonical location: %s", relocation_note)

        # Sweep index files for broken links caused by deletions
        deleted_paths = [Path(p) for p in summary.deleted]
        self.sweep_index_files(deleted_paths)

        # Commit all changes
        self.commit_results(summary)

        # Update the last_audit_date in state
        if not self.dry_run:
            self._record_audit_date()

        return summary

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        """Lazy-init Anthropic client."""
        if self._client is None:
            if _anthropic_module is None:
                raise RuntimeError("anthropic package is not installed")
            self._client = _anthropic_module.Anthropic()
        return self._client

    def _call_llm_for_verdict(self, report: str, model: str) -> Verdict:
        """Send the verification report to the LLM and parse the verdict."""
        prompt = f"""You are a documentation auditor. Given the following verification report
for a documentation file, produce a verdict.

{report}

---

Based on the verification results above, issue one of these verdicts:

KEEP — all or nearly all concrete references are verified, doc is accurate
UPDATE — some references are wrong or outdated; list specific corrections
DELETE — the document's core subject does not exist in the codebase

Conservative threshold: prefer UPDATE over DELETE when uncertain.
Only DELETE when the primary subject of the document is verifiably gone.

Respond in EXACTLY this format (no extra text):

VERDICT: [KEEP|UPDATE|DELETE]
CONFIDENCE: [HIGH|LOW]
RATIONALE: [one concise sentence]
CORRECTIONS:
- [correction 1, or "none" if KEEP]
- [correction 2]
"""

        try:
            client = self._get_client()
            response = client.messages.create(
                model=model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            response_text = response.content[0].text.strip()
            return self._parse_verdict(response_text)
        except Exception as e:
            logger.error("LLM call failed (%s): %s", model, e)
            return Verdict(
                action="KEEP", rationale=f"LLM error: {e}", low_confidence=True
            )

    def _parse_verdict(self, text: str) -> Verdict:
        """Parse LLM response into a Verdict dataclass."""
        action = "KEEP"
        rationale = ""
        corrections: list[str] = []
        low_confidence = False

        action_m = re.search(r"VERDICT:\s*(KEEP|UPDATE|DELETE)", text, re.IGNORECASE)
        if action_m:
            action = action_m.group(1).upper()

        confidence_m = re.search(r"CONFIDENCE:\s*(HIGH|LOW)", text, re.IGNORECASE)
        if confidence_m and confidence_m.group(1).upper() == "LOW":
            low_confidence = True

        rationale_m = re.search(r"RATIONALE:\s*(.+?)(?:\n|$)", text)
        if rationale_m:
            rationale = rationale_m.group(1).strip()
        else:
            # Check if response contains uncertain language
            lower = text.lower()
            if any(phrase in lower for phrase in _UNCERTAIN_PHRASES):
                low_confidence = True
            rationale = text[:120].replace("\n", " ")

        # Extract corrections (lines starting with "- " after CORRECTIONS:)
        corrections_m = re.search(r"CORRECTIONS:(.*?)(?:\n\n|\Z)", text, re.DOTALL)
        if corrections_m:
            for line in corrections_m.group(1).strip().splitlines():
                stripped = line.strip().lstrip("- ").strip()
                if stripped and stripped.lower() != "none":
                    corrections.append(stripped)

        return Verdict(
            action=action,
            rationale=rationale,
            corrections=corrections,
            low_confidence=low_confidence,
        )

    def _apply_corrections(self, path: Path, corrections: list[str]) -> None:
        """Apply textual corrections to a document.

        For each correction, attempt a simple find-and-replace.
        Corrections in "OLD → NEW" format are applied directly.
        Other corrections are logged for manual review.
        """
        full_path = self.repo_root / path
        try:
            content = full_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error("Cannot read %s for correction: %s", path, e)
            return

        original = content
        for correction in corrections:
            # Try to parse "old text → new text" or "old text -> new text" pattern
            arrow_m = re.match(r"(.+?)\s*(?:→|->)\s*(.+)", correction)
            if arrow_m:
                old_text = arrow_m.group(1).strip().strip("`'\"")
                new_text = arrow_m.group(2).strip().strip("`'\"")
                if old_text in content:
                    content = content.replace(old_text, new_text, 1)
                    logger.info(
                        "Applied correction in %s: %s → %s", path, old_text, new_text
                    )
                else:
                    logger.warning(
                        "Correction target not found in %s: %s", path, old_text
                    )
            else:
                # Cannot auto-apply — log for visibility
                logger.warning(
                    "Cannot auto-apply correction to %s: %s", path, correction
                )

        if content != original:
            full_path.write_text(content, encoding="utf-8")

    # ------------------------------------------------------------------
    # Doc location enforcement
    # ------------------------------------------------------------------

    #: Subdirectories that are canonical; anything else is non-standard.
    CANONICAL_SUBDIRS = frozenset(
        {"plans", "features", "guides", "testing", "references", "operations"}
    )

    def _check_doc_location(self, path: Path) -> Path | None:
        """Return the correct canonical path if the doc is in a non-canonical subdir.

        Args:
            path: Path relative to repo_root (e.g. ``docs/architecture/foo.md``).

        Returns:
            ``None`` if the doc is already in a canonical location or flat under
            ``docs/``.  Returns the suggested canonical path (also relative to
            repo_root) if the doc lives in a non-standard subdirectory.

        Classification heuristic (no LLM, pure content-based):
        - how-to / step-by-step / getting-started language  → ``docs/guides/``
        - testing patterns / test strategies                 → ``docs/testing/``
        - heavy external URL / package references            → ``docs/references/``
        - code class/function references or code blocks      → ``docs/features/``
        - otherwise                                          → flat ``docs/``

        ``docs/plans/`` is never touched.
        """
        # path is relative to repo_root, e.g. "docs/architecture/foo.md"
        try:
            rel_to_docs = path.relative_to("docs")
        except ValueError:
            # Not under docs/ at all — skip
            return None

        parts = rel_to_docs.parts
        # Flat doc directly under docs/ → OK
        if len(parts) == 1:
            return None

        subdir = parts[0]

        # Already in a canonical subdir → OK
        if subdir in self.CANONICAL_SUBDIRS:
            return None

        # Non-canonical subdir: classify the content
        full_path = self.repo_root / path
        try:
            content = full_path.read_text(encoding="utf-8", errors="replace").lower()
        except Exception:
            content = ""

        filename = path.name
        suggested_subdir = self._classify_doc_content(content)
        return (
            Path("docs") / suggested_subdir / filename
            if suggested_subdir
            else Path("docs") / filename
        )

    # ------------------------------------------------------------------
    # Filename normalization
    # ------------------------------------------------------------------

    #: Filenames that must stay uppercase (universal conventions).
    KEEP_UPPERCASE_FILENAMES = frozenset(
        {"README.md", "CHANGELOG.md", "LICENSE.md", "CONTRIBUTING.md"}
    )

    def _normalize_filename(self, path: Path) -> Path | None:
        """Return the normalized path if the filename needs renaming.

        Rules:
        - README.md, CHANGELOG.md, LICENSE.md, CONTRIBUTING.md: always keep as-is.
        - Any other filename with uppercase letters or underscores: normalize to
          lowercase with underscores replaced by hyphens.

        Args:
            path: Path relative to repo_root (e.g. ``docs/TELEGRAM.md``).

        Returns:
            The normalized path (same parent, new filename) if renaming is needed,
            or ``None`` if the filename is already correct.

        Examples::

            _normalize_filename(Path("docs/TELEGRAM.md"))
                → Path("docs/telegram.md")
            _normalize_filename(Path("docs/TOOL_REBUILD_REQUIREMENTS.md"))
                → Path("docs/tool-rebuild-requirements.md")
            _normalize_filename(Path("docs/MCP-Library-Requirements.md"))
                → Path("docs/mcp-library-requirements.md")
            _normalize_filename(Path("docs/README.md"))
                → None  (exempted)
            _normalize_filename(Path("docs/deployment.md"))
                → None  (already correct)
        """
        filename = path.name
        if filename in self.KEEP_UPPERCASE_FILENAMES:
            return None
        normalized = filename.lower().replace("_", "-")
        if normalized == filename:
            return None  # already correct
        return path.parent / normalized

    def rename_doc(self, path: Path, normalized: Path) -> bool:
        """Rename a doc file using ``git mv`` so git tracks the rename.

        In dry-run mode, prints the rename without performing it.

        Args:
            path: Current path relative to repo_root.
            normalized: Target path relative to repo_root.

        Returns:
            True if the rename succeeded (or would succeed in dry-run), False on error.
        """
        if self.dry_run:
            print(f"[DRY RUN] RENAME {path} → {normalized}")
            return True

        try:
            subprocess.run(
                ["git", "mv", str(path), str(normalized)],
                cwd=self.repo_root,
                check=True,
                capture_output=True,
            )
            logger.info("Renamed %s → %s", path, normalized)
            return True
        except subprocess.CalledProcessError as e:
            logger.error("git mv failed for %s → %s: %s", path, normalized, e)
            return False

    def _classify_doc_content(self, content_lower: str) -> str | None:
        """Classify document content into a canonical subdir name.

        Returns the subdir name (e.g. ``'guides'``) or ``None`` for flat ``docs/``.
        """
        # Guide signals: how-to, step-by-step, getting started, tutorial
        guide_signals = [
            "how to",
            "how-to",
            "step by step",
            "step-by-step",
            "getting started",
            "tutorial",
            "walkthrough",
            "instructions for",
        ]
        if any(signal in content_lower for signal in guide_signals):
            return "guides"

        # Testing signals
        testing_signals = [
            "test pattern",
            "test strategy",
            "testing approach",
            "testing pattern",
            "test suite",
            "pytest",
            "unit test",
            "integration test",
        ]
        if any(signal in content_lower for signal in testing_signals):
            return "testing"

        # Reference signals: heavy external URLs, external package docs
        reference_signals = [
            "https://docs.",
            "https://api.",
            "official documentation",
            "third-party",
            "external api",
            "api reference",
        ]
        reference_count = sum(1 for s in reference_signals if s in content_lower)
        if reference_count >= 2:
            return "references"

        # Feature signals: code class/function references or code blocks.
        # Use specific Python/shell patterns to avoid false positives from
        # common English words like "from" or "import" in prose.
        code_indicators = [
            "```python",
            "```bash",
            "```sh",
            "class ",
            "def ",
            ".py`",
            ".py:",
        ]
        if any(signal in content_lower for signal in code_indicators):
            return "features"

        # Default: stay flat under docs/
        return None

    def _should_skip(self) -> bool:
        """Return True if the audit was run within the last 7 days."""
        state = self._load_state()
        last_audit = state.get("last_audit_date")
        if not last_audit:
            return False
        try:
            last_dt = datetime.fromisoformat(last_audit)
            cutoff = datetime.now() - timedelta(days=self.AUDIT_FREQUENCY_DAYS)
            return last_dt > cutoff
        except ValueError:
            return False

    def _load_state(self) -> dict[str, Any]:
        """Load daydream_state.json from data/ directory."""
        state_path = self.repo_root / self.STATE_FILE
        if not state_path.exists():
            return {}
        try:
            return json.loads(state_path.read_text())
        except Exception:
            return {}

    def _record_audit_date(self) -> None:
        """Write today's date as last_audit_date in daydream_state.json."""
        state_path = self.repo_root / self.STATE_FILE
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state = self._load_state()
        state["last_audit_date"] = datetime.now().isoformat()
        state_path.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Audit documentation files against the actual codebase."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show verdicts without making any changes or committing.",
    )
    args = parser.parse_args()

    auditor = DocsAuditor(
        repo_root=Path(__file__).parent.parent,
        dry_run=args.dry_run,
    )
    summary = auditor.run()
    print(summary)
