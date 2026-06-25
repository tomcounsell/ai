---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-06-22
tracking: https://github.com/tomcounsell/ai/issues/1726
last_comment_id:
revision_applied: true
---

# Narrated Deck Video (MP4 + voiceover) for /do-presentation

## Problem

We can produce slides (static PDF/HTML/PPTX via `/do-presentation`) and we can produce narration audio (a standalone OGG/Opus via `/do-voice-recording` → `valor-tts`), but we **cannot produce a single narrated video** of a deck. A user who wants an MP4 "watch the deck talk itself through" artifact has no path — they'd have to manually composite slide images and audio in an external editor.

**Current behavior:**
- `/do-presentation` → Marp → static PDF / HTML / PPTX. No frames, no timeline, no audio (`.claude/skills-global/do-presentation/SKILL.md:19`).
- Narration is explicitly deferred to `/do-voice-recording` (SKILL.md "Narration / voiceover" section, line ~318): "Feed it the per-slide speaker notes" — but no schema exists to carry those notes today.
- `/do-voice-recording` → `valor-tts` → standalone OGG/Opus, slide-unaware.
- Nothing muxes the two together.

**Desired outcome:**
A narrated **MP4** of a deck: each slide on screen for the duration of its narration, voiceover muxed in, exported as one file — invoked from `/do-presentation` with a `--video` mode.

## Authorization

Issue #1726 was filed as a **feasibility assessment** that "does not commit to an approach." The feasibility work is recorded in this plan: spike-1 (no html-video BYO-audio path), spike-2 (animation not required), and spike-3 (timing data already exists) together select **approach B (in-house ffmpeg slideshow)** over approach A.

**The build is authorized out-of-band.** The operator invoked the full SDLC pipeline (`/do-sdlc`) on this issue end-to-end. Driving the issue through the complete pipeline (Plan → Critique → Build → … → Merge) rather than stopping at the assessment constitutes acceptance of the feasibility output (approach B) and explicit authorization to build it. This note records that the assessment → build transition is intentional and approved, not an unauthorized leap from "evaluate" to "implement."

## Freshness Check

**Baseline commit:** `6b407cde4001b90654922a939d872896b20a132e`
**Issue filed at:** 2026-06-18T04:10:19Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `.claude/skills-global/do-presentation/SKILL.md:19` — "Exports to PDF, HTML, and optionally PPTX." — still holds.
- `.claude/skills-global/do-presentation/SKILL.md` "Narration / voiceover" section (~line 318) — narration deferred to `/do-voice-recording`, no speaker-notes schema — still holds.
- `.claude/skills-global/do-presentation/SKILL.md:291` — export uses `npx --yes @marp-team/marp-cli "<source>.md" --pdf --allow-local-files -o "<source>.pdf"` — still holds; PNG export adds `--images png`.
- `.claude/skills-global/do-voice-recording/SKILL.md` (72 lines) — canonical TTS surface, slide-unaware — still holds.
- `tools/tts/__init__.py:271,274,313,316,327` — result dict returns `duration` (float seconds via `_compute_duration_opus` → ffprobe) — confirmed present.

**Cited sibling issues/PRs re-checked:** None cited in the issue.

**Commits on main since issue was filed (touching referenced files):** None. `git log --since=2026-06-18T04:10:19Z` over the two skill dirs and `tools/tts/` returned no commits.

**Active plans in `docs/plans/` overlapping this area:** `pm-voice-refinement.md` exists but concerns the PM persona's spoken voice, not deck video — no overlap.

**Notes:** All issue claims hold against current main. No drift.

## Prior Art

No prior issues or merged PRs found related to narrated deck video. Searches:
- `gh issue list --state closed --search "presentation video narration"` → empty.
- `gh pr list --state merged --search "presentation marp video"` → empty.

The two relevant existing skills (`/do-presentation`, `/do-voice-recording`) and the `valor-tts` binary are the building blocks; none has ever been combined into a video artifact. This is greenfield composition over existing tools.

## Research

External research targeted the one decision-driving unknown: whether `nexu-io/html-video` can mux pre-supplied external audio (our `valor-tts` output), bypassing MiniMax.

**Queries used:**
- WebFetch `https://github.com/nexu-io/html-video` — audio input, BYO-audio path, engines, runtime.
- WebFetch `https://github.com/nexu-io/html-video/blob/main/CLAUDE.md` — audio config schema, ffmpeg mux separability.
- WebSearch "nexu-io html-video bring your own audio external audio file MP4 export MiniMax bypass".

**Key findings:**
- **No documented bring-your-own-audio path.** html-video's audio is described as "Optional background music + narration via MiniMax, mixed into the MP4 at export." The README and CLAUDE.md expose **no config field or CLI flag** (`narration`, `soundtrack`, `music`, `audioPath`, etc.) for supplying a pre-existing audio file. Source: https://github.com/nexu-io/html-video and its CLAUDE.md.
- **Hyperframes is the only working render engine** (HTML+CSS+GSAP). Remotion / Motion Canvas / Revideo are "Planned/Researching." It does **not** consume Marp markdown or Marp-rendered slides — adopting it means re-authoring decks into Hyperframes templates, losing our Marp theme + design system. Source: same.
- **Runtime cost of adoption:** Node 20+, pnpm 9+, Playwright (Chromium), ffmpeg, plus a MiniMax API key for the only documented audio path. Source: same.
- **`valor-tts` already returns `duration`** (float seconds via ffprobe) in its result dict (`tools/tts/__init__.py:271-327`), so per-slide on-screen timing can derive directly from each narration clip's measured length.
- **`ffmpeg` is installed** at `/opt/homebrew/bin/ffmpeg`; **Marp** is invoked via `npx --yes @marp-team/marp-cli` (no global binary needed; `--images png` produces one PNG per slide).

These findings settle the central acceptance criterion and the A-vs-B choice (see Spike Results and Solution).

## Spike Results

### spike-1: Can html-video mux pre-supplied external audio, bypassing MiniMax?
- **Assumption**: "html-video can accept a `valor-tts` OGG/WAV as the narration track at export, skipping MiniMax."
- **Method**: web-research (README + CLAUDE.md + ecosystem search; full clone-and-export was unnecessary because the documented surface already answers the question decisively).
- **Finding**: **No.** There is no documented config field or CLI flag for supplying external audio. The only documented narration path is MiniMax. While its export ffmpeg step technically muxes audio (so a BYO path is *conceivable* via a fork or upstream contribution), nothing supported exists today. Relying on it would mean importing a heavy Node/pnpm/Playwright/GSAP toolchain **and** either a MiniMax dependency or a maintained fork — to render a deck format (Hyperframes) we don't author in.
- **Confidence**: high (documented absence of the feature; corroborated across README, CLAUDE.md, and ecosystem articles).
- **Impact on plan**: **Selects approach B (in-house ffmpeg slideshow) over A (adopt html-video).** Approach A's integration value collapses without a supported BYO-audio path: re-authoring decks out of Marp + abandoning our design system + adding MiniMax/Node/Playwright is disproportionate to the goal of "static slides with our existing voiceover muxed in."

