---
status: Ready
type: feature
appetite: Medium
owner: Valor
created: 2026-03-14
tracking: https://github.com/tomcounsell/ai/issues/410
last_comment_id:
---

# Autoexperiment: Autonomous Prompt Optimization

## Problem

System prompts and configurations (observer, summarizer, stage detector) are manually tuned. When we notice suboptimal behavior — observer routing wrong, summarizer losing voice, stage detector missing transitions — we fix it by hand. There's no systematic way to discover better phrasings, instruction orderings, or thresholds.

**Current behavior:**
Prompts are written once, updated reactively when bugs surface. We have no data on whether small changes improve or degrade overall quality.

**Desired outcome:**
An overnight autonomous loop that proposes, tests, and commits prompt improvements — using ultra-cheap models ($0.001/call) for hypothesis generation and existing eval infrastructure for measurement. Wake up to measurably better prompts.

## Prior Art

No prior issues found related to this work. This is greenfield.

## Data Flow

1. **Entry point**: `scripts/autoexperiment.py` invoked via CLI or launchd schedule
2. **Target selection**: Loads an `ExperimentTarget` (file path + eval function + metric direction)
3. **Baseline measurement**: Runs eval function against current file state → baseline score
4. **Hypothesis generation**: Sends current prompt + eval results to cheap LLM (KimiK2.5 via OpenRouter) → proposed edit
5. **Apply edit**: Writes proposed change to target file on experiment branch
6. **Evaluation**: Runs eval function again → new score
7. **Decision**: If improved → git commit on experiment branch; if regressed → git checkout to revert
8. **Logging**: Appends result to `data/experiments/{target}/{timestamp}.jsonl`
9. **Loop**: Repeat steps 4-8 for N iterations
10. **Output**: Summary report with best score achieved, total improvements, cost
11. **Winner workflow**: If net improvement found → auto-create GitHub issue with winning diff, scores, and rationale → issue goes through standard SDLC for proper integration with docs update

## Appetite

**Size:** Medium

**Team:** Solo dev, PM review of plan only

**Interactions:**
- PM check-ins: 1 (plan review)
- Review rounds: 1 (PR review)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `OPENROUTER_API_KEY` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('OPENROUTER_API_KEY')"` | OpenRouter API access for cheap models |
| Git repo clean | `git diff --quiet` | No uncommitted changes before experiment |

Run all checks: `python scripts/check_prerequisites.py docs/plans/autoexperiment.md`

## Solution

### Key Elements

- **ExperimentTarget**: Dataclass defining what to optimize (file, eval fn, metric direction, constraints)
- **ExperimentRunner**: Core loop engine — hypothesize, edit, evaluate, keep/revert
- **Eval functions**: Modular evaluators for each target (observer routing, summarizer voice, etc.)
- **Results logger**: JSONL append-only log with full history per target
- **Model client**: Thin OpenRouter wrapper using ultra-cheap models

### Flow

**CLI invocation** → Select target → Measure baseline → [Hypothesize → Edit → Evaluate → Keep/Revert] × N → Generate report → Exit

### Technical Approach

#### Model Strategy (Ultra-Cheap)

Add to `config/models.py`:
```python
# Ultra-cheap experiment models (via OpenRouter)
OPENROUTER_QWEN35_FLASH = "qwen/qwen3.5-flash"           # $0.0001/$0.0004 per M tokens, 1M context
OPENROUTER_QWEN35_9B = "qwen/qwen3.5-9b"                 # $0.00005/$0.00015 per M tokens, 256k context
OPENROUTER_KIMI_K2_5 = "moonshotai/kimi-k2.5"            # $0.00045/$0.0022 per M tokens, 262k context
OPENROUTER_STEP_FLASH_FREE = "stepfun/step-3.5-flash:free"  # Free, 256k context
MODEL_EXPERIMENT = OPENROUTER_QWEN35_FLASH                # Default: best cost/quality ratio
MODEL_EXPERIMENT_FREE = OPENROUTER_STEP_FLASH_FREE        # Free tier for bulk iterations
# Haiku remains safe fallback for quality-sensitive evaluation judging
```

**Model selection rationale**: Qwen 3.5 Flash is the default — extremely cheap ($0.0001/M input), 1M context window, strong instruction-following. Free models (Step Flash, Nemotron) available for bulk hypothesis generation where quality tolerance is higher. Haiku stays as fallback for evaluation judging where accuracy matters more than cost.

#### Core Framework (`scripts/autoexperiment.py`)

