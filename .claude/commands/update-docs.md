# Update Documentation Cascade

After a significant change lands (merged PR, new service, architecture decision), systematically propagate it through all documentation layers.

## Trigger

Run this after any change that introduces a new pattern, service, infrastructure component, or architectural decision that other parts of the system need to be aware of.

**Input**: Provide the PR number/URL, commit, or describe the change that landed.

## Procedure

### 1. Understand the change

Launch two parallel agents:

**Agent A — Explore the change itself:**
- If a PR: `gh pr view <number> --json state,headRefName,mergedAt` and `gh pr diff <number> --name-only` then read the changed files
- If a commit: `git show <sha> --stat` then read the changed files
- If described: search the codebase for the relevant code

Extract:
- **What was added/changed** (new files, new patterns, new dependencies)
- **Key API surface** (function signatures, model fields, config keys, CLI commands)
- **Any deviations from plan** (implementation may differ from what was planned)

**Agent B — Discover all documentation:**
Find every documentation file that could be affected. Search these locations:

| Location | What lives there |
|----------|-----------------|
| `CLAUDE.md` | Primary project guidance — architecture, rules, patterns, infra |
| `docs/*.md` | Convention docs, guides, integration patterns |
| `docs/plans/*.md` | Implementation plans (may reference this as prerequisite) |
| `.claude/skills/*.md` | Workflow skills that may reference tools or patterns |
| `.claude/commands/*.md` | Slash commands |
| `docs/templates/` | Templates that may need structural updates |
| `settings/README.md` | Settings module documentation |
| `render.yaml` | Infrastructure-as-code (if infra changed) |

Read each file found and note which ones reference the area that changed.

### 2. Triage: which docs need updates?

When both agents complete, cross-reference the change against the documentation inventory. For each file, ask:

- Does this doc **reference** the area that changed? (e.g., a table listing tools, a diagram showing services)
- Does this doc **depend on** the change? (e.g., a plan that listed this as a prerequisite)
- Does this doc **teach a pattern** that this change establishes or modifies?
- Does this doc **configure infrastructure** affected by the change?
- Does this doc **orchestrate a workflow** that uses the changed components? (e.g., skills that call tools)

Skip files that have no connection to the change. Be conservative — only update what's directly affected.

### 3. Create a task list

Create one task per file that needs updating. Work through them in dependency order: foundational docs first (conventions, integration guides), then consuming docs (CLAUDE.md, skills, plans).

### 4. Make targeted edits

For each file, **read it first**, then make precise, minimal edits — not rewrites. The goal is surgical updates that preserve existing content:

| Doc type | What to update |
|----------|---------------|
| **Convention/guide docs** | Add new patterns, update decision matrices, add cross-references |
| **CLAUDE.md** | Add to architecture sections, update tables, add new rules or links |
| **Plan docs** | Status → Done, fix API discrepancies, update architecture diagrams |
| **Skills/commands** | Add notes about new alternatives, update tool references |
| **Dependent plans** | Mark prerequisites as done, update interface descriptions to match actual API |
| **Templates** | Update structural elements if the change affects template content |
| **Infrastructure files** | Add service definitions (render.yaml, docker-compose, etc.) |

### 5. Verify

Review all changes to confirm only intended files were touched and each edit is targeted:

```bash
# Show all changed files — compare against your task list
git diff --stat

# Review the actual diff for correctness
git diff <file1> <file2> ...
```

Check for pre-existing unstaged changes that may be mixed into the diff. Only your documentation updates should be included in the commit.

## Principles

- **Targeted, not exhaustive**: Edit only what's directly affected. Don't rewrite docs that happen to be nearby.
- **Read before edit**: Always read the full file (or relevant section) before editing. Understand existing structure before modifying.
- **Match the actual API**: If implementation differs from the plan (e.g., different status names, different model choices), update docs to match reality.
- **Forward-looking**: Update dependent plans and skills so the next builder has accurate context.
- **No stale references**: If a technology was replaced or a pattern superseded, remove old references — don't leave "old vs new" comparisons.
- **Document what IS, not what WAS**: Per CLAUDE.md conventions, only describe current state.
- **Cross-reference, don't duplicate**: When a pattern is fully documented in one place (e.g., `docs/AI_CONVENTIONS.md`), other docs should link to it rather than repeating the details.

## Example changes that warrant this cascade

- New architectural pattern (Named AI Tools, service layer, new convention)
- New infrastructure service (background tasks, file storage, caching)
- Django version upgrade or major dependency change
- New deployment service (worker, cron job)
- Dependency swap (e.g., Celery → native tasks, sub-agents → PydanticAI services)
- New app or major new capability within an existing app
