---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-07-30
tracking: https://github.com/tomcounsell/ai/issues/2474
last_comment_id: none
---

# /watch Skill + Scoping Controls for valor-video-watch

## Problem

Someone drops a 40-minute conference talk in chat and asks "what's the architecture diagram he shows around 22 minutes?"

Today the system does two things, neither of which answers that. The cheap **push tier** (`bridge/enrichment.py`) pulls captions and injects a wall of transcript text — the diagram is not in the transcript. The expensive **pull tier** (`valor-video-watch`) can extract frames, but nothing tells the agent to reach for it, and if it does, it reads the *first* 1800 seconds and evenly subsamples to 60 frames. That is one frame every 30 seconds. The 22-minute mark gets one frame, maybe, at 512px. The diagram is unreadable and possibly not even captured.

**Current behavior:**

1. **Nothing encodes when to escalate.** The only automatic nudge is a signpost string appended when a transcript comes back under `VIDEO_WATCH_THIN_TRANSCRIPT_CHARS` (`tools/video_watch/constants.py:52`, consumed at `bridge/enrichment.py:186`). A narrated slide deck produces a long transcript and no signal at all. There is no `/watch` skill anywhere — not in `.claude/skills-global/`, not in `.claude/skills/`, not in `~/.claude/skills/`. Escalation depends on the agent noticing a row in `CLAUDE.md`'s command table.
2. **No way to ask about a moment.** `tools/video_watch/cli.py` accepts a URL, an optional question, and `--json`. Nothing else.
3. **No way to read small on-screen text.** `VIDEO_WATCH_FRAME_WIDTH` is pinned at 512px, env-overridable but not per-invocation.
4. **One cost tier.** `_extract_scene_frames` is the only extraction strategy in the package. No cheap first pass, no exhaustive pass.
5. **Local files are rejected.** `detect_source()` classifies URLs only; a screen recording on disk falls through to yt-dlp and fails.

**Desired outcome:**

A `/watch` skill exists that encodes the escalation judgment, and the CLI it wraps has the controls that judgment needs: a time window, a frame width, a cost mode, and local-file input. "What does the diagram at 22:00 show?" returns dense frames from 22:00, labeled with timestamps that match the source video.

## Freshness Check

**Baseline commit:** `5d9515671103cea1c312b47f0d3e8f4218ade698`
**Issue filed at:** 2026-07-30T07:14:56Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `tools/video_watch/constants.py:52` — `VIDEO_WATCH_THIN_TRANSCRIPT_CHARS` — still holds (issue said line 57; the constant is defined at line 52, its comment block starts at 49). Minor line-number correction only.
- `tools/video_watch/cli.py` — accepts exactly `url`, `question`, `--json` — still holds (lines 79-91).
- `tools/video_watch/pipeline.py::detect_source` — classifies URLs only, returns `youtube`/`x`/`other` — still holds (lines 51-65).
- `tools/video_watch/pipeline.py::_extract_scene_frames` — `-t` placed as an input option before `-i`, so a seek slots in beside it — still holds (lines 199-215).
- `tools/video_watch/pipeline.py` transcription gate — compares total `duration` against `VIDEO_WATCH_MAX_DURATION` — still holds (line 404).
- `_extract_audio` — no `-ss`/`-t` bounds at all — still holds (lines 147-161).
- Frame filenames `frame_%05d.jpg` then `sorted(frames_dir.glob(...))` — still holds (lines 214, 230).
- Persisted frames `f"frame_{i:03d}_..."` — still holds (line 385).
- `watch_video` docstring claims transcript is "timestamp-prefixed when available" — still holds (line 333); `transcribe_audio_file` returns plain text.
- `scripts/update/hardlinks.py::_sync_skills` iterates `skills-global/` for `SKILL.md` — still holds; no registration step, no `RENAMED_REMOVALS` entry needed for a net-new skill.

**Cited sibling issues/PRs re-checked:**
- #1920 / PR #1953 — closed/merged 2026-07-10. Built `valor-video-watch`. Frame-extraction parity achieved; out of scope here.
- #1951 / PR #2054 — closed/merged 2026-07-13. Swapped Whisper backend to Groq. Out of scope here.
- #1371 / PR #1372 — merged 2026-05-10. `valor-youtube-transcribe` (push tier). Untouched by this work.

**Commits on main since issue was filed (touching referenced files):**
`git log --since=2026-07-30T07:14:56Z -- tools/video_watch/ bridge/enrichment.py .claude/skills-global/ scripts/update/hardlinks.py` returns **nothing**. The issue was filed today and no relevant commit has landed.

**Active plans in `docs/plans/` overlapping this area:** none. The three most recent (`hook-registration-manifest-dispatcher`, `destructive-git-shared-checkout-guardrail`, `merge-guard-cross-repo-cwd-blind-spot`) touch hooks, git guardrails, and merge gating — no overlap with video tooling or the skills-global surface.

**Notes:** No drift. Every recon claim in the issue verified against current main.

## Prior Art

- **Issue #1920 / PR #1953**: "Add a frames-capable 'watch' path for YouTube/video links (visual grounding)" — merged 2026-07-10. Built the whole `tools/video_watch/` package by deliberately adopting `claude-video`'s balanced defaults (scene-change extraction, 16x16 grayscale MAD dedup, 60-frame cap). **Succeeded.** This plan builds directly on top of it and does not revisit the extraction algorithm.
- **Issue #1951 / PR #2054**: "Swap Whisper transcription backend to Groq (whisper-large-v3)" — merged 2026-07-13. **Succeeded.** Transcription cost parity already achieved; out of scope.
- **Issue #1371 / PR #1372**: `valor-youtube-transcribe` CLI + agent guidance — merged 2026-05-10. Established the push tier. **Succeeded.** Untouched here.
- **Issue #1726**: "Assess: can nexu-io/html-video render /do-presentation decks as narrated video" — unrelated (video *generation*, not analysis).

No prior attempt at a time window, cost mode, frame-width flag, local-file input, or a `/watch` skill. This is additive greenfield work on a shipped foundation. **No "Why Previous Fixes Failed" section — nothing has failed here.**

## Research

**Queries used:**
- `ffmpeg showinfo vs metadata=print filter sidecar pts_time extract frames`

**Key findings:**

