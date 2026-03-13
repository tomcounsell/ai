---
name: do-docs
description: "Use when cascading documentation updates after code changes. Finds every document referencing the changed area and makes targeted surgical updates. Triggered by 'update docs', 'sync the docs', or any request about documentation updates."
---

# Update Docs — Cascade Skill

After a code change lands, find every document that references the changed area and make targeted, surgical updates so docs match the actual implementation.

## Session Progress Tracking

Extract the session ID from the conversation context. The bridge injects `SESSION_ID: {id}` into enriched messages. Look for this pattern and store it:

```bash
# Extract SESSION_ID from context
# Look for a line like "SESSION_ID: abc123" in the message you received
# Store in variable: SESSION_ID="abc123"

# Mark DOCS stage as in_progress at the start
python -m tools.session_progress --session-id "$SESSION_ID" --stage DOCS --status in_progress 2>/dev/null || true
```

After all documentation updates are complete and committed (Step 4):

```bash
# Mark DOCS stage complete
python -m tools.session_progress --session-id "$SESSION_ID" --stage DOCS --status completed 2>/dev/null || true
```

## Goal Alignment

When invoked by `do-build`, this skill should receive the **plan context** (high-level goal, tracking issue, and acceptance criteria) so doc updates are aligned with the feature's intent — not just the raw diff.

**How to get plan context** (in priority order):
1. If the caller passed plan context inline (e.g., `do-build` includes it in the prompt), use that directly
2. Check the PR body for `Closes #N` — fetch the issue, then look for `docs/plans/{slug}.md`
3. Check the current git branch — if `session/{slug}`, look for `docs/plans/{slug}.md`
4. If no plan found, proceed without it — the diff alone is sufficient for doc cascading

When plan context is available, use it to:
- Understand the *purpose* of the change (not just what moved)
- Identify which docs are conceptually related even without keyword overlap
- Write doc updates that explain the "why" alongside the "what"

## Cross-Repo Resolution

For cross-project work, the `GH_REPO` environment variable is automatically set by `sdk_client.py`. The `gh` CLI natively respects this env var, so all `gh` commands automatically target the correct repository. No `--repo` flags or manual parsing needed.

## Principles

1. **Document what IS, not what WAS** — match the actual API/behavior, not the plan
2. **Full context, not just diffs** — read entire modified files, not just changed lines. The diff shows what moved; the full file shows what it means.
3. **Cross-reference, don't duplicate** — link to the source of truth rather than restating
4. **Surgical edits only** — preserve existing structure, change only what the code change invalidates
5. **Read before edit** — always read the full file before modifying it
6. **Hunt stale references** — after a refactor, grep for old pattern keywords across all docs. If the code stopped using "history" as a data bus, search docs for "history" too.
7. **When in doubt, create an issue** — flag conflicts needing human judgment rather than guessing

## Step 1: Understand the Change (Parallel Exploration)

Launch three agents in parallel using the Task tool.

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

