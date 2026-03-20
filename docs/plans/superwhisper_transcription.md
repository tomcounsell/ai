---
status: Ready
type: feature
appetite: Small
owner: Valor
created: 2026-03-20
tracking: https://github.com/tomcounsell/ai/issues/446
last_comment_id:
---

# SuperWhisper Voice Transcription Backend

## Problem

When users send voice messages via Telegram, the bridge transcribes them using OpenAI's Whisper API at $0.006/minute. This machine already runs SuperWhisper (paid subscription) with the "Ultra" model — a high-quality local transcription engine that's sitting idle.

**Current behavior:**
Voice messages hit the OpenAI Whisper API over the internet. Requires `OPENAI_API_KEY`, costs money per minute, and adds network latency.

**Desired outcome:**
Voice transcription uses SuperWhisper as the primary backend (free, local, fast). Falls back to OpenAI Whisper API when SuperWhisper is unavailable. The `transcribe()` function signature in `tools/transcribe/__init__.py` doesn't change — callers work without modification.

## Prior Art

No prior issues or PRs found related to SuperWhisper integration.

## Spike Results

### spike-1: Validate SuperWhisper filesystem interface
- **Assumption**: "SuperWhisper writes meta.json with transcription results to a predictable directory"
- **Method**: filesystem inspection
- **Finding**: Confirmed. Recordings are at `~/Documents/superwhisper/recordings/{unix_timestamp}/`. Each folder contains `meta.json` and `output.wav`. The `meta.json` includes `result` (transcription text), `rawResult`, `segments` (with start/end timestamps), `duration` (ms), `processingTime` (ms), `datetime`, and `modelName`. Folder names are unix timestamps (e.g., `1773979378`).
- **Confidence**: high
- **Impact on plan**: The recordings path in the issue was wrong (`~/Library/Application Support/`) — corrected to `~/Documents/superwhisper/recordings/`.

### spike-2: Validate SuperWhisper process detection
- **Assumption**: "SuperWhisper can be detected via pgrep"
- **Method**: process inspection
- **Finding**: `pgrep -x superwhisper` returns PID `47500` when running. The app is installed at `/Applications/superwhisper.app` (found via `mdfind`).
- **Confidence**: high
- **Impact on plan**: Process detection approach is valid.

## Data Flow

1. **Entry point**: User sends voice message on Telegram
2. **Bridge (`bridge/telegram_bridge.py`)**: Downloads `.ogg` file via Telethon
3. **Media handler (`bridge/media.py:process_incoming_media`)**: Detects voice type, calls `transcribe_voice(filepath)`
4. **Media handler (`bridge/media.py:transcribe_voice`)**: Currently calls OpenAI Whisper API directly via httpx
5. **Output**: Transcription text prepended to message as `[Voice message transcription: "..."]`

Note: `bridge/media.py:transcribe_voice()` does NOT call `tools/transcribe/`. It has its own inline OpenAI Whisper call. The plan should update `bridge/media.py` to use the tool module, which will then handle the SuperWhisper → OpenAI fallback chain.

## Architectural Impact

- **New dependencies**: None — uses `subprocess` and `pathlib` (stdlib only)
- **Interface changes**: None — `transcribe()` signature unchanged
- **Coupling**: Reduces coupling to OpenAI; adds soft dependency on macOS SuperWhisper app
- **Data ownership**: SuperWhisper owns its recordings directory; we only read from it
- **Reversibility**: Easy — remove the SuperWhisper backend, fallback to OpenAI resumes

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| SuperWhisper installed | `pgrep -x superwhisper` | Primary transcription backend |
| `OPENAI_API_KEY` (fallback) | `python -c "import os; assert os.environ.get('OPENAI_API_KEY')"` | Whisper API fallback |

## Solution

### Key Elements

- **SuperWhisper backend**: A `_transcribe_superwhisper(audio_path)` function that sends audio to SuperWhisper via `open -g -a` and polls for results
- **Fallback chain**: `transcribe()` tries SuperWhisper first, falls back to existing OpenAI Whisper API code
- **Bridge integration**: Update `bridge/media.py:transcribe_voice()` to call `tools.transcribe.transcribe()` instead of its inline OpenAI code

### Flow

**Voice message arrives** → bridge downloads .ogg → `transcribe(filepath)` → try SuperWhisper (`open -g -a`, poll meta.json) → if unavailable/timeout → fallback to OpenAI Whisper API → return `{"text": "...", ...}`

### Technical Approach

- Snapshot `~/Documents/superwhisper/recordings/` directory before sending file
- Run `subprocess.run(["open", "-g", "-a", "superwhisper", str(audio_path)])`
- Poll every 500ms for a new folder (folder name > max existing timestamp)
- Read `meta.json` from new folder, extract `result` field
- Timeout after 30s → fall through to OpenAI
- Cache SuperWhisper availability check (process running + app exists) for 60s to avoid repeated pgrep calls
- Recordings path configurable via `SUPERWHISPER_RECORDINGS_DIR` env var, defaulting to `~/Documents/superwhisper/recordings/`

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] SuperWhisper not installed → graceful fallback to OpenAI, logged at DEBUG
- [ ] SuperWhisper running but hangs (no meta.json written) → timeout after 30s, fallback to OpenAI
- [ ] meta.json exists but `result` field is empty → treat as failure, fallback to OpenAI
- [ ] Recordings directory doesn't exist → skip SuperWhisper, fallback to OpenAI

### Empty/Invalid Input Handling
- [ ] Empty audio file (0 bytes) → return error dict before attempting any backend
- [ ] Unsupported format → return error dict (existing validation)

### Error State Rendering
- [ ] All failure modes log clearly and fall through to OpenAI — no silent swallowing

## Test Impact

