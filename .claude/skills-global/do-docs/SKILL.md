---
name: do-docs
description: "Cascade documentation updates after a code change. Triggered by 'update docs', 'sync the docs', or any request about documentation updates."
---

# Update Docs — Cascade Skill

After a code change lands, find every document that references the changed area and make targeted, surgical updates so docs match the actual implementation.

## Repo Context Probe

If `.claude/skill-context/do-docs.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

The context file is where a repo layers its own automation onto this generic baseline. It may declare: a stage/status marker command to run at the start and end of the cascade; how to resolve plan context for the change; the canonical doc locations and index files to scan; a semantic doc-impact tool; an auto-fix substrate to run before manual edits; index tables to maintain; and how to mark an associated plan complete. When the file is absent (the common case), this skill runs entirely on `git` and `gh` — no repo-specific tooling required.

## Principles

1. **Document what IS, not what WAS** — match the actual API/behavior, not the plan
2. **Full context, not just diffs** — read entire modified files, not just changed lines. The diff shows what moved; the full file shows what it means.
3. **Cross-reference, don't duplicate** — link to the source of truth rather than restating
4. **Surgical edits only** — preserve existing structure, change only what the code change invalidates
5. **Read before edit** — always read the full file before modifying it
6. **Hunt stale references** — after a refactor, grep for old pattern keywords across all docs. If the code stopped using "history" as a data bus, search docs for "history" too.
7. **When in doubt, create an issue** — flag conflicts needing human judgment rather than guessing

## Step 1: Understand the Change (Parallel Exploration)

Launch the exploration agents in parallel using the Task tool. Agents A and B are always run; Agent C runs only if the context file declares a semantic doc-impact tool; Agent D runs only when `gh` is available and the host has GitHub issues.

**Agent-type pin (issue #2022):** every sub-agent this skill spawns runs shell commands (`git`, `gh`, `grep`), so spawn them ONLY on a Bash-capable agent type — `documentarian` or `general-purpose`. Never select an agent type without shell tools for docs work: a tool-less child cannot execute its first `git` command and wedges, emitting the command as plain text with zero tool calls.

### Agent A — Change Explorer

Spawn a sub-agent with this prompt:

```
Explore and summarize the code change described by: <INPUT>

### Pass 1: Structural read (diff only)

1. If this looks like a PR number (e.g. "123" or "#123"):
   - Run: gh pr view <number> --json title,body,files,additions,deletions
   - Run: gh pr diff <number>

2. If this looks like a commit SHA:
   - Run: git show <sha> --stat
   - Run: git show <sha>

3. If this is a text description:
   - Search the recent git log for matching commits
   - Run: git log --oneline -20
   - Identify the most relevant recent commits and inspect their diffs

### Pass 2: Full file reads (not just diff)

For every modified file, read the ENTIRE file — not just the changed lines.
This reveals context the diff hides: return type conventions, naming patterns,
how callers use the changed code, and whether the change is consistent with
the rest of the file.

### Pass 3: Cross-file data flow

Trace how data flows between modified files. Look for:
- String keys used in one file and matched in another (fragile coupling)
- Shared data structures written by one module, read by another
- Import changes that add or remove cross-module dependencies

### Pass 4: Intent vs. actual changes

If a PR description or commit message exists, compare what it CLAIMS changed
against what the diff ACTUALLY shows. Flag discrepancies — stated removals
that are actually additions, described refactors that are incomplete, etc.

### Produce a structured summary:
- **What changed**: Files modified, functions added/removed/renamed, config keys changed
- **API surface changes**: New/removed/renamed public interfaces, parameters, return types
- **Behavioral changes**: Different defaults, new error conditions, changed workflows
- **Cross-file couplings**: Data that flows between modified files and how
- **Key terms**: Important identifiers (function names, config keys, command names, file paths) that docs might reference
- **Retired terms**: Old identifiers, patterns, or concepts that the change replaces — these become grep targets for stale references
```

### Agent B — Documentation Inventory

Spawn a sub-agent with this prompt:

```
Inventory all documentation in this repository. For each file, note its purpose and what it references.

Discover documentation with git rather than assuming a fixed layout:
  git ls-files '*.md' 'docs/**' 'README*'

If the repo-context file declares canonical doc locations or an index file, scan those
first and in the priority order it gives. Otherwise, prioritize by convention:
  1. README.md and any top-level project guidance file (e.g. CONTRIBUTING.md)
  2. docs/**/*.md
  3. Any documentation index/table-of-contents file
  4. Everything else returned by git ls-files

