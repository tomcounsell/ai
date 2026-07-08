---
status: Planning
type: feature
appetite: Large
owner: Valor Engels
created: 2026-07-08
tracking: https://github.com/tomcounsell/ai/issues/1920
last_comment_id:
---

# Frames-capable "watch" path for video links (YouTube + X/Twitter visual grounding)

## Problem

Valor "reads" videos but never "sees" them. Every YouTube link is enriched
transcript-only: `bridge/enrichment.py` step 2 auto-calls
`tools.link_analysis.process_youtube_urls_in_text`, which pulls captions →
falls back to OpenAI `whisper-1` → GPT-4o-mini summary → injects a text context
string. No frame is ever extracted. For anything where the meaning is on screen
and not in the audio — slide decks, product demos, UI walkthroughs, charts,
"as you can see here" narration, silent/music-only clips — the agent answers
blind.

The scope also now covers **Twitter/X videos**, not just YouTube. Today there is
no path at all for an x.com/twitter.com status link with an attached video: the
enrichment YouTube branch never fires, and a raw HTML fetch of an X URL returns
anti-bot markup with no transcript and no frames.

**Current behavior:**
- YouTube link → transcript-only text enrichment (blind to on-screen content).
- X/Twitter video link → nothing useful (no transcript, no frames, anti-bot HTML).

**Desired outcome:**
- The cheap transcript-first reflex stays the default push path for a bare link
  (no token/latency regression on the common case).
- A new **opt-in, agent-invoked "watch" tier** extracts scene-sampled frames +
  a timestamped transcript from a video URL (YouTube **or** X/Twitter) that the
  agent `Read`s as images — giving real visual grounding when the question is
  visual or the transcript came back thin.
- For X/Twitter links specifically, the x.ai (Grok) API supplies X-native
  context (post text, author, thread) and acts as the media-understanding
  fallback when yt-dlp can't pull the clip.

## Freshness Check

**Baseline commit:** `37d4cc74`
**Issue filed at:** 2026-07-06T07:03:45Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `bridge/enrichment.py:158-182` — "step 2 auto-calls process_youtube_urls_in_text, transcript-only" — still holds.
- `tools/link_analysis/__init__.py:376-492` (`process_youtube_url`) — "captions → whisper-1 → GPT-4o-mini summary, no visual path" — still holds.
- `tools/link_analysis/__init__.py:267,321` — Whisper/summary read `os.getenv("OPENAI_API_KEY")` directly (not via `config.settings`) — confirmed; the new module follows the same direct-`os.getenv` pattern.
- `tools/link_analysis/__init__.py:41` — `MAX_VIDEO_DURATION = int(os.getenv("YOUTUBE_MAX_VIDEO_DURATION", "36000"))` — confirms the env-tunable-constant convention the frame-cap/resolution constants should mirror.
- `pyproject.toml [project.scripts]` — `valor-youtube-transcribe = "tools.link_analysis.cli:main"` present.

**Cited sibling issues/PRs re-checked:** none cited in the issue body.

**Commits on main since issue was filed (touching referenced files):**
- `f7bc0f5e`, `5b9face7`, etc. — dependency bumps touching only `pyproject.toml` / `uv.lock` (claude-agent-sdk). Irrelevant to the enrichment/link_analysis logic. No behavioral drift.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** `yt-dlp` (2025.12.08) and `ffmpeg` (8.1) are installed on this
machine. No grok/xai reference exists anywhere in `config/`, `tools/`,
`bridge/`, or `agent/` — the Grok integration is greenfield.

## Prior Art

Searched closed issues (`youtube watch frames visual`) and merged PRs
(`youtube video frames`): **no prior issues or PRs found** related to frame
extraction or visual video grounding. This is the first attempt.

- The audit basis is the external repo `bradautomates/claude-video` (`/watch`
  skill, MIT, ~3.6k stars) documented in issue #1920. Direction: **adopt-and-
  vendor** the frame-extraction technique (download → ffmpeg scene sampling →
  dedup → frames+transcript to the model), do **not** take a live plugin
  dependency on a young single-maintainer repo.

