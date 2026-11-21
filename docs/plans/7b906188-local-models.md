# Local models: document what each Mac needs, restore the missing, list the unused

Tom, Slack DM 2026-11-21 14:51 UTC: the local models serve dictation and
meeting recording, and Valor should document which models Valor and Yudame
Tools need. His correction at 14:54 UTC: "you should know which ones are used
and can delete the ones that aren't". This task documents what is required, restores
what a cleanup removed, and lists the unused models with their sizes as a
recommendation. It deletes nothing: the kernel's request and the Brief say
"Delete nothing" and forbid deleting any model or cache. Tom's 14:54 UTC
correction ("you should know which ones are used and can delete the ones that
aren't") reached this session but not the kernel's record of the request, so
the deletions go to a follow-up task that carries it, working from the list
this task writes. The report says so.

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
   supporting evidence, never as the only reason. Smart Turn v3,
   all-MiniLM-L6-v2, faster-whisper and the MLX whisper models have no user
   in the rebuilt code, and the request names /hear and Smart Turn as
   Valor's. Before any of them is called unused, search the installed plugin
   skills and scripts (/hear, meeting recording), the old system's jobs and
   code (from the upstream repo's `old-system` tag, or `~/src/ai`), and the
   launchd jobs on each Mac. A model with a user anywhere stays.
2. **Write `docs/local-models.md`**: one table (model, size, path, user with
   file:line, Macs that need it, reinstall command), a section listing the
   models nothing uses, per Mac with sizes, as a recommendation to delete,
   and how to tell required from unused during a cleanup (`ollama list`,
   `du` paths). Link it from `docs/rollout.md` step 9 (replacing its loose
   "about 1 GB" note) and `docs/machine.md`.
3. **Restore on the Captain**: `$PY -m tools.voice fetch`. Check that the
   Parakeet and Silero VAD folders are back in `~/.cache/huggingface/hub`,
   run `launchctl kickstart -k gui/$(id -u)/com.valor.voice` (the service
   holds the loaded model in memory, so only a restart proves the files
   load), then check that `com.valor.voice` on port 8714 returns a transcript for a short
   clip. Check where the Captain's Yudame Tools dictation keeps its chosen
   speech model; if it was in the emptied Hugging Face cache, download it
   again and confirm the app finds it. Re-pull any other required model a
   Mac lacks, for example the Ollama editor model.
4. **Yudame Tools PR**: add the same list, limited to its own models, to that
   repo (its README or `docs/`), on a branch, PR opened and not merged.
5. **Report** in `.valor/done.md`: the required table, where the doc lives,
   the unused list per Mac with sizes as a recommendation, what could not
   be ruled out and why, the Captain restore, and that deleting waits for a
   follow-up task carrying Tom's 14:54 correction.

## Out of scope

Code changes to Valor or Yudame Tools, deleting any model or cache on any Mac, merging
the Yudame Tools PR, and changing which models either app uses.

## Tests

The work is docs plus machine operations, with no production code, so there
are no unit tests. Run `tests/test_bloat.py`. The evidence the build records:
- Each Mac's `ollama list` and HF listing, matched against the doc's
  required and unused lists.
- On the Captain, the Parakeet and Silero VAD folders present and a
  `/transcribe` round trip on port 8714 returning text, plus Yudame Tools'
  chosen speech model found where the app looks for it.
- On each Mac, the Yudame Tools editor model still listed, and dictation's
  chosen speech model folder still present.
- Edge cases: a model one Mac uses and another does not (keep it per Mac,
  following that Mac's settings); `:cloud` stubs, which take no disk; a
  blob two Ollama tags share (count its size once); a model only the old system or
  Tom's own tools use (keep, and name it).
