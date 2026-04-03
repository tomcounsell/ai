---
status: In Progress
type: enhancement
appetite: Small
owner: Valor
created: 2026-04-03
tracking: https://github.com/tomcounsell/ai/issues/671
last_comment_id:
---

# Standardize All Ollama Usage on gemma4:e2b

## Problem

**Current behavior:**
After commit e7a6c34b standardized most Ollama references on `gemma4:e2b`, three callsites still hardcode `qwen3:1.7b`:

1. `bridge/routing.py` line 356 -- message classification (work vs ignore)
2. `bridge/routing.py` line 532 -- SDLC vs question classification
3. `intent/__init__.py` line 20 -- intent recognition default model

Additionally, the update flow (`scripts/update/run.py`) pulls `gemma4:e2b` but never removes superseded models (`gemma2:3b`, `gemma3:4b`, `qwen3:1.7b`, `qwen3:4b`), wasting ~10+ GB disk per machine. There is no smoke test verifying the model actually loads after pull. Two docs still reference old models.

**Desired outcome:**
Every Ollama inference call imports from `config/models.py`. The update flow cleans up old models and smoke-tests the active one. Docs reflect reality.

## Prior Art

- **Commit e7a6c34b**: Updated defaults in `verify.py`, `run.py`, `judge.py`, `test_ai_judge.py`, `config/models.py` -- but missed `bridge/routing.py` and `intent/__init__.py`
- **PR #438**: Config consolidation -- established the pattern of centralizing settings in `config/`
- **PR #275**: Semantic session routing -- introduced the Ollama-based routing in `bridge/routing.py`

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: None -- existing env var overrides (`OLLAMA_MODEL`, `OLLAMA_SUMMARIZER_MODEL`) continue to work
- **Coupling**: Slightly tighter coupling to `config/models.py`, which is the intended centralization point
- **Data ownership**: No change
- **Reversibility**: High -- all changes are simple constant swaps or additive cleanup logic

## Appetite

**Size:** Small (half-day)

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope fully defined by issue)
- Review rounds: 1

## Prerequisites

No prerequisites. All files to modify already exist.

## Solution

### Key Elements

- **Centralized constant**: Add `OLLAMA_LOCAL_MODEL` to `config/models.py` with value `"gemma4:e2b"`, plus a `OLLAMA_SUPERSEDED_MODELS` list for cleanup
- **Import at callsites**: Replace hardcoded strings in `bridge/routing.py` (2 locations) and `intent/__init__.py` (1 location) with imports from `config/models.py`
- **Update cleanup**: After successful pull in `scripts/update/run.py`, iterate `OLLAMA_SUPERSEDED_MODELS` and best-effort `ollama rm` each
- **Smoke test**: After pull, run a simple prompt through the model and verify a response within timeout
- **Docs fix**: Update stale model references in two doc files

### Technical Approach

#### 1. Add constants to `config/models.py`

Add a new `LOCAL OLLAMA MODELS` section:

```python
# =============================================================================
# LOCAL OLLAMA MODELS
# Used for fast, local inference (message classification, intent, AI judge)
# =============================================================================

OLLAMA_LOCAL_MODEL = "gemma4:e2b"

# Models superseded by OLLAMA_LOCAL_MODEL — cleaned up during /update
OLLAMA_SUPERSEDED_MODELS = [
    "gemma2:3b",
    "gemma3:4b",
    "qwen3:1.7b",
    "qwen3:4b",
]
```

#### 2. Replace hardcoded references in `bridge/routing.py`

Import `OLLAMA_LOCAL_MODEL` from `config.models` and replace both `"qwen3:1.7b"` occurrences:

- Line 356: `model="qwen3:1.7b"` -> `model=OLLAMA_LOCAL_MODEL`
- Line 532: `model="qwen3:1.7b"` -> `model=OLLAMA_LOCAL_MODEL`

Both are inside `try` blocks with `import ollama`, so the config import goes at module level.

#### 3. Replace hardcoded reference in `intent/__init__.py`

Import `OLLAMA_LOCAL_MODEL` from `config.models` and use it as the default:

```python
from config.models import OLLAMA_LOCAL_MODEL
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", OLLAMA_LOCAL_MODEL)
```

This preserves the env var override for machines with different hardware.

#### 4. Add cleanup and smoke test to `scripts/update/run.py`

After the existing Ollama pull logic (Step 4, ~line 422):