## Research

**Queries used:**
- yt-dlp Twitter/X extractor support and cookie requirements
- xAI (Grok) API — vision/chat capabilities and X-native grounding

**Key findings:**
- `yt-dlp` ships a first-class `twitter` extractor covering x.com / twitter.com
  status URLs with video; the same download → ffmpeg → transcript pipeline used
  for YouTube generalizes to X with no new download library. Protected /
  age-gated / some quote-tweet media may require cookies and can fail — that is
  the failure mode the Grok fallback covers.
- xAI's API is OpenAI-compatible (`https://api.x.ai/v1`, chat/completions +
  vision-capable Grok models). xAI has first-party access to the X corpus, so
  Grok can return post context (author, text, thread) and describe an attached
  video that an anti-bot HTML fetch cannot. Grok does **not** offer a
  Whisper-equivalent audio-transcription endpoint — audio transcription stays on
  OpenAI `whisper-1` (existing path).
- No relevant external findings contradict the vendor-the-technique approach.

## Spike Results

### spike-1: yt-dlp handles x.com video URLs with the same interface as YouTube
- **Assumption**: "yt-dlp can download/inspect an x.com status video with the same CLI surface used for YouTube."
- **Method**: code-read + tool-version check
- **Finding**: `yt-dlp` 2025.12.08 present; documented `twitter` extractor covers x.com/twitter.com. Same `-f`, `--write-info-json`, audio-extract flags apply. Confirmed at the tooling level; a live network pull against a real X URL is a build-time verification, not a plan blocker.
- **Confidence**: high
- **Impact on plan**: The watch tool is source-agnostic over yt-dlp-supported hosts; X support is a source-detection + Grok-context add-on, not a second pipeline.

### spike-2: GROK_API_KEY already provisioned; no `op`/1Password step needed
- **Assumption**: "The x.ai key must be retrieved from 1Password via `op`."
- **Method**: code-read of the vault `.env`
- **Finding**: `op` CLI is **not installed** on this machine, but `GROK_API_KEY=xai-…` (84 chars) is **already present** in `~/Desktop/Valor/.env`. The key is live and usable without `op`.
- **Confidence**: high
- **Impact on plan**: Secrets task reduces to (a) `.env.example` placeholder, (b) `config/settings.py` field, (c) direct `os.getenv("GROK_API_KEY")` in the Grok client. No 1Password retrieval in the build path.

## Data Flow

**Push path (default, unchanged):**
1. **Entry**: Telegram message with a YouTube URL arrives → bridge persists record.
2. **Worker enrichment** (`bridge/enrichment.py` step 2): `process_youtube_urls_in_text` → captions → whisper-1 → summary → injected `[YouTube video … transcript summary: …]`.
3. **New signpost (this plan)**: if the resolved transcript is empty/very short, append `[transcript thin — run valor-video-watch <url> for visual grounding]` so the agent knows to escalate instead of guessing. No frames on this path.

**Pull path (new "watch" tier, agent-invoked):**
1. **Entry**: agent runs `valor-video-watch <url> ["question"]` via Bash (question optional; informs nothing beyond human-readable framing).
2. **Source detection**: classify URL → `youtube` | `x` | `other` (yt-dlp-supported).
3. **Acquire**: `yt-dlp` download (video + audio) into a temp dir.
4. **Frames**: ffmpeg scene-change sampling → JPEG frames at `t=MM:SS`, near-duplicate frames deduped (grayscale mean-abs-diff), capped by env-tunable frame count + resolution.
5. **Transcript**: reuse the existing captions→whisper-1 path (`tools.link_analysis`) for the audio track; emit timestamped text.
6. **X-native context (x source only)**: call Grok (`tools/video_watch/grok.py`, `GROK_API_KEY`) for post text/author/thread + video description. If step 3 (yt-dlp) failed to obtain media for an X URL, Grok's description is the **fallback** understanding.
7. **Output**: print frame JPEG paths (with `t=MM:SS` markers), the timestamped transcript, and any Grok X-context block — a payload the agent `Read`s image-by-image.

