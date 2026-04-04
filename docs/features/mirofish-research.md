# MiroFish Swarm Intelligence Research

> **Business context:** See [Podcasting](~/work-vault/Cuttlefish/Podcasting.md) in the work vault for how MiroFish fits into the podcast research pipeline.

MiroFish is the 6th automated research source in the podcast production pipeline. Unlike the other six tools (Perplexity, GPT-Researcher, Gemini, Together, Claude, Grok) which produce **factual web-sourced research**, MiroFish produces **perspective-oriented outputs**: stakeholder reaction modeling, prediction generation, counter-argument stress-testing, and audience reception simulation.

## Architecture

MiroFish runs as a **sidecar service** (Docker container or local process), not an embedded Python library. Communication is via HTTP API.

```
Django (Cuttlefish) ──HTTP──> MiroFish Backend (Flask @ localhost:5001)
                                  │
                                  ├── Multi-agent swarm simulation
                                  ├── Stakeholder modeling
                                  └── Prediction generation
```

### Components

| File | Purpose |
|------|---------|
| `apps/podcast/tools/mirofish_research.py` | HTTP client wrapper (`run_mirofish_simulation`, `check_health`) |
| `apps/podcast/services/research.py` | `run_mirofish_research()` -- service function with graceful-skip pattern |
| `apps/podcast/tasks.py` | `step_mirofish_research()` -- pipeline task wiring |
| `apps/podcast/services/analysis.py` | Creates `prompt-mirofish` and `p2-mirofish` placeholder artifacts |
| `apps/podcast/services/prompts/craft_research_prompt.md` | Prompt engineering guidance for MiroFish |

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MIROFISH_API_URL` | Yes | `http://localhost:5001` | Base URL of the MiroFish backend |

If `MIROFISH_API_URL` is not set or the service is unreachable, MiroFish research is **gracefully skipped** -- a `[SKIPPED]` artifact is created and the pipeline continues with other research sources.

## Data Flow

1. **Question Discovery** phase creates a `prompt-mirofish` artifact with a perspective-oriented prompt
2. **Targeted Research** phase runs `step_mirofish_research` in parallel with GPT, Gemini, Together, and Claude
3. `run_mirofish_research()` checks the env var, health-checks the service, then calls `run_mirofish_simulation()`
4. The HTTP client POSTs to `/api/predict` on the MiroFish backend
5. The response is parsed and saved as a `p2-mirofish` `EpisodeArtifact`
6. The fan-in signal checks all `p2-*` artifacts have content before advancing

## Prompt Design

The MiroFish prompt emphasises perspective simulation over factual search:

- **Stakeholder reactions**: Simulate diverse panels reacting to key claims
- **Predictions**: Evidence-based forecasts about outcomes and consequences
- **Counter-arguments**: Stress-test the episode's thesis from critical perspectives
- **Audience modeling**: What will resonate, what will be controversial

This avoids overlap with factual research tools (Perplexity, Gemini, etc.) and leverages MiroFish's unique multi-agent simulation capability.

## Graceful Degradation

The integration follows the same graceful-skip pattern as all other research tools:

| Condition | Behavior | Artifact Content |
|-----------|----------|-----------------|
| `MIROFISH_API_URL` not set | Skip | `[SKIPPED: MIROFISH_API_URL not configured]` |
| Service unreachable | Skip | `[SKIPPED: MiroFish service unreachable]` |
| HTTP error from API | Skip | `[SKIPPED: MiroFish returned no content - http_error]` |
| Timeout (>10 min) | Skip | `[SKIPPED: MiroFish returned no content - timeout]` |
| Empty/malformed response | Skip | `[SKIPPED: MiroFish returned no content - ...]` |
| Unexpected exception | Skip | `[SKIPPED: MiroFish research failed - ...]` |

Downstream services (Cross-Validation, Synthesis) handle missing `p2-mirofish` gracefully since they already handle missing research sources.

## Testing

```bash
# Run MiroFish-specific tests
pytest apps/podcast/tests/test_mirofish_research.py -v

# Run all research tool tests
pytest apps/podcast/tools/tests/test_research_tools.py -v
```

## Tracking

- **Issue**: [#171](https://github.com/yudame/cuttlefish/issues/171)
- **Plan**: `docs/plans/mirofish-research-integration.md`