### spike-2: Is GSAP animation a real requirement, or does static-slide-with-voiceover suffice?
- **Assumption**: "A static-slide-per-narration-segment MP4 satisfies the actual use case ('watch the deck talk itself through')."
- **Method**: code-read + requirement analysis against the issue's stated desired outcome.
- **Finding**: The issue's desired outcome is "each slide on screen for the duration of its narration, voiceover muxed in" — a *narrated slideshow*, not an animated explainer. Animation is a "nice to have," not the stated requirement. Static slides satisfy it.
- **Confidence**: high.
- **Impact on plan**: Confirms B is sufficient. Animation, if ever wanted, is a separate future effort (a real engine swap) — out of scope here.

### spike-3: Does the timing data already exist?
- **Assumption**: "Per-slide on-screen duration can be derived without new measurement infrastructure."
- **Method**: code-read of `tools/tts/__init__.py`.
- **Finding**: `valor-tts` already computes and returns `duration` per synthesized clip (`_compute_duration_opus`, ffprobe). Synthesize one clip per slide → each clip's `duration` *is* that slide's on-screen time. **Caveat:** `_compute_duration_opus` returns `0.0` (not an exception) on ffprobe-missing or probe-failure (`tools/tts/__init__.py:327-354`) — a best-effort metadata field by design. A naive consumer would silently produce a zero-length slide.
- **Confidence**: high.
- **Impact on plan**: No new timing infra needed. The per-slide schema only needs to carry **narration text**; durations are measured at synthesis time. The compositor must **floor** the duration: any narrated clip with `duration <= 0.0` is re-probed directly via `ffprobe`, and if still `<= 0.0` the compositor raises rather than emitting a zero-length slide (see Technical Approach and Data Flow step 4).

## Data Flow

End-to-end flow for `/do-presentation --video`:

1. **Entry point**: User invokes `/do-presentation <topic> --video` (or `/do-presentation` produces a deck, then the user asks for video).
2. **Deck + notes authoring** (`/do-presentation`): Marp markdown is authored as today, **plus** a per-slide narration block (see schema below) carrying the speaker text for each slide.
3. **Slide image export** (Marp): `npx --yes @marp-team/marp-cli deck.md --images png --allow-local-files` → one PNG per slide. **Ordering safeguard:** Marp's `--images` output uses zero-padded sequence suffixes (`deck.001.png`, `deck.002.png`, ...), so a 3-digit pad keeps lexicographic order correct through 999 slides. The compositor does **not** rely on shell glob/lexicographic sort as the source of truth — it pairs each PNG with its narration by **explicit document-order index** derived from the same slide-split of the Marp source (the index `i` that produced PNG `i` is the index that produced narration block `i`), and asserts the Marp-emitted filenames are zero-padded; if Marp ever emits non-padded names, the compositor sorts numerically by the parsed sequence number rather than lexicographically.
4. **Per-slide narration synthesis** (`valor-tts`, one call per slide): each slide's narration text → one OGG/Opus clip. **`synthesize()` does NOT always return `{path, duration}`.** On a real failure it returns `{"error": "..."}` with **no `path` and no `duration` key** — e.g. text over `MAX_TEXT_LENGTH` (4096), both backends failing, missing `OPENAI_API_KEY`, or an ffmpeg/transcode timeout (`tools/tts/__init__.py:234,260,286,389,391`). The compositor must therefore **check `res.get("error")` FIRST** for every per-slide synthesis result and, if it is truthy, raise a descriptive `DeckVideoError` (including the slide index and the error string) and abort — *before* reading `res["duration"]` or `res["path"]`. Reading `res["duration"]` on an error dict yields `None`, and `None <= 0.0` raises `TypeError`; `res["path"]` raises `KeyError`. The error check prevents a mid-deck TTS failure from crashing with an opaque traceback instead of a clean abort. **Only after confirming no error** does the compositor read `res["path"]`/`res["duration"]` and apply the duration floor: `duration` is not trusted blindly — `_compute_duration_opus` returns `0.0` (not an exception) when `ffprobe` is missing or the probe fails (`tools/tts/__init__.py:327-354`). If a narrated clip reports `duration <= 0.0`, the compositor re-probes the clip directly with `ffprobe` (the binary it already requires); if that still yields `<= 0.0` it raises a descriptive error rather than emitting a zero-length slide. Slides with empty narration get a default hold duration (configurable; provisional `DECK_VIDEO_DEFAULT_HOLD_SECS=4.0`) and are not synthesized at all (so they never hit this path).
5. **Compositing** (new `tools/deck_video` module, ffmpeg): the canonical unit is the **slide**, not the clip. Assert `len(pngs) == total_slide_count == len(narration_blocks)` (one narration block per slide; silent slides have an empty block). For each slide *i*: if its narration block is non-empty, hold PNG *i* for that clip's floored `duration_i` (`> 0.0` by the floor in step 4); if it is empty, hold PNG *i* for `DECK_VIDEO_DEFAULT_HOLD_SECS` with no audio. Concatenate the **narrated** clips into one audio track (silent slides contribute silence of their hold length so audio and video timelines stay aligned). Then branch on whether any audio clips exist:
   - **At least one narrated slide:** encode video (libx264) and mux the combined audio (`-c:v libx264 -c:a aac`, `-pix_fmt yuv420p`) into a single MP4.
   - **All slides silent (zero audio clips):** there is no audio track to mux — produce a **video-only MP4** (`-c:v libx264 -pix_fmt yuv420p`, **no `-c:a` / no `-shortest`**), each slide held for `DECK_VIDEO_DEFAULT_HOLD_SECS`. This avoids constructing an ffmpeg command that references a non-existent audio input (which would produce a broken stream / non-zero exit).
6. **Output**: `deck.mp4` written next to the deck, reported to the user with total runtime and file path.

## Architectural Impact