## Architectural Impact

- **New dependencies**: `ffmpeg` + `yt-dlp` become required OS deps for the pull
  path (both already installed here; must be propagated by the update system).
  New Python HTTP call to the xAI API (reuse existing `httpx`/`openai`-compatible
  client already vendored for OpenAI; no new package).
- **Interface changes**: new `[project.scripts]` entry `valor-video-watch`; new
  module `tools/video_watch/`. `bridge/enrichment.py` gains a thin-transcript
  signpost branch (additive, no signature change).
- **Coupling**: `tools/video_watch` reuses `tools.link_analysis`'s transcript
  helpers (import, no duplication). Otherwise self-contained.
- **Data ownership**: frames + audio land in a temp dir under the workspace temp
  path, cleaned per invocation. No new persistent store, no Popoto model.
- **Reversibility**: high — a new opt-in tool + one additive enrichment branch;
  removing the `[project.scripts]` entry and the signpost fully reverts.

## Appetite

**Size:** Large

**Team:** Solo dev (fanned to builders), PM, code reviewer

**Interactions:**
- PM check-ins: 2-3 (Grok-role decision, tool-naming decision, scope alignment)
- Review rounds: 1 (PR review gate before merge)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `yt-dlp` on PATH | `yt-dlp --version` | Video/audio download for both sources |
| `ffmpeg` on PATH | `ffmpeg -version` | Scene-change frame extraction |
| `OPENAI_API_KEY` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('OPENAI_API_KEY')"` | Whisper transcript fallback |
| `GROK_API_KEY` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('GROK_API_KEY')"` | x.ai X-native context + X media fallback |

Run via `python scripts/check_prerequisites.py docs/plans/video-watch-visual-grounding.md`.

## Solution

### Key Elements

- **`tools/video_watch/` module**: source-agnostic "watch" pipeline. Detects
  YouTube vs X/Twitter vs other; yt-dlp download → ffmpeg scene-change frame
  sampling with dedup → timestamped transcript (reusing `tools.link_analysis`)
  → returns frame paths + transcript (+ Grok X-context for X links).
- **`tools/video_watch/grok.py`**: thin xAI (Grok) client. For X/Twitter URLs,
  fetches post text/author/thread context and a video description; doubles as
  the media-understanding fallback when yt-dlp cannot pull the clip. Reads
  `GROK_API_KEY` directly via `os.getenv`.
- **`valor-video-watch` CLI**: new `[project.scripts]` entry point
  (`tools.video_watch.cli:main`). Pull-based: the agent invokes it on demand.
  Emits frame JPEG paths (`t=MM:SS`) + transcript + optional Grok context.
- **Thin-transcript signpost**: additive branch in `bridge/enrichment.py` that
  appends a `[transcript thin — run valor-video-watch …]` hint when the
  transcript is empty/short, so the agent escalates instead of answering blind.
- **Secrets/config wiring**: `grok_api_key` field on `APISettings`,
  `GROK_API_KEY` placeholder in `.env.example` (with the required comment line
  above it), value already live in the vault.

### Flow

Agent sees a video URL or a thin-transcript signpost → decides the question is
visual → runs `valor-video-watch <url>` → tool downloads, extracts deduped
scene frames + timestamped transcript (+ Grok X-context for X links) → prints
frame paths + transcript → agent `Read`s each JPEG and answers with visual
grounding.

### Technical Approach

- **Source-agnostic core, X-specific add-on.** One pipeline over yt-dlp-supported
  hosts. `youtube` and `x`/`twitter` differ only in (a) URL detection and (b)
  whether the Grok X-context step runs. Do **not** build two pipelines.
