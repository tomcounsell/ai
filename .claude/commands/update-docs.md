---
description: Cascade documentation updates after code changes — finds and surgically edits all affected docs
argument-hint: <PR-number, commit-SHA, or change-description>
---

# Update Docs — Cascade Command

After a code change lands, find every document that references the changed area and make targeted, surgical updates so docs match the actual implementation.

**Input**: $1

## Principles

1. **Document what IS, not what WAS** — match the actual API/behavior, not the plan
2. **Cross-reference, don't duplicate** — link to the source of truth rather than restating
3. **Surgical edits only** — preserve existing structure, change only what the code change invalidates
4. **Read before edit** — always read the full file before modifying it
5. **When in doubt, create an issue** — flag conflicts needing human judgment rather than guessing

## Step 1: Understand the Change (Parallel Exploration)

Launch two agents in parallel using the Task tool.

### Agent A — Change Explorer

Spawn a sub-agent with this prompt:

```
Explore and summarize the code change described by: $1

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

Produce a structured summary:
- **What changed**: Files modified, functions added/removed/renamed, config keys changed
- **API surface changes**: New/removed/renamed public interfaces, parameters, return types
- **Behavioral changes**: Different defaults, new error conditions, changed workflows
- **Key terms**: Important identifiers (function names, config keys, command names, file paths) that docs might reference
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

Wait for both agents to complete before proceeding.

## Step 2: Triage — Cross-Reference Changes Against Docs

Using the outputs from Agent A (change summary) and Agent B (doc inventory), evaluate every doc file against these four triage questions:

For each document in the inventory, ask:

| # | Question | If YES |
|---|----------|--------|
| 1 | Does it **reference** the area that changed? (mentions the same files, functions, config keys, commands) | Needs update |
| 2 | Does it **depend on** the change? (describes behavior that the change alters) | Needs update |
| 3 | Does it **teach a pattern** this change modifies? (examples, templates, workflows using old API) | Needs update |
| 4 | Does it **orchestrate a workflow** using the changed components? (step-by-step instructions that now have different steps) | Needs update |

If ALL four answers are NO, skip that doc.

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
     --body "The /update-docs cascade found a potential conflict in <filepath> after <change description>. The doc references <X> but the code now does <Y>. Human judgment needed to resolve."
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

   **No changes needed**: (list if applicable)
   ```

## Edge Cases

- **New feature with no existing docs**: Nothing to cascade. Report "no existing docs reference this area."
- **Deleted feature**: Remove references from docs, but do NOT delete the feature doc itself — flag for human review.
- **Renamed function/file**: Use grep to find all references across docs and update each one.
- **Config key change**: Check CLAUDE.md, .env references in docs, setup commands, and deployment docs.

## Integration

This command pairs with:
- `/build` — run after build completes to cascade doc updates for the shipped code
- `/make-plan` — plans created here may need updating when prerequisites ship
- `/review` — review can invoke this to verify docs match implementation
