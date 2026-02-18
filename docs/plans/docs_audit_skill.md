---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-02-18
tracking: https://github.com/tomcounsell/ai/issues/145
---

# Comprehensive Documentation Audit Skill

## Problem

Documentation rot is invisible until it's embarrassing. We just deleted 8 docs that described a completely non-existent architecture — PydanticAI agents, mcp_servers/ Python files, FastAPI health endpoints, Huey task queues. These weren't ancient files; they were written with confidence and left to mislead whoever read them next.

**Current behavior:**
The existing `step_update_docs` in daydream checks file modification timestamps. If a doc hasn't been touched in 30 days, it's flagged. This is actively harmful — a freshly-written doc describing an unbuilt feature passes the check; an accurate 60-day-old doc gets flagged.

**Desired outcome:**
Every doc in `docs/` gets verified against the actual codebase — not by age, but by content. Every class name, file path, module import, CLI command, env var, and config key mentioned in a doc is checked to see if it still exists and still works the way the doc says it does. Docs that describe reality are kept. Docs that are partially wrong are corrected. Docs that describe things that don't exist are deleted.

This runs autonomously in daydream so documentation rot gets caught before it misleads anyone.

## Appetite

**Size:** Medium

**Team:** Solo dev + PM. One check-in to align on the per-batch strategy and daydream integration approach. One review round.

**Interactions:**
- PM check-ins: 1-2 (scope of what counts as a "reference", batching strategy)
- Review rounds: 1

## Prerequisites

No external prerequisites — uses existing Anthropic API key and Claude Code toolchain already present.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ANTHROPIC_API_KEY` | `python -c "import os; assert os.environ.get('ANTHROPIC_API_KEY')"` | Anthropic API for LLM-powered doc analysis |
| `claude` CLI | `which claude` | Used by the skill for parallel agent spawning |

Run all checks: `python scripts/check_prerequisites.py docs/plans/docs_audit_skill.md`

## Solution

### Key Elements

- **`/do-docs-audit` skill**: Claude Code skill that orchestrates the audit. Enumerates all docs, spawns parallel Explore agents in batches of 12, collects verdicts, executes decisions, cleans index files, commits.
- **`scripts/docs_auditor.py`**: Standalone Python module that implements the same audit logic using the Anthropic API directly. Used by daydream (a Python process, not a Claude Code session).
- **Daydream step replacement**: `step_update_docs` (naive timestamp checker) is replaced with `step_audit_docs` that calls `docs_auditor.py` — the first daydream step to do real semantic verification.
- **Verdict execution**: After collecting all KEEP/UPDATE/DELETE verdicts, apply them: delete files, apply targeted corrections, sweep index files for broken links, commit with a detailed summary.

### Flow

**Manual invocation:**
`/do-docs-audit` → enumerate all docs → batch into groups of 12 → parallel Explore agents per batch → collect verdicts → execute → commit

**Daydream invocation:**
daydream `step_audit_docs` → `docs_auditor.py` → same enumeration + analysis via Anthropic API → same verdict execution → daydream records findings

### Technical Approach

**Reference extraction (what each doc analysis looks for):**
- File paths (`bridge/telegram_bridge.py`, `scripts/start.sh`)
- Python module imports (`from claude_agent_sdk import ...`, `import pydantic_ai`)
- Class names (`ClaudeSDKClient`, `ValorAgent`, `UnifiedMessageProcessor`)
- Function names (`handle_new_message`, `run_reflection`)
- CLI commands (`valor-service.sh`, `./scripts/start_bridge.sh`)
- Environment variables (`TELEGRAM_API_ID`, `USE_CLAUDE_SDK`)
- Config keys in JSON/YAML files (`mcp_servers`, `projects.json` keys)
- Tool/package names (`telethon`, `python-telegram-bot`, `fastmcp`)

**Verification approach:**
Each Explore agent reads the doc, extracts references, then searches the codebase:
- File paths: `ls` or `Glob`
- Python imports: `Grep` for the import statement
- Class/function names: `Grep` for `class X` or `def x`
- CLI commands: check `scripts/` or installed commands
- Env vars: check `.env.example`, `config/`, and code that reads them
- Package names: check `pyproject.toml`, `package.json`, `uv.lock`

**Verdict format (structured):**
```
KEEP   — all references verified, doc is accurate
UPDATE — partially wrong: [specific list of corrections]
DELETE — describes nonexistent things: [reason]
```

**Batching:**
- 51 current docs; batches of 12 → ~5 batches
- All agents within a batch run in parallel
- Batches run sequentially to avoid overwhelming context

**Index file cleanup (post-decisions):**
After deleting any docs, sweep these files for broken links:
- `docs/README.md`
- `docs/features/README.md`
- `CLAUDE.md`
- Any doc that cross-references a deleted doc

**`docs_auditor.py` architecture:**
```python
class DocsAuditor:
    def enumerate_docs(self) -> list[Path]    # find all .md in docs/ except plans/
    def analyze_doc(self, path: Path) -> Verdict  # Anthropic API call per doc
    def execute_verdict(self, verdict: Verdict)   # delete / apply corrections / skip
    def sweep_index_files(self, deleted: list[Path])  # remove broken links
    def commit_results(self, summary: AuditSummary)   # git commit + push