- **Grok's role is X-native grounding + fallback, not frame vision.** Frames go
  to the agent (Claude) via `Read` — the model-agnostic claude-video pattern.
  Grok is used where it is genuinely differentiated: first-party X post/thread
  context, and describing an X video when yt-dlp download fails. (This is the
  #1 Open Question to confirm with the PM — see below.)
- **Frame extraction adopts claude-video's technique**: ffmpeg scene-change
  detection, near-duplicate dedup via 16×16 grayscale mean-abs-diff, env-tunable
  frame cap + resolution as **provisional/tunable named constants**
  (`VIDEO_WATCH_MAX_FRAMES`, `VIDEO_WATCH_FRAME_WIDTH`, `VIDEO_WATCH_MAX_DURATION`),
  each `os.getenv(NAME, default)` with a grain-of-salt comment, mirroring
  `MAX_VIDEO_DURATION` in `link_analysis`.
- **Transcript reuse**: import the captions→whisper helpers from
  `tools.link_analysis` rather than reimplementing. Keep OpenAI `whisper-1` as
  the transcription backend (Groq swap explicitly out of scope — see Rabbit Holes).
- **Pull, not push**: frames are never attached on the default enrichment path.
  The push path only gains a cheap text signpost.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Every `except` in `tools/video_watch/` (yt-dlp download failure, ffmpeg
  failure, Grok HTTP error, missing key) must log a warning AND surface an
  actionable error string in the CLI output — assert observable behavior in
  tests, no silent `except: pass`.
- [ ] X URL with yt-dlp download failure but Grok key present → assert the tool
  falls back to Grok context and reports the degraded mode (frames absent,
  context present), not a bare crash.

### Empty/Invalid Input Handling
- [ ] Non-video URL / unsupported host → clear error, exit non-zero, no traceback.
- [ ] Empty/whitespace URL → error message, exit 1 (mirror `link_analysis/cli.py`).
- [ ] Video with no audio track (silent demo) → transcript empty, frames still
  emitted, output notes "no transcript (silent)".
- [ ] `GROK_API_KEY` unset on an X URL → tool still returns frames+transcript
  from yt-dlp and notes Grok context unavailable (graceful degrade, no crash).

### Error State Rendering
- [ ] The thin-transcript signpost path is tested: a fixture transcript below
  threshold produces the `[transcript thin — run valor-video-watch …]` string
  in the enriched text (assert it reaches the enriched output, not swallowed).
- [ ] CLI failure paths print the error to stderr and exit non-zero.

## Test Impact

- [ ] `tests/unit/test_enrichment*.py` (if present for the YouTube branch) — UPDATE: add a case asserting the thin-transcript signpost is appended when the transcript is empty/short; assert it is NOT appended for a healthy transcript (no regression on the common path).
- [ ] No existing `tools/link_analysis` tests are modified — the watch pipeline is additive and imports the transcript helpers without changing their behavior. New tests live under `tools/video_watch/tests/` and `tests/`.

If a dedicated enrichment test module does not exist, add one rather than
skipping this coverage.

## Rabbit Holes

- **Swapping the Whisper backend to Groq.** The issue floats Groq as a
  cheaper/faster Whisper backend. Tempting, but it is a separable
  infra swap with its own key provisioning and adds no visual-grounding value.
  Keep OpenAI `whisper-1`; do not rabbit-hole here. Deferred (see No-Gos).
- **Auto-watching on the push/enrichment path.** Blindly attaching frames to
  every link would blow token budget and latency — exactly the regression the
  issue warns against. The push path gets a *text signpost only*; the frame
  pipeline is strictly pull. Do not build duration-threshold auto-escalation
  into enrichment.
- **Sending frames to Grok vision for description.** Redundant with the agent
  `Read`ing frames directly and couples visual grounding to one vendor. Keep
  frame understanding in the agent; Grok is X-context only.
- **Perfect scene-detection tuning.** Adopt claude-video's balanced defaults as
  provisional env-tunable constants and move on; do not chase optimal fps/dedup
  thresholds.
- **Supporting every yt-dlp host (TikTok/IG/Loom/local).** The pipeline will
  technically accept them, but YouTube + X are the committed, tested surfaces;
  do not build/verify host-specific handling beyond those two.

## Risks

### Risk 1: yt-dlp fails on protected/age-gated X media
**Impact:** No frames/transcript for some X videos.
**Mitigation:** Grok X-context fallback provides post text + video description
so the agent still gets grounding; the tool reports degraded mode explicitly.

### Risk 2: Frame payload token/latency blowup
**Impact:** A long video could emit dozens of JPEGs, ballooning agent context.
**Mitigation:** Env-tunable frame cap (`VIDEO_WATCH_MAX_FRAMES`) + resolution +
max-duration guard, defaulting conservative; dedup drops near-identical frames.
Pull-only invocation keeps this off the common path.

### Risk 3: xAI API contract / auth drift
**Impact:** Grok call errors break the X-context step.
**Mitigation:** Grok step is wrapped and non-fatal — failure degrades to
frames+transcript-only with a logged warning; never crashes the watch.

### Risk 4: ffmpeg/yt-dlp absent on other machines
**Impact:** The watch tool fails on machines without the OS deps.
**Mitigation:** Update-system task ensures both are installed/propagated; the
tool emits an actionable "install ffmpeg/yt-dlp" error when missing.

## Race Conditions

No race conditions identified. The watch pipeline is a synchronous, single-shot
subprocess flow per invocation writing to a per-invocation temp dir; there is no
shared mutable state, no cross-process coordination, and no concurrent writers.
The enrichment signpost is a pure string append on data already resolved
sequentially in `enrich_message`.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1951] Swap the Whisper transcription backend to Groq
  (`whisper-large-v3`). Separable infra swap with its own key provisioning; no
  visual-grounding value. Filed as its own issue.
