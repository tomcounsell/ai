# Documentation Audit

The documentation audit system keeps docs accurate by verifying claims against the actual codebase. It checks file paths, environment variables, Python symbols, CLI commands, and package names referenced in each doc, then recommends whether to keep, update, or delete it. The audit runs weekly as part of daydream and is also available as the `/do-docs-audit` manual skill.

## Why It Exists

Documentation drifts. A guide written when a feature shipped may reference a file that was later renamed, an environment variable that was removed, or a class that was refactored. Stale docs mislead more than they help. The audit system automates detection of this drift so it gets corrected before it causes confusion.

## Manual Invocation

Invoke the `/do-docs-audit` Claude Code skill to trigger a full audit of `docs/`. The skill calls `scripts/docs_auditor.py`, reports a KEEP/UPDATE/DELETE verdict per file, applies corrections, and commits.

To preview what would happen without making changes:

```bash
python scripts/docs_auditor.py --dry-run
```

The `--dry-run` flag prints verdicts and reasons to stdout without writing, deleting, or committing anything (`scripts/docs_auditor.py:L295`).

## Automatic Execution via Daydream

The audit is integrated into daydream as step 5 (`step_audit_docs`) in `scripts/daydream.py:L644`. It replaces the older `step_update_docs` approach, which used a 30-day timestamp check — a mechanism that was actively harmful (a freshly-written doc describing an unbuilt feature would pass; an accurate 60-day-old doc would be flagged).

**Frequency gating.** The step reads `last_audit_date` from `data/daydream_state.json`. If that date is fewer than 7 days ago, the step is skipped (`scripts/docs_auditor.py:L891`). This prevents redundant full-corpus scans during daily daydream runs while ensuring the audit runs at least weekly.

After a successful run, daydream records findings and writes back to state:

```json
{
  "last_audit_date": "2026-02-18T09:15:00",
  "audit_docs": {
    "kept": 12,
    "updated": 3,
    "deleted": 1,
    "skipped": false
  }
}
```

## Verdict Types

Each audited document receives one of three verdicts:

| Verdict | Meaning | Action taken |
|---------|---------|-------------|
| `KEEP` | All references verified; doc is accurate | No changes |
| `UPDATE` | Some references are broken or outdated | Targeted corrections applied |
| `DELETE` | Too much of the doc is unverifiable | File deleted, index links swept |

**Conservative threshold.** A document is marked `DELETE` only when more than 60% of its verifiable references cannot be confirmed against the codebase (`scripts/docs_auditor.py:L189`). Everything below that threshold gets `UPDATE`, giving a human or agent a chance to revise rather than lose content outright.

## What References Are Checked

The auditor extracts and verifies six categories of references (`scripts/docs_auditor.py:L118`):

| Category | Examples |
|----------|---------|
| File paths | `bridge/telegram_bridge.py`, `config/projects.json` |
| Environment variables | `USE_CLAUDE_SDK`, `TELEGRAM_BOT_TOKEN` |
| Python imports | `from agent.sdk_client import ...`, `import telethon` |
| Class and function names | `DocsAuditor`, `TelegramBridge`, `handle_new_message` |
| CLI commands | `pytest`, `black`, `ruff`, `valor-service.sh` |
| Package names | Entries in `pyproject.toml` or `requirements.txt` |

References are extracted from Markdown prose and code blocks using regex patterns. The auditor checks existence statically — it does not execute code.

## Directory Structure Standards

The auditor enforces placement conventions in addition to content accuracy. Each subdirectory has a defined purpose (`scripts/docs_auditor.py:L700`):

| Directory | Purpose |
|-----------|---------|
| `docs/guides/` | Step-by-step guides and how-tos for developers |
| `docs/references/` | Copies or thin wrappers of third-party docs; prefer URLs to the upstream source over local copies that drift |
| `docs/testing/` | Testing patterns and practices for this codebase |
| `docs/features/` | Unique and extensive feature documentation, may include `file:line` code references |
| `docs/operations/` | Operational runbooks, monitoring, incident response |
| `docs/plans/` | Plans for new work items — **not audited** against codebase content |
| `docs/` (flat) | Extra docs, patterns, and best practices that don't fit a subdirectory |

