---
status: Ready
type: feature
appetite: Large
owner: Valor Engels
created: 2026-07-08
tracking: https://github.com/tomcounsell/ai/issues/1920
last_comment_id:
revision_applied: true
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
3. **New signpost (this plan)**: inside the existing `for r in youtube_results:` loop, for each result whose `transcript` field is empty/very short (`len((r.get("transcript") or "").strip()) < VIDEO_WATCH_THIN_TRANSCRIPT_CHARS`), append a per-URL `[transcript thin for <url> — run valor-video-watch <url> for visual grounding]`. Gated on `transcript` length **only** — `process_youtube_url` returns a non-empty `context` even on failure, so gating on `context` would silently never/always fire. No frames on this path.

**Pull path (new "watch" tier, agent-invoked):**
1. **Entry**: agent runs `valor-video-watch <url> ["question"]` via Bash (question optional; informs nothing beyond human-readable framing).
2. **Source detection**: classify URL → `youtube` | `x` | `other` (yt-dlp-supported).
3. **Acquire**: `yt-dlp` download (video + audio) into a context-managed `work` scratch dir (auto-cleaned), with a subprocess timeout.
4. **Frames**: ffmpeg scene-change sampling → JPEG frames written to a **separate persistent** `frames_dir` (`tempfile.mkdtemp`, NOT auto-deleted), at `t=MM:SS`, near-duplicate frames deduped (grayscale mean-abs-diff), capped by env-tunable frame count + resolution.
5. **Transcript**: reuse `transcribe_audio_file` for the audio track; emit timestamped text.
6. **X-native understanding (x source only)**: call Grok (`tools/video_watch/grok.py`, `GROK_API_KEY`) primarily to **describe the X video when yt-dlp could not fetch it** (the visual-grounding fallback for X); it also returns post/author/thread context in the same call. Subject to Open Question #1 (Tom's intended Grok role).
7. **Output**: print persistent `frames_dir` JPEG paths (with `t=MM:SS` markers), the timestamped transcript, and any Grok block — a payload the agent `Read`s in a later tool call (paths must still exist then; see the two-dir discipline in Task 1).

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
- **Data ownership**: download/audio scratch is auto-cleaned per invocation;
  emitted frame JPEGs live in a separate `video_watch_frames_*` dir that
  deliberately outlives the process (the agent Reads them in a later tool call)
  and is swept by an age-based reaper. No new persistent store, no Popoto model.
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
- **Thin-transcript signpost**: additive branch in `bridge/enrichment.py`,
  **inside the existing `for r in youtube_results:` loop**, that appends a
  per-URL `[transcript thin for <url> — run valor-video-watch <url> …]` hint
  when `len((r.get("transcript") or "").strip()) < VIDEO_WATCH_THIN_TRANSCRIPT_CHARS`.
  The check gates on the `transcript` field **only** — never on `context`, which
  `process_youtube_url` populates with a non-empty string even on failure (see
  Technical Approach for why).
- **Secrets/config wiring**: `GROK_API_KEY` placeholder in `.env.example` (with
  the required comment line above it), value already live in the vault. The
  Grok client reads `os.getenv("GROK_API_KEY")` directly — **no `APISettings`
  field**, because the sub-model's `env_nested_delimiter="__"` would bind a
  field to `API__GROK_API_KEY`, not the plain `GROK_API_KEY` that is provisioned
  (matching how `link_analysis` reads `OPENAI_API_KEY` directly).

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
- **Grok's role is the X-video-understanding fallback, not frame vision.** Frames
  go to the agent (Claude) via `Read` — the model-agnostic claude-video pattern.
  Grok's primary, visual-grounding-relevant job is to **describe an X video when
  yt-dlp cannot fetch it** (protected/age-gated media) — the "seeing" fallback
  for X. Post/author/thread text context rides along in the same call but is
  secondary. A reviewer flagged that generic X text-context is separable from
  "seeing"; keeping Grok scoped to the fallback (with text context as a free
  byproduct) keeps this on the visual-grounding thesis while honoring the
  x.ai-involvement mandate. This is Open Question #1 — the exact Grok role is a
  Tom-intent call; the single `fetch_x_context` seam makes it cheap to re-aim.