- [EXTERNAL] Installing `ffmpeg`/`yt-dlp` on machines the agent cannot reach —
  the update system automates it, but the actual install runs on each machine
  during `/update`, outside this PR's execution.

## Update System

- **`scripts/update/` must ensure `ffmpeg` and `yt-dlp` are present** on every
  machine that runs the worker (the pull path needs both). `yt-dlp` is already a
  Python dep in `pyproject.toml`; `ffmpeg` is an OS package — add/verify a
  `brew install ffmpeg` (macOS) step in the update flow if not already covered,
  and a doctor check.
- **New secret `GROK_API_KEY`**: already in the vault `.env`; add the
  `.env.example` placeholder (with comment line) so the completeness check
  passes on fresh installs. No migration — additive nullable config.
- No Popoto model changes → no `scripts/update/migrations.py` entry.

## Agent Integration

- **New CLI entry point required**: `valor-video-watch = "tools.video_watch.cli:main"`
  in `pyproject.toml [project.scripts]`. This is the agent's surface — the agent
  invokes it via Bash and `Read`s the emitted frame JPEGs (pull-based, matching
  the existing `valor-youtube-transcribe` pattern).
- **No MCP server / `.mcp.json` change** — the tool is a Bash-invoked CLI, not an
  MCP tool.
- **Bridge import**: the only bridge-side change is the additive thin-transcript
  signpost in `bridge/enrichment.py` (already imported in the worker path). No
  new bridge import of the watch module — watch is strictly agent-pull.
