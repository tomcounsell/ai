# Audit Skill Best Practices

Extracted from the 4 existing audit skills in this repo: `audit-models`, `audit-next-tool`, `do-docs-audit`, `do-skills-audit`.

## Anatomy of a Good Audit Skill

Every audit skill answers five questions:
1. **What** gets audited? (the subject — models, tools, docs, skills)
2. **Where** does it look? (the scan target — directory, file pattern, API)
3. **How** does it check? (the rules — deterministic checks, heuristics, AI judges)
4. **What** does it report? (the output — severity-grouped findings, verdicts, counts)
5. **What** happens next? (the disposition — auto-fix, human review, commit, issue)

## Design Decisions

### 1. Script-backed vs. Prompt-only

| Approach | When to use | Examples |
|----------|-------------|---------|
| **Script-backed** | Checks are deterministic, regex-based, or structural. Benefits from caching, CLI flags, JSON output. | `do-skills-audit` (Python script with 12 rules) |
| **Prompt-only** | Checks require semantic understanding, cross-referencing, or judgment calls. | `audit-models` (relationship analysis), `do-docs-audit` (reference verification) |
| **Hybrid** | Some checks are deterministic, others need LLM reasoning. | `audit-next-tool` (structure checks + quality judgment) |

**Rule of thumb**: If you can write the check as a regex or AST walk, make it a script. If it requires "does this make sense?", keep it in the prompt.

### 2. Disposition: What Happens After Findings

| Disposition | When to use | Example |
|-------------|-------------|---------|
| **Report only** | Domain expert (human) must decide. Audit surfaces info they can't easily see themselves. | `audit-models` — pauses for architect review |
| **Auto-fix trivial** | Some findings have obvious mechanical fixes (rename, add field). Complex ones need human. | `do-skills-audit --fix` — fixes missing name fields |
| **Full apply** | All corrections are safe and reversible. Skill commits results. | `do-docs-audit` — applies UPDATE/DELETE verdicts, commits |

**Rule of thumb**: Auto-fix only when the fix is unambiguous and the blast radius is contained to the audited files.

### 3. Severity Levels

Use exactly three levels, consistently:

| Level | Meaning | Action required |
|-------|---------|-----------------|
| **CRITICAL / FAIL** | Broken, missing, or dangerous. Must fix before shipping. | Block / immediate fix |
| **WARNING / WARN** | Suboptimal, drifted, or inconsistent. Should fix soon. | Track / schedule fix |
| **INFO** | Observation, suggestion, or documentation gap. Nice to fix. | Optional |

### 4. Parallelization

For audits scanning many items (10+), batch and parallelize:
- `do-docs-audit`: batches of 12 files, spawns parallel Task agents per batch
- `do-skills-audit`: sequential (usually <15 skills, fast enough single-threaded)

**Rule of thumb**: Parallelize when items are independent and count > 10. Use Task tool for batched parallelism.

### 5. Naming Convention

Three tiers based on portability:

| Pattern | When | Portable? | Examples |
|---------|------|-----------|---------|
| `do-{subject}-audit` | General-purpose audits usable in any repo (docs, skills, deps, env vars) | Yes — works anywhere | `do-docs-audit`, `do-skills-audit` |
| `audit-{subject}` | Repo-specific feature audits tied to this project's domain | No — project-specific | `audit-models` (Popoto), `audit-next-tool` (Valor tools) |

**Decision rule**:
- Would this audit make sense in a different repo? → `do-{subject}-audit`
- Does it audit something unique to this project's architecture? → `audit-{subject}`

**Slash command ergonomics**:
- General: `/do-docs-audit`, `/do-skills-audit`, `/do-deps-audit`
- Feature-specific: `/audit-models`, `/audit-next-tool`, `/audit-prompts`

The `do-` prefix groups general audits together in autocomplete. Feature-specific audits cluster under `audit-` and are clearly project-local.

## Structural Rules

### SKILL.md Structure (required sections)

