---
name: new-audit-skill
description: "Create a new audit skill from established patterns. Use when creating an audit, or on 'create an audit', 'new audit skill', 'add an audit', 'make an audit for', 'I want to check X for problems'."
allowed-tools: Read, Write, Edit, Glob, Grep, Bash
argument-hint: "<subject-to-audit>"
---

# New Audit Skill

**Goal:** turn a request like "I want to check X for problems" into a complete, working audit skill — interviewed requirements, well-designed checks, a tested output format, and a clear disposition — following the patterns of the existing audit skills. Success is an audit that produces accurate, actionable findings on its first real invocation, not a template that merely looks right.

## When to load sub-files

- For the audit SKILL.md template skeleton → read [AUDIT_TEMPLATE.md](AUDIT_TEMPLATE.md)
- For best practices, design decisions, and anti-patterns → read [BEST_PRACTICES.md](BEST_PRACTICES.md)
- For the generic skill creation rules (frontmatter, field constraints) → read [../new-skill/SKILL.md](../new-skill/SKILL.md)

## Existing audit skills (reference implementations)

| Skill | Subject | Approach | Disposition | Autonomy |
|-------|---------|----------|-------------|----------|
| `audit-models` | Data-layer models | Prompt-only, 6 heuristic checks | Report only (human decides) | Low — exploratory |
| `audit-skills` | Skill SKILL.md files | Script-backed, 20 rules | Auto-fix trivial, report complex | High — deterministic |
| `audit-tools` | Python tools in `tools/` | Prompt-only, 10 checks | Report only (human decides) | Medium |
| `do-design-audit` | Web UI quality | Prompt-only, 10-dimension rubric | Report only (findings + top 3 fixes) | Medium — opinionated |
| `docs-auditor` (substrate) | Docs + cross-references | Prompt + parallel agents | Auto-fix, commit, threshold router | High — mechanical |
| `do-integration-audit` | Feature wiring | Prompt-only, 12 semantic checks | Report only (human decides) | High — exploratory |

## Quick start

### 1. Interview: gather requirements

Before writing anything, establish clarity on these six dimensions. If the user has already described what they want (e.g., "I need an audit for our env vars"), extract answers from the conversation first — don't re-ask what's already been said.

| Dimension | Question | Example answer |
|-----------|----------|----------------|
| **Subject** | What is being audited? | "Environment variables" |
| **Scan target** | Where do the items live? | `.env*`, `config/*.py`, CI yaml files |
| **Key concerns** | What problems have you seen? What should checks catch? | "Vars defined in .env but never referenced in code" |
| **Disposition** | Should it auto-fix, report only, or commit? | "Report only — I want to review before changing" |
| **Trigger scenarios** | When should someone run this audit? | "After adding new env vars", "periodic housekeeping" |
| **Autonomy level** | How much freedom should the audit have? | "Tight — step-by-step, no judgment calls" |

The **autonomy level** determines the instruction style:
- **High autonomy** (exploratory audits): provide guiding principles, let the model reason about edge cases. Good for audits requiring semantic judgment (e.g., "is this naming consistent?").
- **Low autonomy** (mechanical audits): provide step-by-step instructions or scripts. Good for audits with deterministic pass/fail criteria (e.g., "does this field exist?").

### 2. Design the checks

Each check needs:
- A short name (kebab-case, e.g., `missing-docstring`, `orphan-reference`)
- A one-sentence description of what it validates — explain the **why**, not just the **what**
- A severity: CRITICAL, WARNING, or INFO
- A verification method: how to determine pass/fail

**Rules for good checks** (from best practices):
- Each check must be independently useful
- Each check must be verifiable — not subjective ("code quality" is not a check; "function has return type" is)
- False positives are worse than false negatives — conservative thresholds
- Checks should be additive (easy to add more later without restructuring)
- Explain the reasoning behind the check so the model can handle edge cases intelligently, rather than rigid MUST/NEVER rules

Aim for 4-8 checks for a focused audit, 8-12 for comprehensive audits.

### 3. Choose the approach

| If checks are... | Use |
|-------------------|-----|
| Regex, structural, AST-based | Script-backed (an `audit.py` under the new skill dir's `scripts/`) |
| Semantic, cross-referencing, judgment | Prompt-only (instructions in SKILL.md) |
| Mix of both | Hybrid (script for deterministic, prompt for semantic) |

### 4. Create the skill

1. Read [AUDIT_TEMPLATE.md](AUDIT_TEMPLATE.md) for the skeleton
2. Read [BEST_PRACTICES.md](BEST_PRACTICES.md) for design guidance
3. Create the directory: `mkdir -p .claude/skills/audit-{subject}/`
4. Fill in the template, replacing all UPPERCASE placeholders
5. If script-backed, create `.claude/skills/audit-{subject}/scripts/audit.py` with CLI flags (`--fix`, `--json`, `--target`)
6. Write 2-3 concrete examples showing real audit output — not pseudocode, but realistic items/findings/verdicts that someone would actually see when running the audit

### 5. Write the description

The description is the primary triggering mechanism — Claude reads it to decide whether to load the skill. Audit skills tend to under-trigger because users don't always say "audit"; they say "check", "validate", "review", "what's wrong with", "are there problems in".

**Format**: `"Audit SUBJECT for PROBLEMS. Use when TRIGGER_1. Also use when TRIGGER_2, TRIGGER_3, or TRIGGER_4."`

**Include**:
- The subject and what problems it catches
- 3-5 natural language trigger phrases users might say
- Adjacent keywords: "validate", "check", "review", "scan", "lint", "verify"
- Keep under 200 chars ideally, 1024 max

### 6. Naming

Two naming tiers based on portability:

| Pattern | When to use | Examples |
|---------|-------------|---------|
| `do-{subject}-audit` | General-purpose, works in any repo | `docs-auditor` (substrate), `do-deps-audit` |
| `audit-{subject}` | Repo-specific, tied to this project's domain | `audit-models`, `audit-tools`, `audit-prompts`, `audit-skills` |

**Decision rule**: Would this audit make sense in a different repo? If yes → `do-{subject}-audit`. If it audits something unique to this project → `audit-{subject}`.

The `do-` prefix groups general audits together in slash-command autocomplete. The `name` in frontmatter must match the directory name.

### 7. Test the audit

After creating the skill, run it once on real data to verify:

1. **Smoke test**: Invoke `/audit-{subject}` and confirm it discovers the right items
2. **Check coverage**: Do the checks catch the known problems the user mentioned in the interview?
3. **False positive check**: Are any findings incorrect or misleading?
4. **Output readability**: Is the report clear enough to act on without re-reading the skill?

If findings are wrong or missing, revise the checks and re-run.

### 8. Validate structure

Final structural checklist:
- [ ] Frontmatter has `name`, `description` (trigger-oriented, includes synonyms), `allowed-tools`
- [ ] "What this skill does" has numbered steps: scan → check → report → act
- [ ] Each check has name, description (with why), severity, and verification method
- [ ] Output format section shows 2-3 concrete examples with realistic data
- [ ] Disposition section clearly states what happens after findings
- [ ] SKILL.md is under 500 lines (use sub-files for detailed reference material)
- [ ] Description includes trigger synonyms beyond just "audit" (check, validate, review, scan)