```python
@dataclass
class ExperimentTarget:
    name: str                          # e.g., "observer-routing"
    file_path: str                     # e.g., "bridge/observer.py"
    extract_fn: Callable[[str], str]   # Extract the prompt/config section from file
    inject_fn: Callable[[str, str], str]  # Inject modified section back into file
    eval_fn: Callable[[], float]       # Run evaluation, return score
    metric_direction: str              # "higher" or "lower"
    description: str                   # Human-readable description
    model: str = MODEL_EXPERIMENT      # Which model to use for hypotheses

@dataclass
class ExperimentResult:
    iteration: int
    hypothesis: str
    diff: str
    baseline_score: float
    new_score: float
    kept: bool
    cost_usd: float
    timestamp: str

class ExperimentRunner:
    def __init__(self, target: ExperimentTarget, branch: str = None)
    def run_one(self) -> ExperimentResult
    def run_loop(self, n: int = 100) -> list[ExperimentResult]
    def report(self) -> dict
```

#### Initial Experiment Targets

**Target 1: Observer Routing Accuracy** (Priority)
- File: `bridge/observer.py` → `OBSERVER_SYSTEM_PROMPT`
- Eval: Suite of 20+ scenarios (message + session state → expected decision)
- Metric: % correct STEER/DELIVER decisions (higher is better)
- Eval corpus: `data/experiments/observer/eval_corpus.jsonl`
- **Inputs**: Mined from real bridge logs (actual messages and session states)
- **Expected outputs**: Hand-crafted idealized routing decisions (not copied from logs — existing routing decisions may be wrong)

**Target 2: Summarizer Voice Quality**
- File: `bridge/summarizer.py` → `SUMMARIZER_SYSTEM_PROMPT`
- Eval: Feed 10+ sample agent outputs through summarizer, AI-judge voice adherence
- Metric: Average judge score 0-1 (higher is better)
- Judge criteria: direct, concise, no preamble, professional
- **Inputs**: Real agent output samples from bridge logs
- **Expected outputs**: Hand-written idealized summaries that exemplify the target voice (existing summaries are too variable to use as gold standard)

**Target 3: Stage Detector Accuracy**
- File: `bridge/stage_detector.py` → regex patterns and stage mappings
- Eval: Corpus of agent transcripts with known stage transitions
- Metric: F1 score (higher is better)
- **Inputs**: Real agent transcripts from session logs
- **Expected outputs**: Hand-labeled stage transitions (existing auto-detected stages may be incorrect)

#### Git Safety

- All experiments run on branch `experiment/{target-name}`
- Branch created fresh from main at start of each run
- Only the target file is modified (scoped edits via extract/inject functions)
- Auto-revert on score regression — score must strictly improve to commit
- Best result across all iterations is tracked; if final score < best seen, revert to best

#### Results Storage

```
data/experiments/
├── observer/
│   ├── eval_corpus.jsonl          # Test scenarios
│   ├── 2026-03-14_001.jsonl       # Run results
│   └── best_score.json            # Current best
├── summarizer/
│   └── ...
└── stage_detector/
    └── ...
```

#### Scheduling