- **Integration test**: assert `valor-video-watch --help` resolves via the
  installed console script (entry-point wiring), and a mocked/short-fixture run
  emits frame paths + transcript. A CLAUDE.md Quick Commands row documents the
  new command so the agent discovers it.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/video-watch-visual-grounding.md` — the two-tier
  model (transcript push default vs. frame pull escalation), source coverage
  (YouTube + X/Twitter), Grok's X-native role, env-tunable constants, and the
  thin-transcript signpost.
- [ ] Add an entry to `docs/features/README.md` index table.
- [ ] Update `docs/features/markitdown-ingestion.md` / any YouTube-tool docs
  that describe the current transcript-only reaction, so they cross-link the new
  watch tier.

### Inline Documentation
- [ ] Docstrings on `tools/video_watch/` public functions and the CLI.
- [ ] Grain-of-salt comments on the provisional frame-cap/resolution constants.
- [ ] Add a `valor-video-watch` row to the CLAUDE.md Quick Commands table.

## Success Criteria

- [ ] `valor-video-watch <youtube-url>` emits deduped scene-frame JPEG paths
  (`t=MM:SS`) + a timestamped transcript the agent can `Read`.
- [ ] `valor-video-watch <x-url>` works on an x.com/twitter.com video: frames +
  transcript when yt-dlp succeeds; Grok X-context (+ description fallback) when
  it doesn't.
- [ ] Default enrichment for a bare YouTube link is unchanged (transcript-only,
  no frames) — no token/latency regression on the push path.
- [ ] Thin/empty transcript on the push path appends the `valor-video-watch`
  signpost to the enriched text.
- [ ] `GROK_API_KEY` wired: `.env.example` placeholder + `APISettings` field;
  Grok client reads it via `os.getenv`; missing key degrades gracefully.
- [ ] Frame cap / resolution / max-duration are env-overridable named constants
  with grain-of-salt comments.
- [ ] `grep "valor-video-watch" pyproject.toml` confirms the entry point.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

- **Builder (watch-core)**
  - Name: `watch-core-builder`
  - Role: `tools/video_watch/` pipeline — source detection, yt-dlp download, ffmpeg frame extraction + dedup, transcript reuse, CLI + pyproject entry.
  - Agent Type: builder
  - Resume: true

- **Builder (grok-context)**
  - Name: `grok-builder`
  - Role: `tools/video_watch/grok.py` xAI client, `grok_api_key` settings field, `.env.example` placeholder, graceful-degrade wiring.
  - Domain: MCP-tool/API integration (see DOMAIN_FRAMING.md)
  - Agent Type: builder
  - Resume: true

- **Builder (enrichment-signpost)**
  - Name: `signpost-builder`
  - Role: thin-transcript signpost branch in `bridge/enrichment.py` + its test.
  - Agent Type: builder
  - Resume: true

- **Validator (watch)**
  - Name: `watch-validator`
  - Role: verify frames+transcript emission, X path, graceful degrade, signpost, and no push-path regression.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `watch-doc`
  - Role: feature doc + index + CLAUDE.md row + cross-links.
  - Agent Type: documentarian
  - Resume: true

The three builders touch **disjoint file sets** (`tools/video_watch/` core vs.
`tools/video_watch/grok.py` + config vs. `bridge/enrichment.py`) so they fan out
into the single session worktree without commit interleaving.

## Step by Step Tasks

### 1. Watch-core pipeline + CLI
- **Task ID**: build-watch-core
- **Depends On**: none
- **Validates**: `tools/video_watch/tests/` (create), `grep valor-video-watch pyproject.toml`
- **Informed By**: spike-1 (yt-dlp handles X same interface)
- **Assigned To**: watch-core-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/video_watch/__init__.py` with `watch_video(url, ...)`: source detection (`youtube`|`x`|`other`), yt-dlp download to temp, ffmpeg scene-change frame sampling + 16×16 grayscale dedup, env-tunable `VIDEO_WATCH_MAX_FRAMES`/`VIDEO_WATCH_FRAME_WIDTH`/`VIDEO_WATCH_MAX_DURATION` constants.
- Reuse `tools.link_analysis` captions→whisper-1 helpers for the transcript; emit `t=MM:SS` markers.
- Create `tools/video_watch/cli.py` (`main`) mirroring `link_analysis/cli.py` arg/exit conventions; print frame paths + transcript (+ Grok block when present).
- Add `valor-video-watch = "tools.video_watch.cli:main"` to `pyproject.toml [project.scripts]`.

