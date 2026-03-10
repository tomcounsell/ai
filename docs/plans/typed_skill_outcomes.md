---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-03-10
tracking: https://github.com/tomcounsell/ai/issues/328
---

# Typed Outcomes from /do-* Skills

## Problem

Our `/do-*` skills (do-plan, do-build, do-test, do-patch, do-pr-review, do-docs) return unstructured prose. The SDLC dispatcher and Observer Agent must parse free-form text with regex heuristics and LLM classification to determine what happened.

**Current behavior:**
- `bridge/stage_detector.py` regex-matches `/do-*` invocations in transcript text to infer stage transitions
- `bridge/observer.py` uses a Sonnet LLM call to classify whether output represents completion, failure, or status update
- Artifacts (PR URLs, plan paths, branch names) are extracted by regex from transcript text via `bridge/summarizer.py:extract_artifacts()`
- No structured contract between skills and the pipeline — every skill outputs different prose

**Desired outcome:**
- Every `/do-*` skill emits a parseable `SkillOutcome` JSON block at completion
- The dispatcher reads `outcome.status` directly for clear success/fail decisions (no LLM needed)
- Artifacts flow between stages as structured data, not transcript-grepped URLs
- Stage detector cross-checks typed outcomes against its regex detections

## Prior Art

- **Issue #329 (closed)**: Context fidelity modes — addressed how much context sub-agents get, not what they return
- **Issue #331 (closed)**: Goal gates — added deterministic gate checks (`GateResult` dataclass) that validate stage completion via filesystem/GitHub API. The `GateResult` pattern is a direct precedent for `SkillOutcome`
- **Issue #330 (closed)**: Machine-readable DoD — added verification tables to plan documents. `agent/verification_parser.py` parses structured tables from markdown — same pattern of extracting structured data from skill output
- **Issue #332 (open)**: Checkpoint/resume — `PipelineCheckpoint` already stores `completed_stages` and `artifacts` per stage. `SkillOutcome` would populate these checkpoints with richer data

No prior PRs attempted typed skill outcomes directly.

## Data Flow

1. **Entry point**: Human sends message → bridge classifies → `/sdlc` dispatcher runs
2. **Dispatcher**: `/sdlc` assesses pipeline state, invokes `/do-{stage}` skill
3. **Skill execution**: Claude Code runs the skill (plan/build/test/etc.), produces prose output
4. **Observer**: `bridge/observer.py` receives worker output, runs stage detector, decides steer vs deliver
5. **Stage detector**: `bridge/stage_detector.py` regex-parses transcript, writes session history entries
6. **Checkpoint**: `agent/checkpoint.py` saves stage completion + artifacts to `data/checkpoints/{slug}.json`
7. **Next stage**: Observer crafts coaching message, enqueues continuation job

**Current gap**: Steps 3→4 lose structured information. The skill knows exactly what it did (success/fail, PR URL, test count) but encodes it in prose that steps 4-6 must re-extract with heuristics.

**With typed outcomes**: Step 3 emits a `SkillOutcome` JSON block. Steps 4-6 parse it deterministically. The LLM observer is only consulted for ambiguous cases.

## Architectural Impact

- **New module**: `agent/skill_outcome.py` — pure dataclass, no external dependencies
- **Interface changes**: Each `/do-*` SKILL.md gets an "Outcome Contract" section defining expected artifacts
- **Coupling**: Reduces coupling — observer no longer needs to understand each skill's prose format, just reads `outcome.status`
- **Data ownership**: `SkillOutcome` becomes the single source of truth for "what happened in this stage"
- **Reversibility**: Fully backward compatible — stage detector continues to work if outcome block is missing (graceful degradation)

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope alignment on which skills get outcomes first)
- Review rounds: 1 (code review)

## Prerequisites

No prerequisites — this work has no external dependencies. Uses only stdlib (dataclasses, json, re).

## Solution

### Key Elements

- **`agent/skill_outcome.py`**: `SkillOutcome` dataclass with serialization, plus `parse_outcome_from_text()` that extracts the JSON block from mixed prose output
- **SKILL.md outcome contracts**: Each `/do-*` skill's SKILL.md defines what artifacts it produces on success/failure
- **Observer integration**: Observer reads typed outcome when available, falls back to LLM classification when not
- **Checkpoint enrichment**: `SkillOutcome.artifacts` flows directly into checkpoint artifacts

### Flow

**Skill completes** → emits `<!-- OUTCOME {...} -->` JSON block → **Observer** parses block → reads `status` → **steer** (next stage with artifacts) or **deliver** (to human)

### Technical Approach

- Use HTML comment syntax (`<!-- OUTCOME {...} -->`) for the structured block so it's invisible in rendered markdown but parseable by the pipeline
- `SkillOutcome` is a stdlib dataclass (not Pydantic) — keeps it lightweight and consistent with `PipelineCheckpoint` and `GateResult`
- Parser uses a single regex to extract the JSON block from output text, returns `None` if not found (graceful degradation)
- Each SKILL.md instructs the agent to emit the outcome block as its final action

