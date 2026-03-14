---
name: do-docs-audit
description: "Use when auditing documentation files against the actual codebase. Removes or corrects stale references. Triggered by 'audit docs', 'clean up docs', 'check docs accuracy', or 'do-docs-audit'."
allowed-tools: Read, Write, Edit, Glob, Bash, Task
context: fork
---

# Documentation Audit Skill

Systematically audits every documentation file in `docs/` against the actual codebase. Works on any repository. Verifies concrete references (file paths, class names, function names, CLI commands, env vars, packages) exist in the codebase. Issues verdicts of KEEP, UPDATE, or DELETE for each file, applies the changes, sweeps index files for broken links, enforces canonical directory structure, then commits with a concise summary of actual changes (routing large audits to GitHub issues instead of bloated commit messages).

## When to Use

- Periodic housekeeping: docs have drifted from the codebase
- After large refactors that may have invalidated many references
- When you notice broken references in docs and want a full sweep
- Invoked by `/do-docs-audit` or when user asks to audit, clean up, or verify docs accuracy

## Invocation

```
/do-docs-audit [directory] [--full]
```

- Default directory: `docs/`
- Excludes: `docs/plans/` (plans are intentionally forward-looking)
- `--full`: Also audit root markdown files (`CLAUDE.md`, `README.md`), `.claude/skills/*/SKILL.md`, and `.claude/commands/*.md` for stale references (these extra files are NOT relocated)
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
- Python class names (e.g., "DocsAuditor", "ReflectionsRunner")
- Python function names (e.g., "analyze_doc", "run_llm_reflection")
- CLI commands (e.g., "./scripts/start_bridge.sh", "valor-telegram", "python scripts/reflections.py")
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

## Step 6: Enforce Doc Directory Structure

After executing all verdicts and sweeping index files, check that every surviving doc lives in a canonical subdirectory.

### Canonical subdirectories (universal 5-subdir taxonomy)

| Subdir | Purpose |
|--------|---------|
| `docs/features/` | Shipped feature docs, technical descriptions, test strategies, script docs |
| `docs/guides/` | How-to guides, tutorials, external references, third-party docs, audits, walkthroughs |
| `docs/designs/` | .pen files, design components, UI mockups, wireframes |
| `docs/media/` | Screenshots, static files, visual assets |
| `docs/plans/` | Forward-looking plans — **never audited or moved** |

Only `docs/README.md` is allowed flat under `docs/`. All other docs must be in a canonical subdir.

**Non-canonical subdirs** (anything not in the list above, e.g. `docs/architecture/`, `docs/testing/`, `docs/references/`, `docs/operations/`) should have their docs relocated.

### Classification heuristic

For a doc that needs relocation (non-canonical subdir or flat), classify by content:

1. Content contains design/mockup/wireframe/UI/UX/prototype/.pen → `docs/designs/`
2. Content contains code blocks, class/def references, .py refs, test patterns → `docs/features/`
3. Content contains how-to, tutorial, walkthrough, external URLs, "official documentation", third-party, audit, setup → `docs/guides/`
4. Default fallback → `docs/guides/`

### Relocation steps

For each doc that needs relocation:

1. Use `git mv` to move the file:
   ```bash
   git mv docs/architecture/foo.md docs/features/foo.md
   ```
2. Update cross-references in:
   - `docs/README.md`
   - `docs/features/README.md`
   - `CLAUDE.md`
   - Any other docs that link to the moved file (use `grep -r`)
3. After all relocations, remove any now-empty non-canonical subdirs:
   ```bash
   rmdir docs/architecture/ docs/experiments/ 2>/dev/null || true
   ```

### Summary reporting

Include a **RELOCATED** count in the final output:

```
KEEP: N  UPDATE: N  DELETE: N  RELOCATED: N
```

List each relocated file:
```
#### Relocated
- `docs/architecture/system-overview.md` → `docs/features/system-overview.md`
- `docs/tools/quality-standards.md` → `docs/guides/quality-standards.md`
```

---

## Step 6.5: Threshold Router — Decide Commit Strategy

Before committing, count how many files were actually changed (UPDATE + DELETE + RELOCATED). This count determines the commit strategy.

