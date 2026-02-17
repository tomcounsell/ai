# Code Impact Finder

Semantic search tool that surfaces code, configs, and docs coupled to a proposed change. Used during `/do-plan` Phase 1 to inform blast radius analysis.

## Architecture

Two-stage pipeline (shared with [doc impact finder](semantic-doc-impact-finder.md)):

1. **Embedding recall**: Index codebase chunks → embed query → cosine similarity → top-N candidates
2. **LLM reranking**: Claude Haiku scores each candidate for relevance (0-10) → filter to score >= 5

The shared pipeline lives in `tools/impact_finder_core.py`. The code finder provides code-specific configuration via `tools/code_impact_finder.py`.

## Chunking Strategy

| File Type | Strategy | Granularity |
|-----------|----------|-------------|
| Python (`.py`) | `ast` module | Functions, classes (full body + per-method), preamble |
| Markdown (`.md`) | `## ` headings | One chunk per section |
| Config (`.json`, `.toml`) | Top-level keys | Single chunk if <100 lines, else per-key |
| Shell (`.sh`) | Function definitions | One chunk per function + non-function code |

Python classes get **dual-level chunks**: one for the entire class body (captures conceptual coupling) and one per method (captures specific dependencies).

## Usage

### CLI

```bash
# Find code coupled to a change
.venv/bin/python -m tools.code_impact_finder "change session ID derivation"

# Check index status
.venv/bin/python -m tools.code_impact_finder --status

# Rebuild index only
.venv/bin/python -m tools.code_impact_finder --index-only
```

### Python API

```python
from tools.code_impact_finder import find_affected_code, index_code

# Build/refresh the index
index_code()

# Find affected code
results = find_affected_code("change session ID derivation")
for r in results:
    print(f"{r.relevance:.2f} | {r.path} | {r.section} | {r.impact_type} | {r.reason}")
```

### Output Model

```python
class AffectedCode(BaseModel):
    path: str           # bridge/telegram_bridge.py
    section: str        # "def handle_message"
    relevance: float    # 0.0 - 1.0
    impact_type: str    # "modify" | "dependency" | "test" | "config" | "docs"
    reason: str         # "Reads session_id which is being restructured"
```

The `impact_type` is determined by Haiku during reranking (it classifies the relationship to the change). Falls back to path-based heuristics when Haiku doesn't provide one.

## Integration with /do-plan

During Phase 1, after narrowing the problem, the planner runs:

```bash
.venv/bin/python -m tools.code_impact_finder "PROBLEM_STATEMENT"
```

Results map to plan sections:
- `impact_type="modify"` → **Solution** (code to change)
- `impact_type="dependency"` → **Risks** (unexpected coupling)
- `impact_type="test"` → **Success Criteria** (tests to update)
- `impact_type="config"` → **Solution** (configs to adjust)
- `impact_type="docs"` → **Documentation** (docs to update)
- Low relevance (<0.5) → **Rabbit Holes** (tangential coupling)

## Self-Healing

- Missing/corrupt index → auto-rebuilds on next run
- Model mismatch (provider change) → full rebuild
- Content hashing → only changed files re-embedded
- Cost warning at >1000 chunks (informational, non-blocking)

## Index Storage

- **File**: `data/code_embeddings.json` (gitignored)
- **Format**: Flat JSON with version, model, and chunk array
- Estimated corpus: ~400 files, ~2500 chunks

## Related

- [Semantic Doc Impact Finder](semantic-doc-impact-finder.md) — Same pipeline for docs, used by `/do-docs`
- `tools/impact_finder_core.py` — Shared pipeline infrastructure
- `.claude/skills/do-plan/SKILL.md` — Integration point (Phase 1, step 3)