### Phase 1: Define the type and parser

Create `agent/skill_outcome.py`:

```python
@dataclass
class SkillOutcome:
    status: str           # "success", "fail", "partial", "retry", "skipped"
    stage: str            # "PLAN", "BUILD", "TEST", "REVIEW", "DOCS"
    artifacts: dict       # {"pr_url": "...", "plan_path": "...", "branch": "..."}
    notes: str            # human-readable summary
    failure_reason: str | None = None
    next_skill: str | None = None
```

Plus `parse_outcome_from_text(text: str) -> SkillOutcome | None` and `format_outcome(outcome: SkillOutcome) -> str`.

### Phase 2: Update SKILL.md files

Add an "Outcome Contract" section to each `/do-*` SKILL.md defining:
- What `status` values are valid
- What `artifacts` keys are expected on success
- Template for the `<!-- OUTCOME {...} -->` block the agent should emit

### Phase 3: Wire into Observer

Update `bridge/observer.py` to:
1. Call `parse_outcome_from_text(worker_output)` before LLM classification
2. If outcome found with `status: "success"` → steer to next stage (no LLM needed)
3. If outcome found with `status: "fail"` → check retry path or deliver to human
4. If no outcome found → fall back to current LLM-based routing (backward compat)

### Phase 4: Wire into stage detector and checkpoint

Update `bridge/stage_detector.py`:
- After regex detection, cross-check against typed outcome if available
- Prefer typed outcome's artifacts over regex-extracted ones

Update checkpoint save in `_save_stage_checkpoint()`:
- Use `outcome.artifacts` directly instead of extracting from session metadata

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `parse_outcome_from_text()` handles malformed JSON gracefully (returns None, logs warning)
- [ ] Observer falls back to LLM routing when parse returns None
- [ ] Missing or extra fields in outcome JSON don't crash the parser

### Empty/Invalid Input Handling
- [ ] Empty string input to parser returns None
- [ ] Outcome with unknown `status` value is treated as ambiguous (falls back to LLM)
- [ ] Outcome with empty `artifacts` dict is valid (not all stages produce artifacts)

### Error State Rendering
- [ ] `format_outcome()` with `status: "fail"` includes `failure_reason` in the block
- [ ] Observer delivers fail outcomes with clear failure context to human

## Rabbit Holes

- **Full graph-based pipeline**: Attractor uses DOT syntax for DAG routing. Our pipeline is linear — don't build graph infrastructure
- **Forcing all skills to emit outcomes immediately**: This should be incremental. Start with do-build and do-test (highest value), add others over time
- **Replacing the stage detector entirely**: The regex detector is a valuable cross-check. Outcomes augment it, they don't replace it
- **Pydantic models**: Keep it stdlib dataclasses for consistency with checkpoint and gate_result patterns

## Risks

### Risk 1: Skills forget to emit the outcome block
**Impact:** Silent degradation to current behavior (not catastrophic)
**Mitigation:** Graceful fallback — if no outcome block found, Observer uses LLM classification as today. Add a log warning when expected outcome is missing.

### Risk 2: LLM generates malformed JSON in the outcome block
**Impact:** Parser returns None, falls back to LLM routing
**Mitigation:** Robust regex extraction + JSON parsing with error handling. HTML comment wrapper makes the boundary unambiguous.

## Race Conditions

No race conditions identified. Outcome parsing is a pure function operating on the final output string. No concurrent access or shared mutable state.

## No-Gos (Out of Scope)

- No changes to how skills are invoked (still via SKILL.md prompts)
- No graph-based routing or conditional next-stage logic
- No persistence of outcomes outside existing checkpoint system
- No changes to the Telegram message format (outcomes are pipeline-internal)
- No mandatory outcome emission — backward compatibility is non-negotiable

## Update System

No update system changes required — this feature adds a new Python module and updates SKILL.md markdown files. No new dependencies, no config changes, no migration steps.

## Agent Integration