1. **Frontmatter** — name, description, allowed-tools. Set `disable-model-invocation: true` if the audit should only run on explicit `/slash-command`.
2. **What this skill does** — numbered list of steps (scan → check → report → act).
3. **Audit Checks** — each check gets a subsection with name, description, and severity.
4. **Output Format** — exact template of what the report looks like. Use code blocks.
5. **After the Audit** — what happens with findings (disposition).

### Check Design Rules

1. **Each check must be independently useful** — don't create checks that only make sense in combination.
2. **Each check must have a clear severity** — don't leave severity ambiguous.
3. **Each check must be verifiable** — "code quality" is not a check. "Function has return type annotation" is.
4. **Checks should be additive** — easy to add new checks without restructuring the skill.
5. **False positives are worse than false negatives** — conservative thresholds. When uncertain, downgrade severity.

### Output Rules

1. **Always show a summary table before acting** — human sees what's coming.
2. **Always include counts** — PASS: N, WARN: N, FAIL: N (or KEEP/UPDATE/DELETE).
3. **Group findings by severity** — CRITICAL first, then WARNING, then INFO.
4. **Include the item name and check name in every finding** — `[check-name] ItemName: finding`.
5. **Keep findings actionable** — not "this is wrong" but "expected X, found Y".

### Commit Rules (for audits that auto-fix)

1. **Never include unchanged items in commit messages** — only list what was modified.
2. **Keep commit messages under 50 lines** — use GitHub issues for large reports.
3. **Threshold router**: <=5 changes = inline commit message, >5 changes = create GitHub issue + reference it.

## Writing Style

### Explain the Why

Prefer explaining reasoning over rigid rules. Instead of:
> "ALWAYS include a docstring on every function."

Write:
> "Functions without docstrings are invisible to the agent — it can't discover capabilities it doesn't know exist. Check that public functions have docstrings so the agent can find and use them."

The model is smart enough to handle edge cases when it understands the motivation. MUST/NEVER in all caps is a yellow flag — if you need it, you probably haven't explained the reasoning well enough.

### Concrete Examples Over Abstract Formats

Every output format section should include 2-3 examples with realistic data. Not:
> "The report shows findings grouped by severity."

But:
> ```
> #### CRITICAL
> - [missing-key] UserSession: no `project_key` field — sessions will be global instead of project-scoped
> - [orphan] DeadLetter: no FK references to/from any other model — data is unreachable
>
> #### WARNING
> - [naming-drift] AgentSession.agent_session_id: convention is `session_id` — inconsistent with 4 other models
> ```

### Autonomy Calibration

Match the instruction style to the audit's freedom level:

| Autonomy | Instruction style | Example audit |
|----------|-------------------|---------------|
| **High** | Guiding principles, let model reason about edge cases | `audit-models` — "Flag when the same concept uses different names" |
| **Medium** | Structured phases with judgment within each phase | `audit-next-tool` — 4-phase process, judgment on quality |
| **Low** | Step-by-step scripts, deterministic pass/fail | `do-skills-audit` — Python script with 12 boolean rules |

Don't mix autonomy levels within a single check. If a check has a deterministic part and a judgment part, split it into two checks.

## Anti-patterns

1. **Monolithic checker** — one giant function that checks everything. Split into independent checks.
2. **Audit without examples** — the output format section must show a concrete example, not just describe the format.
3. **Silent fixes** — auto-fixing without reporting what was changed. Always show before/after.
4. **Unbounded scope** — auditing everything everywhere. Define the scan target precisely.
5. **Checks that require external state** — an audit should be reproducible from the filesystem alone (or clearly document external dependencies).
6. **Missing exit criteria** — "audit the code quality" has no clear pass/fail. Define thresholds.
7. **Overfitting to known issues** — designing checks that only catch the specific problems you've seen. Generalize: if you saw a missing field on one model, check ALL models for missing fields, not just that one.
8. **Keyword-trigger descriptions** — writing descriptions that only match "audit X". Users say "check", "validate", "review", "what's wrong with", "scan", "lint". Include synonyms.
9. **Oppressive MUSTs** — rigid rules without reasoning. The model will follow them blindly and produce bad results on edge cases. Explain why the rule exists.