**Cleanup**: Import `OLLAMA_SUPERSEDED_MODELS` from `config.models`. For each model, run `ollama rm <model>` via subprocess. Catch and log any failures (best-effort -- do not fail the update). Only run cleanup after a successful pull of the active model.

**Smoke test**: After pull, run `ollama run <model> "hi"` via subprocess with a timeout. If it responds within the timeout, log success. If it fails or times out, add a warning (do not fail the update).

#### 5. Update stale docs

- `docs/features/sdlc-first-routing.md` line 27: Change `qwen3:1.7b` to `gemma4:e2b`
- `docs/plans/memory-test-suite-516.md`: Update references to `gemma2:3b` and `gemma3:4b` to note they have been superseded by `gemma4:e2b`

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `bridge/routing.py`: Ollama import failure still falls back to Haiku (existing behavior, unchanged)
- [ ] `intent/__init__.py`: Ollama connection failure still falls back to regex heuristics (existing behavior, unchanged)
- [ ] `scripts/update/run.py`: `ollama rm` failure for any superseded model is caught and logged as warning, does not abort update
- [ ] `scripts/update/run.py`: Smoke test timeout or error is caught and logged as warning, does not abort update

### Empty/Invalid Input Handling
- [ ] Smoke test handles empty response from model gracefully
- [ ] Cleanup handles model-not-found from `ollama rm` gracefully (model may already be removed)

### Error State Rendering
- [ ] Cleanup logs which models were removed and which failed
- [ ] Smoke test logs success/failure with the model name and any error detail

## Test Impact

No existing tests affected -- the three modified Python files (`bridge/routing.py`, `intent/__init__.py`, `scripts/update/run.py`) have no unit tests that assert specific model name strings. The `tests/ai_judge/test_ai_judge.py::test_default_config` asserts `gemma4:e2b` which is already correct and unchanged by this work. The changes are constant swaps at the call layer, not logic changes.

Justification: `bridge/routing.py` tests mock the Ollama client; `intent/__init__.py` has no dedicated tests; `scripts/update/run.py` tests (if any) do not assert model names.

## Rabbit Holes

- Renaming the `OLLAMA_SUMMARIZER_MODEL` env var -- it is a valid override for machines with different hardware, leave it alone
- Adding a model auto-detection or benchmarking system -- far beyond scope
- Removing `qwen/qwen3-vl-72b` or `qwen/qwen3-32b` from `config/models.py` -- those are OpenRouter cloud models, not local Ollama models
- Building a generic "model registry" abstraction -- the simple constant pattern is sufficient

## Risks

### Risk 1: gemma4:e2b classification quality differs from qwen3:1.7b
**Impact:** Message routing accuracy may change (work vs ignore, SDLC vs question)
**Mitigation:** gemma4:e2b has already been validated in the AI judge path. The classification prompts are simple single-word responses. Any regression would be caught by manual testing during PR review.

### Risk 2: Cleanup removes a model still in use on one machine
**Impact:** A machine with `OLLAMA_MODEL=qwen3:1.7b` override loses its model
**Mitigation:** The cleanup list is static and known. Machines using env var overrides are expected to manage their own model inventory. Document this in the PR description.

## Race Conditions

No race conditions. All changes are static constant swaps or sequential update-time operations.

## No-Gos (Out of Scope)

- Removing or renaming env var overrides (`OLLAMA_MODEL`, `OLLAMA_SUMMARIZER_MODEL`)
- Changing OpenRouter model references in `config/models.py`
- Adding model benchmarking or auto-selection logic
- Modifying the AI judge model (already uses `gemma4:e2b`)
- Changing `remote-update.sh` (already clean, no old model references)

## Update System

The update system is directly modified by this work:

- `scripts/update/run.py` gains cleanup logic (best-effort `ollama rm` for superseded models) and a smoke test (simple prompt verification) after the existing model pull step
- The superseded models list is imported from `config/models.py` so future model changes only need one edit
- No new dependencies or config files
- No migration steps needed -- the cleanup is idempotent (removing already-absent models is a no-op)

## Agent Integration

No agent integration required. All changes are to bridge-internal code (`bridge/routing.py`, `intent/__init__.py`) and the update system (`scripts/update/run.py`). No MCP server changes, no `.mcp.json` changes, no new tools.

## Documentation