```

The `analyze_doc` method uses Haiku (fast, cheap) for reference extraction, and Sonnet for the final verdict when Haiku isn't confident.

## Rabbit Holes

- **Don't auto-generate replacement docs** — if a doc is deleted, we just delete it. Writing new accurate docs is a separate `/do-docs` concern triggered by code changes.
- **Don't score docs by "staleness"** — a 1-year-old doc that's accurate stays; a 1-day-old doc that describes fiction goes. Time is irrelevant.
- **Don't audit `plans/`** — plans describe intended future state, not current state. Auditing them against the codebase would delete valid in-progress plans.
- **Don't audit `.claude/` skills or commands** — these are procedural instructions, not descriptions of existing code. Different verification model needed.
- **Don't try to verify every English sentence** — only concrete, verifiable references (file paths, class names, etc.). Prose descriptions and rationale don't need verification.

## Risks

### Risk 1: False positives — deleting accurate docs
**Impact:** Lose real documentation, require manual recovery from git history.
**Mitigation:** The Explore agent is instructed to apply a conservative threshold: only verdict DELETE when the doc's core claims cannot be verified. Ambiguous cases get UPDATE with a note. The commit is reviewed before pushing; the skill outputs a summary for human review before committing.

### Risk 2: Batch size causing context bloat
**Impact:** Explore agents receive too much doc content and give poor verdicts.
**Mitigation:** Each agent analyzes exactly ONE doc. Batching is about how many agents run in parallel, not how many docs one agent handles.

### Risk 3: Slow execution in daydream
**Impact:** Daydream session runs long, other steps delayed.
**Mitigation:** `docs_auditor.py` uses async Anthropic API calls. 51 docs at ~2 API calls each = ~100 calls. With batching and Haiku speed, should complete in 3-5 minutes. Add a timeout and graceful partial commit if exceeded.

### Risk 4: Corrections introduce new errors
**Impact:** An UPDATE verdict applies incorrect "corrections."
**Mitigation:** UPDATE verdicts list specific line-level changes, not wholesale rewrites. The executor applies minimal diffs, and the commit message includes the specific changes for review.

## No-Gos (Out of Scope)

- Auditing `.claude/skills/`, `.claude/commands/`, or `.claude/agents/` — different verification model
- Auditing `docs/plans/` — plans describe future state
- Auto-generating replacement documentation after deletes
- Slack/Telegram notification when audit finds issues (daydream report already handles this)
- Scheduling frequency configuration — it runs every daydream cycle

## Update System

The `scripts/remote-update.sh` and `/update` skill don't need changes. The new `scripts/docs_auditor.py` is a local module with no new dependencies beyond the existing `anthropic` package already in `pyproject.toml`. No new config files, no environment variables, no migration steps.

## Agent Integration

No MCP server changes needed. This feature is entirely internal:
- The skill runs within Claude Code's native tool ecosystem (Glob, Grep, Read, Bash, Task)
- The `docs_auditor.py` uses the `anthropic` package already in the project
- Neither requires new MCP tools or `.mcp.json` changes

The audit results are committed directly to the repo, making them visible through git history and the daydream report.

## Documentation

- [ ] Create `docs/features/documentation-audit.md` describing the audit system (scope, verdict types, daydream integration, how to invoke manually)
- [ ] Add entry to `docs/features/README.md` index table for `documentation-audit.md`
- [ ] Update `docs/operations/daydream-system.md` to document the new `step_audit_docs` replacing `step_update_docs`

## Success Criteria

- [ ] `/do-docs-audit` skill runs end-to-end and produces KEEP/UPDATE/DELETE verdicts for all docs in `docs/` (excluding `plans/`)
- [ ] At least one UPDATE or DELETE is correctly applied on a known-stale doc (can verify with `docs/upgrade-workflow.md` or similar)
- [ ] Deleted docs are automatically removed from `docs/README.md` and `docs/features/README.md`
- [ ] `scripts/docs_auditor.py` runs standalone (`python scripts/docs_auditor.py --dry-run`) and produces verdicts without errors
- [ ] Daydream `step_update_docs` replaced with `step_audit_docs` that calls `docs_auditor.py`
- [ ] Daydream full run completes with the new step in < 10 minutes total (audit step < 5 min)
- [ ] Commit message after audit lists all changes with specific rationale per doc
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (skill + auditor)**
  - Name: skill-builder
  - Role: Implement the `/do-docs-audit` skill SKILL.md and `scripts/docs_auditor.py` Python module
  - Agent Type: builder
  - Resume: true

- **Builder (daydream integration)**
  - Name: daydream-builder
  - Role: Replace `step_update_docs` with `step_audit_docs` in `scripts/daydream.py`, wire to `docs_auditor.py`
  - Agent Type: builder
  - Resume: true

- **Validator (skill)**
  - Name: skill-validator
  - Role: Run the skill against a subset of docs, verify verdict correctness and commit output
  - Agent Type: validator
  - Resume: true

- **Validator (daydream)**
  - Name: daydream-validator
  - Role: Run `python scripts/daydream.py` and verify the audit step completes, produces findings in daydream state
  - Agent Type: validator
  - Resume: true

- **Test writer**
  - Name: test-writer
  - Role: Write tests for `docs_auditor.py` — verdict parsing, reference extraction, dry-run mode
  - Agent Type: test-writer
  - Resume: true

- **Documentarian**
  - Name: doc-writer
  - Role: Write `docs/features/documentation-audit.md`, update index and daydream-system.md
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Build skill + auditor module
- **Task ID**: build-skill
- **Depends On**: none
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/skills/do-docs-audit/SKILL.md` with full audit workflow (enumerate docs, batch into groups of 12, parallel Explore agents, collect verdicts, execute, clean index files, commit)
- Create `scripts/docs_auditor.py` with `DocsAuditor` class — `enumerate_docs`, `analyze_doc` (Anthropic API), `execute_verdict`, `sweep_index_files`, `commit_results`
- Add `--dry-run` flag to `docs_auditor.py` that shows verdicts without making changes

