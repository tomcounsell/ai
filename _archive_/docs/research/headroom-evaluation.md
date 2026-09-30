# Headroom Context-Compression Evaluation — Findings

**Spike for [#1611](https://github.com/tomcounsell/ai/issues/1611). Status: complete.**
**Recommendation: NO-GO on broad integration. Conditional narrow-YES on a `gh`/log compression helper behind a default-OFF flag (optional follow-up).**

## TL;DR

[Headroom](https://github.com/chopratejas/headroom) (`headroom-ai` 0.27.0, Apache 2.0) is a real, working context-compression layer. On **our** content it delivers strong, lossless-or-signal-safe savings on exactly **two** of our four pain points — `gh` JSON output and log tails — and **zero** on the other two (`dashboard.json` and prose/markdown SDLC reads) as actually invoked through its library API. The headline "60-95%" holds only for idealized shapes (uniform JSON arrays, repetitive logs), not our heterogeneous nested JSON or our plan-doc prose. Combined with a 1.3 GB dependency footprint that produced **zero** measured value, and a genuine (if not catastrophic) tension with Anthropic's 5-minute prompt cache, the cost/benefit does not justify broad adoption now.

## Method

- Installed `headroom-ai[mcp,code,ml]` into a **throwaway** venv (`/tmp/headroom-spike/venv`, Python 3.14) — never the production `.venv`, never `pyproject.toml`, never the live harness. No `headroom wrap claude-code`. Per issue scope: `[mcp]` + `[code]` + `[ml]`, **not** `[all]`, **not** `[memory]`.
- Captured a **real corpus** of our heavy reads:
  - `dashboard.json` — live `curl localhost:8500/dashboard.json` (73 KB).
  - `worker_log_tail.txt` — `tail -300 logs/worker.log` (37 KB).
  - `gh_pr_list.json` — `gh pr list --json ...` shape (PR-list JSON array).
  - `sdlc_stage_read.md` — a real 129 KB plan doc (`docs/plans/sdlc-1219.md`), standing in for a large SDLC stage read.
  - `code_sample.py` — real source (`ui/app.py`, 23 KB).
- Measured via the lowest-risk surface — the **library `compress()` API** on captured fixtures (no live-traffic interception) — plus the underlying transforms directly (`SmartCrusher`, `LogCompressor`, `CodeAwareCompressor`) to separate true compressor capability from pipeline-router gating.
- Token counts are Headroom's own (`CompressResult.tokens_before/after`, tiktoken-based). Latency via `time.perf_counter()`, cold + warm.

## Results — token reduction per content type

Measured through the library `compress()` API with `compress_user_messages=True, protect_recent=0` (the config an integration would *have* to set, since our heavy reads arrive as `role:user` tool results, which Headroom protects by default):

| Fixture | Content type | bytes | tok before | tok after | **reduction** | latency |
|---|---|---:|---:|---:|---:|---:|
| `gh_pr_list.json` | uniform JSON array | 22,787 | 6,533 | 3,599 | **44.9%** | 28 ms |
| `worker_log_tail.txt` | logs | 37,451 | 10,722 | 2,861 | **73.3%** | 28 ms |
| `dashboard.json` | nested heterogeneous JSON | 73,002 | 20,880 | 20,880 | **0.0%** | 122 ms |
| `code_sample.py` | python source | 23,067 | 6,613 | 6,613 | **0.0%** | 1,302 ms |
| `sdlc_stage_read.md` | prose / markdown | 128,589 | 36,762 | 36,762 | **0.0%** | 11 ms |

### Why the zeros happen (this is the important part)

The zeros are not bugs in our test — they're how Headroom 0.27.0 actually behaves on our shapes:

- **`dashboard.json` (0% via `compress()`, but the data IS compressible).** Calling `SmartCrusher.crush()` directly on the whole file cuts **27.4%** (73,002 → 53,023 bytes) via a *lossless table* strategy, and the individual sub-arrays compress well — `dashboard.sessions` **30.8%**, `dashboard.reflections` **36.9%**. But the `compress()` pipeline's `ContentRouter` does **not recursively descend a top-level dict** to find the compressible nested arrays, so end-to-end it returns 0%. Our #1 pain point is *theoretically* a 27-37% win but the shipped library API leaves it on the table.
- **`code_sample.py` (0%).** `CodeAwareCompressor` ran, produced output, then **self-rejected**: *"Code compression produced invalid syntax for python, returning original."* This is the *correct* safety behavior — better 0% than corrupted code — but it means no code savings on our real source.
- **`sdlc_stage_read.md` (0%).** Prose compression needs the Kompress ML model, which was **never loaded** (`headroom.embedding_available()` → `False`) despite installing `[ml]` (1.3 GB of torch + transformers). The prose path delivered nothing through the library API.

### Direct-transform numbers (true compressor capability, bypassing the router gate)

| Transform | Fixture | byte reduction | strategy / note |
|---|---|---:|---|
| SmartCrusher | `gh_pr_list.json` | **45.1%** | `lossless:table` |
| SmartCrusher | `dashboard.json` (whole) | **27.4%** | `lossless:table` ×2 — *but router won't invoke this end-to-end* |
| LogCompressor | `worker_log_tail.txt` | **73.5%** | line dedup |
| CodeAwareCompressor | `ui/app.py` | **0%** | self-rejected (invalid syntax) |

## Latency

- **Warm:** ~3-5 ms per compress for logs and gh-JSON. Negligible.
- **Cold (first call):** 11-122 ms typical; one outlier at **1.3 s** (the code path, attempting AST parse before rejecting). One-time per process.
- Verdict: latency is **not** a blocker for the content types that actually compress.

## Quality regression check

Byte counts alone are insufficient — the real question is whether load-bearing detail survives. Diffed the compressed worker-log output against the original for unique signal (two signal classes measured on the log fixture; SHAs/flags were not separately present in this corpus and were not measured):

| Signal class | unique in original | retained | dropped |
|---|---:|---:|---:|
| ERROR/WARN/CRITICAL/Traceback markers | 2 | 2 | **0** |
| session-ish IDs (8+ hex/alnum) | 9 | 9 | **0** |
| exception class names | 0 | 0 | 0 |

**LogCompressor is signal-safe.** It works by **deduplicating repeated log lines**, not by dropping unique content — every error marker and every distinct session ID survived the 73.5% cut. SmartCrusher's table strategy is explicitly **lossless**. CodeAwareCompressor's self-rejection means it never ships lossy code. No quality regression was observed on any path that actually compressed.

## KV-cache / Anthropic prompt-caching verdict

The issue's central worry — *"`CacheAligner` rewrites prefixes, could fight the 5-min KV cache"* — is **already neutralized in 0.27.0**:

- `CacheAligner` is now **detector-only** (per their "P2-23 fix"): it *warns* about volatile content but **never mutates, moves, or normalizes** messages. Its config defaults to `enabled=False`. So it will **not** fight Anthropic caching.
- Headroom's `AnthropicCacheOptimizer` models the real constants correctly: **TTL 300 s**, cache-write **1.25×**, cache-read **0.1×**.

The *actual* tension is subtler and inherent to any compressor: **compressing a recurring read changes its bytes, which busts the Anthropic prefix cache covering that message.** Our worst pain points (`dashboard.json`, log tails) recur every turn — precisely the content a 5-min prefix cache would otherwise serve at 0.1× cost. Compressing them trades a one-time token cut against repeated cache-write penalties. For high-recurrence reads this can be net-negative on cost even when token count drops. This must be modeled before any integration, not assumed.

## Install footprint

`headroom-ai[mcp,code,ml]` = **1.3 GB** installed (torch 2.12, transformers 5.12, tokenizers, tree-sitter language packs). The `[ml]` extra — the bulk of that footprint — produced **zero** measured value in this spike (prose model never loaded, `embedding_available=False`). If any narrow integration proceeds, drop `[ml]` entirely.

## Integration surface comparison

| Surface | Fit | Risk |
|---|---|---|
| **Library `compress()`** | Best for measurement and for a narrow, explicit helper. We control exactly what gets compressed. | Low. Requires non-default config; nested-dict router gating limits reach. |
| **HTTP proxy (`:8787`)** | Zero-code drop-in, but it sits on the live request path. | **High** — intercepts the production harness; explicitly out of scope. |
| **MCP server** | Clean tool boundary (`headroom_compress`/`retrieve`/`stats`). | Medium — adds a server process + CCR retrieval round-trips; overkill for lossless table cuts. |

Lowest-risk surface, if anything: **library `compress()` invoked explicitly** on `gh`/log output, never the proxy/wrap path.

## Relationship to context-fidelity-modes (#329)

Our in-house [context-fidelity-modes](../features/context-fidelity-modes.md) operate at the **skill-dispatch layer** — how much session state (FULL/COMPACT/MINIMAL/STEERING) forwards to a sub-agent. Headroom operates at the **raw-byte layer** — squeezing the actual tool/log/output text. They are **complementary, not competing**, and this spike does not change anything about #329.

## Go / No-Go

**NO-GO on broad integration.** Reasons:

1. Only **2 of 4** pain points actually compress via the shipped API; our #1 pain point (`dashboard.json`) returns 0% end-to-end because the router won't descend nested dicts.
2. The `[ml]` extra is **1.3 GB for zero value** — the headline prose/code claims didn't materialize on our content.
3. The KV-cache trade-off on high-recurrence reads is real and unmodeled; compressing every-turn reads can bust a cheap prefix cache.
4. We already have a complementary in-house layer (#329); adding a heavy third-party dependency for a partial win violates our "intelligent systems over rigid patterns / minimal dependencies" posture.

**Conditional narrow-YES (optional follow-up, not this spike):** A small, explicit helper that runs `SmartCrusher` on `gh` JSON output and `LogCompressor` on log tails — installed `headroom-ai[code]` only (no `[ml]`, no proxy, no MCP), behind a **default-OFF** feature flag — would capture the genuine 45%/73% lossless-and-signal-safe wins on the two content types that work, with negligible warm latency. It must first model the prompt-cache cost interaction for recurring reads. If that follow-up is not pursued, the simpler path is to **build the same table-dedup behavior in-house** (the lossless transforms are not complex) and avoid the dependency entirely.

## Reproduction

All measurements are reproducible from `/tmp/headroom-spike/` (throwaway):
```
python -m venv venv
./venv/bin/pip install "headroom-ai[mcp,code,ml]"
./venv/bin/python measure.py    # cold pass (shows default-config protection)
./venv/bin/python measure2.py   # realistic configs, per-content-type
```
Fixtures captured from live `dashboard.json`, `logs/worker.log`, a real plan doc, and `ui/app.py`.
