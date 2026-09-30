# Do-Build AI Evaluator

## Overview

The AI evaluator is a semantic build gate that runs during the `/do-build` pipeline. After the deterministic `validate_build.py` checks pass, the AI evaluator reads the plan's `## Acceptance Criteria` section and compares each criterion against the actual code changes (`git diff main..HEAD`) using Claude Haiku as an AI judge.

The evaluator is **advisory and non-blocking** — evaluator failures never halt the pipeline. FAIL verdicts trigger up to 2 `/do-patch` cycles to fix gaps; PARTIAL verdicts log as warnings; errors are caught and logged before the pipeline continues to the review stage.

## Pipeline Position

The AI evaluator runs as **step 16c** in the `/do-build` workflow:

```
validate_build.py (step 16b)  → deterministic checks pass
  ↓
evaluate_build.py (step 16c)  → AI semantic evaluation
  ↓
advance_stage('review') (step 17)
```

The evaluator runs **after** all deterministic checks and **before** the pipeline advances to the review stage.

## Verdict Types

Each acceptance criterion receives one of three verdicts:

| Verdict | Meaning | Pipeline Response |
|---------|---------|------------------|
| `PASS` | Criterion clearly met in the diff | No action — proceed normally |
| `PARTIAL` | Criterion partially met or uncertain | Log as warning, proceed (non-blocking) |
| `FAIL` | Criterion not met | Route to `/do-patch` (max 2 iterations) |

## Routing Logic

### FAIL Verdicts

When one or more FAIL verdicts are found:

1. All FAIL verdicts are bundled into a **single** `/do-patch` call (not one per FAIL)
2. The log emits: `[AI Evaluator] FAIL on N criteria — routing to patch cycle (attempt X/2)`
3. After the patch, `evaluate_build.py` re-runs
4. If FAIL persists after 2 iterations: log `"AI evaluator: 2 iterations reached, proceeding to review"` and continue
5. If FAIL resolves: proceed to review stage

### PARTIAL Verdicts

PARTIAL verdicts are **warning-only**:
- Logged to `logs/evaluate_build.log` at INFO level
- Emitted to stderr as: `WARNING: AC criterion PARTIAL — {criterion}: {evidence}`
- Do not block the pipeline or trigger patches

### Errors (Non-blocking)

Any evaluator error (API timeout, JSON parse failure, missing script) is caught and logged:
- `"AI evaluator failed (non-blocking): {error}"`
- Pipeline proceeds to review stage without delay

## Exit Code Reference

| Code | Meaning | Pipeline Action |
|------|---------|----------------|
| `0` | All criteria PASS or PARTIAL | Log PARTIAL warnings, proceed to review |
| `1` | Unexpected error (API error, JSON parse failure) | Log warning, proceed to review (non-blocking) |
| `2` | One or more FAIL verdicts | Route to `/do-patch` (max 2 iterations) |
| `3` | No `## Acceptance Criteria` section, or empty diff | Log skip warning, proceed to review |

## How to Disable

The AI evaluator is **automatically skipped** when:
- The plan has no `## Acceptance Criteria` section (exit code 3)
- The `## Acceptance Criteria` section is present but empty (exit code 3)
- The git diff is empty (no code changes) (exit code 3)

To intentionally skip evaluation, omit the `## Acceptance Criteria` section from the plan, or leave it empty.

## Script Reference

**File:** `scripts/evaluate_build.py`

**Usage:**
```bash
python scripts/evaluate_build.py <plan-path>
python scripts/evaluate_build.py --dry-run <plan-path>  # Mock PASS verdicts, no API call
python scripts/evaluate_build.py --help
```

**Output format** (stdout, JSON):
```json
{
  "verdicts": [
    {"criterion": "The evaluator script exists", "verdict": "PASS", "evidence": "Found scripts/evaluate_build.py in diff."},
    {"criterion": "FAIL verdicts route to /do-patch", "verdict": "PARTIAL", "evidence": "Routing logic present but patch invocation not verified."}
  ]
}
```

**Logging:** Events are written to `logs/evaluate_build.log` (same directory as `logs/bridge.log`). FAIL verdicts log at WARNING; PASS/PARTIAL at INFO.

## Model Selection

The evaluator uses **Claude Haiku** (`config.models.HAIKU`) for speed and cost efficiency. Haiku is well-suited for structured JSON classification tasks like verdict generation. The 60-second internal timeout ensures builds are not delayed beyond 1 minute in worst-case scenarios.

## Design Decisions

- **Additive, not a replacement**: The deterministic `validate_build.py` runs first. The AI evaluator adds a semantic layer on top — it never replaces structural checks.
- **Non-blocking by design**: Any evaluator malfunction (API error, timeout, missing file) is caught at exit code 1 and treated as a skip. The pipeline always advances to review.
- **Max 2 patch iterations**: Capped to prevent runaway cycles on builds where the AI evaluator is over-strict. Human review handles any remaining gaps.
- **All FAILs bundled**: Multiple FAIL verdicts are sent to `/do-patch` as a single structured list, not one call per FAIL. This keeps patch cycles atomic and reduces noise.
- **Read-only contract**: `evaluate_build.py` never modifies any files. It reads the plan and diff, calls the AI, writes to stdout only.