- **`metadata=print:file=` only emits keys already present in frame metadata** ([ffmpeg-filters docs](https://ffmpeg.org/ffmpeg-filters.html), corroborated by the [ffmpeg-python frame-timestamp discussion](https://github.com/kkroening/ffmpeg-python/issues/759)). The `pts_time` lines the current pipeline parses exist only because `gt(scene,...)` sets `lavfi.scene_score`. Any select expression that drops the `scene` term silently produces an **empty sidecar** — no error, no warning. This is a direct hazard for the planned keyframe mode and is confirmed empirically in spike-4.
- **`showinfo` prints unconditionally**, one line per frame, at log level `info` ([showinfo reference](https://hhsprings.bitbucket.io/docs/programming/examples/ffmpeg/video_data_visualization/showinfo.html)). It writes to stderr rather than a file, so it must be captured and regexed — the tradeoff for mode-independence. The pipeline already captures stderr (`capture_output=True`), so this costs nothing structurally.
- **The image2 muxer's `frame_pts` option puts a timestamp into the filename**, but the value is in the *filter-output* timebase, not milliseconds — usable only if you also know the timebase. Confirmed unreliable as a primary timestamp source in spike-3.

These findings drove the central technical decision below: **replace `metadata=print` with `showinfo` as the single timestamp source across all three cost modes.**

Sources:
- [How to get frames & their timestamps — frame-pts option? (ffmpeg-python #759)](https://github.com/kkroening/ffmpeg-python/issues/759)
- [FFmpeg Filters Documentation](https://ffmpeg.org/ffmpeg-filters.html)
- [showinfo — ffmpeg examples](https://hhsprings.bitbucket.io/docs/programming/examples/ffmpeg/video_data_visualization/showinfo.html)

## Spike Results

All five spikes ran against a synthesized 300s fixture (`testsrc` at 10fps with a hard luma cut every 5s via `negate=enable='lt(mod(t,10),5)'`) on ffmpeg 8.1, in an isolated worktree. Nothing was committed.

### spike-1: Does input seek make reported frame timestamps window-relative?
- **Assumption**: "Seeking before `-i` shifts ffmpeg's reported timestamps so the seek point becomes ~0" (the issue's first pre-requisite hazard).
- **Method**: prototype
- **Finding**: **Confirmed, and worse than stated.** With `-ss 120` before `-i`, sidecar `pts_time` restarts at 0 (max 175 for a 300s file = 295 − 120). With `-ss` *after* `-i`, timestamps stay absolute — but the sidecar then contains **60 entries for 36 written files**, because output seek filters after the metadata tap. Positional zip of sidecar rows to files silently pairs the wrong timestamp to the wrong frame. Output seek also cost ~9x the CPU (0.11s vs 0.02s real seeking to 280s), and the gap scales with seek depth and resolution.
- **Confidence**: high
- **Impact on plan**: Use **input seek** (`-ss` before `-i`) and add the window offset back to every parsed timestamp. Output seek is rejected on both cost and the sidecar-desync correctness trap. The 1:1 sidecar↔file invariant holds only under input seek and must be asserted in code.

### spike-2: Is `-t` after an input seek a duration or an absolute end time?
- **Assumption**: "`-t` means duration-from-seek-point, so `--end` must be converted to `end - start`."
- **Method**: prototype
- **Finding**: Confirmed a duration. `-ss 120 -t 60` yielded 12 frames spanning `pts_time` 0–55, i.e. exactly the absolute 120–180s span. Compare `-ss 120 -t 1800`: 36 frames, max 175.
- **Confidence**: high
- **Impact on plan**: `--end` is converted to `duration = end - start` before being handed to ffmpeg. The existing `VIDEO_WATCH_MAX_DURATION` cap becomes `min(requested_duration, VIDEO_WATCH_MAX_DURATION)`.

### spike-3: Do `-frame_pts` filenames sort correctly with `sorted(glob(...))`?
- **Assumption**: "`%05d` values at or above 100000 sort incorrectly" (the issue's blast-radius note — flagged as "verify rather than assume").
- **Method**: prototype
- **Finding**: **Confirmed, and reachable inside the existing cap.** The filename number is the frame's pts in the *filter-output* timebase — for CFR input that is `pts_time * fps`, i.e. the frame index. `%05d` is plain printf: values ≥ 100000 print as **6 digits, no truncation, no error**. `frame_100000.jpg` sorts lexicographically *before* `frame_95000.jpg`. Boundary: ~3333s at 30fps (~56 min), **~1667s at 60fps (~28 min) — inside the current `-t 1800` cap.** This is a live pre-existing bug, not a theoretical one.
- **Confidence**: high
- **Impact on plan**: Sort frame files by parsed integer, never `sorted(glob(...))`. Fixed as part of this work (the window feature makes deep offsets and high frame counts far more reachable).

### spike-4: Does keyframe extraction work with the existing `metadata=print` sidecar?
- **Assumption**: "A keyframe cost mode can reuse the existing timestamp-recovery path."
- **Method**: prototype
- **Finding**: **Invalidated.** Both `-skip_frame nokey` and `select='eq(pict_type\,I)'` correctly produce keyframes (60 each on the fixture), but `metadata=print` emits **zero** entries for either — it only prints frames carrying metadata, and today's `pts_time` lines come from `lavfi.scene_score` set by the `scene` expression. Additionally, stacking `-skip_frame nokey` with the scene select collapsed 60 frames to 2, because scene scores are computed against the previous *kept* frame.
- **Confidence**: high
- **Impact on plan**: Keyframe mode uses `select='eq(pict_type\,I)'` in the `-vf` chain (not the `-skip_frame` decoder flag), and the timestamp source moves off `metadata=print` entirely — see spike-5.

### spike-5: Can `showinfo` serve as a single mode-independent timestamp source?
- **Assumption**: "`showinfo` prints unconditionally and preserves the 1:1 frame↔timestamp invariant in all three modes."
- **Method**: prototype
- **Finding**: **Confirmed. Unify on `showinfo`.** Exact 1:1 in all three modes under `-ss 120 -t 60`: scene → 12 files / 12 `pts_time`; keyframe → 12 / 12; exhaustive (`fps=1`) → 60 / 60. Values are window-relative exactly as with `metadata=print`, so one `+offset` correction applies uniformly. Placement before vs after `scale` does not change values or counts (after `scale` is preferred so the logged `s:WxH` matches the written JPEG). Four gotchas surfaced:
  - Count `pts_time:` occurrences, **not** `Parsed_showinfo` lines — showinfo emits a second unconditional color-metadata line per frame, so line-counting over-counts 2x.
  - The filter index is baked into the tag (`[Parsed_showinfo_1]` before `scale`, `_2` after). Anchor the regex on `pts_time:`.
  - showinfo's `pts:` field and the `-frame_pts` filename number are in **different timebases** (input tb 1/10240 vs output tb 1/fps). Join sidecar rows to files by the `n:` ordinal, never by `pts:`.
  - **`-loglevel error` and `-loglevel warning` suppress showinfo entirely** while still writing every JPEG — a silent failure that yields untimestamped frames. Production must not lower the log level below `info`.
  - stderr volume: ~427 B/frame. A 1800s window at `fps=1` projects to ~750 KB. Pass `-hide_banner`, and strip `Parsed_showinfo` lines before surfacing an error message (collapses a 60-frame mode-C run from 24825 B to 1512 B).
- **Confidence**: high
- **Impact on plan**: This is the load-bearing design decision. `metadata=print` is removed; `showinfo` becomes the sole timestamp source for all modes. A hard assertion (`pts_time count == file count`) guards the loglevel silent-failure mode.

**Verified command shape** (all three modes differ only in `{SELECT}`):

```
ffmpeg -nostdin -hide_banner -ss {START} -t {DURATION} -i VIDEO \
  -vf "{SELECT},scale={WIDTH}:-2,showinfo" \
  -vsync vfr -frame_pts true -qscale:v 3 FRAMES_DIR/frame_%05d.jpg
```

No `-loglevel` flag. `{SELECT}` is `select='eq(n\,0)+gt(scene\,{THRESH})'` (scene) | `select='eq(pict_type\,I)'` (keyframe) | `fps=1` (exhaustive). Absolute time = `pts_time + START`.

## Data Flow

**Windowed pull-tier request, end to end:**

1. **Entry point**: A human asks about a moment in a video. The agent loads `/watch`, which reads `.claude/skill-context/watch.md`, decides the transcript is insufficient, derives a window from the question, and picks a cost mode.
2. **`tools/video_watch/cli.py`**: parses `--start`/`--end`/`--frame-width`/`--mode`, converts the window to `(start_seconds, duration_seconds)`, validates the range, classifies the positional source as URL or local path, and calls `watch_video(...)`.
3. **`pipeline.watch_video`**: acquires media — `_download_video` for a URL, or a direct path bind for a local file (download step never invoked). Probes duration via `_probe_duration`.
4. **Window clamping**: the requested window is intersected with the actual media duration and capped at `VIDEO_WATCH_MAX_DURATION`. The effective `(start, duration)` is recorded in the result payload.
5. **`_extract_frames`** (renamed from `_extract_scene_frames`): builds the mode-specific `-vf` chain, runs ffmpeg with input seek, parses `pts_time` from **stderr** (`showinfo`), asserts a 1:1 count against written files, sorts files by parsed integer, and adds `start` back to every timestamp to yield **absolute source-video times**.
6. **Dedup + cap**: `_dedup_frames` runs unless exhaustive mode relaxes it; `_subsample` enforces `VIDEO_WATCH_MAX_FRAMES` in **every** mode.
7. **`_extract_audio`**: receives the same `(start, duration)` and bounds the audio track, so transcript and frames cover the same span. The transcription length gate evaluates the **effective window duration**, not the total video duration.
8. **Output**: frames are persisted to `output_dir` with zero-padding wide enough for the frame cap, and the result dict carries `window` and `input_kind` fields so a consumer can tell absolute from relative timestamps. The CLI renders human or JSON output; the skill instructs the agent to `Read` each JPEG in order.

## Architectural Impact

- **New dependencies**: none. yt-dlp, ffmpeg, and Pillow are already present. The issue's constraint holds.
- **Interface changes**: `watch_video()` gains keyword-only parameters (`start`, `end`, `frame_width`, `mode`). `_extract_scene_frames` is renamed to `_extract_frames` and gains parameters. `detect_source()` gains a sibling classifier for local paths. The result dict gains `window` and `input_kind` keys — additive, existing consumers unaffected.
- **Coupling**: unchanged and deliberately so. `tools/video_watch/constants.py` remains `os`-only and stays the sole bridge-facing seam; any new mode/knob table added there must respect that (enforced by `tools/video_watch/tests/test_import_discipline.py`). The pipeline's only caller stays the CLI.
- **Data ownership**: unchanged. Frames still land in a temp dir reaped by `tools/video_watch/reaper.py`.
- **Reversibility**: high for Part A (additive flags with existing defaults preserved). High for Part B (deleting a skill directory plus its context file is a clean revert; `RENAMED_REMOVALS` would need an entry only on a later move).

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (the local-file security boundary and the cost-mode naming are the two decisions worth confirming)
- Review rounds: 1

Two coupled halves with a hard ordering dependency, three correctness pre-requisites that must land with the window feature rather than after it, and a first-of-its-kind test surface (`cli.py` has zero coverage today). Not Small. Not Large — the extraction algorithm, dedup, cap, and reaper all stay as-is.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ffmpeg` on PATH | `ffmpeg -version` | Frame + audio extraction; the whole feature |
| `ffprobe` on PATH | `ffprobe -version` | Duration probing and test fixture assertions |
| `yt-dlp` on PATH | `yt-dlp --version` | URL media acquisition (unchanged path) |
| Pillow importable | `python -c "import PIL"` | Frame dedup and the frame-width dimension assertions |

## Solution

### Key Elements

- **Window controls** — `--start` / `--end` accepting `MM:SS`, `HH:MM:SS`, or raw seconds, applied consistently to frame extraction *and* audio transcription, with absolute source-video timestamps preserved.
- **Timestamp source unification** — `showinfo` replaces `metadata=print`, giving one mode-independent timestamp path with a hard 1:1 invariant assertion.
- **Frame-width override** — a per-invocation pixel-width flag, named for what it controls.
- **Cost modes** — three named extraction strategies with the frame cap in force in all of them.
- **Local-file input** — cwd-independent classification, download step bypassed, clean failure on a missing path.
- **The `/watch` skill** — the escalation judgment the code cannot make, living in `.claude/skills-global/watch/` with its repo specifics in `.claude/skill-context/watch.md`.

### Flow

Chat message with a video link → push tier injects transcript automatically → human asks a visual question → **agent loads `/watch`** → skill reads the context file and decides transcript is insufficient → derives a window from the question and picks a cost mode → **runs the CLI with `--start`/`--end`/`--mode`/`--frame-width`** → CLI prints frame paths with absolute `t=MM:SS` markers → **agent `Read`s each JPEG in order** → answers the question grounded in what is on screen.

### Technical Approach

**Part A gates Part B.** The skill would teach flags that do not exist; sequence accordingly.

**A1 — Time-parsing helper.** A pure function converting `MM:SS` / `HH:MM:SS` / raw-seconds strings to float seconds. Rejects negatives, malformed input, and `start >= end`. Pure and trivially testable; lives in `pipeline.py` (or a small `timespec.py`) — **not** in `constants.py`, which must stay `os`-only.

**A2 — Window plumbing.** `watch_video()` gains keyword-only `start`/`end`. The effective window is `(start, min(end - start, VIDEO_WATCH_MAX_DURATION))`, further intersected with the probed media duration. It is threaded to both `_extract_frames` and `_extract_audio` and recorded in `result["window"]`.

**A3 — Timestamp correctness (pre-requisite, not optional).** Per spike-1 and spike-5:
- Input seek (`-ss` before `-i`) for cost, with `+start` added back to every parsed timestamp so reported times are **absolute positions in the source video**.
- `metadata=print` → `showinfo`, parsed from stderr with a regex anchored on `pts_time:` (not on the `Parsed_showinfo` tag, whose filter index shifts, and not by counting lines, which over-counts 2x).
- Assert `pts_time count == written file count`; raise `VideoWatchError` on mismatch rather than emitting untimestamped frames. This is the guard against the `-loglevel` silent-failure mode.
- No `-loglevel` flag on the frame-extraction command. Add `-hide_banner`. Strip `Parsed_showinfo` lines from stderr before putting it in an error message.
- Sort frame files by **parsed integer**, never `sorted(glob(...))` (spike-3).

**A4 — Audio windowing (pre-requisite).** `_extract_audio` gains `-ss {start} -t {duration}` as input options (spike-5 item 6 confirmed placement). The transcription length gate at `pipeline.py:404` changes from `duration > VIDEO_WATCH_MAX_DURATION` to comparing the **effective window duration**, so a 2-hour video with a 2-minute window transcribes normally.

**A5 — Frame width.** `--frame-width N` (px), threaded as a **keyword** argument into `_extract_frames`, defaulting to `VIDEO_WATCH_FRAME_WIDTH`. Named for the pixel width it controls — no "resolution class" abstraction.

**A6 — Cost modes.** `--mode {scene,keyframe,exhaustive}`, default `scene` (today's behavior, bit-for-bit).
- `scene` — `select='eq(n\,0)+gt(scene\,{THRESH})'` (unchanged).
- `keyframe` — `select='eq(pict_type\,I)'`. **Not** `-skip_frame nokey` (spike-4: decoder-level flag, poisons scene scores, footgun).
- `exhaustive` — `fps=1` fixed grid (spike-5: count predictable from window duration, independent of source fps).

Deliberately **not** named "efficient" — per the issue's Revised bucket, keyframe extraction saves decode time, not necessarily frames. **`VIDEO_WATCH_MAX_FRAMES` remains in force in every mode**; no mode may uncap it. Exhaustive mode relaxes deduplication (that is the escape hatch the issue calls for) but still passes through `_subsample`.

**A7 — Local-file input.** A `classify_input(value) -> ("url" | "local", resolved)` function, cwd-independent by construction: local iff the value starts with `file://`, `/`, or `~`. A bare relative `clip.mp4` is **rejected** with a message telling the caller to pass an absolute path or a `file://` URI — this is what makes classification cwd-independent, per the issue's Revised bucket. `~` is expanded (nothing in the package does today). A nonexistent local path fails cleanly *before* the download step. The X-specific Grok step is already gated on source type and is skipped for free. `result["input_kind"]` records the classification.

**A8 — Persisted-frame padding.** `f"frame_{i:03d}_..."` widens to accommodate `VIDEO_WATCH_MAX_FRAMES`. Derive the width from the cap rather than hardcoding a new magic number.

**A9 — Docstring correction.** `watch_video`'s "timestamp-prefixed when available" claim about the transcript is false (`transcribe_audio_file` returns plain text). Correct the docstring — do not make it true; adding transcript timestamps is a separate concern.

**B1 — The `/watch` skill.** `.claude/skills-global/watch/SKILL.md`. Because it references a `valor-`prefixed command family, `rule_13_coupling_signals` requires the exact probe sentence — the invariant suffix is *"exists, read it and honor its declarations; otherwise use the generic defaults described below."* No `valor-`prefixed name may appear in the body or any bundled sub-file; those live in `.claude/skill-context/watch.md`. Frontmatter must satisfy rules 03/04/05/11/12 (name matching the dir, a trigger phrase in the description, a length budget, known fields only, `argument-hint` present). Generic baseline in a foreign repo: describe the escalation judgment and the read-frames-in-order protocol without assuming any specific CLI is installed.

**B2 — The context file.** `.claude/skill-context/watch.md` declares the concrete `valor-video-watch` invocation, every flag and its accepted formats, the `--json` payload shape including the new `window`/`input_kind` fields, the push-tier interaction (a transcript may already be in context — do not re-fetch it), the local-file constraint, and the `docs/features/video-watch-visual-grounding.md` cross-reference.

**Infrastructure:** no INFRA doc. No new dependencies, services, API keys, quotas, or deployment changes — the ffmpeg/yt-dlp/Pillow prerequisites are pre-existing and already documented in the feature doc's Prerequisites section.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `pipeline.py::_probe_duration` — `except (subprocess.SubprocessError, ValueError)` logs a warning and returns `None`. Existing behavior; assert the windowed path degrades sanely when duration is unknown (window applied as requested, no crash).
- [ ] `pipeline.py::_dedup_frames::thumb` — `except Exception` logs and returns `None`, keeping the frame. Existing; add a case covering exhaustive mode with dedup relaxed.
- [ ] `pipeline.py::watch_video` — `except Exception` around transcription appends a note. Existing; assert the note still appears when a **windowed** transcription fails.
- [ ] `cli.py::main` — `except Exception` around `reap_stale_frame_dirs()` prints a warning to stderr and continues; `except Exception` around `asyncio.run` prints and exits 1. Both currently **untested** — `cli.py` has zero coverage. Add tests asserting the observable behavior (stderr text, exit code) rather than just the absence of a traceback.
- [ ] **New**: the 1:1 `pts_time`/file-count assertion must raise `VideoWatchError` and be surfaced as a note, never swallowed. Test with a stderr fixture missing `pts_time` lines (simulating the `-loglevel` silent-failure mode).

### Empty/Invalid Input Handling
- [ ] Empty/whitespace source → existing "URL is required." path preserved; assert it still holds for the renamed/classified input.
- [ ] `--start`/`--end` with: empty string, `abc`, `-5`, `99:99:99`, `10:00` with `--end 5:00` (start >= end), `--end` without `--start`, `--start` without `--end`. Each asserts a non-zero exit and a clear message.
- [ ] `--frame-width 0`, negative, and non-integer → rejected with a clear message.
- [ ] `--mode bogus` → argparse choices rejection, exit code 2.
- [ ] Local path that does not exist, and a path that is a directory → clean failure, download step never invoked (assert `_download_video` not called).
- [ ] Relative local path (`clip.mp4`) → rejected with the "pass an absolute path or file:// URI" message; assert the rejection is identical regardless of cwd.
- [ ] Window entirely beyond the media duration → clear note, no crash, no frames rather than a traceback.

### Error State Rendering
- [ ] `_format_human` with a result carrying `window` and `input_kind` renders them; with zero frames renders "Frames: none" (existing).
- [ ] Failure path emits the error to stderr and exits 1, with `--json` still printing the payload first (existing `cli.py:106-111` behavior — currently untested).
- [ ] ffmpeg stderr surfaced in a `VideoWatchError` message must have `Parsed_showinfo` lines stripped, so the real error is not buried under ~750 KB of frame logs (spike-5 item 5). Assert on a synthetic stderr fixture.

## Test Impact

- [ ] `tools/video_watch/tests/test_watch.py::test_youtube_happy_path` — UPDATE: `fake_frames(video_path, workdir)` has a fixed 2-positional signature and will `TypeError` once `_extract_frames` is called with the new keyword arguments. Update the fake to accept `**kwargs`.
- [ ] `tools/video_watch/tests/test_watch.py::test_audio_extracted_before_transcription` — UPDATE: same fake-signature issue for `fake_frames`; additionally `fake_extract_audio(video_path, workdir)` must accept the new window keywords.
- [ ] `tools/video_watch/tests/test_watch.py::test_oversized_duration_skips_transcription_with_exact_note` — UPDATE: this test asserts the **old** gate semantics (total duration > `VIDEO_WATCH_MAX_DURATION` skips transcription). Under A4 the gate evaluates the effective window, so the test must be re-expressed as "no window requested, total duration over the cap" and a **new** sibling added for "long video, short window → transcribes normally". The exact note string `[audio too long to transcribe — frames only]` is plan-committed and must survive.
- [ ] `tools/video_watch/tests/test_watch.py::test_oversized_audio_bytes_skips_transcription_with_exact_note` — UPDATE: `fake_extract_audio` signature only; the byte-ceiling gate is unchanged.
- [ ] `tools/video_watch/tests/test_watch.py::test_silent_video_emits_frames_without_transcript` — UPDATE: `fake_frames` and `fake_extract_audio` signatures.
- [ ] `tools/video_watch/tests/test_watch.py::test_x_download_fails_falls_back_to_grok` — UPDATE: verify the Grok path is still reached for X URLs after `classify_input` is introduced (source detection must not regress).
- [ ] `tools/video_watch/tests/test_watch.py::test_detect_source` — UPDATE: extend the parametrization with local-path cases now that classification is two-stage.
- [ ] `tools/video_watch/tests/test_watch.py::test_subsample_caps_and_preserves_span` — no change expected; re-run to confirm the cap still applies in all three modes.
- [ ] `tools/video_watch/tests/test_import_discipline.py` — no change expected, but it is the **guard** on the `constants.py` `os`-only rule. Any mode table added there must keep all five tests green. Re-run explicitly.
- [ ] `tools/video_watch/tests/test_e2e_visual_grounding.py` — UPDATE if it stubs extraction; verify against the new signatures.
- [ ] `tests/unit/test_enrichment_watch_signpost.py` — verify only. The signpost consumes `WATCH_CLI_NAME` and `VIDEO_WATCH_THIN_TRANSCRIPT_CHARS`, neither of which changes. Confirm no regression.
- [ ] **NEW** `tools/video_watch/tests/test_cli.py` — CREATE: `cli.py` has zero coverage today. Cover flag parsing, every validation error, exit codes, and both output formats.
- [ ] **NEW** ffmpeg argument-vector assertion — CREATE: every extraction test currently patches the subprocess away, so nothing would catch a misplaced `-ss`, a missing `-t`, a stray `-loglevel`, or the wrong `{SELECT}`. Add a test that captures the constructed `cmd` list and asserts on it for all three modes.

## Rabbit Holes

- **Making the transcript genuinely timestamp-prefixed.** The docstring claims it; it is not true. Correct the docstring (A9). Building segment-level timestamped transcription means changing `tools/link_analysis.transcribe_audio_file`'s contract and every caller — a separate piece of work.
- **VFR (variable-frame-rate) timestamp math.** The `-frame_pts` filename number is `pts * fps` only for CFR input. Do **not** build a general VFR timestamp reconstruction; `showinfo`'s `pts_time` is authoritative and correct for both, which is exactly why the plan joins on the `n:` ordinal instead of on `pts:`.
- **Auto-escalation from the push tier.** Tempting to make the bridge decide to spend frames. The pull tier is strictly opt-in by design (issue Definitions); the whole point of Part B is that the *agent* makes the judgment. Do not add automatic escalation.
- **Tuning scene/dedup thresholds per mode.** Parity was settled in #1920. Modes select an extraction strategy; they do not re-tune the algorithm.
- **A `--max-frames` flag.** Sounds symmetric with `--frame-width`, but the cap is the cost guardrail the issue explicitly says no mode may disable. Leave it env-only.
- **Broadening local-file input beyond the CLI.** See the No-Go — this is the security boundary, not a convenience gap.
- **Generalizing "read frames back image by image" into a reusable protocol doc.** It is three sentences in the skill body. Do not build a framework.

## Risks

### Risk 1: The `-loglevel` silent-failure mode reaches production
**Impact:** Someone later adds `-loglevel error` to quiet ffmpeg (a natural-looking cleanup). Frames still land on disk; `showinfo` output vanishes; every frame gets a fallback index-as-timestamp and the agent reasons confidently about the wrong moment in the video. Exactly the failure class the issue's first pre-requisite warns about, reintroduced through a different door.
**Mitigation:** The 1:1 `pts_time count == file count` assertion raises `VideoWatchError` rather than degrading. A comment at the ffmpeg command site states that no `-loglevel` flag may be added and why. The argument-vector test asserts `-loglevel` is **absent** — an anti-criterion, wired into the Verification table.

### Risk 2: Window-relative timestamps leak into the payload
**Impact:** The highest-consequence bug in this plan. A frame genuinely at 22:03 gets labeled `00:03` in both the payload and the filename. The agent answers confidently about the wrong moment, and nothing in the output looks wrong.
**Mitigation:** The offset is added back in exactly one place. A test extracts from a known window of a synthesized fixture and asserts the reported seconds fall inside the **absolute** requested range. `result["window"]` records the applied window so a consumer can independently verify. This test is a hard acceptance criterion.

### Risk 3: Test fakes with fixed signatures break in a confusing way
**Impact:** Six tests patch `_extract_scene_frames`/`_extract_audio` with 2-positional fakes. Adding keyword arguments produces `TypeError` inside a `patch.object` context — a failure that reads as unrelated to the change.
**Mitigation:** Called out explicitly in Test Impact with per-test dispositions, and budgeted as its own task rather than absorbed into a build task. Fakes accept `**kwargs`.

### Risk 4: The new skill fails `audit-skills`
**Impact:** A global skill that leaks `valor-`prefixed specifics ships to every machine and misfires in every other repo. `rule_13` fails the build.
**Mitigation:** The audit is a Verification-table row, run against the new skill before the PR opens. The probe sentence is quoted verbatim in the plan (A/B1) so it cannot be paraphrased into failure. `rule_21` also applies — no bare project-only slash-command references (`/sdlc`, `/setup`, `/prime`) in the body.

### Risk 5: Exhaustive mode floods context despite the cap
**Impact:** `fps=1` over a 1800s window is 1800 frames pre-cap. Dedup is relaxed by design in this mode, so `_subsample` is the only thing standing between the caller and a 1800-frame payload. If a future change reorders dedup/subsample or special-cases exhaustive, the cap silently stops applying.
**Mitigation:** `_subsample` runs unconditionally in every mode, after dedup. A test asserts the frame count is `<= VIDEO_WATCH_MAX_FRAMES` for all three modes given an over-cap input. Also note stderr volume (~750 KB at 1800 frames) — hence `-hide_banner` and `Parsed_showinfo` stripping before error surfacing.

### Risk 6: Persisted-frame filename padding overflows
**Impact:** `frame_{i:03d}` stops sorting past 1000 frames. Reachable only if `VIDEO_WATCH_MAX_FRAMES` is raised via env, but exhaustive mode makes that a plausible thing for someone to do.
**Mitigation:** Derive the padding width from `VIDEO_WATCH_MAX_FRAMES` rather than hardcoding. Same defect class as spike-3's `%05d` finding; fix both together so the lesson does not have to be relearned.

## Race Conditions

No race conditions identified. The pipeline is a strictly sequential `async` function whose only `await` is a single `transcribe_audio_file` call with no concurrent readers or writers of shared state. All subprocess calls are synchronous `subprocess.run`. Temp directories are per-invocation (`tempfile.TemporaryDirectory` for the workdir, `tempfile.mkdtemp` for the output dir), so two concurrent `valor-video-watch` processes cannot collide on a path.

One adjacent hazard worth naming (not a race in this code path): `reap_stale_frame_dirs()` runs at CLI startup and deletes `video_watch_frames_*` dirs older than `VIDEO_WATCH_FRAME_DIR_MAX_AGE` (24h). It is age-gated well beyond any single run's lifetime, and a concurrently running watch's output dir is freshly created, so the reaper cannot delete a live run's frames. **Data prerequisite:** the output dir's mtime must be current when the reaper scans — guaranteed by `mkdtemp` immediately preceding extraction. No change needed; recorded so a future `MAX_AGE` reduction is understood as load-bearing.

## No-Gos (Out of Scope)

- `[SEPARATE-SLUG #2474]` **Widening local-file input beyond the CLI.** The pipeline's only caller is the CLI. A message-derived path reaching `watch_video()` would turn "read your own screen recording" into arbitrary local file disclosure, with extracted artifacts persisted to a temp dir for 24 hours. Local-file input stays operator-invoked via the CLI; the bridge must never construct a local path from message content. Tracked as a constraint of this issue itself and asserted by an anti-criterion below.
- `[SEPARATE-SLUG #1920]` **Frame-extraction and dedup algorithm parity with claude-video.** Shipped and closed. Not revisited.
- `[SEPARATE-SLUG #1951]` **Cheaper transcription backend.** Shipped and closed. Not revisited.

Everything else the issue raises — including all three pre-requisite correctness hazards, both blast-radius padding defects, and the false docstring claim — is **in scope for this plan** and fixed here.

## Update System

Minimal, and it happens automatically.

- **No changes to `scripts/remote-update.sh` or the `/update` skill.** `scripts/update/hardlinks.py::_sync_skills` iterates every directory under `.claude/skills-global/` containing a `SKILL.md` and hardlinks it into `~/.claude/skills/`. A net-new skill needs no registration step — verified in the Freshness Check.
- **No `RENAMED_REMOVALS` entry.** That mechanism is for renames and moves between `skills/` and `skills-global/`. `watch` is net-new in `skills-global/` and has no stale user-level copy to clean up. (If it is later moved to `skills/`, an entry becomes mandatory.)
- **No new dependencies or config files to propagate.** ffmpeg, yt-dlp, and Pillow are already prerequisites of the shipped `valor-video-watch`.
- **No migration steps for existing installations.** All CLI changes are additive with defaults preserving current behavior exactly.
- **Post-merge:** run `/update` per repo convention so the hardlink lands on every machine, then verify the user-level copy exists — the `skills-global` hardlink has been observed to break silently, leaving the live skill on pre-merge text.

## Agent Integration

The agent reaches this work through both surfaces the repo supports, and neither needs new wiring.

- **CLI entry point:** `valor-video-watch` is already declared in `pyproject.toml [project.scripts]`. The new flags extend an existing entry point; **no new `[project.scripts]` entry is required.**
- **Bridge imports:** `bridge/telegram_bridge.py` does not call the pipeline and must not start. `bridge/enrichment.py` imports **only** `tools.video_watch.constants` (`WATCH_CLI_NAME`, `VIDEO_WATCH_THIN_TRANSCRIPT_CHARS`) so the cheap push path never loads the heavy pull pipeline. **This invariant must survive.** Any mode/knob table added to `constants.py` may import only `os`; `tools/video_watch/tests/test_import_discipline.py` enforces this in a fresh interpreter and must stay green.
- **Skill surface:** the `/watch` skill is how the agent *decides* to invoke the CLI. It reaches the tool through the Bash tool — no MCP server, no `.mcp.json` change.
- **Integration tests:** the new `test_cli.py` invokes the CLI's `main()` the way the agent's Bash tool would (argv in, exit code and stdout out), which is the actual agent-facing contract. `test_import_discipline.py` covers the bridge side.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/video-watch-visual-grounding.md`: add a section describing the `/watch` skill as the entry point to the pull tier; document `--start`/`--end`/`--frame-width`/`--mode` and the local-file input in the Usage section; document the new `window` and `input_kind` payload fields; update the Pipeline section for the `metadata=print` → `showinfo` change and the absolute-timestamp guarantee; extend Env-Tunable Constants if any are added.
- [ ] Update the `docs/features/README.md` index row (line 246) to mention the `/watch` skill and the scoping controls.

### Inline Documentation
- [ ] Comment at the ffmpeg frame-extraction command site: why input seek is used, why the offset is added back, and why **no `-loglevel` flag may be added** (spike-5).
- [ ] Comment at the frame-file sort: why files are sorted by parsed integer and not `sorted(glob(...))` (spike-3, with the ~28min@60fps reachability note).
- [ ] Correct `watch_video`'s docstring: remove the false "timestamp-prefixed when available" claim about the transcript (A9). Document the new keyword parameters and the `window`/`input_kind` result keys.
- [ ] `.claude/skill-context/watch.md` is itself documentation of the repo-specific invocation surface.

### Repo Guidance
- [ ] Update the `CLAUDE.md` command-table rows for `valor-video-watch` (lines 137-138) to reflect the new flags, and add a row for the `/watch` skill.

## Success Criteria

**Part A — CLI**
- [ ] `--start` / `--end` accept `MM:SS`, `HH:MM:SS`, and raw seconds; invalid ranges (negative, `start >= end`, malformed) exit non-zero with a clear message.
- [ ] Frame timestamps for a windowed run are **absolute positions in the source video** — asserted by extracting from a known window of a synthesized fixture and checking the reported seconds fall inside the requested absolute range.
- [ ] A windowed run transcribes only audio inside the window, and the length gate evaluates the **window** duration — asserted with a video longer than `VIDEO_WATCH_MAX_DURATION` and a short requested window.
- [ ] `--frame-width` changes the pixel width of emitted JPEGs — asserted on actual image dimensions via Pillow.
- [ ] `--mode {scene,keyframe,exhaustive}` selects the extraction strategy; `VIDEO_WATCH_MAX_FRAMES` holds in **all three**; exhaustive relaxes dedup.
- [ ] A local video file is analyzed with the download step never invoked and the X/Grok step never entered; a nonexistent path fails cleanly before download.
- [ ] Local-vs-URL classification does not depend on cwd — asserted by running the same relative-path input from two different working directories and getting identical rejection.
- [ ] The result payload records `window` and `input_kind`.
- [ ] `tools/video_watch/tests/test_cli.py` exists and covers parsing, validation errors, exit codes, and both output formats.
- [ ] At least one test asserts the constructed ffmpeg argument vector for all three modes, including the **absence** of `-loglevel`.
- [ ] `pts_time count == file count` is asserted in code and raises rather than degrading.
- [ ] Frame files are sorted by parsed integer; persisted-frame padding derives from `VIDEO_WATCH_MAX_FRAMES`.

**Part B — skill**
- [ ] `.claude/skills-global/watch/SKILL.md` exists and `audit-skills` passes on it, including `rule_13` (probe sentence) and `rule_21`.
- [ ] No `valor-`prefixed command name appears in the skill body or any bundled Markdown sub-file.
- [ ] `.claude/skill-context/watch.md` exists and carries every repo-specific invocation detail.
- [ ] The skill states when the automatic transcript suffices vs. when to extract frames, how to derive a window from the question, which cost mode fits, and how to read frames back.
- [ ] The skill degrades gracefully in a repo with no context file.

**General**
- [ ] Default invocation (no new flags) produces byte-identical behavior to today apart from the `showinfo` timestamp source.
- [ ] `tools/video_watch/tests/test_import_discipline.py` stays green — `constants.py` remains `os`-only.
- [ ] No new dependencies added to `pyproject.toml`.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (pipeline-core)**
  - Name: `pipeline-builder`
  - Role: Time parsing, window plumbing, `showinfo` migration, absolute-timestamp correction, integer sort, audio windowing, gate fix
  - Agent Type: builder
  - Resume: true

- **Builder (cli-surface)**
  - Name: `cli-builder`
  - Role: Flags, validation, input classification, output rendering, `test_cli.py`
  - Agent Type: builder
  - Resume: true

- **Test engineer (extraction)**
  - Name: `extraction-tester`
  - Role: Fake-signature repairs, ffmpeg argument-vector assertions, absolute-timestamp fixture test, per-mode cap tests
  - Agent Type: test-engineer
  - Resume: true

- **Builder (skill)**
  - Name: `skill-builder`
  - Role: `SKILL.md` + `.claude/skill-context/watch.md`, audit-clean
  - Agent Type: builder
  - Resume: true

- **Documentarian**
  - Name: `watch-documentarian`
  - Role: Feature doc, index row, CLAUDE.md rows, inline comments
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: `watch-validator`
  - Role: Verify every success criterion and Verification row
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Pipeline core — window, timestamps, audio
- **Task ID**: build-pipeline-core
- **Depends On**: none
- **Validates**: `tools/video_watch/tests/test_watch.py`, `tools/video_watch/tests/test_import_discipline.py`
- **Informed By**: spike-1 (input seek; window-relative pts; output seek desyncs sidecar), spike-2 (`-t` is a duration from the seek point), spike-3 (`%05d` widens past 99999; sort by parsed int), spike-5 (`showinfo` is 1:1 in all modes; regex on `pts_time:`; join by `n:` ordinal; never lower loglevel)
- **Assigned To**: pipeline-builder
- **Agent Type**: builder
- **Parallel**: true
- Add the time-parsing helper (`MM:SS` / `HH:MM:SS` / seconds → float; reject negative, malformed, `start >= end`). Do **not** put it in `constants.py`.
- Add keyword-only `start`/`end` to `watch_video()`; compute the effective window as `(start, min(end - start, VIDEO_WATCH_MAX_DURATION))` intersected with the probed duration; record it in `result["window"]`.
- Rename `_extract_scene_frames` → `_extract_frames`; add `-ss {start}` **before** `-i` and `-t {duration}`; add `-hide_banner`; add **no** `-loglevel` flag.
- Replace `metadata=print:file=...` with `showinfo` placed **after** `scale`. Parse `pts_time` from **stderr** with a regex anchored on `pts_time:` — not on `Parsed_showinfo` (index shifts) and not by counting lines (2x over-count from the color-metadata continuation line).
- Assert `pts_time count == written file count`; raise `VideoWatchError` on mismatch. Add the comment explaining the `-loglevel` hazard.
- Sort frame files by **parsed integer**, never `sorted(glob(...))`. Add the spike-3 reachability comment.
- Add `start` back to every parsed timestamp so reported times are absolute.
- Add `-ss`/`-t` input options to `_extract_audio`.
- Change the transcription length gate to compare the **effective window duration**, preserving the exact note string `[audio too long to transcribe — frames only]`.
- Widen persisted-frame padding, derived from `VIDEO_WATCH_MAX_FRAMES` (no new magic number).
- Correct the `watch_video` docstring's false transcript-timestamp claim; document new params and result keys.

### 2. Cost modes + frame width
- **Task ID**: build-modes
- **Depends On**: build-pipeline-core
- **Validates**: `tools/video_watch/tests/test_watch.py`
- **Informed By**: spike-4 (`metadata=print` empty for keyframe; `-skip_frame nokey` poisons scene scores — use `select='eq(pict_type\,I)'`), spike-5 (`fps=1` is the right exhaustive shape)
- **Assigned To**: pipeline-builder
- **Agent Type**: builder
- **Parallel**: false
- Add a `mode` keyword to `_extract_frames` selecting `{SELECT}`: scene (unchanged), keyframe (`select='eq(pict_type\,I)'`), exhaustive (`fps=1`).
- Add a `frame_width` keyword defaulting to `VIDEO_WATCH_FRAME_WIDTH`.
- Relax dedup in exhaustive mode only; keep `_subsample` unconditional in **all** modes so the frame cap never lifts.
- If a mode table lands in `constants.py`, it may import only `os`.
- Strip `Parsed_showinfo` lines from stderr before embedding it in any `VideoWatchError` message.

### 3. CLI surface + local files
- **Task ID**: build-cli
- **Depends On**: build-modes
- **Validates**: `tools/video_watch/tests/test_cli.py` (create)
- **Informed By**: issue Revised bucket (cwd-independent classification; `~` unexpanded today), issue Downstream (do not widen local input beyond the CLI)
- **Assigned To**: cli-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `--start`, `--end`, `--frame-width`, `--mode {scene,keyframe,exhaustive}` with `scene` as the default.
- Add `classify_input(value) -> ("url" | "local", resolved)`: local iff the value starts with `file://`, `/`, or `~`; expand `~`; reject bare relative paths with the "pass an absolute path or file:// URI" message. Never touch the filesystem to decide.
- Bypass `_download_video` entirely for local input; fail cleanly on a missing path or a directory before download. Record `result["input_kind"]`.
- Render `window` and `input_kind` in `_format_human`.
- Create `tools/video_watch/tests/test_cli.py` covering flag parsing, every validation error, exit codes, and both output formats — including the currently-untested reaper-warning and `--json`-on-failure paths.

### 4. Test repairs + extraction assertions
- **Task ID**: build-tests
- **Depends On**: build-cli
- **Validates**: `tools/video_watch/tests/`
- **Informed By**: spike-1, spike-3, spike-5; Test Impact section
- **Assigned To**: extraction-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Repair the six fixed-signature fakes in `test_watch.py` (accept `**kwargs`).
- Re-express `test_oversized_duration_skips_transcription_with_exact_note` for the new gate semantics and add the "long video, short window → transcribes" sibling.
- Add the **absolute-timestamp** test: synthesize a fixture, extract from a known window, assert reported seconds fall inside the absolute range.
- Add the ffmpeg **argument-vector** test for all three modes, asserting `-ss` placement, `-t` presence, the correct `{SELECT}`, `-hide_banner`, and the **absence** of `-loglevel`.
- Add per-mode frame-cap tests (count `<= VIDEO_WATCH_MAX_FRAMES` given over-cap input).
- Add the `pts_time`-count-mismatch test using a stderr fixture with no `pts_time` lines.
- Add the cwd-independence test for local-path classification.
- Re-run `test_import_discipline.py` and `tests/unit/test_enrichment_watch_signpost.py` explicitly.

### 5. The /watch skill
- **Task ID**: build-skill
- **Depends On**: build-cli
- **Validates**: `python .claude/skills-global/audit-skills/scripts/audit_skills.py`
- **Informed By**: `rule_13` probe-suffix requirement; `rule_21` project-only slash-command guard; `docs/features/skill-context-convention.md`
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/skills-global/watch/SKILL.md` with valid frontmatter (`name: watch` matching the dir, a trigger phrase in the description, `argument-hint`, known fields only).
- Include the probe sentence verbatim: *"If `.claude/skill-context/watch.md` exists, read it and honor its declarations; otherwise use the generic defaults described below."*
- Body content: when the automatic transcript suffices vs. when to spend tokens on frames; how to derive a time window from the question; which cost mode fits which question; the read-frames-in-order protocol. **No `valor-`prefixed command name anywhere in the body or any sub-file.**
- Ensure the generic baseline degrades gracefully with no context file present.
- Create `.claude/skill-context/watch.md` with the concrete invocation, every flag and accepted format, the `--json` payload shape including `window`/`input_kind`, the push-tier interaction (transcript may already be in context), the local-file constraint, and the feature-doc cross-reference.
- Run the audit and iterate until clean.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests, build-skill
- **Assigned To**: watch-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/video-watch-visual-grounding.md` (skill as pull-tier entry point; new flags; payload fields; `showinfo` pipeline change; absolute-timestamp guarantee).
- Update the `docs/features/README.md` index row.
- Update `CLAUDE.md` rows 137-138 and add a `/watch` row.
- Verify the inline comments from task 1 landed.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: build-pipeline-core, build-modes, build-cli, build-tests, build-skill, document-feature
- **Assigned To**: watch-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every command in the Verification table.
- Confirm every Success Criteria checkbox.
- Confirm the default (no-new-flags) invocation is behaviorally unchanged.
- Report pass/fail.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tools/video_watch/tests/ tests/unit/test_enrichment_watch_signpost.py -q` | exit code 0 |
| Full suite | `scripts/pytest-clean.sh tests/ -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Skill audit clean | `python .claude/skills-global/audit-skills/scripts/audit_skills.py --no-sync` | exit code 0 |
| Skill exists | `test -f .claude/skills-global/watch/SKILL.md` | exit code 0 |
| Context file exists | `test -f .claude/skill-context/watch.md` | exit code 0 |
| Probe sentence present | `grep -c "exists, read it and honor its declarations; otherwise use the generic defaults described below." .claude/skills-global/watch/SKILL.md` | output > 0 |
| CLI test file exists | `test -f tools/video_watch/tests/test_cli.py` | exit code 0 |
| Import discipline holds | `pytest tools/video_watch/tests/test_import_discipline.py -q` | exit code 0 |
| **Anti-criterion:** no `valor-` in skill body | `grep -rc "valor-" .claude/skills-global/watch/` | match count == 0 |
| **Anti-criterion:** no `-loglevel` in extraction cmd | `grep -c '"-loglevel"' tools/video_watch/pipeline.py` | match count == 0 |
| **Anti-criterion:** `metadata=print` fully removed | `grep -c "metadata=print" tools/video_watch/pipeline.py` | match count == 0 |
| **Anti-criterion:** no lexicographic frame sort | `grep -c "sorted(frames_dir.glob" tools/video_watch/pipeline.py` | match count == 0 |
| **Anti-criterion:** local input not reachable from bridge | `grep -rc "classify_input\|watch_video" bridge/` | match count == 0 |
| **Anti-criterion:** constants.py stays os-only | `grep -E '^(import\|from) ' tools/video_watch/constants.py \| grep -vc '^import os$\|^from __future__ '` | match count == 0 |
| No new deps | `git diff main -- pyproject.toml \| grep -c '^+.*dependencies'` | match count == 0 |
| CLI help shows new flags | `valor-video-watch --help \| grep -c -- "--start"` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Local-file security boundary.** The plan takes the stricter of the issue's two options: local input is CLI-only, classified cwd-independently, and an anti-criterion asserts the bridge never references `watch_video`/`classify_input`. The issue also floated "or gate it behind an allowlisted root." Is CLI-only sufficient, or do you want an allowlisted-root gate as well (e.g. `~/Desktop`, `~/Downloads`) as defense in depth?

2. **Cost-mode names.** The plan uses `scene` (default), `keyframe`, `exhaustive`, deliberately avoiding "efficient" per the issue's Revised bucket. `keyframe` is honest about the mechanism but does not tell the caller it may produce *more* frames than scene mode on a static video. Are these names right, or would you rather they describe the intent (`fast` / `balanced` / `thorough`) with the mechanism in the help text?

3. **`showinfo` migration blast radius.** Replacing `metadata=print` with `showinfo` changes the timestamp source for the **default** scene mode too, not just the new modes. It is the only way to get one code path across three modes (spike-4 proved keyframe mode cannot use `metadata=print`), and spike-5 confirmed 1:1 parity — but it means today's shipped behavior gets a new timestamp path. Accept the unified migration, or keep `metadata=print` for scene mode and use `showinfo` only for keyframe/exhaustive (two paths, more code, less risk to the shipped default)?
