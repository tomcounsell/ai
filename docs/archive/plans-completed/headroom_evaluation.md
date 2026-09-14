---
status: Ready
type: chore
appetite: Small
owner: Valor Engels
created: 2026-06-23
tracking: https://github.com/tomcounsell/ai/issues/1611
last_comment_id:
revision_applied: true
---

# Spike: Headroom Context-Compression Evaluation

## Problem

Several routine reads recur on every PM/dev session and are token-heavy:
`dashboard.json` dumps, log tails, `gh` CLI JSON output, and large SDLC stage
reads (plan docs). They inflate context windows and cost on every turn.

[Headroom](https://github.com/chopratejas/headroom) (PyPI `headroom-ai`,
Apache 2.0) is a third-party context-compression layer claiming **60-95%
token reduction** by squeezing tool outputs, logs, RAG chunks, and history
*before* they reach the model. This spike answers a **fit + risk** question:
do the claimed savings hold on **our** content shapes without dropping
load-bearing detail or fighting Anthropic prompt caching?

**Current behavior:** No compression of raw tool/log bytes. Our existing
context-fidelity-modes layer (#329) operates at the *skill-dispatch* layer
(how much session state forwards to sub-agents), not at the raw-byte layer —
so the two are complementary, not competing.

**Desired outcome:** A findings writeup with measured per-content-type
token-reduction numbers, latency, a quality-regression check, a KV-cache
verdict, and a clear **go / no-go** recommendation.

## Freshness Check

**Baseline commit:** plan authored on `session/headroom-evaluation` worktree, main at `9af25e38`.
**Issue filed at:** recently (same-day spike approved by Tom in Agent Builders Chat).
**Disposition:** Unchanged — this is a self-contained evaluation spike independent of any in-flight code. `docs/features/context-fidelity-modes.md` re-read and confirmed to describe a different (skill-dispatch) layer, so no conflation risk.
**Active plans overlapping:** none.

## Prior Art

- **#329 / `docs/features/context-fidelity-modes.md`** — our in-house FULL/COMPACT/MINIMAL/STEERING context compression at the skill-dispatch layer. Complementary to Headroom (different layer). Not a prior attempt at raw-byte compression.
- No prior issue attempted third-party raw-output compression.

## Research

Queries: "headroom-ai pypi extras", "headroom context compression CacheAligner KV cache", "Anthropic prompt caching 5 minute TTL".

Key findings:
- PyPI package is **`headroom-ai`** (not `headroom`), current **0.27.0**, `requires_python >=3.10`, Apache 2.0. (Our venv is Python 3.14 — compatible.)
- Extras confirmed on PyPI: `[proxy] [mcp] [ml] [code] [memory] [relevance] [image] [agno] [langchain] [evals] [pytorch-mps]`. Issue scope = `[mcp]` + `[code]` + `[ml]` (NOT `[all]`, NOT `[memory]`).
- Three integration surfaces: library `compress(messages, model=...)`, HTTP proxy (`headroom proxy --port 8787`), MCP server (`headroom_compress`/`headroom_retrieve`/`headroom_stats`).
- Anthropic prompt caching: 5-min (300s) TTL, cache-write 1.25x, cache-read 0.1x — matched exactly by Headroom's `AnthropicCacheOptimizer` constants.

## Solution (Spike Methodology)

This is a time-boxed evaluation, **not** a production integration. The work:

1. **Isolated install.** Install `headroom-ai[mcp,code,ml]` into a throwaway venv (`/tmp/headroom-spike/venv`) — never the production `.venv`, never the live harness. No `headroom wrap claude-code`.
2. **Real corpus.** Capture genuine fixtures of our pain points: live `dashboard.json` (73KB), worker log tail (37KB), `gh pr list` JSON (PR-list shape), a large plan doc as an SDLC stage read (129KB), and real source (`ui/app.py`).
3. **Measure per content type** via the library `compress()` API and the underlying transforms (`SmartCrusher`, `LogCompressor`, `CodeAwareCompressor`): tokens before/after, % reduction, latency (cold + warm).
4. **Quality regression check.** Verify load-bearing tokens (error/WARN markers and session IDs) survive compression — byte counts alone are insufficient.
5. **KV-cache verdict.** Determine whether `CacheAligner` mutates prefixes (it would fight Anthropic caching) and whether compressing recurring reads busts the 5-min prefix cache.
6. **Recommend a surface** and a go/no-go.

**Lowest-risk surface to prototype first:** library `compress()` on captured fixtures — measures savings without intercepting live traffic.

## Spike Results

Full numbers and analysis (including the separate `compress()`-API vs direct-transform tables) live in the deliverable: **`docs/research/headroom-evaluation.md`**. Headline findings (numbers below are direct-transform / best-case capability unless marked `via compress()`):

| Content type | Compressor | Reduction (real fixture) | Lossless? |
|---|---|---|---|
| `gh` JSON array | SmartCrusher lossless-table | 45.1% direct / 44.9% via `compress()` | yes |
| dashboard sub-arrays (sessions/reflections) | SmartCrusher lossless-table | 30-37% | yes |
| **whole `dashboard.json`** (nested dict) | router gates it out | **27.4% direct but 0% via `compress()`** (router won't descend nested dict) | lossless if reachable |
| worker log tail | LogCompressor (dedup) | **73.5% direct / 73.3% via `compress()`** | lossy-but-signal-safe |
| python source (`ui/app.py`) | CodeAwareCompressor | **0%** (self-rejected: invalid syntax) | n/a |
| markdown / prose (plan doc) | Kompress (ML) | **0%** (model not loaded; `embedding_available=False`) | n/a |

- **Latency:** warm ~3-5ms; one-time cold ~11-70ms. Negligible per-call once warm.
- **Quality:** LogCompressor retained all error/WARN markers and all 9 unique session IDs (the two signal classes measured) while cutting 73.5% — it deduplicates repeated lines, doesn't drop unique signal.
- **KV-cache:** `CacheAligner` is **detector-only** in 0.27.0 (`enabled=False` default; never mutates messages) — so it will NOT fight Anthropic caching. The real tension is that compressing a *recurring* read changes its bytes and busts the 5-min prefix cache for that message.
- **Footprint:** `[ml]` pulls torch+transformers = **1.3 GB**, yet the prose model never engaged via the library API. The ML extra delivered zero measured value in this spike.

**Go/no-go:** see deliverable. Recommendation = **conditional no-go on broad integration; narrow yes on a `gh`/log-specific helper behind a default-OFF flag.**

## Success Criteria

The spike is complete when the deliverable demonstrates all of the following with evidence from real fixtures:

- [x] `headroom-ai[mcp,code,ml]` installed in an isolated venv (compression extras only; no `[all]`, no `[memory]`); production `.venv`/`pyproject.toml` and live harness untouched.
- [x] Token-reduction measured **per content type** (JSON / logs / code / prose) on a real corpus of our heavy reads, with before/after token counts.
- [x] Latency overhead recorded (cold and warm).
- [x] Quality-regression check performed: compressed vs raw, verifying load-bearing tokens (error/WARN markers and session IDs) survive — not just byte counts.
- [x] KV-cache / Anthropic-prompt-caching interaction verified and documented (CacheAligner mutation behavior + recurring-read prefix-cache busting).
- [x] Findings writeup at `docs/research/headroom-evaluation.md` with a clear go/no-go and, if go, the recommended surface and rollout shape.

## No-Gos

- Do NOT integrate Headroom's **SharedContext** / `headroom learn` (collides with subconscious memory).
- Do NOT run `headroom wrap claude-code` or wrap the production bridge/worker.
- Do NOT install `[all]` or `[memory]`.
- Do NOT add Headroom to the production `.venv` or `pyproject.toml` as part of this spike. Any experimental flag stays default-OFF.

## Update System

No update-system changes required — this is an evaluation spike. Nothing is added to the production environment, `pyproject.toml`, or the `/update` flow. If a future follow-up integrates a narrow helper, that work will carry its own Update System section.

## Agent Integration

No agent integration required — the spike runs in a throwaway venv and produces a findings doc. No CLI entry point in `pyproject.toml [project.scripts]`, no bridge import. A future narrow-integration follow-up (if approved) would add a CLI/helper at that time, behind a default-OFF flag.

## Failure Path Test Strategy

The deliverable's quality-regression check **is** the failure-path test: compressed output is diffed against the original for dropped load-bearing tokens (error markers, session IDs, SHAs). A "passing" compressor must cut bytes without dropping unique signal; CodeAwareCompressor's self-rejection on invalid-syntax output is the safety behavior we want and is documented as such.

## Test Impact

No existing tests affected — this is a self-contained spike that adds only a findings doc plus a throwaway, default-OFF experiment script; it changes no production code paths and therefore breaks no existing test. Any future narrow integration would add its own targeted tests.

## Rabbit Holes

- Tuning `kompress_model` / downloading the HuggingFace prose model to chase markdown savings — out of appetite; the ML extra already cost 1.3 GB for zero measured value.
- Recursively descending nested dicts so SmartCrusher reaches dashboard sub-arrays — that's a Headroom-internal pipeline limitation, not our work to fix in a spike.
- Proxy/MCP surface latency benchmarking under live traffic — explicitly out of scope (no live-harness wrapping).

## Documentation

The findings doc is the spike's primary deliverable. Concrete tasks:

- [ ] Create `docs/research/headroom-evaluation.md` with: a results table of measured per-content-type token reduction (JSON / logs / code / prose) on real fixtures; cold/warm latency numbers; the quality-regression check (load-bearing-token survival diff); the KV-cache / Anthropic-prompt-caching verdict; the install-footprint note (1.3 GB `[ml]` for zero measured value); and an explicit go/no-go recommendation with the recommended surface if go.
- [ ] In that doc, add a short "Relationship to context-fidelity-modes" note clarifying Headroom operates at the raw-byte layer vs. #329 at the skill-dispatch layer (complementary, not competing) — do NOT edit `docs/features/context-fidelity-modes.md` itself, since nothing about that feature changes.
- [ ] No changes to `docs/features/README.md` index — a research spike doc is not a shipped feature.

## Open Questions

None blocking — the spike resolved the open questions in the issue (which surface, how to measure, CacheAligner interaction). The go/no-go is a recommendation for human review on the PR.