- [ ] Update `docs/features/sdlc-first-routing.md` line 27: change `qwen3:1.7b` reference to `gemma4:e2b`
- [ ] Update `docs/plans/memory-test-suite-516.md`: note that `gemma2:3b` and `gemma3:4b` references are superseded by `gemma4:e2b`
- [ ] No new feature doc needed -- this is a config consolidation, not a new feature

## Success Criteria

- [ ] `grep -r 'qwen3:1.7b\|qwen3:4b\|gemma2:3b\|gemma3:4b' --include='*.py' .` returns zero matches (excluding `docs/plans/`)
- [ ] All Ollama model references in `.py` files import from `config/models.py` (no hardcoded model strings)
- [ ] `scripts/update/run.py` removes superseded models after successful pull of `gemma4:e2b`
- [ ] `scripts/update/run.py` includes a smoke test (simple prompt -> response) after model pull
- [ ] `docs/features/sdlc-first-routing.md` references `gemma4:e2b`
- [ ] Unit tests pass (`pytest tests/unit/`)

## Step by Step Tasks

### 1. Add constants to config/models.py
- **Task ID**: add-constants
- **Depends On**: none
- **Validates**: `grep OLLAMA_LOCAL_MODEL config/models.py`
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: true
- Add `OLLAMA_LOCAL_MODEL = "gemma4:e2b"` constant
- Add `OLLAMA_SUPERSEDED_MODELS` list with old model names
- Place in a new `LOCAL OLLAMA MODELS` section after the OpenRouter section

### 2. Replace hardcoded references in bridge/routing.py
- **Task ID**: fix-routing
- **Depends On**: add-constants
- **Validates**: `grep -c 'qwen3:1.7b' bridge/routing.py` returns 0
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: true
- Import `OLLAMA_LOCAL_MODEL` from `config.models` at module level
- Replace both `model="qwen3:1.7b"` with `model=OLLAMA_LOCAL_MODEL`

### 3. Replace hardcoded reference in intent/__init__.py
- **Task ID**: fix-intent
- **Depends On**: add-constants
- **Validates**: `grep -c 'qwen3:1.7b' intent/__init__.py` returns 0
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: true
- Import `OLLAMA_LOCAL_MODEL` from `config.models`
- Use as default in `os.environ.get("OLLAMA_MODEL", OLLAMA_LOCAL_MODEL)`

### 4. Add cleanup and smoke test to update flow
- **Task ID**: update-cleanup
- **Depends On**: add-constants
- **Validates**: `grep -c 'OLLAMA_SUPERSEDED_MODELS' scripts/update/run.py` returns > 0
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: false
- Import `OLLAMA_SUPERSEDED_MODELS` from `config.models`
- After successful model pull, iterate superseded list and `ollama rm` each (best-effort)
- Add smoke test: `ollama run <model> "hi"` with timeout, log result
- All errors caught and logged as warnings, never fail the update

### 5. Update stale docs
- **Task ID**: fix-docs
- **Depends On**: none
- **Validates**: `grep -c 'qwen3:1.7b' docs/features/sdlc-first-routing.md` returns 0
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: true
- Fix `docs/features/sdlc-first-routing.md` line 27
- Update `docs/plans/memory-test-suite-516.md` references

### 6. Final validation
- **Task ID**: validate
- **Depends On**: fix-routing, fix-intent, update-cleanup, fix-docs
- **Assigned To**: validator
- **Agent Type**: validator
- **Parallel**: false
- Run `grep -r 'qwen3:1.7b\|qwen3:4b\|gemma2:3b\|gemma3:4b' --include='*.py' .` and verify zero matches outside docs/plans/
- Run `pytest tests/unit/ -x -q` and verify pass
- Run `python -m ruff check bridge/routing.py intent/__init__.py config/models.py scripts/update/run.py`

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| No old model refs | `grep -r 'qwen3:1.7b' --include='*.py' bridge/ intent/ scripts/ config/` | no output |
| Config has constant | `python -c "from config.models import OLLAMA_LOCAL_MODEL; print(OLLAMA_LOCAL_MODEL)"` | `gemma4:e2b` |
| Tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check bridge/routing.py intent/__init__.py config/models.py scripts/update/run.py` | exit code 0 |
| Docs updated | `grep 'gemma4:e2b' docs/features/sdlc-first-routing.md` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

No open questions -- the issue is fully specified with exact file locations, line numbers, and acceptance criteria.
