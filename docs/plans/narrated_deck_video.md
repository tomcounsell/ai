---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-06-22
tracking: https://github.com/tomcounsell/ai/issues/1726
last_comment_id:
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
- **Finding**: `valor-tts` already computes and returns `duration` per synthesized clip (`_compute_duration_opus`, ffprobe). Synthesize one clip per slide → each clip's `duration` *is* that slide's on-screen time.
- **Confidence**: high.
- **Impact on plan**: No new timing infra needed. The per-slide schema only needs to carry **narration text**; durations are measured at synthesis time.

## Data Flow

End-to-end flow for `/do-presentation --video`:

1. **Entry point**: User invokes `/do-presentation <topic> --video` (or `/do-presentation` produces a deck, then the user asks for video).
2. **Deck + notes authoring** (`/do-presentation`): Marp markdown is authored as today, **plus** a per-slide narration block (see schema below) carrying the speaker text for each slide.
3. **Slide image export** (Marp): `npx --yes @marp-team/marp-cli deck.md --images png --allow-local-files` → one PNG per slide (`deck.001.png`, `deck.002.png`, ...).
4. **Per-slide narration synthesis** (`valor-tts`, one call per slide): each slide's narration text → one OGG/Opus clip. Each call returns `{path, duration}`. Slides with empty narration get a default hold duration (configurable; provisional 4.0s).
5. **Compositing** (new `tools/deck_video` module, ffmpeg): build a concat timeline pairing PNG *i* with audio clip *i* for `duration_i` seconds; concatenate audio clips into one track; encode video (libx264) and mux the combined audio (`-c:v libx264 -c:a aac`, `-pix_fmt yuv420p`) into a single MP4.
6. **Output**: `deck.mp4` written next to the deck, reported to the user with total runtime and file path.

## Architectural Impact

- **New dependencies**: `ffmpeg` (already installed on dev machines; the only new *required* binary, and it's already a `valor-tts` dependency via ffprobe). No Node/Playwright/MiniMax. No new Python packages — orchestrate ffmpeg via `subprocess` like the rest of `tools/`.
- **Interface changes**: `/do-presentation` SKILL gains a `--video` mode and a per-slide narration schema. New `tools/deck_video` module + `valor-deck-video` CLI entry point. `valor-tts` unchanged (consumed as-is via its existing CLI/import; reuse its `duration` field).
- **Coupling**: Low. The compositor depends only on Marp PNG output (files on disk) and `valor-tts` output (files + durations). No coupling to `html-video`.
- **Data ownership**: We own the compositing logic and the narration schema. No third-party service owns any step.
- **Reversibility**: High. `--video` is additive; removing it leaves static export untouched. The new module is self-contained.

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
- **`/do-presentation --video` mode**: the user-facing surface that chains deck → PNGs → narration → mux, reusing `/do-voice-recording`'s `valor-tts` for synthesis (consistent with the existing "defer to /do-voice-recording for narration" convention).

### Flow

`/do-presentation <topic> --video` → deck authored with per-slide narration blocks → Marp exports one PNG per slide → `valor-tts` synthesizes one clip per slide (returns duration) → ffmpeg pairs each PNG with its clip for that clip's duration and concatenates → **`deck.mp4` written + reported with total runtime**.

### Technical Approach

- **Approach B (in-house ffmpeg slideshow)** — selected over A per spike-1/spike-2. Keeps Marp + the repo design system, introduces no new deck format, no MiniMax, no Node/Playwright. ffmpeg is the only new binary and is already present.
- **Narration schema**: a per-slide HTML comment block in the Marp markdown, e.g.

  ```markdown
  <!-- narration: Revenue grew 25% this quarter, driven by the new onboarding flow. -->
  ```

  Chosen because Marp ignores HTML comments in rendered output (so static PDF/HTML/PPTX are unaffected) and they are trivially parseable per-slide by splitting on the `---` slide delimiter. Empty/missing narration → configurable default hold (provisional 4.0s, named env-overridable constant per the magic-number convention).
- **Compositing**: synthesize one audio clip per slide; measure each clip's `duration` (reuse `valor-tts`'s returned value). Build the video by holding each PNG for its clip's duration, concatenate the audio clips into one track, then mux: `ffmpeg ... -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest deck.mp4`. Prefer ffmpeg's `concat` demuxer with a per-image duration list for robustness over a single complex `filter_complex`.
- **Integration points**: `valor-tts` (synthesis + duration), `npx @marp-team/marp-cli --images png` (slide PNGs), `/do-presentation` SKILL (authoring + `--video` invocation), new `valor-deck-video` CLI in `pyproject.toml`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The compositor must not swallow ffmpeg/Marp/tts failures silently. Any non-zero subprocess exit raises with stderr surfaced. Test: feed a deck with a deliberately broken Marp export and assert the error propagates (not a zero-byte MP4).
- [ ] No bare `except Exception: pass` in the new module; each subprocess call asserts return code and logs stderr on failure.