Launchd plist for nightly runs:
```xml
<!-- com.valor.autoexperiment.plist -->
<key>StartCalendarInterval</key>
<dict>
    <key>Hour</key><integer>2</integer>  <!-- 2 AM local time -->
    <key>Minute</key><integer>0</integer>
</dict>
```

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] OpenRouter API failures (timeout, 429, 500) → graceful skip, log, continue to next iteration
- [ ] File parse failures (extract_fn can't find prompt section) → abort with clear error
- [ ] Git operations fail (dirty state, merge conflict) → abort run, alert via log

### Empty/Invalid Input Handling
- [ ] LLM returns empty hypothesis → skip iteration, log warning
- [ ] LLM returns identical text (no change) → skip iteration
- [ ] Eval function returns NaN or error → skip iteration, do not commit

### Error State Rendering
- [ ] Summary report includes failed iterations count and reasons
- [ ] Log file captures every skip/error with context

## Rabbit Holes

- **Fine-tuning models** — We're editing prompts, not training models. Don't go there.
- **Complex multi-file experiments** — Start with single-file prompt edits only. Multi-file coordination is a v2 concern.
- **Real-time experiments on live bridge** — Never. Experiments run on branches, merged manually after review.
- **Building a web UI for experiment results** — JSONL + CLI reports are sufficient. No dashboards.
- **Optimizing the eval functions themselves** — Use simple, stable evals. Changing the ruler while measuring defeats the purpose.

## Risks

### Risk 1: Eval Function Doesn't Correlate With Real Quality
**Impact:** Optimizing for the wrong metric — prompt gets "better" on evals but worse in practice.
**Mitigation:** Start with observer routing (unambiguous correct/incorrect). Build eval corpus from real observed failures. Manual review of any merged experiment branches.

### Risk 2: LLM Proposes Destructive Changes
**Impact:** Hypothesis removes critical instructions from prompt, causing degraded behavior.
**Mitigation:** Extract/inject pattern constrains edits to the prompt section only. Git auto-revert on regression. Branch isolation prevents production impact. Human review before merge.

### Risk 3: Cost Overrun
**Impact:** Overnight run costs more than expected.
**Mitigation:** Hard iteration cap (default 100). Cost tracking per iteration with configurable budget ceiling. Kill switch via `data/experiments/STOP` sentinel file.

## Race Conditions

No race conditions identified. The experiment runner is single-threaded and single-process. It operates on its own git branch. The bridge runs on main and is unaffected.

## No-Gos (Out of Scope)

- No live bridge modification — experiments only on branches
- No expensive models (Sonnet/Opus) for hypothesis generation
- No multi-file experiments in v1
- No automatic merge of experiment results — winning strategies are proposed as GitHub issues and go through full SDLC (plan → build → test → review → docs → merge)
- No web dashboard or visualization
- No integration with the bridge's runtime — this is an offline optimization tool

## Update System

- New script `scripts/autoexperiment.py` must be propagated
- New launchd plist `com.valor.autoexperiment.plist` for scheduling
- Install script `scripts/install_autoexperiment.sh` for launchd setup
- New entries in `config/models.py` for cheap experiment models
- `data/experiments/` directory must exist (created by install script)
- No changes to the update skill itself — standard file propagation

## Agent Integration

No agent integration required — this is a standalone script invoked via CLI or launchd, not through the agent/bridge pipeline. The agent may be instructed to run experiments via Telegram ("run autoexperiment on observer"), but this is just a bash command execution, not a tool integration.

## Documentation

- [ ] Create `docs/features/autoexperiment.md` describing the feature, how to add new targets, how to review results
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Add `autoexperiment` commands to `CLAUDE.md` Quick Commands table
- [ ] Inline docstrings on all public functions in `scripts/autoexperiment.py`

## Success Criteria

- [ ] `scripts/autoexperiment.py` runs 100+ iterations on observer target without crashing
- [ ] Observer eval corpus has 20+ scenarios with known-correct routing decisions
- [ ] At least one experiment target shows measurable improvement after a full run
- [ ] Results logged to `data/experiments/` with hypothesis, diff, score, keep/revert
- [ ] Auto-revert works: regression never committed
- [ ] Cost stays under $2 for 1000 iterations (verified via cost tracking)
- [ ] Launchd scheduling works for nightly runs
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (framework)**
  - Name: experiment-builder
  - Role: Implement core ExperimentRunner, ExperimentTarget, model client, CLI
  - Agent Type: builder
  - Resume: true

- **Builder (evals)**
  - Name: eval-builder
  - Role: Build eval corpus and eval functions for observer, summarizer, stage detector
  - Agent Type: builder
  - Resume: true

- **Validator (framework)**
  - Name: experiment-validator
  - Role: Verify framework runs end-to-end, safety mechanisms work
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create feature documentation and update indexes
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add Experiment Models to Config
- **Task ID**: build-models
- **Depends On**: none
- **Assigned To**: experiment-builder
- **Agent Type**: builder
- **Parallel**: true
- Add KimiK2.5, Qwen3-32B, Gemma3 free tier to `config/models.py`
- Add `MODEL_EXPERIMENT` alias

### 2. Build Core Framework
- **Task ID**: build-framework
- **Depends On**: build-models
- **Assigned To**: experiment-builder
- **Agent Type**: builder
- **Parallel**: false
- Implement `ExperimentTarget`, `ExperimentResult` dataclasses
- Implement `ExperimentRunner` with hypothesize/edit/evaluate/keep-revert loop
- Implement OpenRouter client for cheap models (reuse pattern from `tests/ai_judge/judge.py`)
- Implement JSONL results logging
- Implement CLI interface with argparse (target selection, iteration count, budget cap)
- Implement git branch management (create experiment branch, commit on improvement, revert on regression)
- Implement cost tracking and budget ceiling
- Implement STOP sentinel file check

### 3. Build Observer Eval Corpus & Function
- **Task ID**: build-observer-eval
- **Depends On**: none
- **Assigned To**: eval-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `data/experiments/observer/eval_corpus.jsonl` with 20+ scenarios
- Each scenario: `{"input": {"message": "...", "session_state": {...}}, "expected": "STEER"|"DELIVER"}`
- **Inputs**: Mine real messages and session states from bridge logs (`logs/bridge.log`, Redis session data)
- **Expected outputs**: Hand-craft idealized routing decisions — do NOT copy existing observer outputs (they may be wrong; that's what we're optimizing)
- Include edge cases: ambiguous messages, multi-intent, context-dependent routing
- Implement eval function that runs scenarios through observer prompt and measures accuracy

### 4. Build Summarizer Eval Function
- **Task ID**: build-summarizer-eval
- **Depends On**: none
- **Assigned To**: eval-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `data/experiments/summarizer/eval_samples.jsonl` with 10+ sample agent outputs
- **Inputs**: Real agent output samples extracted from bridge logs
- **Expected outputs**: Hand-written idealized summaries for each input (gold standard — existing summary outputs are too variable/dirty to use as reference)
- Implement eval function that runs through summarizer and AI-judges voice quality
- Judge criteria: direct, concise, no preamble, professional, preserves artifacts

### 5. Build Stage Detector Eval Function
- **Task ID**: build-detector-eval
- **Depends On**: none
- **Assigned To**: eval-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `data/experiments/stage_detector/eval_corpus.jsonl` with transcript samples
- **Inputs**: Real agent transcripts from session logs
- **Expected outputs**: Hand-labeled correct stage transitions (existing auto-detected stages may be inaccurate)
- Implement eval function that measures stage detection F1 score

### 6. Register Experiment Targets
- **Task ID**: build-targets
- **Depends On**: build-framework, build-winner-issue, build-observer-eval, build-summarizer-eval, build-detector-eval
- **Assigned To**: experiment-builder
- **Agent Type**: builder
- **Parallel**: false
- Wire up extract/inject functions for observer prompt, summarizer prompt, stage detector patterns
- Register all three targets with CLI

### 7. Build Winner-to-Issue Workflow
- **Task ID**: build-winner-issue
- **Depends On**: build-framework
- **Assigned To**: experiment-builder
- **Agent Type**: builder
- **Parallel**: true
- When a run finds net improvement, auto-create GitHub issue via `gh issue create`
- Issue includes: target name, baseline vs final score, winning diff, cost, iteration count
- Issue is tagged `autoexperiment` and assigned to standard SDLC for integration
- Log issue URL in experiment results JSONL

### 8. Create Scheduling Infrastructure
- **Task ID**: build-scheduling
- **Depends On**: build-framework
- **Assigned To**: experiment-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `com.valor.autoexperiment.plist` launchd config
- Create `scripts/install_autoexperiment.sh` install script
- Ensure `data/experiments/` directory creation

### 9. Validate Framework End-to-End
- **Task ID**: validate-framework
- **Depends On**: build-targets
- **Assigned To**: experiment-validator
- **Agent Type**: validator
- **Parallel**: false
- Run 5 iterations on observer target, verify results logged
- Verify auto-revert works (introduce intentionally worse prompt)
- Verify cost tracking accuracy
- Verify branch isolation (experiment branch created, main untouched)

### 10. Write Tests
- **Task ID**: build-tests
- **Depends On**: build-targets
- **Assigned To**: experiment-builder
- **Agent Type**: builder
- **Parallel**: false
- Unit tests for ExperimentRunner (mock LLM calls)
- Unit tests for extract/inject functions
- Unit tests for git safety (revert on regression)
- Integration test: full 3-iteration run against observer target

### 11. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-framework
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/autoexperiment.md`
- Add entry to `docs/features/README.md`
- Update `CLAUDE.md` Quick Commands

### 12. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: experiment-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Script runs | `python scripts/autoexperiment.py --target observer --iterations 1 --dry-run` | exit code 0 |
| Eval corpus exists | `test -f data/experiments/observer/eval_corpus.jsonl` | exit code 0 |
| Models configured | `python -c "from config.models import MODEL_EXPERIMENT; print(MODEL_EXPERIMENT)"` | output contains qwen |

---

## Resolved Questions

1. **Model preference**: ✅ Default is Qwen 3.5 Flash ($0.0001/M input, 1M context) — best cost/quality ratio on OpenRouter. Free models (Step Flash) for bulk iterations. Haiku as safe fallback for evaluation judging. Framework supports swapping models per-target.

2. **Merge workflow**: ✅ Winning experiments are NOT auto-merged. Instead, the framework auto-creates a GitHub issue with the winning diff, scores, and rationale. That issue then goes through standard SDLC (plan → build → test → review → docs → merge) for proper integration.

3. **Eval corpus strategy**: ✅ Use existing real data for inputs (messages from bridge logs, agent outputs from sessions, transcripts from session logs). But expected outputs must be hand-crafted idealized gold standards — existing output data is dirty/inconsistent and cannot be used as ground truth.