- [ ] `tools/transcribe/tests/test_transcribe.py::TestConfiguration::test_manifest_valid_json` — UPDATE: manifest changes to reflect dual-backend
- [ ] `tools/transcribe/tests/test_transcribe.py` — UPDATE: tests reference `insanely-fast-whisper` CLI which is no longer the primary interface; update to test the Python `transcribe()` function

No existing tests affected for `bridge/media.py:transcribe_voice()` — it has no dedicated tests.

## Rabbit Holes

- **Matching audio to recording folder**: Don't try to match by audio content or filename. Just use timing — the newest folder after sending is the result. SuperWhisper processes one file at a time.
- **Supporting SuperWhisper's LLM modes**: The `llmResult` field is only populated when using AI modes. Ignore this — we just want raw transcription via `result`.
- **Converting audio formats**: SuperWhisper handles format conversion internally. Don't pre-convert `.ogg` to `.wav`.

## Risks

### Risk 1: Polling race — multiple simultaneous voice messages
**Impact:** Could match the wrong recording to the wrong transcription request
**Mitigation:** For v1, this is acceptable since voice messages are rare and sequential. If it becomes an issue, add a lock or compare audio duration to narrow the match.

## Race Conditions

### Race 1: Concurrent transcription requests
**Location:** `tools/transcribe/__init__.py` — SuperWhisper backend
**Trigger:** Two voice messages arrive within seconds, both trigger transcription
**Data prerequisite:** Each request needs its own recording folder to appear
**State prerequisite:** SuperWhisper must finish processing one file before starting another
**Mitigation:** For v1, accept this limitation. Voice messages are infrequent enough that collisions are unlikely. A future version could use a threading Lock to serialize SuperWhisper requests.

## No-Gos (Out of Scope)

- No support for SuperWhisper's LLM/AI processing modes (just raw transcription)
- No audio format pre-conversion (rely on SuperWhisper's built-in format handling)
- No concurrent transcription support (serialize via timing)
- No cleanup of SuperWhisper's recordings directory (that's SuperWhisper's responsibility)

## Update System

No update system changes required — SuperWhisper is a per-machine app with its own update mechanism. The `SUPERWHISPER_RECORDINGS_DIR` env var is optional and defaults sensibly.

## Agent Integration

No agent integration required — this is a bridge-internal change. The transcription happens automatically when voice messages arrive via Telegram. The agent never calls `transcribe()` directly.

## Documentation

- [ ] Create `docs/features/superwhisper-transcription.md` describing the dual-backend architecture
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `tools/transcribe/README.md` to document SuperWhisper as primary backend
- [ ] Update `tools/transcribe/manifest.json` to reflect dual-backend

## Success Criteria

- [ ] `transcribe("/path/to/audio.ogg")` uses SuperWhisper when the app is running
- [ ] `transcribe()` falls back to OpenAI Whisper API when SuperWhisper is not running
- [ ] `transcribe()` falls back to OpenAI Whisper API when SuperWhisper times out (>30s)
- [ ] Return format is identical regardless of which backend is used
- [ ] `bridge/media.py:transcribe_voice()` calls `tools.transcribe.transcribe()` instead of inline OpenAI code
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (transcribe)**
  - Name: transcribe-builder
  - Role: Implement SuperWhisper backend and update bridge integration
  - Agent Type: builder
  - Resume: true

- **Validator (transcribe)**
  - Name: transcribe-validator
  - Role: Verify transcription works with both backends
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create feature docs and update tool docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Implement SuperWhisper backend
- **Task ID**: build-superwhisper-backend
- **Depends On**: none
- **Validates**: tools/transcribe/tests/test_transcribe.py
- **Assigned To**: transcribe-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_transcribe_superwhisper(audio_source)` function to `tools/transcribe/__init__.py`
- Add `_is_superwhisper_available()` with 60s caching
- Update `transcribe()` to try SuperWhisper first, then fall back to existing OpenAI code
- Update `manifest.json` to document dual-backend
- Update `bridge/media.py:transcribe_voice()` to call `tools.transcribe.transcribe()` instead of inline OpenAI Whisper code

### 2. Validate implementation
- **Task ID**: validate-transcribe
- **Depends On**: build-superwhisper-backend
- **Assigned To**: transcribe-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `transcribe()` returns correct format from SuperWhisper
- Verify fallback works when SuperWhisper is not running
- Run `pytest tests/unit/ -x -q`
- Run `python -m ruff check . && python -m ruff format --check .`

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-transcribe
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/superwhisper-transcription.md`
- Add entry to `docs/features/README.md` index table
- Update `tools/transcribe/README.md`

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: transcribe-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| SuperWhisper backend exists | `grep -c '_transcribe_superwhisper' tools/transcribe/__init__.py` | output > 0 |
| Bridge uses tool module | `grep -c 'tools.transcribe' bridge/media.py` | output > 0 |
| Feature docs exist | `test -f docs/features/superwhisper-transcription.md` | exit code 0 |

---

## Open Questions

None — all questions resolved:
- **.ogg compatibility**: Verified. SuperWhisper accepts `.ogg` files via `open -g -a` and transcribes them successfully (tested with a real Telegram voice message, result in ~1.75s).
- **Recordings cleanup**: Leave recordings for SuperWhisper to manage (per owner decision).

## Spike Validation Log

Tested live on 2026-03-20: sent `data/media/voice_20260203_064508_4327.ogg` to SuperWhisper via `open -g -a`. New recording folder appeared in ~1s, `meta.json` populated in ~2s. Transcription: "I need instructions on how to fix this, because as far as I'm aware you're not able to fix this for me." Model: Ultra (Cloud), processing time: 1750ms. Implementation note: must poll for `meta.json` existence (not just folder creation — folder appears before meta.json is written).
