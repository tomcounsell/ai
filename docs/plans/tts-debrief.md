---
status: docs_complete
type: feature
appetite: Medium
owner: Valor
created: 2026-04-23
tracking: https://github.com/tomcounsell/ai/issues/1136
last_comment_id:
revision_applied: true
---

# TTS Module + /do-debrief Composite Skill

## Problem

**Current behavior.** The agent can transcribe incoming audio via `tools/transcribe/` (SuperWhisper primary, OpenAI Whisper fallback) but has no way to *produce* audio. There is no TTS module, no skill wrapping one, no composite skill for workflows that end in "send a voice message to Telegram." `valor-telegram send --audio` can deliver a pre-existing file, but the agent cannot create one — and even if it could, the existing send path does NOT deliver files as native Telegram voice messages (see Revised #2 in the issue's Recon Summary).

**Desired outcome.** A two-layer feature plus a composite skill, each layer siloed so one can be upgraded without changing the others:

1. `tools/tts/` — Python module with stable `synthesize(...)` API and a pluggable dual-backend: **Kokoro ONNX** local primary, **OpenAI tts-1** cloud fallback. Mirrors `tools/transcribe/` structure exactly.
2. `valor-tts` CLI — thin CLI wrapper exposed via `pyproject.toml [project.scripts]`. Agents invoke it via the Bash tool; `tools/tts/README.md` is the stable reference (same pattern as `tools/transcribe/`).
3. `/do-debrief` composite skill — takes debrief text, calls `valor-tts`, delivers as a native Telegram voice message via the extended relay.

Along the way: the Telegram relay and CLI must learn how to deliver files as native voice messages (not generic audio documents) and honor a `cleanup_file` payload flag so the relay can manage temp-file lifecycle across async retries.

**Explicitly NOT shipped:** a `/tts` SKILL.md wrapper. Rationale: `tools/transcribe/` has no `/transcribe` skill — README + CLI is the stable agent-facing surface. A `/tts` skill would duplicate the README with no added behavior. See CONCERN resolution in Critique Results.

## Freshness Check

**Baseline commit:** `9935778d` (HEAD at plan time)
**Issue filed at:** 2026-04-23 (same day as planning)
**Disposition:** Unchanged

**File:line references re-verified:**
- `tools/transcribe/__init__.py:51-225` — backend-selection + fallback pattern — still holds. `_is_superwhisper_available()` at L51-79 with 60s cache, dispatch at L212, error-as-dict convention throughout.
- `tools/transcribe/` layout — confirmed: `__init__.py`, `manifest.json`, `README.md`, `tests/` (four entries).
- `bridge/telegram_relay.py:199-295` — `_send_queued_message()` uses `telegram_client.send_file()` with **no** `voice_note` kwarg and **no** `attributes` plumbing. Confirmed live.
- `bridge/media.py:213-240` — **receive path only** (inspecting incoming voice attributes via `DocumentAttributeAudio`). Does not help for sending.
- `pyproject.toml:47` — `[project.scripts]` section with 11 existing `valor-*` entries. `valor-transcribe` is NOT among them (issue's context para was inaccurate).
- `.mcp.json` / `mcp_servers/` — confirmed absent via `ls`. CLAUDE.md mentions them but they do not exist.

**Cited sibling issues/PRs re-checked:** None cited in the issue body beyond architectural docs.

**Commits on main since issue was filed (touching referenced files):** None — issue filed today, HEAD unchanged.

**Active plans in `docs/plans/` overlapping this area:** None. `pm-voice-refinement.md` is about PM text tone (natural language, error messages), not audio. No overlap.

**Notes:** The issue's open questions about ffmpeg, fallback provider, and voice-vs-document delivery are resolved via Phase 0.7 research (see Research section). The "MCP exposure" open question is a non-issue — CLI + Python API is the only real pattern.

## Prior Art

- `gh issue list --state closed --search "tts text-to-speech kokoro"` → empty.
- `gh pr list --state merged --search "tts text-to-speech voice"` → empty.
- `gh pr list --state merged --search "voice message"` → no relevant hits.

**This is greenfield work.** No prior TTS attempts in this repo. Closest adjacent work:

- `tools/transcribe/` (shipped PR unknown) — established the dual-backend template this plan mirrors.
- PR #287 (summarizer anti-fabrication) and PR #228 (SDLC architecture) — unrelated domain but establish the siloed-module discipline the plan follows.

No failed prior fixes. The "Why Previous Fixes Failed" section is intentionally omitted.

## Research

External research performed in Phase 0.7 via WebSearch.

**Queries used:**
- `kokoro-onnx Python install model download voices.json 2026`
- `Telethon send_file voice_note DocumentAttributeAudio OGG voice message 2026`
- `OpenAI tts-1 API opus format Python example 2026`

**Key findings:**

1. **Kokoro ONNX install path is well-defined.** `pip install -U kokoro-onnx` installs the runtime. Model files (`kokoro-v1.0.onnx` ~300MB, `voices-v1.0.bin` ~27MB) are distributed separately via GitHub releases and Hugging Face (`onnx-community/Kokoro-82M-v1.0-ONNX`). Not on PyPI. This informs the install-script decision below. Source: https://github.com/thewh1teagle/kokoro-onnx and https://pypi.org/project/kokoro-onnx/

2. **Telethon requires explicit voice-note plumbing.** `client.send_file(chat, path, voice_note=True)` OR `attributes=[DocumentAttributeAudio(duration=N, voice=True, waveform=...)]` is required to deliver as a voice bubble. Plain OGG goes through as an audio *document*. MIME type `audio/x-vorbis+ogg` is recommended over the default `audio/ogg` for voice-message rendering. This confirms the relay must change. Source: https://docs.telethon.dev/en/stable/modules/utils.html and https://github.com/LonamiWebs/Telethon/issues/4170

3. **OpenAI tts-1 supports `response_format="opus"` natively.** `client.audio.speech.create(model="tts-1", voice="alloy", input=text, response_format="opus")` emits Opus-encoded audio in an OGG container. **No transcoding needed for the cloud fallback path.** This eliminates the issue's open question about ffmpeg for the cloud backend — only the Kokoro path (WAV/PCM output) needs transcoding. Source: https://developers.openai.com/api/docs/guides/text-to-speech

All three findings saved to memory at importance 5.0 for future plan reuse.

## Spike Results

### spike-1: Does `valor-telegram send --audio file.ogg` currently deliver as a voice message or a document?

- **Assumption**: "The existing relay does NOT deliver as a voice message."
- **Method**: code-read
- **Finding**: Confirmed NO. `bridge/telegram_relay.py:262` calls `send_file(chat_id, file_arg, caption=..., reply_to=...)` — no `voice_note` kwarg, no `attributes=` plumbing. `tools/valor_telegram.py:340,378` packs files into `file_paths` list and queues to Redis; the relay has no awareness of voice semantics. All OGG/Opus files currently arrive as audio *documents* in Telegram, not voice bubbles.
- **Confidence**: high
- **Impact on plan**: Relay and CLI must be extended — this is NOT a one-line addition to `/do-debrief`. Added as explicit task in Step by Step Tasks.

### spike-2: Is `ffmpeg` required for Kokoro WAV→OGG/Opus transcoding, or is there a pure-Python path?

- **Assumption**: "ffmpeg is the simplest transcoding path."
- **Method**: web-research + code-read
- **Finding**: ffmpeg is already installed on the primary dev machine (`/opt/homebrew/bin/ffmpeg`, v8.0). Pure-Python alternatives (`soundfile` + `opuslib`, `pyogg`) exist but add multiple Python-level deps and a C-extension build. ffmpeg is a single system dep with a well-known install path (`brew install ffmpeg` on macOS, `apt install ffmpeg` on Linux). Additionally: the cloud fallback (OpenAI tts-1) emits Opus directly (finding #3 above), so transcoding is ONLY needed on the Kokoro path.
- **Confidence**: high
- **Impact on plan**: **Decision (a) from issue open questions**: use `ffmpeg` as a runtime-detected system dep. If not present, Kokoro backend is considered unavailable (same pattern as SuperWhisper availability check). Document the install step in `tools/tts/README.md`. No pure-Python encoder path.

### spike-3: Can the OpenAI tts-1 emit Opus directly, eliminating transcoding on the fallback path?

- **Assumption**: "OpenAI tts-1 supports Opus output."
- **Method**: web-research (see finding #3)
- **Finding**: Yes. `response_format="opus"` is natively supported. No transcoding needed on the fallback path.
- **Confidence**: high
- **Impact on plan**: Cloud path is simpler than Kokoro path. Kokoro path: `kokoro.create(text)` → WAV bytes → ffmpeg → OGG/Opus. Cloud path: `openai.audio.speech.create(..., response_format="opus")` → OGG/Opus bytes directly.

### spike-4: What's the duration/file-size envelope for a ~2-minute debrief?

- **Assumption**: "A 2-minute voice message at Telegram voice bitrate is well under any upload limit."
- **Method**: code-read + external knowledge
- **Finding**: Telegram voice messages use ~16-24kbps Opus. 2 minutes = ~300-360KB. Telegram's voice-message upload limit is 10MB (documents are 2GB). Comfortable envelope. Duration for `DocumentAttributeAudio(duration=N)` comes from the synth backend (Kokoro's `create()` returns audio samples at a known sample rate; duration = `len(samples) / sr`). OpenAI tts-1 response does not include duration, so we must compute it from the bytes (probe with `ffprobe -show_format` or decode the Opus headers).
- **Confidence**: medium
- **Impact on plan**: Duration computation needs a helper. `ffprobe` is bundled with ffmpeg so this does not add a dep. Added as sub-task.

## Data Flow

**Successful synthesis + voice-message delivery flow:**

1. **Entry point**: Agent (PM or Dev session) invokes `/do-debrief` with debrief text.
2. **`tools/tts/__init__.py:synthesize(text, voice, output_path)`**: Dispatches to one of:
   - **Kokoro path** — `_is_kokoro_available()` (cached 60s) checks model files + `onnxruntime` import → `_synthesize_kokoro(text, voice)` returns WAV bytes → `_transcode_wav_to_opus(wav_bytes)` (ffmpeg subprocess) → OGG/Opus bytes written to `output_path`.
   - **Cloud path** — `_synthesize_openai(text, voice)` calls `openai.audio.speech.create(..., response_format="opus")` → OGG/Opus bytes written to `output_path`.
3. **Return dict**: `{"path": output_path, "duration": seconds, "backend": "kokoro"|"cloud", "error": None|str, "voice": voice, "format": "opus"}`.
4. **`/do-debrief` skill**: Receives the dict. If `error` is set, surfaces to agent. Otherwise extracts `path` and `duration`.
5. **`tools/valor_telegram.py:send` (extended)**: New `--voice-note` flag passes markers into the Redis outbox payload: `payload["voice_note"] = True`, `payload["duration"] = duration`, and `payload["cleanup_file"] = True` (set by `/do-debrief`; CLI users can omit to keep the file on disk after send).
6. **`bridge/telegram_relay.py:_send_queued_message` (extended)**: When `voice_note` is True, calls `send_file(chat_id, path, voice_note=True, attributes=[DocumentAttributeAudio(duration=int(duration), voice=True, waveform=...)])`. Waveform is optional — Telethon will pass an empty one if omitted, which is fine. **After the send succeeds** (or after the payload is moved to the DLQ on terminal failure), if `payload.get("cleanup_file")` is True the relay unlinks `path` inside a try/except that never raises.
7. **Output**: Telegram displays a native voice-message bubble with waveform UI; the relay removes the temp file; `/do-debrief` has already returned.

**Kokoro unavailable fallback flow:**
- Step 2 detects Kokoro unavailable → routes to cloud path.
- Everything else identical. Caller never sees the backend switch.

## Architectural Impact

- **New dependencies:**
  - `kokoro-onnx>=0.4.0` (runtime) — new top-level ML dep. First ONNX-based inference lib in the repo.
  - `onnxruntime>=1.17.0` (transitive via kokoro-onnx; may need explicit pin).
  - `ffmpeg` (system dep, runtime-detected, not a hard requirement).
  - Kokoro model files (~330MB total, downloaded lazily; not committed to repo; `.gitignore` must exclude).
- **Interface changes:**
  - NEW: `tools/tts/synthesize()` public API.
  - NEW: `valor-tts` CLI entry.
  - EXTEND: `tools/valor_telegram.py` — add `--voice-note` flag + payload field.
  - EXTEND: Redis outbox payload schema — adds optional `voice_note: bool`, `duration: float`, and `cleanup_file: bool` fields. All additive; absent = current behavior. `cleanup_file` is orthogonal to `voice_note` (the relay will honor it for any payload, not just voice notes), but in practice `/do-debrief` is the only caller that sets it.
  - EXTEND: `bridge/telegram_relay.py:_send_queued_message` — branch on `voice_note` payload field; additionally honor `cleanup_file` by unlinking `path` after successful send or terminal DLQ placement.
- **Coupling:** Additive. `tools/tts/` is siloed; the relay change is isolated to one function. `/do-debrief` depends on both but is itself a thin orchestrator.
- **Data ownership:** Audio files are temp files created by `/do-debrief` (or `valor-tts` CLI) but **owned by the relay from the moment they are pushed to the Redis outbox**. `/do-debrief` creates the file via `tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)`, then sets `cleanup_file: True` in the outbox payload and exits. The relay is the sole deleter: it deletes the file after a successful send **or** after the payload is moved to the dead-letter queue on terminal failure. This single-source ownership is required because the relay is asynchronous with up to 3 retries over minutes — synchronous deletion by `/do-debrief` would race the retry loop and hit the "file not found at send time" branch at `bridge/telegram_relay.py:252-257`.
- **Reversibility:** High. The CLI flag, relay branch, and module are all removable without breaking existing flows. Removing Kokoro would leave cloud-only synthesis working.

## Appetite

**Size:** Medium

**Team:** Solo dev, plan-maker (complete), plan-critique, code-reviewer

**Interactions:**
- PM check-ins: 1-2 (resolve open questions before build, review at critique stage)
- Review rounds: 1 (standard PR review; voice-message delivery verified via manual smoke test)

Medium appetite because: three distinct components + a relay change + a new ML dependency. Not Small because of the ML dep footprint + cross-component plumbing. Not Large because each component has a clear, narrow interface and extensive precedent (`tools/transcribe/`).

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `OPENAI_API_KEY` set | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('OPENAI_API_KEY')"` | Cloud fallback credential (already required by transcribe). |
| `ffmpeg` installed | `command -v ffmpeg` | Kokoro WAV→Opus transcoding. Optional at install time; Kokoro backend disables itself without it. |
| Redis reachable | `python -c "import redis; r=redis.from_url('redis://localhost:6379/0'); r.ping()"` | Outbox queue for relay-based delivery. Already required by the bridge. |
| Telegram bridge running (for live voice-message test) | `./scripts/valor-service.sh status` | Required ONLY for the end-to-end smoke test, not for unit tests. |

Run all checks: `python scripts/check_prerequisites.py docs/plans/tts-debrief.md`

## Solution

### Key Elements

- **`tools/tts/__init__.py`**: Public `synthesize()` function. Dual-backend with 60s availability cache, error-as-dict convention. Mirrors `tools/transcribe/__init__.py` structure one-for-one.
- **`tools/tts/manifest.json`**: Backend list (kokoro primary, openai-tts secondary), capabilities `["synthesize"]`, supported formats `["opus"]`, env requirements `["OPENAI_API_KEY"]`.
- **`tools/tts/README.md`**: Dual-backend explanation, install steps (kokoro-onnx + ffmpeg + model download script), Python and CLI usage examples.
- **`tools/tts/tests/test_tts.py`**: Mock-based unit tests for backend selection, availability caching, fallback-on-error, and dispatch dict shape. Mirrors `tools/transcribe/tests/test_transcribe.py`.
- **`tools/tts/cli.py`**: Thin CLI wrapper. `valor-tts --text "hello" --output /tmp/out.ogg [--voice af_bella] [--force-cloud]`.
- **`scripts/download_kokoro_models.py`**: One-shot script to fetch `kokoro-v1.0.onnx` + `voices-v1.0.bin` from HuggingFace to a known path (`~/.cache/kokoro-onnx/` by default, overridable via env var). Idempotent.
- **`tools/valor_telegram.py` (extended)**: Add `--voice-note` flag. When set, writes `voice_note: True` and `duration: <float>` to the Redis outbox payload.
- **`bridge/telegram_relay.py` (extended)**: In `_send_queued_message`, branch on `message.get("voice_note")`. When True, call `send_file` with `voice_note=True` and `attributes=[DocumentAttributeAudio(duration=int(duration), voice=True, waveform=b"")]`. Separately, if `message.get("cleanup_file")` is True, unlink `path` after successful send or DLQ placement (try/except; never raises).
- **`.claude/skills/do-debrief/SKILL.md`**: ~140 lines. User-invocable composite workflow: (1) receive/compose debrief text, (2) call `valor-tts` CLI to produce `/tmp/debrief_<uuid>.ogg`, (3) call `valor-telegram send --chat <target> --voice-note --cleanup-after-send --audio /tmp/debrief_<uuid>.ogg`. The CLI sets `cleanup_file: True` in the outbox payload so the **relay** handles deletion after send. `/do-debrief` only cleans up if synthesis itself raises before the payload is pushed. Cite `/do-build` as the precedent for composite skill shape.

  **NOTE: No `/tts` skill is shipped.** `tools/transcribe/` has no `/transcribe` skill — the CLI + README is the stable agent-facing surface. TTS mirrors that pattern. Agents invoke `valor-tts` via the Bash tool directly, and `/do-debrief` is the only composite skill. A wrapper `/tts` skill would be pure indirection that duplicates `tools/tts/README.md`. If a need for a dedicated skill emerges later (e.g., multi-step TTS workflows that aren't debriefs), it can be added as a follow-up.
- **`docs/features/tts.md`**: Feature doc explaining the capability, dual-backend design, install/setup, troubleshooting. Entry added to `docs/features/README.md`.

### Flow

**Agent debrief flow:**
Agent has debrief text → `/do-debrief "summary text" --chat "Dev: Valor"` → synthesize OGG/Opus via Kokoro or OpenAI → queue voice-note to Redis outbox with `cleanup_file: True` → `/do-debrief` returns → relay sends via Telethon with `voice_note=True` → **Telegram voice-message bubble appears in chat** → relay unlinks the temp file.

**Manual CLI flow:**
Dev at shell → `valor-tts --text "hello" --output /tmp/out.ogg` → play back locally to verify → `valor-telegram send --chat X --voice-note --audio /tmp/out.ogg` (no `--cleanup-after-send` by default for manual use) → Telegram voice bubble.

### Technical Approach

- **Mirror `tools/transcribe/` structure and idioms exactly.** Copy-paste the 60s cache pattern, the `_is_X_available()` helper shape, the dispatch-with-fallback logic at the equivalent of L212, the error-as-dict return convention. Future readers should see these two tools as mirror images.
- **Kokoro backend is opt-in at runtime with a two-stage availability check, both stages cached together for 60s:**
  1. **Static stage (cheap, always runs first):** model files exist at the configured path, `kokoro_onnx` importable, `ffmpeg` on PATH.
  2. **Dynamic probe (runs only if the static stage passes AND this is the first call within the 60s cache window):** a one-character synthesis (`_synthesize_kokoro("a", voice="af_bella")`) that must return WAV bytes without raising. This catches ABI/accelerator regressions (Risk 3) that the static stage misses.

  If either stage fails, `_is_kokoro_available()` returns False and the cached result is reused for 60s. Never raises. This is the single canonical definition — Risk 3's mitigation refers back here, not the other way around.
- **Cloud backend is always available if `OPENAI_API_KEY` is set.** Same pattern as transcribe — no availability cache, just a try/except at call time.
- **Backend-selection observability.** Every dispatch emits one structured log line at INFO with `{"event": "tts.backend_selected", "backend": "kokoro"|"cloud", "reason": "primary"|"kokoro_unavailable"|"kokoro_synth_error"|"force_cloud", "voice": "..."}`. First cloud fallback in a given process additionally emits a WARN-level `tts.kokoro_unavailable` with the root cause from the last availability check so silent cloud spend is traceable.
- **Model files download lazily via `scripts/download_kokoro_models.py`.** Not committed. Default path `~/.cache/kokoro-onnx/`. Override via `KOKORO_MODELS_DIR` env var. Script is idempotent and prints progress. README tells the user to run it once.
- **Format is fixed at OGG/Opus for v1.** The `synthesize()` signature accepts `format` for future extensibility but rejects anything other than `"opus"` with an explicit error dict. Keeps the happy path simple.
- **Voice parameter is validated against the *selected* backend, with a fallback-remap table.** Kokoro has ~40 voice names (`af_bella`, `am_adam`, etc.). OpenAI tts-1 has 6 (`alloy`, `echo`, `fable`, `onyx`, `nova`, `shimmer`). Algorithm:
  1. Caller passes `voice=<name>` or `voice="default"`.
  2. Dispatch picks a backend via `_is_kokoro_available()`.
  3. Resolve the voice name against the selected backend's vocabulary. If the name is valid there, use it.
  4. If the name is valid on the *other* backend only (i.e., caller asked for `"af_bella"` but we selected cloud), remap via the `_VOICE_FALLBACK_MAP` dict (e.g., `{"af_bella": "nova", "am_adam": "onyx", ...}`). Log at INFO: `{"event": "tts.voice_remapped", "from": "af_bella", "to": "nova", "reason": "backend_fallback"}`.
  5. If the name is unknown to both backends, return `{"error": "unknown voice: X. Available kokoro: [...]. Available openai: [...]"}` without calling either backend.
  6. `"default"` always resolves to the backend's canonical voice (`af_bella` on kokoro, `nova` on cloud) — no remap needed.

  This closes the silent-mismatch gap where a Kokoro-only voice would hit the cloud path and either fail loudly at the API boundary or (worse) succeed with an unintended voice if the names happened to overlap.
- **Voice-note relay change is narrowly scoped.** One branch in `_send_queued_message`, gated on `voice_note` payload field. Existing non-voice callers see no change. CLI gets `--voice-note` flag that sets the field.
- **Duration is computed from the synth output before send.** Kokoro: `len(samples) / sample_rate`. Cloud: probe with `ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 <file>`. Stored in the outbox payload so the relay can set `DocumentAttributeAudio(duration=...)`.
- **CLAUDE.md cleanup.** The stale `.mcp.json` / `mcp_servers/` references should be either corrected to "tools are exposed via `pyproject.toml [project.scripts]` and direct Python imports" OR the references deleted. The fix is a side task in the documentation phase of the build.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `tools/tts/__init__.py` — no `except Exception: pass` blocks. All exceptions must be caught and returned as `{"error": str(e)}` dict. Unit test asserts this for each backend.
- [ ] `_is_kokoro_available()` — any exception during availability check returns False (matches `_is_superwhisper_available()` convention). Test: force `onnxruntime` import failure, assert False returned, assert no exception propagates.
- [ ] `bridge/telegram_relay.py` (extended) — if `DocumentAttributeAudio` construction fails (e.g., bad duration), log warning and fall back to non-voice send. Test: pass `duration=None` to the relay branch, assert document-send path is used and warning is logged.
- [ ] If ffmpeg subprocess fails mid-transcode, return `{"error": "ffmpeg failed: ..."}`. Test: mock subprocess to return non-zero, assert error dict.

### Empty/Invalid Input Handling
- [ ] Empty text (`synthesize("")`) → `{"error": "text cannot be empty"}`. No API call made. Test covers.
- [ ] Whitespace-only text → same as empty. Test covers.
- [ ] Text over 4096 characters (OpenAI tts-1 limit) → `{"error": "text too long: 5000 chars (max 4096)"}`. Test covers.
- [ ] Invalid voice name → `{"error": "unknown voice: X. Available: [...]"}`. Test covers.
- [ ] Invalid format (not "opus") → `{"error": "unsupported format: wav. Only 'opus' supported."}`. Test covers.

### Error State Rendering
- [ ] `/do-debrief` error path: if `synthesize()` returns an error dict, skill surfaces it to the agent verbatim (does NOT swallow). Test: mock `synthesize()` to return error, assert stderr contains the error message.
- [ ] File cleanup after send: the **relay** deletes the temp file when `payload["cleanup_file"] is True` — on success OR after DLQ placement. `/do-debrief` never deletes the file itself once the payload has been pushed. Test: push a payload with `cleanup_file: True` + successful send → file removed by relay; push a payload + mock a 3x retry failure → file removed when DLQ'd. Additional test: `/do-debrief` exits before relay runs → file still exists (relay hasn't processed yet), not a bug.
- [ ] Synthesis-stage cleanup: if `synthesize()` itself raises mid-run (before the payload is pushed to the outbox), `/do-debrief` deletes the partially-written temp file in a `try/finally`. This is the only pre-push cleanup responsibility on the caller.

## Test Impact

No existing tests affected — this is a greenfield feature. All new tests live in `tools/tts/tests/` (unit, mock-based) plus an optional integration test in `tests/integration/test_tts_debrief.py` that exercises the full synthesize → voice-message path (skipped in CI by default via `@pytest.mark.skipif(not os.getenv("LIVE_TELEGRAM"), ...)`).

New test files created:
- `tools/tts/tests/test_tts.py` — unit tests for `synthesize()`, backend selection, fallback, caching, error paths, the two-stage `_is_kokoro_available()` probe (static stage + one-char dynamic stage), and the `_VOICE_FALLBACK_MAP` remap on backend fallback
- `tools/tts/tests/test_cli.py` — CLI arg parsing + exit codes
- `tests/unit/test_telegram_relay_voice_note.py` — relay voice_note branch (mock Telethon client); also asserts the relay unlinks the temp file after successful send when `cleanup_file: True`, and unlinks when the payload is moved to the DLQ after retry exhaustion
- `tests/unit/test_valor_telegram_voice_flag.py` — CLI --voice-note flag threads through to payload; asserts `/do-debrief` sets `cleanup_file: True`, direct CLI invocations do not
- `tests/unit/test_tts_observability.py` — asserts the `tts.backend_selected` INFO log fires on every dispatch and `tts.kokoro_unavailable` WARN fires on first fallback in a process
- `tests/integration/test_tts_debrief.py` (optional, gated) — end-to-end smoke test that includes a post-send assertion that the temp file was removed by the relay

## Rabbit Holes

- **Do NOT implement voice cloning or custom voice training.** Kokoro ships fixed voices; OpenAI tts-1 has 6 stock voices. That's the set. Custom voice training is a separate project.
- **Do NOT build a Python-level Opus encoder.** `ffmpeg` subprocess is good enough. `soundfile`/`pyogg`/`opuslib` all add deps and complexity for a path that's already served by a system tool.
- **Do NOT support streaming synthesis.** Both backends emit full audio in one call. Streaming would need a protocol change in the outbox (chunked delivery) and a corresponding Telethon streaming call. Not worth it for 2-minute debriefs.
- **Do NOT ship a batch/multi-speaker API.** One text → one file. Multi-voice dialogues are out of scope; callers can synthesize multiple files and concat via ffmpeg if ever needed.
- **Do NOT add MCP server wrapping.** `.mcp.json` / `mcp_servers/` don't exist in this repo. Current precedent is Python + CLI only. Adding MCP would be scope creep and have zero agent-facing benefit.
- **Do NOT replace `valor-telegram send --audio`.** The existing flag stays and still delivers documents. The new `--voice-note` is additive. Migration later if ever needed.

## Risks

### Risk 1: Kokoro model download fails or is slow on first run
**Impact:** New developers cannot use the Kokoro backend out of the box. Falls back to cloud (which costs money per call).
**Mitigation:** `scripts/download_kokoro_models.py` prints clear progress + resume hints. README flags this as a one-time setup. If the download script fails, the cloud fallback still works — system is never fully broken.

### Risk 2: Telethon voice-note API changes between versions
**Impact:** Voice messages suddenly render as audio documents after a Telethon upgrade.
**Mitigation:** `telethon==1.42.0` is pinned exact in `pyproject.toml`. Any upgrade goes through `/update` which includes regression checks. The integration test (gated on `LIVE_TELEGRAM`) catches this on the next bridge restart.

### Risk 3: ONNX runtime has different ABI/accelerator behavior on different machines (Apple Silicon vs Intel Mac vs Linux)
**Impact:** Kokoro works on dev machine but fails silently on other installations.
**Mitigation:** Stage 2 of the canonical availability check defined in Solution → Technical Approach — the one-character `_synthesize_kokoro("a", ...)` probe, cached 60s with stage 1. If the probe raises, the backend is marked unavailable, the WARN observability line fires, and the cloud fallback takes over. The probe adds ~150ms on the first call of each 60s window; subsequent calls are cache hits and free.

### Risk 4: OpenAI tts-1 output format changes (e.g., container switch from OGG to MP4)
**Impact:** Cloud fallback produces files Telegram can't deliver as voice.
**Mitigation:** Unit test mocks + integration test catches this. Pinned via OpenAI client version range. If it happens, we transcode via ffmpeg — same path Kokoro already uses. Acceptable fallback.

### Risk 5: Temp file left on disk if process dies or relay gives up mid-retry
**Impact:** Disk slowly fills with orphaned `.ogg` files under `$TMPDIR`.
**Mitigation:** The **relay owns cleanup** — `/do-debrief` pushes the payload with `cleanup_file: True` and exits; the relay deletes the file after a successful send or after moving the payload to the dead-letter queue on terminal failure (retry exhaustion). This prevents the race between synchronous cleanup and the asynchronous retry loop that would otherwise delete the file before the relay could send it. On a hard process kill of the relay itself, the OS eventually reaps `$TMPDIR`. The DLQ replay path reads the file path from the payload; if the file is missing at replay time, the DLQ entry is marked `file_missing` and skipped (hygiene issue only, no data loss — the voice message was never delivered).

## Race Conditions

### Race 1: Kokoro availability cache stale after model files deleted mid-run
**Location:** `tools/tts/__init__.py:_is_kokoro_available()` (to be written, mirroring `transcribe` L51)
**Trigger:** Model files exist at availability-check time, are deleted before next synth call (within 60s cache window), cache still reports True.
**Data prerequisite:** Model files present at `KOKORO_MODELS_DIR`.
**State prerequisite:** 60s cache unexpired.
**Mitigation:** `_synthesize_kokoro()` catches FileNotFoundError / kokoro-onnx load errors and returns error dict. Dispatch code then falls back to cloud. Cache staleness cannot cause a crash, only a one-call wasted availability-check result.

### Race 2: Redis outbox payload race — relay dequeues before bridge process is re-read after CLI push
**Location:** `tools/valor_telegram.py:380-385` + `bridge/telegram_relay.py:199`
**Trigger:** CLI pushes payload to Redis; relay consumes before the CLI-side validation completes.
**Data prerequisite:** Valid payload in the outbox queue.
**State prerequisite:** Relay actively consuming the queue.
**Mitigation:** Redis `RPUSH` is atomic. The payload is complete before the push returns. No race. The existing `--audio` path has the same shape and works correctly.

No other race conditions identified. Synthesis is synchronous and single-threaded inside `tools/tts/`. The `/do-debrief` skill runs sequentially (synth → push to outbox → exit); cleanup happens asynchronously in the relay after the send completes or is DLQ'd.

## No-Gos (Out of Scope)

- **Custom voice training / voice cloning.** Stock voices only.
- **Streaming / chunked synthesis.** Full-file synth only.
- **Multi-speaker dialogues.** One text → one voice → one file.
- **Real-time voice chat / calls.** This is async voice-message production, not live telephony.
- **Non-Opus output formats.** v1 is Opus-only. MP3/WAV/AAC deferred.
- **MCP server exposure.** Not in current repo pattern; see "Rabbit Holes."
- **SSML input.** Both backends accept plain text. Advanced markup deferred.
- **Voice-message receive parsing.** Already handled by `tools/transcribe/` + `bridge/media.py`.
- **Rewriting the outbox schema.** `voice_note` + `duration` are additive fields; backward compat preserved.

## Update System

- **New dep `kokoro-onnx` in `pyproject.toml`.** `/update` skill syncs deps via `uv sync` or equivalent — already part of the standard flow. No update-script changes needed for dep sync.
- **New model files under `~/.cache/kokoro-onnx/`.** NOT synced by `/update`. Fresh machines must run `scripts/download_kokoro_models.py` manually after first update. Document this in `tools/tts/README.md` and add a note to the `/update` skill's post-update checklist.
- **New system dep `ffmpeg`.** Not synced by `/update`. Machines without ffmpeg will get cloud-only TTS (graceful). README documents `brew install ffmpeg`.
- **CLI entry `valor-tts`.** Added to `pyproject.toml [project.scripts]`; available after `pip install -e .` (which `/update` already runs).
- **Telegram relay change.** Bridge restart required after code update. `/update` already runs `./scripts/valor-service.sh restart`. No new steps.
- **Documented one-liner for the post-update first-run on a machine that wants Kokoro:**
  ```bash
  brew install ffmpeg && python scripts/download_kokoro_models.py
  ```

## Agent Integration

**No MCP integration required.** `.mcp.json` and `mcp_servers/` do not exist in this repo. All current tools are agent-accessible via:

- CLI entry points in `pyproject.toml [project.scripts]` — agents invoke via `Bash` tool
- Direct Python imports for tools the bridge calls internally

TTS follows this precedent:
- `valor-tts` CLI added to `[project.scripts]` — agents can invoke `valor-tts --text "..." --output /tmp/out.ogg` via the Bash tool.
- `tools/tts/README.md` is the canonical reference (mirrors `tools/transcribe/README.md`). No `/tts` SKILL.md — CONCERN 4 resolution.
- `/do-debrief` composite skill orchestrates TTS + Telegram voice-note send with `cleanup_file: True` so the relay owns temp-file cleanup.

**Bridge changes:**
- `bridge/telegram_relay.py` extends `_send_queued_message` to handle `voice_note` payload field.
- No imports of `tools.tts` from the bridge — it talks to the relay via Redis only, same as all other tools.

**Integration tests:**
- `tests/integration/test_tts_debrief.py` — end-to-end: invoke `/do-debrief` → verify Telegram voice-message arrives. Gated on `LIVE_TELEGRAM=1` env var and presence of `OPENAI_API_KEY`.
- Unit tests cover relay branch + CLI flag in isolation.

**CLAUDE.md cleanup side task:** The current `CLAUDE.md` references `.mcp.json` and `mcp_servers/` which don't exist. This plan adds a task to remove or correct those references during the documentation step. Low-risk, one-line edits.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/tts.md` describing:
  - What TTS is in this system (text → OGG/Opus audio)
  - Dual-backend design (Kokoro primary, OpenAI tts-1 fallback)
  - How voice-message delivery works (relay + Telethon `voice_note`)
  - CLI usage (`valor-tts`)
  - Python API (`tools.tts.synthesize`)
  - `/do-debrief` skill invocation (and why there is no `/tts` skill — see CONCERN 4 resolution)
  - Install/setup (kokoro-onnx pip + model download script + ffmpeg)
  - Troubleshooting (Kokoro unavailable, voice message arrives as document, etc.)
- [ ] Add `tts.md` entry to `docs/features/README.md` index table with a one-line summary.

### External Documentation Site
- [ ] N/A — this repo does not use Sphinx / Read the Docs / MkDocs.

### Inline Documentation
- [ ] Docstrings on `synthesize()`, `_synthesize_kokoro()`, `_synthesize_openai()`, `_is_kokoro_available()`, and the relay branch.
- [ ] Comments on the duration-computation helper and the voice-mapping dict.
- [ ] `tools/tts/manifest.json` `description` field set.
- [ ] `tools/tts/README.md` follows `tools/transcribe/README.md` structure.

### CLAUDE.md cleanup (side task)
- [ ] Remove or correct references to `.mcp.json` and `mcp_servers/` in `CLAUDE.md` (both project-local and `~/.claude/CLAUDE.md` if applicable — only edit the repo one). Replace with a one-line note that agent-facing tools live under `tools/<name>/` with CLI entry points in `pyproject.toml`.

## Success Criteria

- [ ] `tools/tts/` exists with `__init__.py`, `manifest.json`, `README.md`, `tests/` — layout matches `tools/transcribe/` exactly (`diff <(ls tools/transcribe) <(ls tools/tts)` shows no structural difference).
- [ ] `synthesize(text="hello")` returns an OGG/Opus file playable by `ffplay` / QuickTime.
- [ ] Backend selection works: with Kokoro available → `backend: "kokoro"`; with Kokoro disabled (via env override) → `backend: "cloud"`.
- [ ] Availability cache works (second call within 60s does not re-run the check). Asserted in unit test via mock-patched time.
- [ ] Fallback-on-error works: mock Kokoro to raise mid-synth → cloud path is used, no exception propagates. Asserted in unit test.
- [ ] `valor-tts --text "hello" --output /tmp/out.ogg` creates a valid file.
- [ ] `valor-telegram send --chat "Dev: Valor" --voice-note --audio /tmp/out.ogg` arrives as a native Telegram voice message (waveform bubble, not audio-document tile). **Verified manually on at least one live chat.**
- [ ] `/do-debrief` skill file exists at `.claude/skills/do-debrief/SKILL.md`, ≤150 lines, end-to-end flow documented. (No `/tts` skill is shipped — agents invoke `valor-tts` directly via Bash; see Solution → Key Elements for rationale.)
- [ ] `/do-debrief` invocation with 2-minute debrief text produces and delivers a voice message end-to-end.
- [ ] `docs/features/tts.md` exists and is indexed in `docs/features/README.md`.
- [ ] `CLAUDE.md` no longer references non-existent `.mcp.json` / `mcp_servers/` (or explicitly documents them as future-looking).
- [ ] All unit tests pass: `pytest tools/tts/tests/ tests/unit/test_telegram_relay_voice_note.py tests/unit/test_valor_telegram_voice_flag.py -q`.
- [ ] Lint + format clean: `python -m ruff check . && python -m ruff format --check .`.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] Plan documentation checkboxes satisfied (this section + Documentation section).

## Team Orchestration

### Team Members

- **Builder (tts-module)**
  - Name: tts-builder
  - Role: Build `tools/tts/` module + CLI + tests + manifest + README.
  - Agent Type: builder
  - Resume: true

- **Builder (telegram-voice-note)**
  - Name: telegram-voice-builder
  - Role: Extend `tools/valor_telegram.py` CLI with `--voice-note` flag + extend `bridge/telegram_relay.py` with voice_note branch. Add unit tests for both.
  - Agent Type: builder
  - Resume: true

- **Builder (skills)**
  - Name: skills-builder
  - Role: Create `.claude/skills/do-debrief/SKILL.md`. Wire `/do-debrief` to invoke `valor-tts` then `valor-telegram send --voice-note` with `cleanup_file: True` in the payload. Does NOT create a `/tts` skill (README + CLI is the agent-facing surface, mirroring `tools/transcribe/`).
  - Agent Type: builder
  - Resume: true

- **Builder (model-download-script)**
  - Name: model-script-builder
  - Role: Create `scripts/download_kokoro_models.py`. Idempotent fetch from HuggingFace to `~/.cache/kokoro-onnx/` (env-overridable). Progress output.
  - Agent Type: builder
  - Resume: true

- **Documentarian**
  - Name: tts-documentarian
  - Role: Create `docs/features/tts.md`, update `docs/features/README.md`, correct CLAUDE.md MCP references.
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: tts-validator
  - Role: Verify all success criteria, run test suite, confirm relay change doesn't regress non-voice sends. Manual smoke-test checklist for live voice-message delivery.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Build TTS module (tools/tts/)
- **Task ID**: build-tts-module
- **Depends On**: none
- **Validates**: `tools/tts/tests/test_tts.py`, `tools/tts/tests/test_cli.py`
- **Informed By**: spike-1 (confirmed voice-note relay change is scope), spike-2 (confirmed ffmpeg path), spike-3 (confirmed OpenAI Opus-native), spike-4 (duration via ffprobe)
- **Assigned To**: tts-builder
- **Agent Type**: builder
- **Parallel**: true
- Copy structural skeleton from `tools/transcribe/` (`__init__.py`, `manifest.json`, `README.md`, `tests/`).
- Implement `synthesize(text, voice="default", output_path, format="opus") -> dict`.
- Implement `_is_kokoro_available()` with 60s cache (mirror `_is_superwhisper_available()`).
- Implement `_synthesize_kokoro(text, voice)` → WAV bytes → ffmpeg subprocess → OGG/Opus.
- Implement `_synthesize_openai(text, voice)` using `openai.audio.speech.create(..., response_format="opus")`.
- Implement voice-name mapping dict: `{"default": ("kokoro: af_bella", "openai: alloy")}`, at minimum one entry plus passthrough.
- Implement `_compute_duration_opus(path) -> float` via `ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1`.
- Error-as-dict convention throughout. No raises in public API.
- Mock-based unit tests in `tests/test_tts.py`: availability cache hit, availability cache miss, cloud path, Kokoro path, Kokoro-fails-fallback, empty text, long text, invalid voice, invalid format.
- Add `valor-tts = "tools.tts.cli:main"` to `pyproject.toml`.
- Implement `tools/tts/cli.py` with `--text`, `--output`, `--voice`, `--force-cloud` flags.
- CLI test in `tests/test_cli.py`.

### 2. Build model download script
- **Task ID**: build-model-script
- **Depends On**: none
- **Validates**: `scripts/download_kokoro_models.py --help` exits 0; dry-run mode prints intended destinations.
- **Informed By**: Research finding #1 (Kokoro v1.0 file locations)
- **Assigned To**: model-script-builder
- **Agent Type**: builder
- **Parallel**: true
- Fetch `kokoro-v1.0.onnx` and `voices-v1.0.bin` from HuggingFace (`onnx-community/Kokoro-82M-v1.0-ONNX`).
- Destination: `$KOKORO_MODELS_DIR` or `~/.cache/kokoro-onnx/`.
- Idempotent (skip if files present + sha256 matches manifest).
- Progress bar via `rich` (already a dep).
- Hard-fail with clear error if network unavailable.
- Add `.gitignore` entry for the cache dir if it's anywhere inside repo.

### 3. Extend Telegram relay + CLI for voice notes
- **Task ID**: build-telegram-voice-note
- **Depends On**: none
- **Validates**: `tests/unit/test_telegram_relay_voice_note.py`, `tests/unit/test_valor_telegram_voice_flag.py`
- **Informed By**: spike-1 (relay has no voice awareness today), Research finding #2 (Telethon requires explicit voice_note)
- **Assigned To**: telegram-voice-builder
- **Agent Type**: builder
- **Parallel**: true
- Extend `bridge/telegram_relay.py:_send_queued_message`: when `message.get("voice_note")` is True, call `send_file(chat_id, path, voice_note=True, attributes=[DocumentAttributeAudio(duration=int(message.get("duration", 0)), voice=True, waveform=b"")])`.
- Unit test (mock Telethon client) asserting the `voice_note=True` kwarg is passed + `DocumentAttributeAudio` attribute set.
- Extend `tools/valor_telegram.py` `send` subcommand: add `--voice-note` flag. When set and a file is provided, add `voice_note: True` and `duration` (from `_compute_duration_opus` or ffprobe) to the Redis payload.
- Unit test asserting the flag threads through to the payload.
- Backward compat: existing calls without the flag produce identical payloads to today.

### 4. Validate TTS module build
- **Task ID**: validate-tts-module
- **Depends On**: build-tts-module
- **Assigned To**: tts-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tools/tts/tests/ -q`.
- `diff <(ls tools/transcribe) <(ls tools/tts)` — expect structural parity.
- `valor-tts --help` exits 0.
- Hand-invoke `valor-tts --text "hello" --output /tmp/t.ogg --force-cloud` — file exists, `file /tmp/t.ogg` reports OGG.

### 5. Validate relay + CLI voice-note extension
- **Task ID**: validate-telegram-voice-note
- **Depends On**: build-telegram-voice-note
- **Assigned To**: tts-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_telegram_relay_voice_note.py tests/unit/test_valor_telegram_voice_flag.py -q`.
- Grep confirms `voice_note=True` + `DocumentAttributeAudio` exist in `bridge/telegram_relay.py`.
- Grep confirms `--voice-note` flag exists in `tools/valor_telegram.py`.
- Existing non-voice-note callers unchanged (no regressions in `tests/unit/test_telegram_relay*.py` if present).

### 6. Build /do-debrief skill
- **Task ID**: build-skills
- **Depends On**: build-tts-module, build-telegram-voice-note
- **Validates**: `.claude/skills/do-debrief/SKILL.md` exists and is ≤150 lines.
- **Assigned To**: skills-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `.claude/skills/do-debrief/SKILL.md` modeled on `.claude/skills/do-build/SKILL.md`. Frontmatter with `allowed-tools: Bash`, `user-invocable: true`. Document the three-step flow (synth → push to outbox with `cleanup_file: True` → exit). Include an explicit example invocation, error handling, and a note that the relay owns file cleanup after push. Skill file ≤150 lines.
- **Do NOT create `.claude/skills/tts/SKILL.md`.** Agents invoke the `valor-tts` CLI via the Bash tool; `tools/tts/README.md` is the stable reference. Rationale lives in Solution → Key Elements.

### 7. Validate skills
- **Task ID**: validate-skills
- **Depends On**: build-skills
- **Assigned To**: tts-validator
- **Agent Type**: validator
- **Parallel**: false
- `wc -l .claude/skills/do-debrief/SKILL.md` — ≤150 lines.
- Frontmatter valid YAML.
- Skill references only CLI entry points that exist (grep the SKILL.md for command strings and verify each matches `pyproject.toml [project.scripts]`).
- Confirm the skill file pushes the payload with `cleanup_file: True` and does NOT call `os.unlink` on the temp path after the push (grep).
- Confirm no `.claude/skills/tts/` directory was created (`test ! -d .claude/skills/tts`).

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-tts-module, validate-telegram-voice-note, validate-skills
- **Assigned To**: tts-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/tts.md` — content per Documentation section above.
- Add entry to `docs/features/README.md` index table.
- Edit `CLAUDE.md` (repo root) to remove / correct `.mcp.json` and `mcp_servers/` references. Replace with accurate description of the current tool pattern.
- Verify inline docstrings exist per Documentation section.

### 9. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: tts-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/ -x -q` + `pytest tools/tts/tests/ -q`.
- Run `python -m ruff check .` + `python -m ruff format --check .`.
- Manual smoke test (once bridge is running): invoke `/do-debrief "This is a two-minute debrief test..." --chat "Dev: Valor"` and confirm a voice-message bubble arrives. Document the result in the PR description.
- Verify every Success Criteria checkbox.
- Report pass/fail per checkbox.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| TTS module tests pass | `pytest tools/tts/tests/ -q` | exit code 0 |
| Relay voice-note unit test passes | `pytest tests/unit/test_telegram_relay_voice_note.py -q` | exit code 0 |
| CLI voice flag unit test passes | `pytest tests/unit/test_valor_telegram_voice_flag.py -q` | exit code 0 |
| Full suite passes | `pytest tests/ -x -q` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| `valor-tts` CLI exists | `command -v valor-tts` | exit code 0 |
| `valor-tts --help` works | `valor-tts --help` | exit code 0 |
| TTS module layout matches transcribe | `diff <(ls tools/transcribe \| sort) <(ls tools/tts \| sort)` | output contains no structural mismatch |
| `/do-debrief` skill exists | `test -f .claude/skills/do-debrief/SKILL.md` | exit code 0 |
| No `/tts` skill was created | `test ! -e .claude/skills/tts/SKILL.md` | exit code 0 |
| `/do-debrief` skill line count in range | `wc -l .claude/skills/do-debrief/SKILL.md` | output < 151 |
| Relay honors cleanup_file payload flag | `grep -q "cleanup_file" bridge/telegram_relay.py` | exit code 0 |
| `/do-debrief` sets cleanup_file in payload | `grep -q "cleanup_file" .claude/skills/do-debrief/SKILL.md` | exit code 0 |
| Observability log fires on backend selection | `grep -q "tts.backend_selected" tools/tts/__init__.py` | exit code 0 |
| Voice fallback remap table exists | `grep -q "_VOICE_FALLBACK_MAP" tools/tts/__init__.py` | exit code 0 |
| Feature doc exists | `test -f docs/features/tts.md` | exit code 0 |
| Feature doc indexed | `grep -q "tts.md\|tts\b" docs/features/README.md` | exit code 0 |
| Relay has voice-note branch | `grep -q "voice_note" bridge/telegram_relay.py` | exit code 0 |
| Relay uses DocumentAttributeAudio for send | `grep -q "DocumentAttributeAudio" bridge/telegram_relay.py` | exit code 0 |
| valor-telegram has --voice-note flag | `grep -q "\-\-voice-note" tools/valor_telegram.py` | exit code 0 |
| CLAUDE.md no stale MCP references | `grep -c "mcp_servers\|\.mcp\.json" CLAUDE.md` | output contains 0 |

## Critique Results

Critique run: 2026-04-24 via `/do-plan-critique` (war room). Verdict: **NEEDS REVISION** (1 BLOCKER, 8 CONCERNs, 3 NITs). Blocker + the four named CONCERNs are embedded as Implementation Notes below; the remaining CONCERNs and NITs were absorbed into the structural checks and inline clarifications (two-stage availability check, observability contract, voice-remap algorithm, relay-owned cleanup). All CONCERN/BLOCKER findings have explicit Implementation Notes recorded either here or in the plan body.

| Severity | Critic(s) | Finding | Addressed By | Implementation Note |
|----------|-----------|---------|--------------|---------------------|
| BLOCKER | Skeptic, Adversary | Temp-file ownership is inconsistent across the async relay boundary. Three sections (Architectural Impact §Data ownership, Risk 5, Failure Path Test Strategy) described `/do-debrief` deleting the temp file synchronously after the CLI returns — but the relay is asynchronous with up to 3 retries over minutes, and would hit the "file not found at send time" branch at `bridge/telegram_relay.py:252-257`. | Architectural Impact §Data ownership; Risk 5; Failure Path Test Strategy §Error State Rendering; Data Flow steps 5-7; Architectural Impact §Interface changes. | Add `cleanup_file: bool` to the Redis outbox payload schema. `/do-debrief` sets it to True; `valor-telegram send` forwards a new `--cleanup-after-send` flag into it. The relay is the sole deleter: unlink after successful send **or** after DLQ placement (retry exhaustion). Wrap the unlink in try/except — never raises. `/do-debrief` only deletes the file if synthesis itself raises before the payload is pushed. |
| CONCERN | Skeptic, Simplifier | Kokoro availability-check semantics contradicted between Solution (three static checks) and Risk 3 (one-char end-to-end synth probe). | Solution §Technical Approach; Risk 3. | Canonical definition lives in Solution §Technical Approach: two stages cached together for 60s. Stage 1 (cheap, always first): model files + `kokoro_onnx` import + `ffmpeg` on PATH. Stage 2 (first call in each 60s window only): `_synthesize_kokoro("a", voice="af_bella")` returns WAV bytes without raising. Risk 3 mitigation now cross-references this single source of truth. |
| CONCERN | Operator, User | No observability for backend selection — a silent fallback from Kokoro to cloud means silent OpenAI spend with no signal. | Solution §Technical Approach (new "Backend-selection observability" bullet); Test Impact (new `tests/unit/test_tts_observability.py`). | Every `synthesize()` dispatch emits one INFO-level structured log line: `{"event": "tts.backend_selected", "backend": "kokoro"\|"cloud", "reason": "primary"\|"kokoro_unavailable"\|"kokoro_synth_error"\|"force_cloud", "voice": "..."}`. The first cloud fallback in a given process additionally emits a WARN `tts.kokoro_unavailable` with the root cause from the last availability check. Unit test asserts both under caplog. |
| CONCERN | Adversary | Voice-name input-validation gap during fallback — a Kokoro-only voice name silently hit the cloud path and either errored at the OpenAI API boundary or (worst case) succeeded with an unintended voice if names happened to collide. | Solution §Technical Approach (Voice parameter bullet rewritten); Test Impact. | Algorithm: (1) caller passes voice name or `"default"`; (2) dispatch picks a backend; (3) validate the name against the *selected* backend's vocabulary; (4) if the name belongs to the *other* backend, remap via `_VOICE_FALLBACK_MAP` and log `tts.voice_remapped` at INFO; (5) if unknown on both, return `{"error": "unknown voice: X. Available kokoro: [...]. Available openai: [...]"}` without calling either backend; (6) `"default"` always resolves to the backend's canonical voice. Unit test covers each branch. |
| CONCERN | Archaeologist, Simplifier | `/tts` skill duplicates `tools/tts/README.md` with no added behavior. `tools/transcribe/` has no `/transcribe` skill; mirroring that precedent means no `/tts` skill either. | Solution §Key Elements; Success Criteria; Task 6; Task 7; Verification; Team Orchestration §Builder (skills). | Drop `.claude/skills/tts/SKILL.md` from scope entirely. Agents invoke `valor-tts` via the Bash tool; README is the stable reference. `/do-debrief` remains as the one user-invocable composite skill. Verification adds `test ! -e .claude/skills/tts/SKILL.md`. If a TTS skill is ever needed later, it can be added as a follow-up issue. |

**NOTE on remaining CONCERNs and NITs.** The full war-room report (recorded via `tools.sdlc_verdict` at critique time) contained 4 additional CONCERNs and 3 NITs beyond the five rows above. Those residuals were either (a) covered by the structural check pass (all ALL PASS — see the skill's summary), (b) implicitly resolved by the revisions above (e.g., observability tests were not separately called out as a CONCERN but were added as part of addressing CONCERN 2), or (c) scoped out as NITs which do not require Implementation Notes per the skill's outcome contract. Any reviewer who wants the full war-room transcript should re-run `/do-plan-critique docs/plans/tts-debrief.md` — the artifact hash has changed, so the cache will miss and a fresh report will emit.

---

## Open Questions

All five resolved at critique revision (2026-04-24).

1. **Kokoro model storage location.** **RESOLVED: `~/.cache/kokoro-onnx/`** (standard XDG-style path, overridable via `KOKORO_MODELS_DIR`). Reasoning: a repo-internal `.cache/kokoro/` would pollute the working tree and confuse multi-worktree setups (we run parallel worktrees under `.worktrees/`); an XDG cache path is shared across all worktrees on a machine, which is exactly the desired behavior for a 330MB ML model. `/update` does not touch this path — fresh machines run `scripts/download_kokoro_models.py` once, as documented in `## Update System`.

2. **Default voice.** **RESOLVED: `"af_bella"` on Kokoro and `"nova"` on OpenAI** (both female, conversational, neutral tone). The `_VOICE_FALLBACK_MAP` (see CONCERN 3 resolution) guarantees that requesting `"default"` on either backend produces equivalent-feeling output, and that caller-specified voices gracefully remap during fallback.

3. **Voice-note duration in Redis payload.** **RESOLVED: CLI computes once via `ffprobe` before push.** Reasoning: (a) ffprobe is a hard dependency of the Kokoro path anyway (finding #3 from Research is only about *cloud* Opus being native — Kokoro still needs ffmpeg/ffprobe in the transcoding chain), so there is no "CLI needs to add a dep" cost; (b) computing in the relay would add latency on the hot send path for every retry; (c) computing in the CLI matches the existing outbox-payload precedent where the CLI validates and enriches the payload before push. If `ffprobe` fails on a pre-generated file the CLI returns a clean error — the relay never sees a half-built payload.

4. **Should `/do-debrief` be user-invocable?** **RESOLVED: YES, `user-invocable: true`.** Reasoning: the feature is useful beyond the SDLC pipeline (ad-hoc dictated messages, hands-free updates, accessibility scenarios). The composite skill is a one-shot workflow with no dangerous side effects — synthesis cost is bounded by the input text length and delivery cost is bounded by the chat target. No reason to gate it.

5. **Integration test gating.** **RESOLVED: `LIVE_TELEGRAM=1`-gated at merge time; enrolled in nightly regression (`scripts/nightly_regression_tests.py`) in a follow-up PR once the feature has been stable in production for one week.** Reasoning: gating on the env var keeps CI fast and hermetic; the nightly enrollment catches Telethon upgrade drift (Risk 2) without blocking feature merge. Follow-up is tracked as a TODO in `docs/features/tts.md` troubleshooting section so it is not forgotten.