- **New dependencies**: `ffmpeg` (already installed on dev machines; the only new *required* binary, and it's already a `valor-tts` dependency via ffprobe). No Node/Playwright/MiniMax. No new Python packages — orchestrate ffmpeg via `subprocess` like the rest of `tools/`.
- **Interface changes**: `/do-presentation` SKILL gains a `--video` mode and a per-slide narration schema. New `tools/deck_video` module + `valor-deck-video` CLI entry point. `valor-tts` unchanged (consumed as-is via its existing CLI/import; reuse its `duration` field).
- **Coupling**: Low. The compositor depends only on Marp PNG output (files on disk) and `valor-tts` output (files + durations). No coupling to `html-video`.
- **Data ownership**: We own the compositing logic and the narration schema. No third-party service owns any step.
- **Reversibility**: High. `--video` is additive; removing it leaves static export untouched. The new module is self-contained.
- **Temp artifacts**: all intermediate files (PNGs, per-slide audio, concat list) live under one dedicated temp directory removed in a `try/finally` on both success and failure; only `deck.mp4` survives. No orphaned scratch files.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM check-in, 1 review round

**Interactions:**
- PM check-ins: 1-2 (confirm the surface decision and the narration-schema shape)
- Review rounds: 1 (compositor correctness + skill wiring)

The coding is bounded (one ffmpeg-orchestration module + skill edits + CLI entry). The bottleneck is aligning on the narration schema and the invocation surface, both surfaced as Open Questions.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ffmpeg` on PATH | `command -v ffmpeg` | Slide-to-video encode + audio mux |
| `ffprobe` on PATH | `command -v ffprobe` | Per-clip duration measurement (already a valor-tts dep) |
| Marp CLI reachable | `npx --yes @marp-team/marp-cli --version` | PNG-per-slide export |
| `valor-tts` resolvable | `command -v valor-tts \|\| test -x "$HOME/src/ai/.venv/bin/valor-tts"` | Narration synthesis |

Run all checks: `python scripts/check_prerequisites.py docs/plans/narrated_deck_video.md`

## Solution

### Key Elements

- **Per-slide narration schema** (in `/do-presentation` Marp markdown): a structured place to put each slide's narration text so it survives from authoring through to synthesis. Carries text only — timing is measured, not declared.
- **`tools/deck_video` compositor**: a Python module that orchestrates Marp PNG export, per-slide `valor-tts` synthesis, and ffmpeg compositing into one MP4. Exposed as `valor-deck-video` CLI.
- **`/do-presentation --video` mode**: the user-facing surface that chains deck → PNGs → narration → mux, reusing `valor-tts` for synthesis. **Decision (resolves the convention conflict):** today `do-presentation/SKILL.md:316-318` says "defer to `/do-voice-recording` ... don't shell out to `valor-tts` here." The `valor-deck-video` compositor *does* call `valor-tts` directly, because per-slide synthesis with programmatic per-clip `duration` capture is not something the conversational `/do-voice-recording` skill returns — the compositor needs the structured `{path, duration}` per slide. We therefore **amend `do-presentation/SKILL.md` to carve out the `valor-deck-video` CLI tool as an approved direct consumer of `valor-tts`** (the "defer to `/do-voice-recording`" rule still governs the *manual* narration path; the automated `--video` pipeline is the documented exception). This keeps a single TTS engine (`valor-tts`/Kokoro) across both paths — no second speech surface is introduced.

### Flow

`/do-presentation <topic> --video` → deck authored with per-slide narration blocks → Marp exports one PNG per slide (zero-padded filenames for correct ordering) → `valor-tts` synthesizes one clip per **narrated** slide (returns duration, floored against the zero-length guard); **silent** slides hold for `DECK_VIDEO_DEFAULT_HOLD_SECS` → ffmpeg pairs each PNG with its duration and concatenates → **`deck.mp4` written + reported with total runtime** (= sum of floored narrated durations + silent-slide hold time).

### Technical Approach

- **Approach B (in-house ffmpeg slideshow)** — selected over A per spike-1/spike-2. Keeps Marp + the repo design system, introduces no new deck format, no MiniMax, no Node/Playwright. ffmpeg is the only new binary and is already present.
- **Narration schema**: a per-slide HTML comment block in the Marp markdown, e.g.

  ```markdown
  <!-- narration: Revenue grew 25% this quarter, driven by the new onboarding flow. -->
  ```

  Chosen because Marp ignores HTML comments in rendered output (so static PDF/HTML/PPTX are unaffected) and they are trivially parseable per-slide by splitting on the `---` slide delimiter. Empty/missing narration → configurable default hold (`DECK_VIDEO_DEFAULT_HOLD_SECS`, provisional `4.0`, named env-overridable constant with a grain-of-salt comment per the magic-number convention).
- **Total-runtime definition (mixed narrated/silent decks)**: a deck may mix narrated slides (with a clip) and silent slides (empty narration, no clip). Total runtime is therefore **not** "sum of clip durations" — that is undefined for silent slides. It is defined as:

  ```
  total_runtime = sum(floored narrated-clip durations) + (count_silent_slides * DECK_VIDEO_DEFAULT_HOLD_SECS)
  ```

  where "floored" means each narrated duration has passed the zero-length-slide guard above. The end-to-end test asserts the MP4's measured duration matches this formula within tolerance, for a fixture deck that deliberately contains **both** a narrated and a silent slide.
- **Compositing**: synthesize one audio clip per **narrated** slide; measure each clip's `duration` (reuse `valor-tts`'s returned value, then **floor it** — see below). Build the video by holding each PNG for its clip's duration (narrated) or `DECK_VIDEO_DEFAULT_HOLD_SECS` (silent), concatenate the narrated clips into one track. **Branch on audio presence:**
  - **≥1 narrated slide:** mux audio + video: `ffmpeg ... -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest deck.mp4`.
  - **All-silent deck (zero audio clips):** no audio track exists — emit a **video-only MP4**: `ffmpeg ... -c:v libx264 -pix_fmt yuv420p deck.mp4` with **no `-c:a` and no `-shortest`** (a `-c:a aac`/`-shortest` command with no audio input would yield a broken stream or non-zero exit). The code must explicitly check `if not audio_clips:` and take this branch rather than constructing the mux command unconditionally.

  Prefer ffmpeg's `concat` demuxer with a per-image duration list for robustness over a single complex `filter_complex`.
- **Synthesis-error guard (check `error` before `duration`/`path`)**: `synthesize()` returns `{"error": "..."}` with **no `path` and no `duration` key** on real failures — text over `MAX_TEXT_LENGTH` (4096), both backends failing, missing `OPENAI_API_KEY`, or an ffmpeg transcode timeout (`tools/tts/__init__.py:234,260,286,389,391`). The success dict's `duration`/`path` keys are simply absent in that case. The per-slide synthesis loop must therefore **check `res.get("error")` FIRST**: if truthy, raise `DeckVideoError(f"slide {i}: TTS synthesis failed: {res['error']}")` and abort. This is mandatory because the duration-floor logic below reads `res["duration"]` — on an error dict that is `None`, and `None <= 0.0` raises `TypeError`; `res["path"]` raises `KeyError`. Without the error-first check a mid-deck TTS failure crashes with an opaque traceback instead of a clean, slide-attributed `DeckVideoError`. Apply the duration floor **only after** confirming `res.get("error")` is falsy.
- **Duration floor (zero-length-slide guard)**: `valor-tts`'s `duration` is best-effort — `_compute_duration_opus` returns `0.0` (not an exception) when `ffprobe` is missing or fails (`tools/tts/__init__.py:327-354`). Deriving on-screen time straight from that value would silently produce a zero-length slide. The compositor therefore treats `duration <= 0.0` from a **narrated** clip (one that passed the synthesis-error guard above) as a probe failure, not a real zero: it re-probes the clip directly with `ffprobe` (already a required binary), and if the re-probe still yields `<= 0.0`, it raises a descriptive error (clip path + slide index) and aborts — never emitting a zero-length slide. Empty-narration slides do not go through this path; they use `DECK_VIDEO_DEFAULT_HOLD_SECS` (see below).
- **Integration points**: `valor-tts` (synthesis + duration), `npx @marp-team/marp-cli --images png` (slide PNGs), `/do-presentation` SKILL (authoring + `--video` invocation), new `valor-deck-video` CLI in `pyproject.toml`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The compositor must not swallow ffmpeg/Marp/tts failures silently. Any non-zero subprocess exit raises with stderr surfaced. Test: feed a deck with a deliberately broken Marp export and assert the error propagates (not a zero-byte MP4).
- [ ] No bare `except Exception: pass` in the new module; each subprocess call asserts return code and logs stderr on failure.

### Empty/Invalid Input Handling
- [ ] Slide with empty/missing narration → uses the default hold duration, not a crash and not a zero-length clip. Test asserts the slide appears for the default duration with silence.
- [ ] **All-silent deck (every slide has empty narration → zero audio clips)** → the compositor takes the video-only branch and produces a playable **video-only MP4** (no audio stream, no non-zero exit, no broken stream). Test: a fixture deck where every slide's narration block is empty; assert the output MP4 is playable, has no audio stream, and total duration ≈ `count_slides * DECK_VIDEO_DEFAULT_HOLD_SECS`.
- [ ] Deck with zero slides, or a narration block but no slides → clear error, no partial MP4. Test asserts a non-zero exit and a descriptive message.
- [ ] Narration text that is whitespace-only → treated as empty (default hold). Test covers this.

### Error State Rendering
- [ ] If `ffmpeg`/`ffprobe`/Marp/`valor-tts` is missing, the CLI emits an actionable message naming the missing tool and exits non-zero (mirrors `/do-voice-recording`'s binary-resolution pattern). Test asserts the message and exit code via a PATH-stripped invocation.
- [ ] A narrated clip whose probed duration is `<= 0.0` (ffprobe-missing/failure) is re-probed; if still `<= 0.0` the compositor raises with the clip path and slide index — no zero-length slide is emitted. Test simulates the failure and asserts the raise.
- [ ] A per-slide `synthesize()` that returns `{"error": ...}` (no `path`/`duration` keys — e.g. text > 4096 chars, both backends failing, missing `OPENAI_API_KEY`, ffmpeg timeout; `tools/tts/__init__.py:234,260,286,389,391`) makes the compositor raise a clean `DeckVideoError` (slide index + error string), **not** a `TypeError` (`None <= 0.0`) or `KeyError` (`res["path"]`). Test: monkeypatch `synthesize` to return an error dict and assert a `DeckVideoError` is raised with the slide index in the message — explicitly assert it is neither `TypeError` nor `KeyError`.

### Temp File Cleanup
- [ ] Intermediate artifacts (per-slide PNGs, per-slide audio clips, the ffmpeg concat list) are written under a single dedicated temp directory (e.g. `tempfile.TemporaryDirectory()` or a slug-named scratch dir) and removed on **both** success and failure — wrap the pipeline in `try/finally` so a partial failure (broken Marp export, tts failure, ffmpeg error) does not leave orphaned scratch files next to the deck or in `/tmp`. The final `deck.mp4` is the only artifact that survives. Test: force a mid-pipeline failure and assert the temp directory is gone afterward.

## Test Impact

No existing tests affected — this is greenfield composition over existing tools (`valor-tts`, Marp, ffmpeg). `/do-presentation` and `/do-voice-recording` are skill markdown with no Python test coverage of their own, and `valor-tts` is consumed unchanged (its `duration` field is already returned and not modified here). New tests are added for the `tools/deck_video` compositor and the `valor-deck-video` CLI.

## Rabbit Holes

- **Adopting html-video / Hyperframes** — tempting because it produces "real" animated video, but spike-1 shows no BYO-audio path and it abandons Marp + our design system. Explicitly out of scope.
- **Per-word / karaoke-style subtitle timing** — alignment of narration text to on-screen highlights is a large separate effort; the goal is slide-level timing only.
- **GSAP / animated transitions between slides** — beyond crossfade, animation needs a real render engine. Out of scope (spike-2).
- **Re-implementing TTS** — never shell out to a new speech path; reuse `valor-tts` exactly as `/do-voice-recording` documents.
- **A standalone `/do-presentation-video` skill** — a whole new skill is heavier than a `--video` flag on the existing one; resist unless the Open Question resolves toward a separate skill.

## Risks

### Risk 1: Per-slide PNG ordering / count mismatch with narration blocks
**Impact:** Audio clip *i* paired with the wrong slide image → narration desynced from slides.
**Mitigation:** Marp emits exactly **one PNG per slide** (narrated or silent), but `valor-tts` synthesizes one clip only per **narrated** slide — so for any deck containing a silent slide, `len(pngs) > len(narration_clips)`. The count invariant is therefore **slide-count parity, not clip parity**: derive the canonical slide count from the same `---`-delimited slide-split of the Marp source that produces the narration list (one narration block per slide, possibly empty), and assert `len(pngs) == total_slide_count == len(narration_blocks)` before compositing — where `narration_blocks` includes empty/silent entries (one per slide). Pair PNG *i* to narration block *i* by explicit document-order index, not by lexicographic filename sort (which scrambles past 9 slides if Marp ever drops zero-padding); a slide whose narration block is empty defaults to a silent hold (`DECK_VIDEO_DEFAULT_HOLD_SECS`) rather than expecting a clip. Sort PNGs numerically by parsed sequence number and assert filenames are zero-padded as Marp emits. Fail loudly on any `len(pngs) != total_slide_count` mismatch. A failure-path test uses a 10+ slide deck to confirm slides past index 9 stay in order, and the mixed-deck E2E fixture (narrated + silent slides) exercises the `len(pngs) > len(narration_clips)` case the old clip-parity assertion would have wrongly rejected.

### Risk 2: ffmpeg image-duration encoding quirks (variable frame timing)
**Impact:** Slides flicker, wrong durations, or A/V drift in the output MP4.
**Mitigation:** Use the `concat` demuxer with explicit per-image `duration` directives and a final-frame repeat, fixed `-r` output frame rate, `-pix_fmt yuv420p` for broad player compatibility; verify total MP4 duration ≈ `sum(floored narrated-clip durations) + (count_silent * DECK_VIDEO_DEFAULT_HOLD_SECS)` within tolerance in a test (the total-runtime formula from the Solution section, which is defined for mixed narrated/silent decks).

### Risk 3: OGG/Opus as ffmpeg audio input
**Impact:** `valor-tts` emits OGG/Opus; concatenating + re-encoding to AAC in MP4 could degrade or fail if streams differ.
**Mitigation:** Let ffmpeg decode each OGG and re-encode the concatenated track once to AAC at mux time (don't stream-copy heterogeneous inputs). Test a 2-slide deck end-to-end and assert a playable MP4 with audio.

## Race Conditions

No race conditions identified — the compositor is a synchronous, single-process pipeline (Marp export → sequential per-slide synthesis → ffmpeg mux). Each step's output files are fully written before the next step reads them; there is no shared mutable state, concurrency, or cross-process coordination.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG] Animated (GSAP/engine-driven) deck video — deferred; requires a real render engine swap and a different deck format. Not filed yet; raise as a new issue only if animation becomes a confirmed requirement.
- Nothing else deferred — the narration schema, the compositor, the CLI, the `--video` surface, and the failure-path tests are all in scope for this plan.

<!-- Note: the animated-video item has no human/world/ordered/destructive blocker; it is genuinely a larger separate effort, not laziness. If the PM wants it tracked, file an issue and convert this to [SEPARATE-SLUG #NNN]. -->

## Update System

The new capability is delivered as (a) edits to `.claude/skills-global/do-presentation/SKILL.md` and (b) a new `tools/deck_video` module with a `valor-deck-video` CLI entry point.

- **Skill propagation:** `/do-presentation` already lives in `.claude/skills-global/`, so `/update`'s `sync_claude_dirs()` hardlink wiring propagates the edited SKILL.md to every machine automatically — no registration step.
- **CLI propagation:** `valor-deck-video` is added to `pyproject.toml [project.scripts]`; it becomes available after the standard `pip install -e .` step that `/update` already runs. No new update-script logic needed.
- **New dependency:** `ffmpeg` is already required by `valor-tts` (ffprobe) and present on dev machines; document it in the feature doc's prerequisites. No new secret, config file, or service to propagate.
- **Migration:** None — additive feature, no existing installation state to migrate.

## Agent Integration

The agent reaches new functionality via a CLI entry point in `pyproject.toml [project.scripts]` (invoked through Bash) or a direct bridge import. This feature uses the CLI path.

- **New CLI entry point:** add `valor-deck-video = "tools.deck_video.cli:main"` to `pyproject.toml [project.scripts]`. The agent invokes it via Bash like `valor-tts`/`valor-ingest`.
- **Primary surface is the skill:** `/do-presentation --video` is the documented invocation; the skill orchestrates the steps and calls `valor-deck-video` (or invokes Marp + valor-tts + the compositor directly per the SKILL instructions).
- **Bridge import:** None — no bridge code change; the agent uses the CLI via Bash, consistent with `/do-presentation`'s existing export step.
- **Integration test:** an end-to-end test that runs `valor-deck-video` on a tiny 2-slide fixture deck and asserts a playable MP4 with the expected duration is produced (real ffmpeg + real valor-tts, per the no-mocks testing philosophy).

## Documentation

### Feature Documentation
- [ ] Create `docs/features/narrated-deck-video.md` describing the `--video` mode, the per-slide narration schema, the compositing pipeline, and prerequisites (ffmpeg/Marp/valor-tts).
- [ ] Add an entry to `docs/features/README.md` index table.

### External Documentation Site
- [ ] Not applicable — this repo has no Sphinx/MkDocs site for skill docs.

### Inline Documentation
- [ ] Docstrings on the `tools/deck_video` public functions (compositor entry, narration parser) and the `valor-deck-video` CLI.
- [ ] Update `.claude/skills-global/do-presentation/SKILL.md`: add the `--video` mode, the narration-block schema, a version-history entry, and the carve-out amending the "Narration / voiceover" section so `valor-deck-video` is an approved direct `valor-tts` consumer.
- [ ] Add `valor-deck-video` to the CLAUDE.md Quick Commands table.

## Success Criteria

- [ ] The plan answers the BYO-audio question with a concrete spike result (spike-1: html-video has **no** supported external-audio path; MiniMax-coupled).
- [ ] The plan picks **approach B** with explicit justification tied to spike-1 (no BYO-audio) and spike-2 (animation not required).
- [ ] The per-slide narration/timing schema is specified (per-slide `<!-- narration: ... -->` block; durations measured from `valor-tts` output, default hold for empty narration).
- [ ] The capability is surfaced as `/do-presentation --video` (plus a `valor-deck-video` CLI), and the invocation is documented.
- [ ] No new external speech dependency (MiniMax) is introduced — narration reuses `valor-tts`/Kokoro.
- [ ] `valor-deck-video` produces a playable MP4 from a fixture deck containing **both a narrated and a silent slide**: each narrated slide held for its (floored) narration duration, each silent slide held for `DECK_VIDEO_DEFAULT_HOLD_SECS`, audio muxed, total runtime ≈ `sum(floored narrated durations) + (count_silent * DECK_VIDEO_DEFAULT_HOLD_SECS)`.
- [ ] A narrated clip reporting `duration <= 0.0` (ffprobe-missing/failure simulation) is re-probed and, if still `<= 0.0`, raises — never produces a zero-length slide.
- [ ] An **all-silent deck** (every slide's narration block empty, zero audio clips) produces a playable **video-only MP4** with no audio stream and total duration ≈ `count_slides * DECK_VIDEO_DEFAULT_HOLD_SECS` — the compositor takes the zero-audio branch, never constructing an audio-referencing ffmpeg command.
- [ ] PNG-count parity is asserted as `len(pngs) == total_slide_count`, not clip parity; a mixed deck where `len(pngs) > len(narration_clips)` composites successfully.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] grep confirms `/do-presentation` SKILL references `valor-deck-video` (or the Marp+tts+compositor chain) for the `--video` path.

## Team Orchestration

The lead agent orchestrates; it never builds directly.

### Team Members

- **Builder (compositor)**
  - Name: `compositor-builder`
  - Role: Implement `tools/deck_video` (narration parser, Marp PNG export orchestration, per-slide synthesis, ffmpeg mux) and the `valor-deck-video` CLI.
  - Agent Type: builder
  - Resume: true

- **Builder (skill-wiring)**
  - Name: `skill-builder`
  - Role: Add `--video` mode + narration-block schema to `/do-presentation` SKILL.md; register the CLI in `pyproject.toml`; add the CLAUDE.md Quick Command.
  - Agent Type: builder
  - Resume: true

- **Test engineer**
  - Name: `deck-video-tester`
  - Role: Write the end-to-end MP4 test (2-slide fixture, real ffmpeg + valor-tts) and the failure-path tests (missing binary, empty narration, slide/audio mismatch).
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: `deck-video-docs`
  - Role: Create `docs/features/narrated-deck-video.md`, update the features index, docstrings.
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: `deck-video-validator`
  - Role: Verify all success criteria, run the verification checks.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Build the compositor module + CLI
- **Task ID**: build-compositor
- **Depends On**: none
- **Validates**: `tests/unit/test_deck_video.py` (create), `tests/integration/test_deck_video_e2e.py` (create)
- **Informed By**: spike-1 (no html-video adoption), spike-3 (reuse valor-tts `duration`)
- **Assigned To**: compositor-builder
- **Agent Type**: builder
- **Parallel**: true
- Implement `tools/deck_video/__init__.py`: parse per-slide `<!-- narration: ... -->` blocks from a Marp source (split on `---`), export PNGs via `npx @marp-team/marp-cli --images png`, synthesize one `valor-tts` clip per slide (reuse returned `duration`), composite via ffmpeg `concat` demuxer + AAC mux into `deck.mp4`.
- Use a named, env-overridable default-hold constant (`DECK_VIDEO_DEFAULT_HOLD_SECS`, provisional 4.0s) for empty-narration slides, with a grain-of-salt comment.
- For every per-slide `synthesize()` result, **check `res.get("error")` FIRST** and raise a descriptive `DeckVideoError` (slide index + the error string) before touching `res["duration"]`/`res["path"]` — `synthesize()` returns `{"error": ...}` with no `path`/`duration` keys on failure (`tools/tts/__init__.py:234,260,286,389,391`), so reading them on an error dict raises `TypeError`/`KeyError`. Define a `DeckVideoError` exception for the module.
- Only after confirming no error, floor every narrated clip's duration: re-probe via `ffprobe` if `valor-tts` returns `<= 0.0`; raise (clip path + slide index) if still `<= 0.0` — never emit a zero-length slide.
- Compute total runtime as `sum(floored narrated durations) + (count_silent * DECK_VIDEO_DEFAULT_HOLD_SECS)`.
- Assert PNG-count parity (`len(pngs) == total_slide_count == len(narration_blocks)`, one block per slide incl. empty) before compositing; pair PNG *i* to narration block *i* by document-order index; empty block → silent hold, not an expected clip.
- Branch the ffmpeg mux on audio presence: `if not audio_clips:` emit a **video-only MP4** (`-c:v libx264 -pix_fmt yuv420p`, no `-c:a`, no `-shortest`); otherwise mux audio + video. Never construct an audio-referencing command when zero clips exist.
- Write all intermediate artifacts (PNGs, audio clips, concat list) under one temp directory and clean it up in a `try/finally` on both success and failure, leaving only `deck.mp4`.
- Implement `tools/deck_video/cli.py:main` (`valor-deck-video`), surfacing missing-binary errors with actionable messages and non-zero exits. **Guard prerequisites at the start of `main` (and the compositor entrypoint):** check `ffmpeg`, `ffprobe`, the Marp CLI (`npx --yes @marp-team/marp-cli --version`), and `valor-tts` are resolvable before doing any work; if any is missing, emit an actionable message naming the missing tool and exit non-zero — do not begin Marp export or synthesis only to fail mid-pipeline.
- **Do NOT edit `pyproject.toml` in this task** — the `[project.scripts]` entry is owned solely by the skill-wiring task (step 2) to avoid a parallel-write conflict between the two `Parallel: true` builders. This task only creates files under `tools/deck_video/`.

### 2. Wire the skill + CLI registration
- **Task ID**: build-skill-wiring
- **Depends On**: none
- **Validates**: manual skill read; `pyproject.toml` script entry present
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `--video` mode and the narration-block schema to `.claude/skills-global/do-presentation/SKILL.md`, plus a version-history entry.
- **Amend the "Narration / voiceover" section of `do-presentation/SKILL.md` (lines 316-318)**: carve out an explicit exception stating that the `valor-deck-video` CLI is an approved direct consumer of `valor-tts` for the automated `--video` pipeline (it needs structured per-clip `duration`), while the "defer to `/do-voice-recording`" rule continues to govern the manual narration path. Removes the contradiction with the current "don't shell out to `valor-tts` here" wording.
- Add `valor-deck-video = "tools.deck_video.cli:main"` to `pyproject.toml [project.scripts]`. **This task is the sole writer of `pyproject.toml`** — the compositor task (step 1) deliberately does not touch it, so the two `Parallel: true` builders never write the file concurrently.
- Add `valor-deck-video` to the CLAUDE.md Quick Commands table.

### 3. Write tests
- **Task ID**: build-tests
- **Depends On**: build-compositor
- **Validates**: `tests/unit/test_deck_video.py`, `tests/integration/test_deck_video_e2e.py`
- **Assigned To**: deck-video-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- E2E: 2-slide fixture deck → `valor-deck-video` → assert playable MP4, total duration ≈ `sum(floored narrated-clip durations) + (count_silent_slides * DECK_VIDEO_DEFAULT_HOLD_SECS)` (within tolerance), audio stream present.
- Failure paths: missing binary message+exit; empty/whitespace narration uses default hold; **all-silent deck (zero audio clips) → video-only MP4 with no audio stream, total duration ≈ `count_slides * DECK_VIDEO_DEFAULT_HOLD_SECS`**; PNG-count parity (`len(pngs) == total_slide_count`) mismatch fails loudly — a mixed deck (narrated + silent) where `len(pngs) > len(narration_clips)` must **pass**, not be rejected; zero-slide deck errors; narrated clip with `duration <= 0.0` (ffprobe failure simulation) re-probes then raises; **per-slide `synthesize()` returning an error dict (`{"error": ...}`, no `path`/`duration`) raises a clean `DeckVideoError` with the slide index — assert it is NOT `TypeError`/`KeyError`, via monkeypatching `synthesize`**; 10+ slide deck preserves order past index 9; partial-failure run leaves no temp files behind.

### 4. Validate build + tests
- **Task ID**: validate-impl
- **Depends On**: build-compositor, build-skill-wiring, build-tests
- **Assigned To**: deck-video-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table checks; confirm the MP4 is produced and playable.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-impl
- **Assigned To**: deck-video-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/narrated-deck-video.md`; add to `docs/features/README.md` index; verify docstrings.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: deck-video-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all verification checks; confirm every success criterion (including docs and the SKILL→CLI grep).

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_deck_video.py tests/integration/test_deck_video_e2e.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check tools/deck_video` | exit code 0 |
| Format clean | `python -m ruff format --check tools/deck_video` | exit code 0 |
| CLI registered | `grep -q 'valor-deck-video' pyproject.toml && echo found` | output contains found |
| Skill wires the video path | `grep -niE 'video|valor-deck-video' .claude/skills-global/do-presentation/SKILL.md` | output > 0 |
| No MiniMax dependency introduced | `grep -rin minimax tools/deck_video pyproject.toml` | exit code 1 |
| Feature doc exists | `test -f docs/features/narrated-deck-video.md && echo found` | output contains found |

## Implementation Notes

Non-blocking, build-time guidance from the third critique pass. The plan is READY TO BUILD; these are notes for the builder, not open decisions.

1. **`---` slide-split robustness.** Parsing per-slide narration by naively splitting the Marp source on `---` is fragile: `---` also appears as YAML front-matter delimiters (the first one or two at the top of the file), Markdown horizontal rules (`---` / `***` / `___` between paragraphs), and inside fenced code blocks (` ``` `). A bare `text.split("---")` will mis-slice the deck and desync narration from PNGs. The builder should mirror Marp's own slide-boundary semantics: skip the leading YAML front-matter block, recognize a slide separator only when `---` sits on its own line in the slide-separator position (Marp treats a line of exactly `---` as a page break, not an arbitrary `---` mid-paragraph), and ignore `---` inside fenced code spans. Prefer reusing Marp's parsed slide count as the source of truth (e.g. derive `total_slide_count` from the number of emitted PNGs) and align the narration-block list to it, rather than trusting a hand-rolled split. The `len(pngs) == total_slide_count == len(narration_blocks)` assertion (Risk 1, Data Flow step 5) is the safety net that catches a mis-split early.

