# Local models: document what each Mac needs, restore the missing, delete the unused

Tom, Slack DM 2026-11-21 14:51 UTC: the local models serve dictation and
meeting recording, and Valor should document which models Valor and Yudame
Tools need. His correction at 14:54 UTC: "you should know which ones are used
and can delete the ones that aren't". So the job is to document what is
required first, then delete every model that nothing uses, on all four Macs.
A model we cannot rule out stays, and the report names it.

## What is known (observed at plan time, 2026-11-21)

- Valor's code: `tools/voice.py` (Parakeet TDT 0.6B v3 and Silero VAD through
  onnx-asr in the HF cache, plus Kokoro v1.0 in `~/.cache/kokoro-onnx`;
  `fetch` reinstalls them) and `core/settings.py:554`. The rebuilt code has no
  Ollama, Smart Turn or sentence-transformers call. `~/Desktop/Valor/.env`
  sets `OLLAMA_LOCAL_MODEL`/`OLLAMA_VISION_MODEL=gemma4:e2b`, but whatever
  reads them is still unknown.
- Yudame Tools (`yudame/yudame-tools`, private, admin access): Ollama editor
  model (`LocalModels/EditorModel.swift`, `Meetings/Notes.swift`), WhisperKit
  or Parakeet speech models (`Dictation/SpeechChoice.swift`,
  `DictationTool.swift`) under `~/Library/Application Support/YudameTools`,
  and an Ollama list in `DiskCleanup/Chosen.swift` / `Snapshot.swift:681`.
- On the Macs: Ollama holds tev1:0.8b, granite4.1:3b, nomic-embed-text,
  gemma4:e2b and granite3.2-vision (Captain only), plus `:cloud` entries
  that take no disk. The HF caches hold parakeet, silero-vad,
  all-MiniLM-L6-v2, smart-turn-v3 (Cowboy), and faster-whisper-small.en and
  mlx whisper base/small/large-v3-turbo (Cowboy, Bald). **The Captain's HF
  cache is gone**, so cleanup task 0b976535bda8 removed Parakeet and Silero VAD.

## Build

1. **Trace every model to a user**, with file and line or setting: Valor
   (repo, `.env`, `~/Desktop/Valor/zshenv.sh`, launchd plists, the
   yudame/valor plugins' skills and scripts, `~/src/ai` if a job runs from
   it) and Yudame Tools (`origin/main` code, each Mac's
   `defaults read` of its domain and its Application Support settings
   showing which speech model and editor model are chosen). Also check who
   ran each model last (Ollama server logs, file access times) as
   supporting evidence, never as the only reason.
2. **Write `docs/local-models.md`**: one table (model, size, path, user with
   file:line, Macs that need it, reinstall command), a section for models
   that look like leftovers and are safe to delete, and how to tell them
   apart during a cleanup (`ollama list`, `du` paths). Link it from
   `docs/rollout.md` step 9 (replacing its loose "about 1 GB" note) and
   `docs/machine.md`. Commit it before any deletion.
3. **Restore on the Captain**: `$PY -m tools.voice fetch`, then confirm
   `com.valor.voice` transcribes a short clip. Re-pull any other required
   model a Mac lacks (for example the Ollama editor model).
4. **Delete the unused, per Mac**, only models the doc lists as unused:
   `ollama rm` for Ollama, and removing the `models--…` folder for HF.
   Record `df` free space and each size before and after. Never touch the
   `:cloud` entries (0 bytes), models we cannot rule out, or non-model caches.
5. **Yudame Tools PR**: add the same list, limited to its own models, to that
   repo (its README or `docs/`), on a branch, PR opened and not merged.
6. **Report** in `.valor/done.md`: the required table, where the doc lives,
   what was deleted per Mac with sizes and free space before and after, what
   was kept unproven and why, and the Captain restore.

## Out of scope

Code changes to Valor or Yudame Tools, deleting non-model caches, merging
the Yudame Tools PR, and changing which models either app uses.

## Tests

The work is docs plus machine operations, with no production code, so there
are no unit tests. Run `tests/test_bloat.py`. The evidence the build records:
- Each Mac's `ollama list` and HF listing before and after, matched against
  the doc.
- On the Captain, a `/transcribe` round trip after the fetch.
- On each Mac, the Yudame Tools editor model still listed, and dictation's
  chosen speech model folder still present.
- Edge cases: a model one Mac uses and another does not (keep it per Mac,
  following that Mac's settings); `:cloud` stubs; a blob two Ollama tags
  share (checked by `ollama rm` itself); a model only the old system or
  Tom's own tools use (keep, and name it).
