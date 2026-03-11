# Skills Dependency Map

Visual map of all skill relationships, agent usage, and sub-file structure. Use this to identify orphans, redundancies, and the critical path through the system.

## Invocation Chain (Deepest Path)

```
User request
  |
  v
/do-plan -----> docs/plans/{slug}.md
  |
  v
/do-build (fork context, isolated worktree)
  |
  |---> [Task: builder]       write code
  |---> [Task: test-engineer]  write tests
  |
  |---> /do-test              run test suite
  |       |---> [Task: test-engineer]   pytest
  |       |---> [Task: validator]       ruff lint
  |       \---> [Task: frontend-tester] browser tests
  |
  |---> /do-patch             (auto-invoked on test failure, up to 3x)
  |       |---> [Task: builder]  apply fix
  |       \---> direct pytest + lint  (no longer dispatches do-test)
  |
  |---> /do-docs              cascade doc updates
  |       \---> [Task: 4 parallel explorers (A: change, B: inventory, C: semantic, D: issue impact)]
  |
  |---> [Task: documentarian]  write feature doc
  |---> [Task: validator]      final quality check
  \---> gh pr create           open PR
          |
          v
/do-pr-review (fork context)
  |---> /prepare-app           start services
  \---> agent-browser          take screenshots
```

## Skill-to-Skill References

Arrows mean "invokes" or "loads from".

```
sdlc .........(describes)........> do-plan, do-build, do-test, do-pr-review

do-build ------> do-test
             \-> do-patch
             \-> do-docs

do-patch -------> (direct pytest + lint, no longer invokes do-test)

do-pr-review ---> prepare-app
              \-> agent-browser

new-valor-skill -> new-skill  (reads SKILL.md + SKILL_TEMPLATE.md)

add-feature ....(references)...> prime, pthread, sdlc, do-pr-review
```

**All other skills have ZERO outgoing skill references.**

## Skill-to-Agent References

| Skill | Agents spawned via Task tool |
|-------|------------------------------|
| do-build | builder, validator, test-engineer, documentarian, + custom per plan |
| do-test | test-engineer, validator, frontend-tester |
| do-patch | builder |
| do-docs | 4 parallel explorers: Change Explorer, Doc Inventory, Semantic Impact, Issue Impact Scanner |
| do-docs-audit | unnamed parallel auditors (not agent definitions) |

**Agents actually referenced by skills (7):** builder, validator, code-reviewer, test-engineer, documentarian, frontend-tester, plan-maker

**Full 25-agent roster (3 tiers):**

| Tier | Agents | Purpose |
|------|--------|---------|
| **Tier 1 — Core (7)** | builder, validator, code-reviewer, test-engineer, documentarian, plan-maker, frontend-tester | Wired into SDLC pipeline |
| **Tier 2 — Specialists (13)** | debugging-specialist, async-specialist, security-reviewer, performance-optimizer, mcp-specialist, agent-architect, api-integration-specialist, data-architect, migration-specialist, documentation-specialist, test-writer, ui-ux-specialist, designer | Recruitable by plans for domain expertise |
| **Tier 2b — Service (5)** | linear, notion, sentry, stripe, render | Domain-specific task delegation |

**Agents consolidated (issue #155):** database-architect (patterns now in builder), infrastructure-engineer (patterns now in performance-optimizer), integration-specialist (patterns now in mcp-specialist), tool-developer (patterns now in mcp-specialist), validation-specialist (patterns now in validator), quality-auditor (covered by code-reviewer), agents README (now in skills README)

## Progressive Disclosure (Sub-files)

| Skill | Sub-files | Loaded when... |
|-------|-----------|----------------|
| do-build | `WORKFLOW.md` | Executing tasks (steps 1-5) |
| do-build | `PR_AND_CLEANUP.md` | Creating PR (steps 6-9) |
| do-plan | `PLAN_TEMPLATE.md` | Writing the plan doc |
| do-plan | `SCOPING.md` | Request is vague, needs narrowing |
| do-plan | `EXAMPLES.md` | Classifying request type |
| new-skill | `SKILL_TEMPLATE.md` | Creating a new skill |
| do-skills-audit | `references/anthropic-skill-creator.md` | Validating against canonical patterns |
| frontend-design | `reference/*.md` (7 files) | Typography, color, spacing, interaction, motion, responsive, UX writing |

## Skill Categories

### SDLC Core (the critical path — see `.claude/skills/sdlc/SKILL.md` for ground truth)
```
do-plan → do-build → do-test → do-patch → do-pr-review → do-patch → do-docs → merge
```
These 6 skills form the autonomous development loop. Everything else is support.

### Standalone User Tools (no dependencies, no dependents)
| Skill | What it does |
|-------|-------------|
| prime | Codebase onboarding |
| setup | New machine config |
| update | Pull + deploy |
| reclassify | Change plan type |
| audit-next-tool | Tool quality check |
| do-skills-audit | Skills quality check |
| do-docs-audit | Docs accuracy check |
| do-design-review | UI quality review |
| frontend-design | Design reference |
| pthread | Parallel execution pattern |

### Model-Only Background Skills (never user-invoked)
| Skill | What it does |
|-------|-------------|
| agent-browser | Browser automation |
| telegram | Read/send Telegram |
| reading-sms-messages | Read SMS/iMessage |
| checking-system-logs | Query bridge logs |
| google-workspace | Google API guide |

### Meta Skills (create other skills)
```
new-skill (generic) <--- new-valor-skill (wraps with Valor patterns)
```

### Reference/Guide Skills (no runtime behavior)
| Skill | What it does |
|-------|-------------|
| add-feature | How to extend the system |
| sdlc | Describes the full lifecycle pattern |

## Observations

### Agent Roster Status (post issue #155 cleanup)
25 agents remain after deleting 7 redundant/merged files. 7 agents are wired into the SDLC pipeline, 13 are specialist agents recruitable by plans, and 5 are service agents. The full tiered roster is documented in PLAN_TEMPLATE.md and the skills README.

### Potential Redundancies
- **sdlc** vs **do-build**: sdlc describes the pattern that do-build executes. sdlc adds Plan + Review phases around do-build. Consider whether sdlc should be folded into CLAUDE.md workflow docs instead of being a skill.
- **do-docs** vs **do-docs-audit**: different purposes (cascade updates vs. accuracy audit) but similar names. Not redundant, just confusingly similar.
- **add-feature** vs **new-valor-skill** vs **new-skill**: three skills about "adding things". add-feature is a guide, new-skill is generic, new-valor-skill is project-specific. Clear separation but worth noting.
- **audit-next-tool** vs **do-skills-audit**: tool audit vs skill audit. Different targets, reasonable separation.
- **do-design-review** vs **frontend-design**: review vs. reference. Different purposes.

### Skills That Could Be Docs Instead of Skills
- **add-feature** - pure reference guide, no runtime behavior
- **sdlc** - describes a workflow, could live in CLAUDE.md
- **prime** - onboarding guide, could be a doc

### Missing Edges
- **do-build** references `documentarian` agent but doesn't invoke `/do-docs` for the agent — it calls do-docs as a skill separately. Two doc paths exist.
- **do-pr-review** references `agent-browser` but not as a Task tool agent — it's invoked via bash commands.