2. **`-shortest` can truncate a slide's tail.** `-shortest` stops the output at the end of the shortest input stream. If a slide's on-screen (video) hold is intended to be longer than its narration audio — or trailing silence is meant to be preserved — `-shortest` will cut the slide short, clipping the tail. The on-screen duration must be honored even when the audio clip is shorter: pad the audio/video to the slide's intended hold rather than truncating the video. The compositor builds the video timeline from the (floored) per-slide durations, so the slide hold is authoritative; ensure the mux does not let `-shortest` shorten a slide below its computed hold. Pad audio with trailing silence (e.g. `apad` / a generated silence segment) to match the video timeline, or drop `-shortest` and rely on the explicitly-built equal-length audio and video tracks. The total-runtime assertion (Risk 2) will catch a regression where `-shortest` clips the output.

3. **All-silent branch — drop-vs-keep is an intentional UX decision, warn the user.** The video-only branch for an all-silent deck (Data Flow step 5, Failure Path "Empty/Invalid Input") is the intended behavior: produce a playable slideshow with no audio rather than erroring. However, an all-silent deck most likely means the user forgot to add `<!-- narration: ... -->` blocks. The CLI should still produce the video-only MP4 but **emit a warning to stderr** (e.g. "No narration blocks found; producing a video-only slideshow — add `<!-- narration: ... -->` comments to narrate") so the absence of narration is surfaced, not silently swallowed. Keep-and-warn, not drop-and-fail.

