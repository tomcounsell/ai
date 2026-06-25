# Plan Critique Triage

The `/do-plan-critique` skill classifies each plan as LITE or FULL before dispatching critics, halving token spend on simple plans. It also resumes interrupted critique runs rather than starting over, and enforces that critics provide Implementation Notes in their first pass instead of looping back.

## LITE vs FULL Routing

**Step 2.6** (triage) runs immediately after structural checks and before any critic is dispatched.

### Force-FULL doctrine paths

The following paths always trigger a FULL critique, regardless of LLM triage:

- `config/personas/`
- `.claude/skills/`
- `.claude/skills-global/`
- `agent/sdlc_router.py`
- `agent/pipeline_graph.py`
- `.claude/hooks/`

Plans with `appetite: Large` in their frontmatter are also force-FULL.

### LLM triage (non-doctrine plans)

For all other plans, an LLM classifier reads the plan and returns `LITE` or `FULL`. The classifier prompt is biased toward FULL — it returns LITE only when the plan clearly touches a single, well-understood surface with no cross-subsystem dependencies. Any ambiguity produces FULL.

### What changes based on tier

| | LITE | FULL |
|---|---|---|
| Critics dispatched | 1 (Consolidated Critic) | 3 (Risk & Robustness, Scope & Value, History & Consistency) |
| Run-dir prefix | `lite-` | (none) |
| Roster size (`_roster.json`) | 1 | 3 |

## Critics

### FULL: three merged critics

The original seven-critic roster was merged into three broader critics for FULL runs. Each merged critic covers the lenses of the critics it replaced:

| Critic | Covers |
|--------|--------|
| **Risk & Robustness** | Adversarial edge cases, exception paths, race conditions, blast radius, serialization boundaries (formerly: Skeptic + Adversary) |
| **Scope & Value** | Feature scope creep, user impact, complexity vs. value, simplification opportunities (formerly: Simplifier + User) |
| **History & Consistency** | Cross-section contradictions, prior art conflicts, multi-machine deployment hazards, env var propagation (formerly: Operator + Archaeologist + Consistency Auditor) |

### LITE: one Consolidated Critic

A single Consolidated Critic covers all perspectives at reduced depth. It applies the same finding format (BLOCKER / CONCERN / NIT) and the same Implementation Note requirement as FULL critics. The LITE path is appropriate only for narrow, low-blast-radius changes.

## Finding Format and First-Pass Implementation Notes

Every CONCERN or BLOCKER finding must include an Implementation Note in the critic's **first and only pass**:

```
SEVERITY: BLOCKER | CONCERN | NIT
LOCATION: Section name or line reference
FINDING: What's wrong (1-2 sentences)
SUGGESTION: How to fix it (1-2 sentences)
IMPLEMENTATION NOTE: [Required for CONCERN/BLOCKER. Exempt for NIT.]
  The specific guard condition, call signature, or gotcha that makes this
  finding implementable without re-investigation.
```

Step 4 (aggregation) is **validation-only**: findings missing a required Implementation Note are excluded from the verdict and logged, but the missing critic is **not re-dispatched**. There is no re-run loop. This makes critique output predictable and keeps the stage bounded.

## Crash-Resume

### How it works

Before dispatching any critics (Step 2b), the skill calls `critique-resume-probe` to check for a surviving, non-stale, incomplete run directory:

```bash
critique-resume-probe --plan PATH --issue N [--base-dir .critique-runs]
# or for slug-keyed plans:
critique-resume-probe --plan PATH --slug SLUG
```

Exit codes:
- **0** — reusable directory found; path printed to stdout.
- **1** — no reusable directory; stdout empty.

Stale directories (plan changed since the run started) are printed to stderr for the caller to GC.

### Reusability contract

A run directory is reusable when **all** of the following hold:

1. Its `.plan_hash` file matches `sha256(plan_file_contents)` — the plan has not changed.
2. The `critique-roster-check` gate reports `complete: false` — the run is still in progress.
3. No `"error"` key in the gate decision — the roster manifest (`_roster.json`) is intact.

### On a cache hit

When `critique-resume-probe` finds a reusable directory:
- The triage step (Step 2.6) is **skipped** — the existing run dir already encodes the tier.
- Only the critics listed as `missing` in the roster gate result are dispatched.
- Already-completed critics' result files are included in aggregation as-is.

### Plan hash

The `.plan_hash` file contains `sha256` of the plan file's byte content. It is written at the start of every critique run. If the plan is edited between runs, the hash mismatches and the prior run dir is treated as stale.

## CLI Reference

```bash
# Find a reusable run dir by issue number
critique-resume-probe --plan docs/plans/my-feature.md --issue 1714

# Find a reusable run dir by slug
critique-resume-probe --plan docs/plans/my-feature.md --slug my-feature

# Custom base directory (default: .critique-runs)
critique-resume-probe --plan docs/plans/my-feature.md --issue 1714 --base-dir /tmp/critique-runs
```

The `critique-resume-probe` CLI is implemented in `tools/critique_resume.py` and registered as a `pyproject.toml` entry point.

## Related

- [`docs/features/sdlc-critique-stage.md`](sdlc-critique-stage.md) — full CRITIQUE stage reference (roster barrier, verdicts, cycle limits, outcome classification)
- [`docs/sdlc/do-plan-critique.md`](../sdlc/do-plan-critique.md) — repo-specific addendum for this project (required section enforcement, Popoto migration check, artifact roster barrier)
- `tools/critique_resume.py` — crash-resume probe implementation
- `.claude/skills-global/do-plan-critique/SKILL.md` — canonical skill prose
