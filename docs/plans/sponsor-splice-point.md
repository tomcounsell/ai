---
status: Ready
type: feature
appetite: Small
owner: Valor
created: 2026-02-14
tracking: https://github.com/yudame/cuttlefish/issues/52
last_comment_id: IC_kwDOPhRNKc7oUKqb
---

# Sponsor Break Splice Point in Podcast Audio

## Problem

The podcast needs a reliable way to insert sponsor audio into episodes during post-production. Currently, `PodcastConfig.sponsor_break` tells the episodeFocus prompt to include a vague "natural transition point," but there is no specific trigger phrase, no timestamp detection, and no automated splicing.

**Current behavior:**
The manual `notebooklm_prompt.py` includes a loose SPONSOR BREAK section ("Include a natural transition point around the 10-12 minute mark"), but the API-side prompt generation has no sponsor instructions at all. Even when NotebookLM does produce a transition, nothing in the pipeline detects it or acts on it.

**Desired outcome:**
A scripted trigger phrase is embedded in the episodeFocus prompt. After transcription (Phase 10), the trigger is detected in the transcript and its timestamp is recorded. An optional splicing step uses ffmpeg to insert sponsor audio at that timestamp, producing a final episode audio file with the sponsor message seamlessly integrated.

## Prior Art

- **Issue #75 (closed)**: MVP end-to-end podcast production -- established the 12-phase pipeline that this work extends. The Audio Processing phase (Phase 10) where splicing will live was created here.
- **Issue #53 (open)**: Sponsor message audio generation -- the companion issue that produces sponsor clips via TTS. This plan consumes the `Sponsor.effective_audio_url` that #53 will provide. Splicing gracefully skips when no sponsor exists.
- **Plan: `sponsor-message-audio.md`**: Details the `Sponsor` model and `generate_sponsor_audio()` service. This plan depends on that model existing but degrades gracefully if it does not.

No prior closed issues or merged PRs attempted splice point detection or audio splicing.

## Data Flow

1. **Entry point**: `PodcastConfig.sponsor_trigger_phrase` field provides the trigger phrase string
2. **Prompt injection**: `generate_episode_focus()` includes verbatim trigger phrase instruction in the episodeFocus prompt sent to NotebookLM
3. **Audio generation**: NotebookLM hosts speak the trigger phrase naturally after the intro section
4. **Transcription**: `transcribe_audio()` calls Whisper API with `timestamp_granularities=["word"]`, stores word-level timestamps as `transcript-words` artifact
5. **Detection**: `detect_splice_point()` reads the `transcript-words` artifact, fuzzy-matches the trigger phrase across the word sequence, returns the timestamp (or None)
6. **Splicing**: `splice_sponsor_audio()` downloads episode audio + sponsor audio, uses ffmpeg to split at timestamp and concatenate `[part1] + [sponsor] + [part2]`, uploads spliced version
7. **Output**: `Episode.audio_url` updated to spliced version; raw audio preserved at `audio-raw.mp3` storage key; chapter generation runs on the spliced audio

## Architectural Impact

