---
name: do-docs-audit
description: "Audit all documentation files against the actual codebase, removing or correcting stale references. Use when the user says 'audit docs', 'clean up docs', 'check docs accuracy', or 'do-docs-audit'."
allowed-tools: Read, Write, Edit, Glob, Bash, Task
---

# Documentation Audit Skill

Systematically audits every documentation file in `docs/` against the actual codebase. Verifies concrete references (file paths, class names, function names, CLI commands, env vars, packages) exist in the codebase. Issues verdicts of KEEP, UPDATE, or DELETE for each file, applies the changes, sweeps index files for broken links, then commits with a detailed summary.

## When to Use

- Periodic housekeeping: docs have drifted from the codebase
- After large refactors that may have invalidated many references
- When you notice broken references in docs and want a full sweep
- Invoked by `/do-docs-audit` or when user asks to audit, clean up, or verify docs accuracy

## Invocation

```
/do-docs-audit [directory]
```

- Default directory: `docs/`
- Excludes: `docs/plans/` (plans are intentionally forward-looking)
- Index-only from `docs/features/README.md` (audits the index links, not every feature doc unless you pass `docs/features/` explicitly)

---

## Step 1: Enumerate Documentation Files

Find all `.md` files in the target directory, excluding `docs/plans/`:

```bash
find docs/ -name "*.md" -not -path "docs/plans/*" | sort
```

Collect the list. If there are no files, stop and report "No documentation files found."

---

## Step 2: Batch and Spawn Parallel Audit Agents

Batch the file list into groups of **12 files** for parallel processing.

For each batch, spawn parallel Task agents — one per document — using the following prompt template for each file:

---

**Audit Agent Prompt Template:**

```
Audit this documentation file against the actual codebase.
File: {path}
Content:
{content}

Your job: Extract all concrete references and verify them against the codebase.

REFERENCE TYPES TO EXTRACT:
- File paths (e.g., "scripts/foo.py", "bridge/telegram_bridge.py", ".claude/skills/bar/SKILL.md")
- Python class names (e.g., "DocsAuditor", "DaydreamRunner")
- Python function names (e.g., "analyze_doc", "run_llm_reflection")
- CLI commands (e.g., "./scripts/start_bridge.sh", "valor-telegram", "python scripts/daydream.py")
- Environment variables (e.g., "ANTHROPIC_API_KEY", "TELEGRAM_API_ID")
- Package/module names (e.g., "telethon", "anthropic", "claude-agent-sdk")
- Config file keys (e.g., "USE_CLAUDE_SDK", "SENTRY_DSN")
- Script names in scripts/ directory

VERIFICATION STEPS (use Glob, Grep, Read, Bash tools):
For each reference extracted:
1. File paths: Check with Glob or Bash `ls` — does the file/directory exist?
2. Class names: Grep for "class {Name}" in Python files
3. Function names: Grep for "def {name}" in Python files
4. CLI commands and scripts: Check scripts/ directory with Glob; check pyproject.toml for entry points
5. Env vars: Grep in .env.example; grep codebase for the variable name
6. Package names: Check pyproject.toml dependencies section
7. Config keys: Grep in .env files, config/ directory, bridge code

VERDICT FORMAT (respond with exactly this structure):

VERDICT: [KEEP | UPDATE | DELETE]
RATIONALE: [one sentence explaining the verdict]
CORRECTIONS:
- [specific correction 1, or "none" if KEEP]
- [specific correction 2]

VERDICT THRESHOLDS:
- KEEP: All or nearly all concrete references verified. Doc is accurate.
- UPDATE: Some references are wrong or outdated. List the specific corrections.
- DELETE: The core subject of the document does not exist in the codebase (e.g., describes a system that was removed, references files that don't exist, entire feature was deleted).
- Conservative threshold: prefer UPDATE over DELETE when uncertain. Only DELETE when the document's primary subject is verifiably gone.
```

---

Spawn all agents in the batch in parallel using the Task tool. Wait for all agents in the batch to complete before processing the next batch.

Collect all verdicts into a results list:
```
{path} → {KEEP|UPDATE|DELETE} — {rationale}
```

---

## Step 3: Display Summary Table

Before making any changes, print a summary table for human review:

```
## Docs Audit Results

| File | Verdict | Rationale |
|------|---------|-----------|
| docs/foo.md | KEEP | All references verified |
| docs/bar.md | UPDATE | scripts/old-script.sh renamed to scripts/new-script.sh |
| docs/baz.md | DELETE | Describes feature that was removed |
```

Print counts:
```
KEEP: N  UPDATE: N  DELETE: N
```

---

## Step 4: Execute Verdicts

Process each verdict:

### DELETE
Remove the file:
```bash
rm {path}
```
Log: `Deleted: {path} — {rationale}`

### UPDATE
Apply each correction listed in the verdict. For each correction:
1. Read the file
2. Apply the specific edit using Edit tool
3. If the correction is complex, rewrite only the affected section

Log: `Updated: {path} — applied N corrections`

### KEEP
No action needed.
Log: `Kept: {path}`

---

## Step 5: Sweep Index Files for Broken Links

After executing all DELETE verdicts, check index files for broken links.

For each deleted file, search these index files for references to it:
- `docs/README.md`
- `docs/features/README.md`
- `CLAUDE.md`

For each broken link found:
1. Read the index file
2. Remove or update the row/link that references the deleted file
3. If it's a table row, delete the entire row
4. If it's a link in prose, replace with a note that the feature was removed

```bash
# Example: check if deleted doc is referenced in index files
grep -l "{deleted_filename}" docs/README.md docs/features/README.md CLAUDE.md 2>/dev/null
```

---

## Step 6: Commit with Detailed Summary

Stage all changes and commit with a message that lists each file's verdict and rationale:

```bash
git add -A docs/ CLAUDE.md
git commit -m "$(cat <<'EOF'
Docs audit: remove stale, correct outdated references

Results:
{for each file: "- {VERDICT} {path}: {rationale}"}

Kept: {N} | Updated: {N} | Deleted: {N}
EOF
)"
```

---

## Output Report

After committing, print the final audit report:

```
## Documentation Audit Complete

**Scanned**: {total} files
**Kept**: {N} files — no changes needed
**Updated**: {N} files — corrections applied
**Deleted**: {N} files — described nonexistent things

### Changes Made

#### Deleted
- `docs/foo.md` — {rationale}

#### Updated
- `docs/bar.md`
  - {correction 1}
  - {correction 2}

### Committed
{commit SHA}
```

---

## Principles

1. **Conservative on DELETE**: The bar for deletion is high. A doc that is partially wrong should be UPDATED, not deleted. Only DELETE when the document's entire subject matter no longer exists in the codebase.
2. **Surgical corrections**: When updating, change only what's wrong. Don't rewrite accurate sections.
3. **Verify before acting**: Every verdict is based on actual filesystem/codebase verification, not assumptions.
4. **Index hygiene**: After deletions, always clean up index files to avoid dead links.
5. **Transparent output**: Print the summary table before making changes so the human can see what's coming.