Scan these locations:
| Location | What lives there |
|----------|-----------------|
| CLAUDE.md | Primary project guidance, architecture, rules |
| docs/features/*.md | Feature documentation |
| docs/plans/*.md | Plans that may reference this as prerequisite |
| .claude/skills/*/SKILL.md | Workflow skill definitions |
| .claude/commands/*.md | Slash commands |
| config/SOUL.md | Agent identity and behavior |
| docs/*.md (top-level) | Deployment, tools-reference, etc. |
| docs/features/README.md | Feature index table |

For each doc file found, produce one line:
  <filepath> | <one-line purpose> | <key topics/identifiers referenced>

Sort by importance: CLAUDE.md first, then features, then commands, then plans, then everything else.
Do NOT read file contents in full — scan headings, section titles, and grep for key identifiers.
```

### Agent C — Semantic Impact Finder

Spawn a sub-agent with this prompt:

```
Run the semantic doc impact finder to identify conceptually related documentation
that may need updating, even if there are no shared keywords.

1. First, ensure the doc index is current:
   python3 -c "import sys; sys.path.insert(0, '.'); from tools.doc_impact_finder import index_docs; index_docs()"

2. Then find affected docs using the change summary:
   python3 -c "
   import sys, json
   sys.path.insert(0, '.')
   from tools.doc_impact_finder import find_affected_docs
   results = find_affected_docs('''<CHANGE_SUMMARY>''')
   for r in results:
       print(f'{r.relevance:.2f} | {r.path} | {r.sections} | {r.reason}')
   "

Replace <CHANGE_SUMMARY> with a 2-3 sentence natural language summary of the code change.

Report the results as a ranked list. If no embedding API key is configured, report
that gracefully — the cascade continues with Agents A and B alone.
```

**Note**: Agent C may return zero results if no embedding API key is configured.
This is expected — the cascade degrades gracefully to lexical-only matching.

### Agent D — Issue Impact Scanner

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

Wait for all four agents to complete before proceeding. If Agent C or D failed or returned no results, proceed with the remaining agents' output only.

## Step 2: Triage — Cross-Reference Changes Against Docs

Using the outputs from Agent A (change summary), Agent B (doc inventory), Agent C (semantic impact finder — if available), and Agent D (issue impact scanner — if available), evaluate every doc file against these four triage questions:

For each document in the inventory, ask:

| # | Question | If YES |
|---|----------|--------|
| 1 | Does it **reference** the area that changed? (mentions the same files, functions, config keys, commands) | Needs update |
| 2 | Does it **depend on** the change? (describes behavior that the change alters) | Needs update |
| 3 | Does it **teach a pattern** this change modifies? (examples, templates, workflows using old API) | Needs update |
| 4 | Does it **orchestrate a workflow** using the changed components? (step-by-step instructions that now have different steps) | Needs update |

If ALL four answers are NO, skip that doc.

### Merge Semantic Results

If Agent C returned results, add any documents it identified that aren't already in the affected list from the triage questions above. Agent C catches conceptual coupling that keyword matching misses (e.g., "changed session scoping" finding session-isolation.md even without shared identifiers).

For each Agent C result with relevance >= 0.5:
- If already in the affected list: note the semantic reason as additional context
- If NOT in the affected list: add it with Agent C's reason as the justification

Produce an ordered task list of affected docs. Order by dependency — foundational docs first (CLAUDE.md, feature docs), then derivative docs (commands, plans, skills).

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

Using the **Retired terms** from Agent A's summary, grep across ALL docs for old keywords that the change replaced. This catches references the triage questions miss — docs that use an old name, old pattern, or old concept without directly depending on the changed file.

```bash
# For each retired term, search all doc locations
rg "<retired-term>" docs/ CLAUDE.md config/SOUL.md .claude/commands/ .claude/skills/
```

Add any new hits to the affected documents list from Step 2.

## Step 2c: What's Missing

After identifying docs that need updating, ask what docs *should exist but don't*:

- Did the change introduce a new feature that has no `docs/features/*.md` entry?
- Did it add a new command or skill with no corresponding documentation?
- Did it create a new config key or environment variable not listed in setup/deployment docs?
- Is there a new cross-file pattern or data flow that warrants a feature doc?

If something is missing, add a task to create it (not just update existing docs).

## Step 3: Make Surgical Edits

For each affected document, in dependency order:

1. **Read** the full file first
2. **Identify** the specific lines/sections that reference the changed area
3. **Edit** only those sections — preserve all surrounding structure, tone, and formatting
4. **Verify** the edit matches the actual implementation (not the plan, not the old behavior)

Rules for edits:
- Update code examples to match new API signatures
- Update file paths if files moved or renamed
- Update behavioral descriptions if defaults or workflows changed
- Update tables/lists if entries were added, removed, or renamed
- Add new entries to index tables (like `docs/features/README.md`) if a new feature doc was created as part of the change
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
   If any document has contradictory information that cannot be resolved from the code alone (e.g., a plan doc describes future work that may or may not still be valid), create a GitHub issue:
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

## Edge Cases

- **New feature with no existing docs**: Nothing to cascade. Report "no existing docs reference this area."
- **Deleted feature**: Remove references from docs, but do NOT delete the feature doc itself — flag for human review.
- **Renamed function/file**: Use grep to find all references across docs and update each one.
- **Config key change**: Check CLAUDE.md, .env references in docs, setup commands, and deployment docs.

## Integration

This skill pairs with:
- `/do-build` — run after build completes to cascade doc updates for the shipped code
- `/do-plan` — plans created here may need updating when prerequisites ship
- `/do-pr-review` — PR review can invoke this to verify docs match implementation