- **Frame extraction adopts claude-video's technique**: ffmpeg scene-change
  detection, near-duplicate dedup via 16×16 grayscale mean-abs-diff, env-tunable
  frame cap + resolution as **provisional/tunable named constants** in
  `constants.py`, each `os.getenv(NAME, default)` with a grain-of-salt comment.
  `VIDEO_WATCH_MAX_DURATION` defaults to ~30 min (**not** `MAX_VIDEO_DURATION`'s
  10-hour value): OpenAI `whisper-1` caps at ~25 MB (~30 min mono 16 kHz); an
  oversized track returns `None`, so beyond the cap the tool must emit an
  explicit `[audio too long to transcribe — frames only]` note, never the
  misleading "no transcript (silent)".
- **Transcript reuse — the source-agnostic helper only**: reuse
  `tools.link_analysis.transcribe_audio_file(filepath)` for the downloaded audio
  track. Do **not** route through `process_youtube_url(url)` — that function is
  YouTube-only (returns `{"success": False, "error": "Not a valid YouTube URL"}`
  for any non-YouTube URL) and cannot serve X media. Keep OpenAI `whisper-1` as
  the transcription backend (Groq swap out of scope — see No-Gos).
- **Low reversal cost for the two PM Open Questions**: the CLI command name
  lives once in the dependency-free `tools/video_watch/constants.py`
  (`WATCH_CLI_NAME`), imported by both `cli.py` and the enrichment signpost; the
  Grok behavior lives behind a single guarded call-site (`fetch_x_context`). A
  rename touches the constant + the Verification grep that points at the constant
  definition; a Grok-role reversal touches one call-site. Neither touches already-
  built pipeline files, so Tasks 1 and 2 can start without blocking on the PM
  answers.
- **The signpost `context`-trap**: `bridge/enrichment.py` applies `yt_enriched`
  wholesale and only reads `success`/`error` per result. `process_youtube_url`
  fills `context` with `"[YouTube video: … transcript unavailable …]"` even on
  failure while leaving `transcript` None. The signpost MUST gate on
  `r.get("transcript")`, never `r.get("context")`.
- **Pull, not push**: frames are never attached on the default enrichment path.
  The push path only gains a cheap per-URL text signpost.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Every `except` in `tools/video_watch/` (yt-dlp download failure, ffmpeg
  failure, Grok HTTP error, missing key) must log a warning AND surface an
  actionable error string in the CLI output — assert observable behavior in
  tests, no silent `except: pass`.
- [ ] X URL with yt-dlp download failure but Grok key present → assert the tool
  falls back to Grok context and reports the degraded mode (frames absent,
  context present), not a bare crash.
- [ ] yt-dlp/ffmpeg exceeding `VIDEO_WATCH_SUBPROCESS_TIMEOUT` raises
  `subprocess.TimeoutExpired` → assert it is caught and routed to the degrade
  path (never propagates to an outer SIGKILL); Grok request timeout →
  `fetch_x_context` returns None + warning.

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

**Temp-dir lifetime (resource, not a race):** two dirs with different lifetimes.
The download/audio **scratch** runs inside `with tempfile.TemporaryDirectory()
as work:` so it is reclaimed on exception (OOM, `TimeoutExpired`,
`CalledProcessError`) as well as success. The emitted **frames_dir**
(`tempfile.mkdtemp`) must intentionally survive the process — the agent Reads
those JPEG paths in a later, separate tool call — so it is NOT context-managed;
an age-based reaper (run at CLI start + registered with hourly session-cleanup)
sweeps stale `video_watch_frames_*` dirs. A subprocess timeout bounds the window
in which a hung yt-dlp/ffmpeg could be SIGKILLed before cleanup. Tests: (a)
printed frame paths still exist after the CLI returns; (b) `work` is gone after a
forced mid-pipeline `CalledProcessError`; (c) the reaper removes over-age dirs.

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
  signpost in `bridge/enrichment.py`. It may import from the **dependency-free**
  `tools/video_watch/constants.py` (for `WATCH_CLI_NAME` /
  `VIDEO_WATCH_THIN_TRANSCRIPT_CHARS`) — that module pulls NO yt-dlp/ffmpeg/httpx.
  The bridge must never import `tools/video_watch/__init__.py` (the heavy
  pipeline); watch execution stays strictly agent-pull.
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

