# Autoexperiment: Autonomous Prompt Optimization

Offline optimization tool that iteratively improves system prompts using ultra-cheap LLM models via OpenRouter. Runs overnight to propose, test, and commit prompt improvements without human intervention.

## How It Works

1. **Target Selection**: Pick a prompt to optimize (observer routing or summarizer voice)
2. **Baseline Measurement**: Run the eval function against the current prompt to get a score
3. **Hypothesis Generation**: Ask a cheap LLM to propose a targeted improvement
4. **Apply & Evaluate**: Write the change, re-run eval, compare scores
5. **Keep or Revert**: If improved, commit on experiment branch; if regressed, revert
6. **Repeat**: Loop for N iterations within a budget ceiling
7. **Report**: Summarize improvements found, cost, and best score achieved

All experiments run on isolated git branches (`experiment/{target-name}`). Winning changes are proposed as GitHub issues for standard SDLC review -- never auto-merged.

## Usage

```bash
# Run 50 iterations on observer routing accuracy
python scripts/autoexperiment.py --target observer --iterations 50 --budget 2.0

# Dry-run (no git operations) on summarizer
python scripts/autoexperiment.py --target summarizer --dry-run

# List available targets
python scripts/autoexperiment.py --list-targets

# View report for a target
python scripts/autoexperiment.py --target observer --report
```

## Available Targets

### Observer Routing (`observer`)
- **File**: `bridge/observer.py` (variable: `OBSERVER_SYSTEM_PROMPT_BODY`)
- **Eval**: Suite of 24 scenarios testing STEER vs DELIVER decisions
- **Metric**: Routing accuracy (higher is better)
- **Corpus**: `data/experiments/observer/eval_corpus.jsonl`

Only the static body template is optimized. The dynamic prompt construction logic (`_build_observer_system_prompt`) is off-limits.

### Summarizer Voice (`summarizer`)
- **File**: `bridge/summarizer.py` (variable: `SUMMARIZER_SYSTEM_PROMPT`)
- **Eval**: 10 sample agent outputs judged for voice quality
- **Metric**: Average quality score 0-1 (higher is better)
- **Corpus**: `data/experiments/summarizer/eval_samples.jsonl`

## Adding a New Target

1. Define extract/inject functions in `scripts/autoexperiment.py`:
   ```python
   def extract_my_prompt(file_content: str) -> str:
       return _extract_prompt_var(file_content, "MY_VARIABLE_NAME")

   def inject_my_prompt(file_content: str, new_prompt: str) -> str:
       return _inject_prompt_var(file_content, "MY_VARIABLE_NAME", new_prompt)
   ```

2. Create an eval function that returns a float score:
   ```python
   def eval_my_target(corpus_path: str | None = None) -> float:
       # Load corpus, run scenarios, return accuracy/quality score 0-1
       ...
   ```

3. Create an eval corpus at `data/experiments/{name}/eval_corpus.jsonl`

4. Register the target in `get_targets()`:
   ```python
   "my-target": ExperimentTarget(
       name="my-target",
       file_path="path/to/file.py",
       extract_fn=extract_my_prompt,
       inject_fn=inject_my_prompt,
       eval_fn=eval_my_target,
       metric_direction="higher",
       description="What this target optimizes",
   ),
   ```

5. Add the target name to the CLI `--target` choices

## Results Storage

Results are logged to `data/experiments/{target}/` as JSONL files:

```
data/experiments/
├── observer/
│   ├── eval_corpus.jsonl          # Test scenarios (checked in)
│   └── 20260314.jsonl             # Run results (gitignored)
├── summarizer/
│   ├── eval_samples.jsonl         # Test samples (checked in)
│   └── 20260314.jsonl             # Run results (gitignored)
└── STOP                           # Sentinel file to halt any running experiment
```

Each result line contains: iteration number, hypothesis text, diff, baseline/new scores, kept/reverted, cumulative cost, timestamp.

## Safety Mechanisms

- **Branch isolation**: Experiments run on `experiment/{target}` branches, never on main
- **Auto-revert**: Score must strictly improve to keep a change
- **Budget ceiling**: Configurable max spend (default $2.00), halts when exceeded
- **STOP sentinel**: Create `data/experiments/STOP` to immediately halt any running experiment
- **Scoped edits**: Extract/inject pattern constrains modifications to the prompt section only
- **No auto-merge**: Winning strategies are proposed as GitHub issues, requiring human review

## Scheduling

Nightly runs via launchd:

```bash
# Install the schedule
./scripts/install_autoexperiment.sh

# The plist runs at 2 AM local time
# Config: com.valor.autoexperiment.plist
```

## Cost Model

Using OpenRouter ultra-cheap models:
- Hypothesis generation: ~$0.0001 per iteration
- Evaluation calls: ~$0.001 per iteration (uses Haiku for judging)
- Typical 100-iteration run: ~$0.10-0.50
- Budget default: $2.00 (configurable via `--budget`)

## Key Files

| File | Purpose |
|------|---------|
| `scripts/autoexperiment.py` | Core framework, CLI, targets, eval functions |
| `config/models.py` | Model aliases (`MODEL_EXPERIMENT`) |
| `data/experiments/observer/eval_corpus.jsonl` | Observer routing test scenarios |
| `data/experiments/summarizer/eval_samples.jsonl` | Summarizer voice test samples |
| `com.valor.autoexperiment.plist` | launchd schedule config |
| `scripts/install_autoexperiment.sh` | Schedule installer |
| `tests/unit/test_autoexperiment.py` | Unit tests |

## Design Decisions

- **Cheap models for hypothesis generation**: The LLM proposing changes doesn't need to be smart -- it needs to be cheap enough for hundreds of iterations. Evaluation uses Haiku for better judgment.
- **Single-file scope**: Each target optimizes exactly one prompt variable in one file. Multi-file experiments are explicitly out of scope.
- **JSONL logging**: Append-only format for durability. Each iteration is one line, easy to analyze.
- **No web UI**: Results are inspected via CLI (`--report`) and JSONL files. Dashboards are out of scope.