### Empty/Invalid Input Handling
- [ ] Slide with empty/missing narration → uses the default hold duration, not a crash and not a zero-length clip. Test asserts the slide appears for the default duration with silence.
- [ ] Deck with zero slides, or a narration block but no slides → clear error, no partial MP4. Test asserts a non-zero exit and a descriptive message.
- [ ] Narration text that is whitespace-only → treated as empty (default hold). Test covers this.

### Error State Rendering
- [ ] If `ffmpeg`/`ffprobe`/Marp/`valor-tts` is missing, the CLI emits an actionable message naming the missing tool and exits non-zero (mirrors `/do-voice-recording`'s binary-resolution pattern). Test asserts the message and exit code via a PATH-stripped invocation.

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
**Mitigation:** Derive both the PNG list and the narration list from the same slide-split of the Marp source, in document order; assert `len(pngs) == len(narration_blocks)` before compositing and fail loudly on mismatch.

### Risk 2: ffmpeg image-duration encoding quirks (variable frame timing)
**Impact:** Slides flicker, wrong durations, or A/V drift in the output MP4.
**Mitigation:** Use the `concat` demuxer with explicit per-image `duration` directives and a final-frame repeat, fixed `-r` output frame rate, `-pix_fmt yuv420p` for broad player compatibility; verify total MP4 duration ≈ sum of clip durations within tolerance in a test.

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
- [ ] Update `.claude/skills-global/do-presentation/SKILL.md`: add the `--video` mode, the narration-block schema, and a version-history entry.
- [ ] Add `valor-deck-video` to the CLAUDE.md Quick Commands table.

## Success Criteria

- [ ] The plan answers the BYO-audio question with a concrete spike result (spike-1: html-video has **no** supported external-audio path; MiniMax-coupled).
- [ ] The plan picks **approach B** with explicit justification tied to spike-1 (no BYO-audio) and spike-2 (animation not required).
- [ ] The per-slide narration/timing schema is specified (per-slide `<!-- narration: ... -->` block; durations measured from `valor-tts` output, default hold for empty narration).
- [ ] The capability is surfaced as `/do-presentation --video` (plus a `valor-deck-video` CLI), and the invocation is documented.
- [ ] No new external speech dependency (MiniMax) is introduced — narration reuses `valor-tts`/Kokoro.
- [ ] `valor-deck-video` produces a playable MP4 from a 2-slide fixture: each slide held for its narration duration, audio muxed, total runtime ≈ sum of clip durations.
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
- Use a named, env-overridable default-hold constant (provisional 4.0s) for empty-narration slides, with a grain-of-salt comment.
- Implement `tools/deck_video/cli.py:main` (`valor-deck-video`), surfacing missing-binary errors with actionable messages and non-zero exits.

### 2. Wire the skill + CLI registration
- **Task ID**: build-skill-wiring
- **Depends On**: none
- **Validates**: manual skill read; `pyproject.toml` script entry present
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `--video` mode and the narration-block schema to `.claude/skills-global/do-presentation/SKILL.md`, plus a version-history entry.
- Add `valor-deck-video = "tools.deck_video.cli:main"` to `pyproject.toml [project.scripts]`.
- Add `valor-deck-video` to the CLAUDE.md Quick Commands table.

### 3. Write tests
- **Task ID**: build-tests
- **Depends On**: build-compositor
- **Validates**: `tests/unit/test_deck_video.py`, `tests/integration/test_deck_video_e2e.py`
- **Assigned To**: deck-video-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- E2E: 2-slide fixture deck → `valor-deck-video` → assert playable MP4, total duration ≈ sum of clip durations (within tolerance), audio stream present.
- Failure paths: missing binary message+exit; empty/whitespace narration uses default hold; slide/audio count mismatch fails loudly; zero-slide deck errors.

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
| Skill wires the video path | `grep -niE 'video\|valor-deck-video' .claude/skills-global/do-presentation/SKILL.md` | output > 0 |
| No MiniMax dependency introduced | `grep -rin minimax tools/deck_video pyproject.toml` | exit code 1 |
| Feature doc exists | `test -f docs/features/narrated-deck-video.md && echo found` | output contains found |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Surface decision:** `--video` flag on `/do-presentation` (recommended — additive, reuses authoring) vs. a separate `/do-presentation-video` composite skill. The plan assumes the flag. Confirm?
2. **Narration schema shape:** per-slide `<!-- narration: ... -->` HTML comment (recommended — invisible to static export, trivially parseable) vs. a YAML sidecar file (`deck.narration.yaml`) vs. Marp HTML-comment "presenter notes". Confirm the comment-block approach?
3. **Default hold for slides with no narration:** provisional 4.0s. Acceptable, or should slides without narration be skipped from the video entirely?
4. **Output resolution / aspect:** match Marp's default 16:9 1280×720 PNG export, or a higher resolution (1080p)? Default assumed is Marp's native export size.