- [x] `valor-video-watch <youtube-url>` emits deduped scene-frame JPEG paths
  (`t=MM:SS`) + a timestamped transcript the agent can `Read`.
- [x] `valor-video-watch <x-url>` works on an x.com/twitter.com video: frames +
  transcript when yt-dlp succeeds; Grok X-context (+ description fallback) when
  it doesn't.
- [x] Default enrichment for a bare YouTube link is unchanged (transcript-only,
  no frames) — no token/latency regression on the push path.
- [x] Thin/empty transcript on the push path appends the `valor-video-watch`
  signpost to the enriched text.
- [x] `GROK_API_KEY` wired: `.env.example` placeholder present; Grok client
  reads it via `os.getenv("GROK_API_KEY")` (no `APISettings` field — see
  Technical Approach); missing key degrades gracefully.
- [x] Frame cap / resolution / max-duration are env-overridable named constants
  with grain-of-salt comments.
- [x] `grep "valor-video-watch" pyproject.toml` confirms the entry point.
- [x] **Visual-grounding outcome (E2E):** a slide-deck or silent-demo fixture
  where the answer is on-screen (not in the transcript) → `valor-video-watch`
  emits frames that let the agent answer correctly, where transcript-only fails.
  Committed evidence: `tools/video_watch/tests/test_e2e_visual_grounding.py`
  runs the real ffprobe/ffmpeg/Pillow pipeline against a synthesized silent
  slide deck (only the two network edges patched) and asserts transcript-only
  yields nothing while the emitted frames are multiple, persistent, and
  pairwise visually distinct.
- [x] Emitted frame paths still exist after the CLI process returns (two-dir
  temp discipline verified).
- [x] Tests pass (`/do-test`).
- [x] Documentation updated (`/do-docs`).

## Team Orchestration

- **Builder (watch-core)**
  - Name: `watch-core-builder`
  - Role: `tools/video_watch/` pipeline — source detection, yt-dlp download, ffmpeg frame extraction + dedup, transcript reuse, CLI + pyproject entry.
  - Agent Type: builder
  - Resume: true

- **Builder (grok-context)**
  - Name: `grok-builder`
  - Role: `tools/video_watch/grok.py` xAI client (single guarded `fetch_x_context` call-site), `.env.example` `GROK_API_KEY` placeholder, graceful-degrade wiring. No `APISettings` field.
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
- Create a **dependency-free** `tools/video_watch/constants.py` (NO yt-dlp/ffmpeg/httpx imports) holding `WATCH_CLI_NAME = "valor-video-watch"`, `VIDEO_WATCH_THIN_TRANSCRIPT_CHARS`, `VIDEO_WATCH_MAX_FRAMES`, `VIDEO_WATCH_FRAME_WIDTH`, `VIDEO_WATCH_MAX_DURATION`, `VIDEO_WATCH_SUBPROCESS_TIMEOUT`, `VIDEO_WATCH_GROK_TIMEOUT` — each `os.getenv(NAME, default)` with a grain-of-salt comment. Both `cli.py` and `bridge/enrichment.py` import from THIS module only (cheap, no heavy deps pulled into the bridge path — satisfies the Agent Integration constraint).
- Create `tools/video_watch/__init__.py` with `watch_video(url, ...)`: source detection (`youtube`|`x`|`other`), yt-dlp download + ffmpeg scene-change frame sampling + 16×16 grayscale dedup.
- **Two-dir temp discipline (frames must outlive the process):** context-manage only the download/audio scratch — `with tempfile.TemporaryDirectory() as work:` (auto-cleaned on exception/exit) — but emit JPEGs into a **separate, non-context-managed** `frames_dir = tempfile.mkdtemp(prefix="video_watch_frames_")` whose paths are printed for the agent to `Read` in a LATER tool call. Do NOT put frames in the auto-deleted dir (that would delete them the instant the CLI returns, before the agent Reads them). Add an age-based reaper for stale `video_watch_frames_*` dirs (standalone helper invoked at CLI start; also register with the hourly session-cleanup reflection).
- Pass `timeout=VIDEO_WATCH_SUBPROCESS_TIMEOUT` to every yt-dlp/ffmpeg `subprocess.run` and catch `subprocess.TimeoutExpired` → fall through to the degrade path (for `x`: `fetch_x_context` fallback), never hang to the outer SIGKILL.
- Reuse `tools.link_analysis.transcribe_audio_file(filepath)` (the source-agnostic helper) for the audio track — NOT `process_youtube_url`, which rejects non-YouTube URLs. Emit `t=MM:SS` markers.
- Create `tools/video_watch/cli.py` (`main`) mirroring `link_analysis/cli.py` arg/exit conventions; print frame paths + transcript (+ Grok block when present).
- Add `valor-video-watch = "tools.video_watch.cli:main"` to `pyproject.toml [project.scripts]`.
- Tests: (a) run the CLI to completion and assert printed frame paths STILL EXIST after the process returns; (b) force a `subprocess.CalledProcessError`/`TimeoutExpired` mid-pipeline and assert the `work` scratch dir is gone; (c) assert the stale-`frames_dir` reaper removes dirs older than its threshold.