4. **Prior art to reuse.** `tools/tts/__init__.py` already orchestrates `ffmpeg`/`ffprobe` via `subprocess` (duration probing in `_compute_duration_opus`, binary resolution, actionable missing-binary errors). The builder should reuse that established subprocess + binary-resolution pattern rather than inventing a new one, for consistency in error surfacing and PATH handling. Marp invocation already exists in `.claude/skills-global/do-presentation/SKILL.md:291` (`npx --yes @marp-team/marp-cli ... --allow-local-files`); reuse that exact invocation shape (adding `--images png`) rather than introducing a divergent Marp call. No other adjacent video-compositing tooling exists in the repo (confirmed in Prior Art) — this remains greenfield composition over `valor-tts` + Marp + ffmpeg.

5. **TTS framing (nit).** The plan's "single TTS engine" / "no second speech surface" wording (Solution, Success Criteria) is about not introducing a *new* speech provider (e.g. MiniMax) — `valor-tts` itself already has a Kokoro-local primary with an OpenAI tts-1 cloud fallback. "Single engine" means a single TTS *surface/tool* (`valor-tts`), not a single backend. Builder/doc writers should phrase it as "reuses the existing `valor-tts` surface" rather than implying `valor-tts` is itself a single-engine tool.

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | critique | TTS `duration=0.0` silently produces zero-length slides (`_compute_duration_opus` returns 0.0, not an exception, on ffprobe failure — `tools/tts/__init__.py:327`) | Data Flow step 4; Technical Approach "Duration floor"; spike-3 caveat | Compositor floors `duration <= 0.0`: re-probe via ffprobe, raise if still `<= 0.0`. Failure-path test added. |
| BLOCKER | critique | "total runtime ≈ sum of clip durations" undefined for mixed narrated/silent decks | Solution "Total-runtime definition"; Risk 2; Success Criteria | Redefined as `sum(floored narrated durations) + (count_silent * DECK_VIDEO_DEFAULT_HOLD_SECS)`. E2E test uses a mixed deck. |
| BLOCKER | critique | Compositor calls `valor-tts` directly, contradicting `do-presentation/SKILL.md:316-318` "defer to /do-voice-recording" | Technical Approach "Decision"; skill-wiring task; Inline Documentation | Decision: amend SKILL.md to carve out `valor-deck-video` as an approved direct `valor-tts` consumer for the automated `--video` path; manual path still defers to /do-voice-recording. |
| CONCERN | critique | Marp PNG lexicographic sort scrambles order past 9 slides | Data Flow step 3; Risk 1 | Pair by explicit document-order index; sort numerically by parsed sequence; assert zero-padding; 10+ slide ordering test. |
| CONCERN | critique | Two unresolved first-class surfaces in Open Question 1 (flag vs separate skill) | Resolved Decisions §1 | Resolved in favor of `--video` flag with rationale; no separate skill. Only the resolution/aspect preference remains deferred. |
| NIT | critique | No temp cleanup on partial failure | Failure Path "Temp File Cleanup"; compositor task; Architectural Impact | Single temp dir, `try/finally` cleanup on success and failure; test forces mid-pipeline failure and asserts temp dir gone. |
| BLOCKER (2nd pass) | critique | `assert len(pngs) == len(narration_blocks)` is wrong for mixed decks — Marp emits one PNG per slide, valor-tts one clip per *narrated* slide, so any silent slide makes the assertion always raise | Risk 1; Data Flow step 5; Technical Approach compositing; compositor task; Success Criteria | Changed invariant to slide-count parity: `len(pngs) == total_slide_count == len(narration_blocks)` (one block per slide, empty for silent); pair PNG↔slide by index; empty block → silent hold. Mixed-deck E2E fixture exercises `len(pngs) > len(narration_clips)`. |
| BLOCKER (2nd pass) | critique | All-silent deck has no defined ffmpeg muxing path — zero audio clips → broken stream / non-zero exit | Data Flow step 5; Technical Approach compositing; Failure Path "Empty/Invalid Input"; compositor task; tests; Success Criteria | Added explicit `if not audio_clips:` branch → video-only MP4 (`-c:v libx264 -pix_fmt yuv420p`, no `-c:a`/`-shortest`). Added all-silent fixture test. |
| BLOCKER (2nd pass) | critique | Plan jumps from feasibility assessment to full build with no authorization gate | New "## Authorization" section | Records that the operator invoked `/do-sdlc` end-to-end on #1726, which accepts the feasibility output (approach B) and authorizes the build; the assessment → build transition is explicit and approved. |
| CONCERN (2nd pass) | critique | No CLI prerequisite guard (ffmpeg/marp present) | Compositor task; Error State Rendering | `main`/compositor entrypoint guards ffmpeg, ffprobe, Marp CLI, valor-tts before any work; actionable message + non-zero exit if missing. |
| CONCERN (2nd pass) | critique | skill-builder over-split with pyproject.toml parallel-write conflict | Compositor task; skill-wiring task | Serialized: step 2 (skill-wiring) is the sole writer of `pyproject.toml`; step 1 (compositor) explicitly does not touch it. |
| BLOCKER (3rd pass) | critique | Synthesis loop assumes `synthesize()` always returns `{path, duration}` and only guards `duration <= 0.0`; but `synthesize()` returns `{"error": ...}` with no `path`/`duration` on failure (`tools/tts/__init__.py:234,260,286,389,391`), so the floor guard hits `None <= 0.0` → `TypeError` and `res["path"]` → `KeyError` — a mid-deck TTS failure crashes with an opaque traceback instead of a clean abort | Data Flow step 4; Technical Approach "Synthesis-error guard"; Failure Path "Error State Rendering"; compositor task; tests | Check `res.get("error")` FIRST and raise a descriptive `DeckVideoError` (slide index + error string) before reading `res["duration"]`/`res["path"]`; apply the duration floor only after. Failure-path test monkeypatches `synthesize` to return an error dict and asserts a clean `DeckVideoError`, not `TypeError`/`KeyError`. |
| NIT (4th pass) | critique | Task 3's E2E bullet still cited the stale "total duration ≈ sum of clip durations" formula, contradicting the mixed-deck definition already fixed in Solution, Risk 2, and Success Criteria | Step by Step Tasks → Task 3 (E2E bullet) | Cross-reference fix: replaced the E2E duration assertion with the mixed-deck formula `sum(floored narrated-clip durations) + (count_silent_slides * DECK_VIDEO_DEFAULT_HOLD_SECS)` verbatim, matching the other three sections. |