### 2. Build daydream integration
- **Task ID**: build-daydream
- **Depends On**: build-skill
- **Assigned To**: daydream-builder
- **Agent Type**: builder
- **Parallel**: false
- In `scripts/daydream.py`: rename `step_update_docs` → `step_audit_docs`, renumber as step 5
- Replace naive timestamp logic with call to `DocsAuditor` from `scripts/docs_auditor.py`
- Capture audit summary as daydream findings (counts of kept/updated/deleted)
- Update `self.steps` list with new step name

### 3. Write tests
- **Task ID**: build-tests
- **Depends On**: build-skill
- **Assigned To**: test-writer
- **Agent Type**: test-writer
- **Parallel**: true
- `tests/test_docs_auditor.py`: test verdict parsing, reference extraction, dry-run mode, index sweep
- Mock Anthropic API responses to avoid real API calls in unit tests

### 4. Validate skill
- **Task ID**: validate-skill
- **Depends On**: build-skill, build-daydream
- **Assigned To**: skill-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `/do-docs-audit` skill on `docs/` (or a subset) and verify verdicts are produced
- Check that DELETE decisions actually delete files and remove broken links from index files
- Verify commit message is informative

### 5. Validate daydream integration
- **Task ID**: validate-daydream
- **Depends On**: build-daydream, validate-skill
- **Assigned To**: daydream-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `python scripts/docs_auditor.py --dry-run` and verify output format
- Verify daydream step runs without errors in isolation
- Check state file captures audit findings correctly

### 6. Validate tests pass
- **Task ID**: validate-tests
- **Depends On**: build-tests, validate-skill
- **Assigned To**: skill-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/test_docs_auditor.py -v`
- All tests must pass

### 7. Write documentation
- **Task ID**: document-feature
- **Depends On**: validate-skill, validate-daydream
- **Assigned To**: doc-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/documentation-audit.md`
- Add entry to `docs/features/README.md`
- Update `docs/operations/daydream-system.md` to document step replacement

### 8. Final validation
- **Task ID**: validate-all
- **Depends On**: validate-tests, document-feature
- **Assigned To**: skill-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria checked off
- Generate final report

## Validation Commands

- `python scripts/docs_auditor.py --dry-run` — verify audit runs without errors, outputs verdicts
- `pytest tests/test_docs_auditor.py -v` — verify unit tests pass
- `python -c "from scripts.docs_auditor import DocsAuditor; print('import OK')"` — verify importable by daydream
- `ls .claude/skills/do-docs-audit/SKILL.md` — skill file exists
- `grep step_audit_docs scripts/daydream.py` — daydream integration present
- `grep -v step_update_docs scripts/daydream.py` — old naive step fully replaced

---

## Open Questions

1. **Conservative vs. aggressive deletions**: For docs that are partially aspirational (describe real systems but also mention features that were planned but never built), should we UPDATE (remove the fictional parts) or DELETE (if the aspirational content outweighs the accurate content)? Suggest: UPDATE unless >60% of the doc is unverifiable.

2. **Frequency in daydream**: Should docs audit run every daydream cycle (daily) or less frequently (weekly)? Concern is cost of ~100 API calls per run. Suggest: weekly, gated by a `last_audit_date` check in the daydream state file.

3. **`CLAUDE.md` and `config/SOUL.md` scope**: Should the audit include these root-level files, or only `docs/`? They contain prescriptive instructions (not descriptions of code), so false positives are likely. Suggest: exclude initially, can add later.