- **New dependencies**: `ffmpeg` binary must be available on the server (already common on Render); no new Python packages required
- **Interface changes**: `transcribe_audio()` gains word-level timestamp output (additive -- plain text transcript still saved to `Episode.transcript`); `generate_episode_focus()` gains `sponsor_trigger_phrase` parameter
- **Coupling**: New `splice.py` module depends on `PodcastConfig`, `Sponsor` (from #53), `EpisodeArtifact`, and file storage service. All are existing interfaces.
- **Data ownership**: Splice metadata (detected timestamp) stored as `EpisodeArtifact` -- no new model fields on `Episode`
- **Reversibility**: Fully reversible. Raw audio preserved at `audio-raw.mp3`. Removing the splice step just means the raw audio becomes the published audio.

## Appetite

**Size:** Small

**Team:** Solo dev. Prompt update, one new service module for detection + splicing, integration into existing Audio Processing phase. No new workflow steps.

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `OPENAI_API_KEY` | `python -c "import os; assert os.environ.get('OPENAI_API_KEY')"` | Whisper API for word-level transcription |
| `ffmpeg` binary | `which ffmpeg` | Audio splitting and concatenation |

## Solution

### Key Elements

- **Trigger phrase on PodcastConfig**: A fixed, deterministic string (default: "Now, let's dive in.") that hosts speak verbatim after the intro
- **Word-level Whisper transcription**: Upgrade `transcribe_audio()` to request `timestamp_granularities=["word"]` and store the result as an artifact
- **Splice detection service**: Fuzzy-match the trigger phrase against the word-level transcript to find the exact timestamp
- **Audio splicing service**: Conditional ffmpeg-based split-and-concatenate that inserts sponsor audio at the detected timestamp

### Flow

**Episode audio generated** -> Whisper transcribes with word timestamps -> detect_splice_point() finds trigger phrase timestamp -> splice_sponsor_audio() inserts sponsor clip via ffmpeg -> **Episode.audio_url updated to spliced version**

### Technical Approach

#### Design Decision: Trigger Phrase is Fixed, Not AI-Generated

The trigger phrase is a fixed, deterministic string configured on `PodcastConfig`. NotebookLM hosts are instructed to say it verbatim. This is far more reliable than asking an AI to "include a natural transition" and then trying to detect what it said.

The phrase must be:
- Short (5-7 words) -- less room for Whisper transcription variance
- Conversational -- does not sound like an ad marker to listeners
- Unique within the episode -- will not match other dialogue

#### Detection: Whisper Word-Level Timestamps + Fuzzy Matching

The current `transcribe_audio()` uses Whisper's basic transcription (text only). This changes to word-level timestamps (`timestamp_granularities=["word"]`) which returns each word with start/end times. A sliding window fuzzy match (using `SequenceMatcher` with 0.8 threshold) finds the trigger phrase and returns the start timestamp.

#### Splicing: ffmpeg, Conditional on Sponsor Audio

Audio splicing is conditional:
1. If `PodcastConfig.sponsor_break` is False -- skip entirely
2. If no active `Sponsor` exists for the podcast -- skip (log warning)
3. If trigger phrase not found in transcript -- skip (log warning, store unspliced)
4. If all conditions met -- splice sponsor audio at the detected timestamp

The original unspliced audio is preserved (stored as `audio-raw.mp3`). The spliced version becomes the published `audio.mp3`.

### File Changes

#### 1. `apps/podcast/models/podcast_config.py` -- Add trigger phrase field

Add `sponsor_trigger_phrase` CharField with default `"Now, let's dive in."` and include it in `to_dict()` export.

#### 2. Prompt generation -- Update to include trigger phrase instruction

Wherever `generate_episode_focus()` or the manual NotebookLM prompt is built, inject a SPONSOR SPLICE POINT block when `sponsor_trigger_phrase` is provided. The instruction tells hosts to say the phrase verbatim after the opening section.

#### 3. `apps/podcast/services/audio.py` -- Update `transcribe_audio()` for word timestamps

Change the Whisper API call to `response_format="verbose_json"` with `timestamp_granularities=["word"]`. Store the word-level transcript as a `transcript-words` EpisodeArtifact. Continue saving `Episode.transcript` as plain text for downstream consumers.

#### 4. `apps/podcast/services/splice.py` -- New module with two public functions

**`detect_splice_point(episode_id: int) -> float | None`**: Reads the `transcript-words` artifact, slides a window across the word sequence, fuzzy-matches against the trigger phrase using `SequenceMatcher`. Returns the start timestamp of the best match (if above 0.8 threshold) or None.

**`splice_sponsor_audio(episode_id: int) -> str | None`**: Checks preconditions (sponsor_break enabled, active Sponsor exists, splice point detected), downloads episode and sponsor audio, uses ffmpeg subprocess to split and concatenate in a temp directory, uploads spliced audio, updates `Episode.audio_url`. Returns the new URL or None if skipped.

#### 5. `apps/podcast/tasks.py` -- Wire splicing into `step_transcribe_audio`

After `audio.transcribe_audio(episode_id)`, call `detect_splice_point()` and conditionally `splice_sponsor_audio()` before enqueuing `step_generate_chapters`.

#### 6. One migration -- Add `sponsor_trigger_phrase` to `PodcastConfig`

Non-destructive CharField with default value. Migration created but not applied per project guidelines.

### What Does NOT Change

- **Workflow steps** -- No new phases. Splicing is part of existing Audio Processing.
- **Episode model** -- No new fields. Splice metadata lives in artifacts.
- **Sponsor model** -- That is #53's domain. This plan assumes `Sponsor` exists with `effective_audio_url`.
- **Chapter generation** -- Runs on the spliced audio, so chapters include the sponsor segment.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `splice_sponsor_audio()` catches ffmpeg subprocess failures and logs them without crashing the pipeline
- [ ] `detect_splice_point()` returns None (not an exception) when the trigger phrase is not found
- [ ] `transcribe_audio()` word timestamp parsing handles malformed API responses gracefully

### Empty/Invalid Input Handling
- [ ] `detect_splice_point()` returns None when `transcript-words` artifact is empty or missing
- [ ] `splice_sponsor_audio()` skips when sponsor audio URL is empty
- [ ] `detect_splice_point()` handles the case where `sponsor_trigger_phrase` is blank

### Error State Rendering
- [ ] No user-visible output -- this is a backend pipeline step. Failures are logged and recorded in `EpisodeWorkflow.history`

## Test Impact

No existing tests affected -- this is a greenfield feature adding new functionality (splice detection and audio insertion) with no modifications to existing test-covered behavior. The `transcribe_audio()` change is additive (word timestamps alongside existing plain text), so existing transcription tests remain valid. New tests will be created for the new module.

## Rabbit Holes

- **Multiple splice points**: Supporting 2+ sponsor breaks (mid-roll, post-roll). Doable by making the trigger phrase a list, but unnecessary for 30-40 minute episodes. Separate project if needed.
- **Loudness normalization**: Matching sponsor audio LUFS to episode audio. Important for production quality but adds ffmpeg filter complexity. Follow-up.
- **Fade in/out**: Cross-fading between episode and sponsor audio. Nice to have, not essential. Can be added to the ffmpeg command later.
- **Dynamic ad insertion (DAI)**: Serving different sponsor audio to different listeners at playback time. Completely different architecture. Out of scope.
- **Whisper model upgrade**: Using a newer/better speech model for more accurate timestamps. Current Whisper-1 is sufficient for short phrase detection.

## Risks

### Risk 1: Whisper transcription variance
**Impact:** Trigger phrase not detected because Whisper transcribes words differently (contractions, capitalization, minor word substitutions).
**Mitigation:** Fuzzy matching with 0.8 similarity threshold. The trigger phrase is deliberately short (5-7 words) to minimize transcription variance. Tests cover common Whisper variants.

### Risk 2: NotebookLM ignores or paraphrases the trigger phrase
**Impact:** Hosts do not say the exact phrase, so detection fails and splicing is skipped.
**Mitigation:** Strong prompt language ("say EXACTLY this phrase", "do NOT paraphrase"). The fuzzy matching threshold (0.8) allows minor variance. If detection fails, the episode publishes without a sponsor break -- no data loss, just no ad.

### Risk 3: ffmpeg not available on server
**Impact:** Splicing step fails with subprocess error.
**Mitigation:** Check for ffmpeg availability in the prerequisite table. Add `apt-get install ffmpeg` to Render build script if not already present.

## Race Conditions

No race conditions identified. The splice detection and audio splicing run sequentially within a single task (`step_transcribe_audio`). The `_acquire_step_lock` mechanism already prevents duplicate task execution. All file operations use temp directories with no shared mutable state.

## No-Gos (Out of Scope)

- No multiple splice points per episode
- No dynamic/per-listener ad insertion
- No loudness normalization (follow-up)
- No audio crossfade (follow-up)
- No new workflow phases
- No Sponsor model changes (that is #53)

## Update System

No update system changes required -- this feature is internal to the podcast production pipeline and does not affect the deployment or update process. The only infrastructure requirement is ffmpeg on the server, which should be verified in the Render build configuration.

## Agent Integration

No agent integration required -- sponsor splicing is an automated pipeline step triggered by the existing task system. No MCP server exposure or bridge changes needed.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/podcast-services.md` to document `splice.py` module and its two public functions
- [ ] Update `docs/reference/podcast-workflow-diagram.md` to show splice detection and insertion within Phase 10

### Inline Documentation
- [ ] Docstrings on `detect_splice_point()` and `splice_sponsor_audio()` explaining parameters, return values, and skip conditions
- [ ] Code comments on the fuzzy matching algorithm and ffmpeg commands

## Success Criteria

- [ ] `PodcastConfig.sponsor_trigger_phrase` field exists with default `"Now, let's dive in."`
- [ ] Prompt generation includes verbatim trigger phrase instruction when sponsor_break is enabled
- [ ] `transcribe_audio()` uses Whisper word-level timestamps and stores `transcript-words` artifact
- [ ] `detect_splice_point()` fuzzy-matches trigger phrase in word timestamps, returns timestamp or None
- [ ] `splice_sponsor_audio()` uses ffmpeg to insert sponsor audio at detected timestamp
- [ ] Raw (unspliced) audio preserved at `audio-raw.mp3` storage key
- [ ] Splicing gracefully skips when: sponsor_break is False, no active Sponsor, trigger phrase not found
- [ ] Existing `step_transcribe_audio` task calls detection and splicing after transcription
- [ ] Tests cover detection (exact match, fuzzy match, not found) and splicing (happy path, skip conditions)
- [ ] Migration created (not applied)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] Pre-commit hooks pass

## Team Orchestration

### Team Members

- **Builder (splice-service)**
  - Name: splice-builder
  - Role: Implement splice detection, audio splicing service, model field, prompt updates, task wiring
  - Agent Type: builder
  - Resume: true

- **Builder (tests)**
  - Name: test-builder
  - Role: Write comprehensive tests for splice detection and audio splicing
  - Agent Type: test-engineer
  - Resume: true

- **Validator (splice)**
  - Name: splice-validator
  - Role: Verify all success criteria, run tests, check graceful degradation paths
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add trigger phrase field to PodcastConfig
- **Task ID**: build-model
- **Depends On**: none
- **Validates**: `apps/podcast/models/podcast_config.py` contains `sponsor_trigger_phrase` field
- **Assigned To**: splice-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `sponsor_trigger_phrase` CharField to `PodcastConfig` with default `"Now, let's dive in."`
- Add field to `to_dict()` export
- Create migration (do not apply)

### 2. Update prompt generation with trigger phrase
- **Task ID**: build-prompt
- **Depends On**: build-model
- **Validates**: Prompt includes trigger phrase instruction when sponsor_break enabled
- **Assigned To**: splice-builder
- **Agent Type**: builder
- **Parallel**: true
- Inject SPONSOR SPLICE POINT block into episodeFocus prompt
- Update manual NotebookLM prompt template

### 3. Upgrade transcription to word-level timestamps
- **Task ID**: build-transcription
- **Depends On**: none
- **Validates**: `apps/podcast/services/audio.py` uses `timestamp_granularities=["word"]`
- **Assigned To**: splice-builder
- **Agent Type**: builder
- **Parallel**: true
- Change Whisper API call to `response_format="verbose_json"` with `timestamp_granularities=["word"]`
- Store word-level output as `transcript-words` EpisodeArtifact
- Continue saving plain text transcript to `Episode.transcript`

### 4. Implement splice detection and audio splicing
- **Task ID**: build-splice
- **Depends On**: build-transcription
- **Validates**: `apps/podcast/services/splice.py` exists with both public functions
- **Assigned To**: splice-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `apps/podcast/services/splice.py` with `detect_splice_point()` and `splice_sponsor_audio()`
- Implement fuzzy matching with SequenceMatcher
- Implement ffmpeg split-and-concatenate in temp directory
- Handle all skip conditions gracefully

### 5. Wire splicing into task pipeline
- **Task ID**: build-tasks
- **Depends On**: build-splice
- **Validates**: `apps/podcast/tasks.py` calls splice functions in `step_transcribe_audio`
- **Assigned To**: splice-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `step_transcribe_audio` to call `detect_splice_point()` and `splice_sponsor_audio()` after transcription

### 6. Write tests
- **Task ID**: build-tests
- **Depends On**: build-splice, build-tasks
- **Validates**: `apps/podcast/tests/test_splice.py` (create)
- **Assigned To**: test-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Test `detect_splice_point()` with exact match, fuzzy match, and not-found cases
- Test `splice_sponsor_audio()` happy path with mocked ffmpeg and storage
- Test skip conditions: sponsor_break False, no active Sponsor, trigger phrase not found
- Test raw audio preservation at `audio-raw.mp3`

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: splice-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/podcast-services.md` with splice module
- Update workflow diagram

### 8. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: splice-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met
- Check pre-commit hooks pass
- Confirm migration created but not applied

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_splice.py -x -q` | exit code 0 |
| Lint clean | `uv run pre-commit run --all-files` | exit code 0 |
| Splice module exists | `test -f apps/podcast/services/splice.py` | exit code 0 |
| Model field exists | `grep -q sponsor_trigger_phrase apps/podcast/models/podcast_config.py` | exit code 0 |
| Word timestamps in transcribe | `grep -q timestamp_granularities apps/podcast/services/audio.py` | exit code 0 |
| Task wiring | `grep -q detect_splice_point apps/podcast/tasks.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **ffmpeg on Render**: Is ffmpeg already available in the Render build environment, or does it need to be added to the build script? This affects the prerequisite setup.
2. **Sponsor model timeline**: Issue #53 (Sponsor model) is also open. Should splice detection be built first (works independently) and splicing wired up after #53 ships, or should both land together?
3. **Trigger phrase alternatives**: The default "Now, let's dive in." is conversational but generic. Should we test a few phrases with NotebookLM to see which one it reproduces most reliably before committing to a default?