Documents found in non-canonical subdirectories are flagged in the audit report with a suggested canonical path. The `/do-docs-audit` skill also performs physical relocation as part of its run.

Non-canonical subdirectories that have been collapsed into the above structure: `docs/architecture/`, `docs/experiments/`, `docs/improvements/`, `docs/tools/`.

## Filename Convention

All documentation files use lowercase-with-hyphens naming (`scripts/docs_auditor.py:L762`):

```
my-feature.md          # correct
bridge-self-healing.md # correct
MyFeature.md           # incorrect — will be renamed
TOOL_REBUILD.md        # incorrect — will be renamed
```

Exempt from renaming: `README.md`, `CHANGELOG.md`, `LICENSE.md`, `CONTRIBUTING.md`.

## DocsAuditor Class

`scripts/docs_auditor.py` is both a CLI script and an importable module (`scripts/docs_auditor.py:L284`).

```python
from scripts.docs_auditor import DocsAuditor

auditor = DocsAuditor(repo_root=Path("."), dry_run=False)
summary = auditor.run()
```

Key methods:

| Method | Description |
|--------|-------------|
| `enumerate_docs()` | Find all `.md` files in `docs/` excluding `plans/` |
| `analyze_doc(path)` | Extract references, verify against filesystem, call LLM for verdict |
| `execute_verdict(path, verdict)` | Delete / apply corrections / skip |
| `sweep_index_files(deleted)` | Remove broken links from `docs/README.md`, `docs/features/README.md`, `CLAUDE.md` |
| `commit_results(summary)` | `git add -A && git commit` with detailed summary |
| `run()` | Full pipeline: frequency gate → enumerate → analyze → execute → sweep → commit |
| `_check_doc_location(path)` | Return canonical path suggestion for non-canonical subdirs |
| `_normalize_filename(path)` | Return renamed path if uppercase or snake_case |
| `_should_skip()` | Return `True` if audit ran within the last 7 days |

### AuditSummary

```python
@dataclass
class AuditSummary:
    kept: list[str]       # paths left unchanged
    updated: list[str]    # paths corrected
    deleted: list[str]    # paths removed
    renamed: list[str]    # "old → new" strings
    relocated: list[str]  # "old -> suggested" strings
    errors: list[str]     # paths that errored during analysis
    skipped: bool         # True if frequency gate fired
    skip_reason: str      # human-readable skip reason
    verdicts: dict        # path → Verdict for all analyzed docs
```

## LLM Usage

The auditor uses two models for analysis (`scripts/docs_auditor.py:L561`):

1. `claude-haiku-4-5-20251001` — fast, cheap; handles reference extraction and initial verdict
2. `claude-sonnet-4-6` — escalated to when Haiku confidence is low (unclear verdicts)

API calls use the `ANTHROPIC_API_KEY` environment variable. In dry-run mode with no API key available, the auditor falls back to `KEEP` verdicts rather than crashing.

## Skill File

`.claude/skills/do-docs-audit/SKILL.md` — Claude Code skill definition. Orchestrates a full audit:

1. Enumerate all docs in `docs/` excluding `plans/`
2. Batch into groups of 12 → spawn parallel Explore agents per batch
3. Collect verdicts → display summary table
4. Execute verdicts (delete / apply corrections)
5. Sweep index files for broken links
6. Enforce directory structure (relocate misplaced docs)
7. Normalize filenames to lowercase-with-hyphens
8. Commit all changes with a detailed message

## See Also

- `scripts/docs_auditor.py` — full implementation
- `scripts/daydream.py` — daydream pipeline including `step_audit_docs`
- `docs/operations/daydream-system.md` — daydream system overview with step table
- `docs/features/README.md` — feature index