---

## Resolved Decisions

The critique flagged Open Question 1 (surface) as having two unresolved first-class candidates. These are now resolved as plan decisions; only one genuinely-business judgment call remains deferred (below).

1. **Surface — RESOLVED: `--video` flag on `/do-presentation`.** Chosen over a separate `/do-presentation-video` composite skill because the flag is additive, reuses the existing deck-authoring flow (the narration blocks live in the same Marp source the user already wrote), and avoids duplicating the deck-research/authoring instructions across two skills. A separate skill would have to re-derive or re-import the entire deck pipeline to add one mux step — disproportionate. The `valor-deck-video` CLI is the implementation surface; `--video` is the user-facing surface. **The two candidates are settled in favor of the flag; no separate skill is created.**
2. **Narration schema — RESOLVED: per-slide `<!-- narration: ... -->` HTML comment.** Chosen over a YAML sidecar (`deck.narration.yaml`, which would drift out of sync with the deck) and Marp presenter notes (which Marp can render into HTML output, leaking narration into static exports). The comment block is invisible to static PDF/HTML/PPTX export and trivially parseable per-slide.
3. **Empty-narration slides — RESOLVED: hold for `DECK_VIDEO_DEFAULT_HOLD_SECS` (provisional 4.0s), do not skip.** Skipping would drop the slide entirely from the video, which a viewer would read as a missing slide. Holding it silently keeps the deck visually complete. The constant is env-overridable.

## Open Questions

1. **Output resolution / aspect (deferred — genuine preference call):** match Marp's default 16:9 1280×720 PNG export, or render at 1080p? The plan defaults to Marp's native export size (1280×720) because it requires no extra Marp config and keeps PNG export fast; bump to 1080p only if the PM wants higher-fidelity output. This is the one remaining item that is a preference, not a technical decision — safe to proceed on the default and revisit if the PM objects.