No agent integration required — this is a bridge-internal change. The agent (Claude Code) emits outcome blocks as part of its normal text output. The parsing happens in bridge/observer.py, which already processes worker output. No MCP server changes, no `.mcp.json` changes.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/typed-skill-outcomes.md` describing the outcome contract system
- [ ] Add entry to `docs/features/README.md` index table

### Inline Documentation
- [ ] Docstrings for `SkillOutcome`, `parse_outcome_from_text()`, `format_outcome()`
- [ ] Comments in Observer explaining the outcome-first routing path

## Success Criteria

- [ ] `agent/skill_outcome.py` exists with `SkillOutcome` dataclass and parser
- [ ] `parse_outcome_from_text()` correctly extracts outcome from mixed prose (15+ test cases)
- [ ] `/do-build` and `/do-test` SKILL.md files include outcome contract and emission template
- [ ] Observer reads typed outcomes for success/fail cases without LLM call
- [ ] Observer falls back gracefully when no outcome block is present
- [ ] Stage detector cross-checks typed outcomes against regex detections
- [ ] Checkpoint save uses outcome artifacts when available
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (skill-outcome)**
  - Name: outcome-builder
  - Role: Implement SkillOutcome dataclass, parser, and observer integration
  - Agent Type: builder
  - Resume: true

- **Validator (skill-outcome)**
  - Name: outcome-validator
  - Role: Verify parser correctness, observer fallback behavior, backward compatibility
  - Agent Type: validator
  - Resume: true

- **Builder (skill-docs)**
  - Name: skill-docs-builder
  - Role: Update SKILL.md files with outcome contracts
  - Agent Type: builder
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create feature docs and update index
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Build SkillOutcome module
- **Task ID**: build-skill-outcome
- **Depends On**: none
- **Assigned To**: outcome-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `agent/skill_outcome.py` with `SkillOutcome` dataclass
- Implement `to_dict()`, `from_dict()`, `to_json()` serialization
- Implement `parse_outcome_from_text(text)` regex extractor for `<!-- OUTCOME {...} -->`
- Implement `format_outcome(outcome)` for emitting the HTML comment block
- Create `tests/unit/test_skill_outcome.py` with 15+ test cases:
  - Round-trip serialization
  - Parse from mixed prose with outcome block
  - Parse with no outcome block (returns None)
  - Malformed JSON handling
  - All status values
  - Empty/missing optional fields

### 2. Update SKILL.md outcome contracts
- **Task ID**: build-skill-contracts
- **Depends On**: build-skill-outcome
- **Assigned To**: skill-docs-builder
- **Agent Type**: builder
- **Parallel**: false
- Add "Outcome Contract" section to `.claude/skills/do-build/SKILL.md`
- Add "Outcome Contract" section to `.claude/skills/do-test/SKILL.md`
- Each section defines: valid status values, expected artifact keys, emission template
- Template instructs agent to emit `<!-- OUTCOME {"status": "...", ...} -->` as final action

### 3. Wire into Observer
- **Task ID**: build-observer-integration
- **Depends On**: build-skill-outcome
- **Assigned To**: outcome-builder
- **Agent Type**: builder
- **Parallel**: false
- In `bridge/observer.py`: parse outcome from worker_output before LLM call
- If outcome.status == "success" and SDLC stages remain → steer (skip LLM)
- If outcome.status == "fail" → deliver to human with failure_reason (skip LLM)
- If no outcome → fall back to current LLM routing
- Add outcome artifacts to session metadata for checkpoint enrichment

### 4. Wire into stage detector and checkpoint
- **Task ID**: build-detector-integration
- **Depends On**: build-observer-integration
- **Assigned To**: outcome-builder
- **Agent Type**: builder
- **Parallel**: false
- In `bridge/stage_detector.py`: accept optional outcome parameter in `apply_transitions()`
- Cross-check: if outcome says "success" but regex didn't detect completion, log warning
- In `_save_stage_checkpoint()`: prefer outcome.artifacts over session metadata extraction

### 5. Validate all integration
- **Task ID**: validate-integration
- **Depends On**: build-detector-integration
- **Assigned To**: outcome-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify Observer correctly routes success outcomes without LLM call
- Verify Observer falls back to LLM when no outcome present
- Verify stage detector cross-check logging
- Verify checkpoint artifacts populated from outcomes
- Run full test suite

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/typed-skill-outcomes.md`
- Add entry to `docs/features/README.md` index table
- Verify all docstrings present

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: outcome-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all verification commands
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_skill_outcome.py -x -q` | exit code 0 |
| Module importable | `python -c "from agent.skill_outcome import SkillOutcome, parse_outcome_from_text"` | exit code 0 |
| Lint clean | `python -m ruff check agent/skill_outcome.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/skill_outcome.py` | exit code 0 |
| Feature doc exists | `test -f docs/features/typed-skill-outcomes.md` | exit code 0 |
| Outcome contract in do-build | `grep -q "Outcome Contract" .claude/skills/do-build/SKILL.md` | exit code 0 |
| Outcome contract in do-test | `grep -q "Outcome Contract" .claude/skills/do-test/SKILL.md` | exit code 0 |

---

## Open Questions

1. **Outcome block format**: `<!-- OUTCOME {...} -->` (HTML comment) vs `\`\`\`json:outcome {...} \`\`\`` (fenced code block)? HTML comment is invisible in rendered markdown but slightly harder to debug visually. Fenced block is visible but could confuse the agent's output formatting. I'm leaning HTML comment — thoughts?

2. **Phase rollout**: Start with do-build and do-test (highest signal value), then add do-plan/do-pr-review/do-docs later? Or all at once? I recommend incremental — it's lower risk and we can validate the pattern works before scaling.
