# Audit Design Choices

Reference for the choices in new-audit-skill/SKILL.md, taken from the audit skills that exist.

## Approach

| Approach | Fits | Example |
|---|---|---|
| Script-backed | Deterministic, structural, or regex checks; JSON output; cacheable | `audit-skills` lint |
| Prompt-only | Semantic judgment, cross-referencing | `audit-models`, `do-integration-audit` |
| Hybrid | Structural checks plus quality judgment | `audit-tools`, `audit-hooks` |

If a check has both a deterministic part and a judgment part, split it into two checks.

## Disposition

| Disposition | Fits |
|---|---|
| Report only | A domain expert must decide; the audit surfaces what they can't easily see |
| Auto-fix trivial | Mechanical, unambiguous fixes confined to the audited files (`audit-skills --fix`) |
| Apply, gated | Every fix is safe and reversible, but the write still passes a review (a PR or the caller's commit) before it is permanent |

For audits that commit or file issues: list only what changed. A large report goes in an
issue, and the commit message references it.

## Checks

- Each check is useful on its own and has one severity.
- It must be verifiable. "Code quality" is not a check; "public function has a return
  annotation" is.
- Generalize from the problems the user has seen. A missing field on one model means checking
  every model.
- State external dependencies (APIs, running services). Otherwise results must be reproducible
  from the filesystem.

## Scale

Past about 10 independent items with judgment per item, fan out subagents in batches. Give
each one the checks and a time budget, and aggregate the results before reporting.

## Invocation

Set `disable-model-invocation: true` when a false-positive auto-invocation would waste real
time, as with heavy or domain-specific audits. Leave it unset for lightweight, broadly useful
audits.
