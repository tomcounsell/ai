# Architecture Rubric (the `--arch` pass)

The lint layer (`scripts/audit_skills.py`) checks what code can verify. This rubric is the
judgment layer: applied per skill by a model, against fixed criteria, with adversarial
verification of every non-keep disposition. It is grounded in Anthropic's agent-decomposition
guidance: skills hold information needed *some* of the time; subagents exist for exactly two
reasons; primitives beat custom tooling; every change is measured against a baseline.

## How to run it

1. Run the lint first (`--json --no-sync`) — its findings are inputs, not conclusions.
2. For a fleet-wide pass, cluster skills by domain (~7 skills or ~3,500 lines per analyst)
   and dispatch parallel analyst subagents, one per cluster. Each analyst gets ONLY this
   rubric and its cluster's files — no seed conclusions.
3. Every non-keep disposition goes to a fresh-context verifier prompted to REFUTE it
   (see the verifier prompt below). The analyst who proposed a merge never confirms it.
4. Synthesize into one report: a row per skill, coverage-complete (fail loudly if any
   skill is missing a row).

## The five lenses

**1. Context economy.** A skill costs context in three tiers with different economics:
the description ships in *every* session; the body loads *per invocation*; sub-files load
*on demand*. Findings are misplacements across tier boundaries: always-true repo policy in
a body (belongs in CLAUDE.md), rarely-needed reference tables in a body (belongs in a
sub-file), implementation detail in a description (belongs in the body).

**2. Primitive fit.** Is this the right shape at all?
- *Skill*: task guidance one context window applies end to end.
- *Workflow*: multi-stage work with independent stages, fan-out, or deterministic control
  flow — where structured handoffs beat one long context. Any workflow recommendation must
  specify the stage boundaries AND the handoff schema; orchestrator↔stage communication
  breakdown is the classic failure mode of decomposition.
- *Subagent*: only for one of the two legitimate reasons — (a) parallelism, (b) fresh-mind
  context isolation (the Claude that wrote it should not review it). Name which one.
  Everything else folds back into the main agent; frontier models manage more context than
  the subagent-per-concern era assumed.
- *Script*: the body is mostly deterministic procedure. Extract to code the skill invokes;
  a model should never be asked to do what code can verify.

**3. Consolidation.** Overlapping trigger surfaces, shared skeletons, thin wrappers.
Every merge recommendation must name the surviving skill, include the proposed merged
description text, and argue that trigger precision survives — a merged description that
fires less reliably than the originals is a net loss even if it saves lines.

**4. Model tier.** Recommend by task property, not model fashion:
- **sonnet** — mechanical: runs scripts, formats output, moves messages.
- **opus** — standard multi-step reasoning: build, test triage, docs, review legwork.
- **fable** — frontier judgment where a wrong call is expensive: plan critique,
  architecture decisions, adversarial verification, client-facing design.
Record the recommendation as a `model:` frontmatter proposal; tier→model mapping lives in
one place so model releases update one table, not sixty rationales.

**5. Efficiency.** Estimate tokens pulled into context per invocation (body + eagerly
loaded sub-files; line count × ~10 tokens/line is sufficient to rank). Flag bodies that
instruct unconditional reads of every sub-file (defeats progressive disclosure) and
descriptions doing documentation work (>200 chars).

## Dispositions

| Disposition | Criteria |
|---|---|
| **keep** | Right size, right primitive, distinct trigger surface |
| **merge → {survivor}** | Overlapping domain/trigger with a named sibling; merged description included |
| **split** | Two unrelated jobs sharing one trigger surface |
| **workflow** | Independent stages + structured handoffs beat one long context |
| **subagent** | Parallelism or fresh-mind isolation — cite which |
| **script** | Deterministic procedure pretending to be prose |
| **retire** | Superseded, orphaned, or unused — check invocation history first |

## Findings schema (per skill)

```json
{
  "skill": "", "dir": "global|project|user", "lines": 0, "files": 0,
  "findings": ["..."],
  "disposition": {"action": "keep|merge|split|workflow|subagent|script|retire",
                   "target": "", "rationale": ""},
  "model": {"tier": "sonnet|opus|fable", "rationale": ""},
  "est_tokens": 0
}
```

## Verifier prompt (fresh context, per non-keep disposition)

> A skills audit proposes: {disposition} for skill {name} ({rationale}). Read the skill
> and try to REFUTE this. Refutation grounds: (1) a merge would blunt trigger precision —
> the originals fire on distinct phrasings a merged description cannot cover; (2) a
> subagent/workflow conversion cites neither parallelism nor fresh-mind isolation, or the
> stages share so much context that handoffs cost more than they isolate; (3) a retire
> target is actually load-bearing (check references and invocation history); (4) a script
> extraction would freeze judgment that genuinely varies per invocation. Default to
> refuted when uncertain. Return: {"refuted": bool, "grounds": "..."}.

Downgrade any disposition with a sustained refutation to **keep + note**.

## After the pass

Dispositions are recommendations. Nothing is executed from the audit itself: accepted
merges/splits/conversions become GitHub issues (one per disposition, each carrying the
`RENAMED_REMOVALS` hardlink-cleanup and doc-sweep requirements that skill moves need).