### 2. Grok X-native context client + secrets wiring
- **Task ID**: build-grok
- **Depends On**: none
- **Validates**: `tools/video_watch/tests/test_grok.py` (create), `.env.example` completeness check
- **Informed By**: spike-2 (GROK_API_KEY already in vault, no `op`)
- **Assigned To**: grok-builder
- **Agent Type**: builder
- **Domain**: MCP-tool/API integration
- **Parallel**: true
- Create `tools/video_watch/grok.py`: OpenAI-compatible client to `https://api.x.ai/v1`, `os.getenv("GROK_API_KEY")`, explicit `timeout=VIDEO_WATCH_GROK_TIMEOUT` on the request. Single guarded `fetch_x_context(url)` call-site → **primarily an X-video description when yt-dlp couldn't fetch the clip** (the visual-grounding fallback), plus post/author/thread context in the same response; non-fatal on error/timeout (returns None + logs a warning when the key is absent or the call fails). Keep the seam a single function so Open Question #1's answer changes one call-site.
- Add `GROK_API_KEY=xai-****` to `.env.example` with the required comment line above it. Do **NOT** add an `APISettings` field — `env_nested_delimiter="__"` would bind it to `API__GROK_API_KEY`, not the provisioned `GROK_API_KEY`; the direct `os.getenv` read is the real wiring (mirrors `link_analysis`'s `OPENAI_API_KEY` read).
- Wire the watch pipeline to call `fetch_x_context` only for `x` source, and use it as the fallback when yt-dlp media acquisition fails.

### 3. Enrichment thin-transcript signpost
- **Task ID**: build-signpost
- **Depends On**: none
- **Validates**: enrichment signpost test (create/UPDATE)
- **Assigned To**: signpost-builder
- **Agent Type**: builder
- **Parallel**: true
- In `bridge/enrichment.py` step 2, **inside the existing `for r in youtube_results:` loop** (~line 174), for each result append a per-URL `[transcript thin for {r['url']} — run valor-video-watch {r['url']} for visual grounding]` to `enriched_text` when `len((r.get("transcript") or "").strip()) < VIDEO_WATCH_THIN_TRANSCRIPT_CHARS` (env-tunable, provisional). Gate on `transcript` **only**, never `context` (which is non-empty even on failure).
- Add a test asserting: (a) the signpost fires for a result with an empty/short `transcript`; (b) it does NOT fire for a healthy transcript; (c) a result with an error `context` but None `transcript` still fires (proves it gates on `transcript`, not `context`); (d) multi-URL messages emit one signpost per thin URL.

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
| Grok read is direct os.getenv | `grep -c 'os.getenv("GROK_API_KEY")' tools/video_watch/grok.py` | output > 0 |
| No decorative settings field | `grep -c "grok_api_key" config/settings.py` | exit code 1 |
| CLI name is a shared constant | `grep -c 'WATCH_CLI_NAME' tools/video_watch/constants.py` | output > 0 |
| Enrichment imports constants only | `grep -c 'from tools.video_watch.constants import' bridge/enrichment.py` | output > 0 |
| Bridge never imports heavy module (import-chain assertion — the grep alone passes vacuously if the package `__init__` is heavy) | `pytest tools/video_watch/tests/test_import_discipline.py -q` | exit code 0 |
| Signpost references the CLI | `grep -rc "valor-video-watch" bridge/enrichment.py` | output > 0 |
| Subprocess timeout wired | `grep -c 'VIDEO_WATCH_SUBPROCESS_TIMEOUT' tools/video_watch/__init__.py` | output > 0 |
| No key hardcoded | `grep -rn "xai-" tools/ config/ bridge/` | match count == 0 |
| Lint clean | `python -m ruff check tools/video_watch bridge/enrichment.py config/settings.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/video_watch bridge/enrichment.py config/settings.py` | exit code 0 |
| Watch unit tests | `pytest tools/video_watch/tests/ -q` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness + History | Thin-transcript signpost under-specified; naive `context` gate silently no-ops it | Data Flow step 3, Key Elements, Technical Approach, Task 3 all now gate on `len(r.get("transcript") or "")` inside the `for r in youtube_results:` loop, per-URL, never `context` | `process_youtube_url` returns non-empty `context` even on failure; test (c) proves gating on `transcript` |
| CONCERN | Risk & Robustness + Scope | `APISettings.grok_api_key` binds to `API__GROK_API_KEY` not `GROK_API_KEY` — decorative | Dropped the settings field; Grok reads `os.getenv("GROK_API_KEY")` directly; Verification row replaced with a direct-read grep + an inverse "no decorative field" row | `env_nested_delimiter="__"`; mirrors link_analysis OPENAI_API_KEY read |
| CONCERN | Scope + History | Tasks 1/2 build settled work against unresolved Open Q #1/#2 | `WATCH_CLI_NAME` module constant + single guarded `fetch_x_context` call-site make a PM reversal one-place-cheap, so builders start without blocking | Name/Grok-role reversal touches one constant / one call-site |
| CONCERN | Risk & Robustness | Temp dir leaks on mid-run crash | `watch_video` wraps the sequence in `tempfile.TemporaryDirectory()`; Race Conditions + Failure Path test added | Cleanup on exception, not only happy path |
| NIT | History | "Reuse whisper helpers" didn't name the source-agnostic function | Task 1 + Technical Approach name `transcribe_audio_file(filepath)` explicitly; forbid `process_youtube_url` for X | `process_youtube_url` rejects non-YouTube URLs |
| BLOCKER (rd2) | Risk & Robustness | Temp-dir fix deleted frames before the agent's later Read | Two-dir discipline: context-managed `work` scratch (auto-clean) + persistent `frames_dir` (mkdtemp, reaped by age); Task 1, Data Flow 7, Architectural Impact, Race Conditions + tests | Frames must outlive the CLI process |
| BLOCKER (rd2) | Scope + History | `WATCH_CLI_NAME` "via import" contradicted the no-bridge-import No-Go | Dependency-free `tools/video_watch/constants.py` holds the name + tunables; both cli.py and enrichment import constants only; Agent Integration relaxed to constants-only; Verification greps enforce it | Heavy pipeline module never imported by bridge |
| CONCERN (rd2) | Risk & Robustness | No subprocess/HTTP timeouts | `VIDEO_WATCH_SUBPROCESS_TIMEOUT` + `VIDEO_WATCH_GROK_TIMEOUT`; catch `TimeoutExpired` → degrade; Failure Path test + Verification row | Never hang to outer SIGKILL |
| CONCERN (rd2) | Scope & Value | Grok text-context is separable non-visual scope | Grok re-scoped to the X-video-description fallback (visual-grounding-relevant); text-context is a byproduct; kept per Tom's x.ai mandate, flagged as Open Q #1 | Single `fetch_x_context` seam |
| NIT (rd2) | Risk & Robustness | whisper-1 25 MB ceiling mislabels long audio as silent | `VIDEO_WATCH_MAX_DURATION` default lowered to ~30 min; over-cap emits `[audio too long to transcribe — frames only]` | ~25 MB / ~30 min whisper limit |
| NIT (rd2) | Scope & Value | All success criteria mechanical | Added an E2E visual-grounding outcome criterion (slide-deck/silent-demo fixture) | Validates the actual "seeing" win |
| NIT (rd2) | History | `VIDEO_WATCH_THIN_TRANSCRIPT_CHARS` location unspecified | Defined in dependency-free `constants.py`, imported by enrichment | Avoids bridge→watch heavy import |

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