**If 0 changes** (all verdicts were KEEP): Skip the commit entirely. Report "No changes needed — all docs are accurate." and proceed to the Output Report.

**If <=5 changes**: Use the **Hotfix Path** (Step 7A below). Commit directly with a concise message listing each changed file.

**If >5 changes**: Use the **Report Path** (Step 7B below). Create a GitHub issue with the full audit report, then commit with a short summary referencing the issue.

---

## Step 7A: Hotfix Path (<=5 changes)

Stage all changes and commit with a concise message listing only files that were actually modified:

```bash
git add -A docs/ CLAUDE.md
git commit -m "$(cat <<'EOF'
Docs audit: fix {N} documentation issues

Changes:
{for each CHANGED file only: "- {VERDICT} {path}: {one-line rationale}"}

Kept: {K} | Updated: {U} | Deleted: {D} | Relocated: {R}
EOF
)"
```

**IMPORTANT**: The commit message must list ONLY files where changes were actually made (UPDATE, DELETE, RELOCATED). Never include KEEP verdicts or files that weren't modified. The message should be short — one line per changed file.

---

## Step 7B: Report Path (>5 changes)

### 7B.1: Create GitHub Issue with Full Report

Before committing, create a GitHub issue containing the complete audit report:

```bash
gh issue create \
  --title "Docs audit: {N} issues found across documentation" \
  --body "$(cat <<'EOF'
## Documentation Audit Report

**Scanned**: {total} files
**Updated**: {U} | **Deleted**: {D} | **Relocated**: {R} | **Kept**: {K}

### Files Changed

{for each UPDATE file: "- **UPDATE** `{path}`: {rationale} — corrections: {list corrections}"}
{for each DELETE file: "- **DELETE** `{path}`: {rationale}"}
{for each RELOCATED file: "- **RELOCATED** `{old}` → `{new}`"}

### Files Kept (no changes needed)

{for each KEEP file: "- `{path}`"}
EOF
)"
```

**Before creating a new issue**, first check for an existing open docs audit issue:
```bash
gh issue list --search "Docs audit" --state open --limit 1
```
If one exists, append to it as a comment instead of creating a new issue.

### 7B.2: Commit with Issue Reference

```bash
git add -A docs/ CLAUDE.md
git commit -m "$(cat <<'EOF'
Docs audit: fix {N} documentation issues

See #{issue_number} for full audit report.

Summary: Updated {U} | Deleted {D} | Relocated {R} | Kept {K}
EOF
)"
```

**IMPORTANT**: The commit message must NOT contain the full per-file audit report. Only reference the issue number and include summary counts. If the GitHub issue creation fails, fall back to the Hotfix Path (Step 7A) format — list only actually changed files inline.

---

### Commit Message Rules (applies to both paths)

1. **Never list KEEP verdicts** in commit messages — they represent no change
2. **Never dump the full audit report** into a commit message
3. **Only reference files that were actually modified** in the working tree
4. **Keep commit messages under 50 lines** — use the GitHub issue for details

---

## Output Report

After committing, print the final audit report:

```
## Documentation Audit Complete

**Scanned**: {total} files
**Kept**: {N} files — no changes needed
**Updated**: {N} files — corrections applied
**Deleted**: {N} files — described nonexistent things
**Relocated**: {N} files — moved to canonical locations

### Changes Made

#### Deleted
- `docs/foo.md` — {rationale}

#### Updated
- `docs/bar.md`
  - {correction 1}
  - {correction 2}

#### Relocated
- `docs/architecture/system-overview.md` → `docs/features/system-overview.md`

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
6. **Structure enforcement**: After every audit, verify all docs are in canonical subdirs. Relocate misplaced docs and update cross-references.
7. **Filename convention**: Filenames must be lowercase-with-hyphens (e.g. `telegram.md`, `tool-rebuild-requirements.md`). `README.md` is the only uppercase exception; `CHANGELOG.md`, `LICENSE.md`, and `CONTRIBUTING.md` are also exempt as standard project files. Any other uppercase filename must be normalized using `git mv`.
8. **Data flow tracing:** When auditing output compliance, don't just check if the renderer works -- trace upstream. Is the data source being populated? Is the tool/function that writes the data actually being called? Grep for expected invocations.