### 2. Grok X-native context client + secrets wiring
- **Task ID**: build-grok
- **Depends On**: none
- **Validates**: `tools/video_watch/tests/test_grok.py` (create), `.env.example` completeness check
- **Informed By**: spike-2 (GROK_API_KEY already in vault, no `op`)
- **Assigned To**: grok-builder
- **Agent Type**: builder
- **Domain**: MCP-tool/API integration
- **Parallel**: true
- Create `tools/video_watch/grok.py`: OpenAI-compatible client to `https://api.x.ai/v1`, `os.getenv("GROK_API_KEY")`. `fetch_x_context(url)` → post text/author/thread + video description; non-fatal on error.
- Add `grok_api_key: str | None` to `APISettings` in `config/settings.py` (+ include in the `validate_api_keys` field validator).
- Add `GROK_API_KEY=xai-****` to `.env.example` with the required comment line above it.
- Wire the watch pipeline to call `fetch_x_context` only for `x` source, and use it as the fallback when yt-dlp media acquisition fails.

### 3. Enrichment thin-transcript signpost
- **Task ID**: build-signpost
- **Depends On**: none
- **Validates**: enrichment signpost test (create/UPDATE)
- **Assigned To**: signpost-builder
- **Agent Type**: builder
- **Parallel**: true
- In `bridge/enrichment.py` step 2, after the YouTube results resolve, if the effective transcript is empty/below a small env-tunable threshold, append `[transcript thin — run valor-video-watch <url> for visual grounding]` to `enriched_text`.
- Add a test asserting the signpost appears for a thin transcript and is absent for a healthy one (no push-path regression).

### 4. Validation
- **Task ID**: validate-watch
- **Depends On**: build-watch-core, build-grok, build-signpost
- **Assigned To**: watch-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table commands; verify frames+transcript emission, X path + graceful degrade (key unset / download fail), signpost behavior, and entry-point wiring.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-watch
- **Assigned To**: watch-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/video-watch-visual-grounding.md`, add index entry, add the CLAUDE.md Quick Commands row, cross-link existing YouTube-tool docs.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: watch-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run the full Verification table + confirm all Success Criteria (including docs). Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Entry point wired | `grep -c "valor-video-watch" pyproject.toml` | output > 0 |
| CLI resolves | `valor-video-watch --help` | exit code 0 |
| Grok key placeholder present | `grep -c "GROK_API_KEY" .env.example` | output > 0 |
| Grok setting field | `grep -c "grok_api_key" config/settings.py` | output > 0 |
| Signpost string present | `grep -rc "valor-video-watch" bridge/enrichment.py` | output > 0 |
| No key hardcoded | `grep -rn "xai-" tools/ config/ bridge/` | match count == 0 |
| Lint clean | `python -m ruff check tools/video_watch bridge/enrichment.py config/settings.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/video_watch bridge/enrichment.py config/settings.py` | exit code 0 |
| Watch unit tests | `pytest tools/video_watch/tests/ -q` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Grok's role — confirm the design.** This plan uses x.ai/Grok as the
   **X-native context layer + media-understanding fallback** (post/thread
   context; describe an X video when yt-dlp can't pull it), while frames go to
   the agent (Claude) via `Read`. It deliberately does **not** route extracted
   frames through Grok vision (redundant, vendor-couples visual grounding). Does
   this match Tom's intent for "x.ai involved," or does he want Grok as the
   primary frame/vision understander?
2. **Tool naming.** The issue proposed `valor-youtube-watch`; the X expansion
   makes that a misnomer, so this plan uses source-agnostic **`valor-video-watch`**.
   Confirm the rename is acceptable (it changes the exact command the agent
   learns).
3. **Groq Whisper backend** is deferred to a separate issue (#1951). Confirm that
   staying on OpenAI `whisper-1` for this feature is acceptable.