For each doc file found, produce one line:
  <filepath> | <one-line purpose> | <key topics/identifiers referenced>

Do NOT read file contents in full — scan headings, section titles, and grep for key identifiers.
```

### Agent C — Semantic Impact Finder (only if the context file declares a tool)

Run this agent **only** if the repo-context file declares a semantic doc-impact tool. The
context file gives the exact invocation. Spawn a sub-agent that runs the declared tool with a
2-3 sentence natural-language summary of the change, and report the ranked results.

If no semantic tool is declared (the generic case), skip Agent C — lexical matching from
Agents A and B plus the stale-reference sweep (Step 2b) is sufficient.

### Agent D — Issue Impact Scanner (only if `gh` and issues are available)

Spawn a sub-agent with this prompt:

```
Review all open GitHub issues to identify any that are affected by the code change
described below. Merged features often have downstream effects on planned work
whose context only lives in issue descriptions.

CHANGE SUMMARY:
<Insert Agent A's structured summary here>

Steps:

1. List open issues:
   gh issue list --state open --json number,title,body,labels,comments --limit 50

2. For each issue, evaluate:
   - Does the issue description reference components, APIs, or patterns that this change modifies?
   - Does this change fulfill a prerequisite or dependency mentioned in the issue?
   - Does this change invalidate assumptions, approaches, or estimates in the issue?
   - Does this change introduce new capabilities the issue could leverage?

3. For each affected issue, post a comment (do NOT edit the issue body):
   gh issue comment <number> --body "$(cat <<'COMMENT'
   **Upstream change notice** — a related change just landed that may affect this issue.

   **What changed:** [1-2 sentence summary of the relevant change]
   **Impact on this issue:** [How it affects the planned work — prerequisite met, approach invalidated, new capability available, etc.]
   **Action needed:** [What should be reconsidered — scope, approach, dependencies, or nothing]

   _Auto-posted by /do-docs cascade_
   COMMENT
   )"

4. Report which issues were commented on and why. If no issues are affected, report that.

IMPORTANT: Only comment on issues where the impact is concrete and actionable.
Do not comment on tangentially related issues. Quality over quantity.
```

Wait for the launched agents to complete before proceeding. If an optional agent (C or D) was skipped or returned no results, proceed with the remaining agents' output only.

## Step 2: Triage — Cross-Reference Changes Against Docs

Using the outputs from Agent A (change summary), Agent B (doc inventory), and Agents C/D when run, evaluate every doc file against these four triage questions:

| # | Question | If YES |
|---|----------|--------|
| 1 | Does it **reference** the area that changed? (mentions the same files, functions, config keys, commands) | Needs update |
| 2 | Does it **depend on** the change? (describes behavior that the change alters) | Needs update |
| 3 | Does it **teach a pattern** this change modifies? (examples, templates, workflows using old API) | Needs update |
| 4 | Does it **orchestrate a workflow** using the changed components? (step-by-step instructions that now have different steps) | Needs update |

If ALL four answers are NO, skip that doc.

### Merge Semantic Results

If Agent C ran, merge its results with relevance >= 0.5 into the affected list: if a doc is already listed, note the semantic reason as additional context; if not, add it with Agent C's reason as the justification. Agent C catches conceptual coupling that keyword matching misses.

Produce an ordered task list of affected docs, ordered by dependency — foundational docs first (primary guidance, feature docs), then derivative docs (commands, plans, skills).

Format:
```
## Affected Documents

1. [ ] `<filepath>` — <what needs to change and why>
2. [ ] `<filepath>` — <what needs to change and why>
...

