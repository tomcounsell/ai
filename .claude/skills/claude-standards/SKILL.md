---
name: claude-standards
description: "Audit Claude Code assets — skills, commands, subagents, hooks, and MCP servers — against best practices and optionally apply conservative conformance changes. Use when reviewing overall Claude Code hygiene, bringing the system in line with documented standards, or after adding new skills/commands/subagents. Invoke explicitly; do not use for feature changes."
disable-model-invocation: true
allowed-tools: Read, Grep, Glob, Bash, Edit, Write
argument-hint: "[skills|commands|subagents|hooks|mcp|all] [--fix]"
---

# Claude Standards Audit

Audits the five classes of Claude Code assets in this repo against the best-practice reference documents in this directory. Default mode is **report only**. With `--fix`, applies a narrow set of conformance-only changes — never feature additions or removals.

## Asset inventory

| Asset class | Location | Standards reference |
|-------------|----------|---------------------|
| Skills | `.claude/skills/*/SKILL.md` | [STANDARDS_SKILLS.md](STANDARDS_SKILLS.md) |
| Slash commands | `.claude/commands/*.md` | [STANDARDS_COMMANDS.md](STANDARDS_COMMANDS.md) |
| Subagents | `.claude/agents/*.md` | [STANDARDS_SUBAGENTS.md](STANDARDS_SUBAGENTS.md) |
| Hooks | `.claude/settings.json` + `.claude/hooks/**` | [STANDARDS_HOOKS.md](STANDARDS_HOOKS.md) (canonical source: `.claude/skills/audit-hooks/BEST_PRACTICES.md`) |
| MCP servers | `config/mcp_library.json` + `mcp_servers/` (if present) | [STANDARDS_MCP.md](STANDARDS_MCP.md) |

Each `STANDARDS_*.md` file holds the rules. Keep those files as the single source of truth; do not inline rules here.

## References

Cross-cutting reference docs live under `references/` and are loaded only when the task calls for them (progressive disclosure):

- [`references/EVALS.md`](references/EVALS.md) — how to evaluate a prompt systematically. Read when iterating on a prompt that ships inside a skill, subagent, hook, or agent loop.
- [`references/PROMPT_ENGINEERING.md`](references/PROMPT_ENGINEERING.md) — how to iterate on a single prompt to make it reliably produce the output you want (techniques, output steering, multimodal, caching). Read alongside `EVALS.md` when refining a shipping prompt.
- [`references/TOOL_USE.md`](references/TOOL_USE.md) — designing tool-enabled prompts: schemas, multi-turn loops, parallel execution, structured output via tools, built-in Anthropic tools.
- [`references/RAG.md`](references/RAG.md) — retrieval-augmented generation: chunking, embeddings, hybrid retrieval, reranking, contextual retrieval, citations.
- [`references/AGENTS_AND_WORKFLOWS.md`](references/AGENTS_AND_WORKFLOWS.md) — when to use a predetermined workflow vs an agent, and the four standard workflow patterns (chaining, parallelization, routing, evaluator-optimizer).
- [`references/MCP.md`](references/MCP.md) — using the Model Context Protocol to integrate tools, resources, and prompts authored outside your application.
- [`references/CLAUDE_CODE.md`](references/CLAUDE_CODE.md) — Claude Code features: context management (CLAUDE.md, @mentions), workflow modes (Plan/Thinking), conversation controls, custom commands, hooks, SDK, and GitHub integration.

None of these are part of the audit itself — they are loaded on demand when a task calls for them.

## Arguments

- `skills | commands | subagents | hooks | mcp` — audit only that domain. Default: `all`.
- `--fix` — apply conformance-only changes. Without it, the skill is report-only.

Parse `$ARGUMENTS` for one domain token (optional) and the `--fix` flag (optional). Any other tokens are a hard error — surface them and stop.

## What counts as a conformance-only change

In `--fix` mode, the ONLY changes permitted are ones that:

1. **Do not change what an asset does.** Runtime behavior for users must be identical before and after.
2. **Do not add features, tools, scopes, or capabilities.** If a skill has `allowed-tools: Read, Grep`, do not add `Write`.
3. **Do not remove features.** If a command exposes an argument, do not drop it. If a subagent exposes a tool, do not strip it.
4. **Are purely structural, stylistic, or metadata.** Examples: adding missing frontmatter keys with values derivable from the file, fixing header casing to match the standard, reordering sections to the documented order, normalizing filenames, adding a `## When to use` section that restates existing content, fixing `allowed-tools` capitalization.
5. **Are unambiguous.** If two valid interpretations exist, report it and do not auto-fix.

If a finding requires judgment or introduces risk, it goes in the report for human review — never into `--fix`.

## Audit procedure

### Step 1: Resolve scope

From `$ARGUMENTS`, determine which domains to run. If unspecified, run all five.

### Step 2: For each in-scope domain

1. **Load the standards file.** Read the relevant `STANDARDS_*.md` in full. If it is empty or contains only placeholders, report "no standards defined for this domain — skipping" and move on. Never invent rules.
2. **Enumerate assets.** Use the location column above. Skip `__pycache__`, `_template`, and anything explicitly marked deprecated in the asset.
3. **Check each asset against each rule.** Record PASS / WARN / FAIL per (asset, rule) pair. Include the specific evidence (file:line or quoted line).
4. **In `--fix` mode only:** for each FAIL or WARN whose remediation is in the "Auto-fix eligible" list inside the standards file, apply the edit. Re-check after editing and flip the disposition to PASS-AFTER-FIX. Everything else stays in the report.

### Step 3: Report

Emit one top-level report with a per-domain section:

```
## Claude Standards Audit

### Summary
- Domains audited: skills, commands, subagents, hooks, mcp
- Assets scanned: N total
- PASS: N | WARN: N | FAIL: N | FIXED: N (--fix only)

### Skills (N assets)
| Asset | Rule | Finding | Severity | Action |
|-------|------|---------|----------|--------|

### Commands (N assets)
...

### Subagents (N assets)
...

### Hooks (N assets)
...

### MCP servers (N assets)
...

### Recommendations
- Items that were not auto-fixable and need human judgment, grouped by domain
```

### Step 4: Disposition

- **Audit mode (default):** stop after the report. Do not edit any files.
- **`--fix` mode:** after edits are applied, run the same audit again against the changed files only and confirm the FIXED items now PASS. If any fix regressed another rule, revert that specific fix and report it.

## Severity levels

- **FAIL** — rule violation that breaks runtime behavior, misleads agents, or blocks invocation (e.g., missing required frontmatter, broken `allowed-tools` syntax, script path that does not exist).
- **WARN** — suboptimal pattern that should be improved but does not break anything (e.g., description under 40 characters, missing `## When to use` section, inconsistent capitalization).
- **PASS** — asset follows all applicable rules in the standards file.

## Hard rules

- **Never invent rules.** If a `STANDARDS_*.md` does not list a check, it is not a check. Do not pattern-match from other audit skills.
- **Never delete an asset.** Deprecated or abandoned assets are reported, not removed.
- **Never touch business logic.** This skill edits frontmatter, headers, section order, and filenames. It does not edit anything inside a hook script's `def main()`, a subagent's tool list, or a command's workflow body.
- **Read-only for `config/mcp_library.json`.** Structural changes to MCP server definitions are always reported, never auto-fixed, because they affect live agent capabilities.

## After the audit

This skill produces findings and (optionally) conformance edits. It does not ship code, open PRs, or restart services. If the report surfaces substantive issues, escalate to the human or to `/sdlc`.