## Unaffected (skipped)
- `<filepath>` — no references to changed area
...
```

If zero documents are affected, report that clearly and stop.

## Step 2b: Stale Reference Sweep

Using the **Retired terms** from Agent A's summary, grep across all tracked docs for old keywords that the change replaced. This catches references the triage questions miss — docs that use an old name, pattern, or concept without directly depending on the changed file.

```bash
# Search every tracked markdown/doc file for each retired term
git grep -n "<retired-term>" -- '*.md' docs/ 2>/dev/null
```

If the context file declares additional doc locations (config files, persona segments, etc.), include them in the sweep. Add any new hits to the affected documents list from Step 2.

## Step 2c: What's Missing

After identifying docs that need updating, ask what docs *should exist but don't*:

- Did the change introduce a new feature that has no documentation entry?
- Did it add a new command or skill with no corresponding documentation?
- Did it create a new config key or environment variable not listed in setup/deployment docs?
- Is there a new cross-file pattern or data flow that warrants a dedicated doc?

If something is missing, add a task to create it (not just update existing docs).

## Step 2d: Auto-Fix Substrate (only if the context file declares one)

If the repo-context file declares an auto-fix substrate, run it against the changed files
**before** doing manual edits. Such a substrate typically handles mechanical fixes —
renamed markdown links, renamed paths/symbols, index entries pointing at deleted files, and
stale-term renames — so Step 3 only handles cases requiring human judgment. Follow the
context file's invocation and output-handling instructions exactly, and do not re-commit
changes the substrate commits itself.

If no substrate is declared (the generic case), skip this step — all edits are made manually in Step 3.

## Step 3: Make Surgical Edits

For each affected document still needing edits, in dependency order:

1. **Read** the full file first
2. **Identify** the specific lines/sections that reference the changed area
3. **Edit** only those sections — preserve all surrounding structure, tone, and formatting
4. **Verify** the edit matches the actual implementation (not the plan, not the old behavior)

Rules for edits:
- Update code examples to match new API signatures
- Update file paths if files moved or renamed
- Update behavioral descriptions if defaults or workflows changed
- Update tables/lists if entries were added, removed, or renamed
- Add new entries to any index/table-of-contents file if a new doc was created as part of the change (the context file names this repo's index file, if any)
- Do NOT rewrite sections that are still accurate
- Do NOT add speculative documentation about future changes
- Do NOT change formatting style or section ordering unless the change requires it

## Step 4: Verify and Report

After all edits are complete:

1. **Check only intended files were touched:**
   ```bash
   git diff --name-only
   ```
   Every file in this list should appear in the Step 2 task list. If there are unexpected files, revert them.

2. **Review each diff is minimal and correct:**
   ```bash
   git diff
   ```
   Each change should be a targeted update, not a rewrite.

3. **Flag conflicts for human review:**
   If any document has contradictory information that cannot be resolved from the code alone (e.g., a plan doc describes future work that may or may not still be valid), create a GitHub issue (when `gh` is available):
   ```bash
   gh issue create --title "Doc conflict: <filepath> may need human review" \
     --body "The /do-docs cascade found a potential conflict in <filepath> after <change description>. The doc references <X> but the code now does <Y>. Human judgment needed to resolve."
   ```

4. **Report summary:**
   ```
   ## Documentation Cascade Complete

   **Change**: <brief description of what triggered the cascade>

   **Documents updated**:
   - `<filepath>` — <what was changed>
   - `<filepath>` — <what was changed>

   **Documents reviewed but not changed**:
   - `<filepath>` — still accurate

   **Issues created for human review**:
   - #<number>: <title> (if any)

   **Open issues commented on** (downstream impact notices):
   - #<number>: <title> — <impact summary> (if any)

   **No changes needed**: (list if applicable)
   ```

5. **Commit changes:**
   ```bash
   git add -A && git commit -m "Docs: cascade updates for <brief change description>"
   ```
   Documentation changes must be persisted. If this fails (e.g., nothing to commit), that's fine — report "no changes needed." Push if the workflow expects it (`git push`).

   **Push-ancestry guard (this repo — #2026).** Before any `git push` to `main`
   from a cascade, run the push-ancestry guard so a worktree HEAD left detached at
   a PR branch head cannot register the open PR's ancestry as its merge:
   ```bash
   sdlc-push-guard || { echo "Push refused: HEAD carries open-PR ancestry — checkout main / merge through gh pr merge"; exit 1; }
   git push
   ```
   The guard only fires when HEAD is at/descended-from an OPEN PR head; a clean
   `main` checkout passes untouched. (The installed pre-push hook runs it too; this
   explicit call makes the protection independent of hook installation.)

## Edge Cases

- **New feature with no existing docs**: Nothing to cascade. Report "no existing docs reference this area."
- **Deleted feature**: Remove references from docs, but do NOT delete the feature doc itself — flag for human review.
- **Renamed function/file**: Use grep to find all references across docs and update each one.
- **Config key change**: Check primary guidance docs, `.env` references in docs, setup commands, and deployment docs.

## Integration

This skill pairs with:
- `/do-build` — run after build completes to cascade doc updates for the shipped code
- `/do-plan` — plans created here may need updating when prerequisites ship
- `/do-pr-review` — PR review can invoke this to verify docs match implementation
