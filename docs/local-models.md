# Local models on a Mac

Which model files on a Valor Mac are required, which belong to a project
and download themselves again, and which nothing uses. Setting up a Mac
installs the required ones ([rollout.md](rollout.md) step 9). A disk
cleanup keeps everything under Required and Kept, whatever its age.
Inventory of all four Macs, 2026-11-21 (Tom: "you should have documentation
on what models are required to run Valor as well as yudame tools").

To see what a Mac holds: `ollama list`, `du -sh ~/.ollama/models
~/.cache/huggingface/hub/models--* ~/.cache/kokoro-onnx ~/.cache/whisper
~/.u2net ~/Library/Application\ Support/YudameTools/Models/*
~/Library/Application\ Support/superwhisper`. A Hugging Face
model's files sit in the cache's shared `blobs/` folder, so `du` on its
`models--` folder alone shows only links. Ollama's `:cloud` entries take no
disk.

## Required

| Model | Size | Where | Used by | Macs | Reinstall |
|---|---|---|---|---|---|
| Parakeet TDT 0.6B v3, ONNX int8 (`istupakov/parakeet-tdt-0.6b-v3-onnx`) | 670 MB | `~/.cache/huggingface/hub` | Valor's voice service transcribes every voice note and audio attachment: `tools/voice.py:75`, model named at `core/settings.py:554` | all four | `$PY -m tools.voice fetch` |
| Silero VAD (`istupakov/silero-vad-onnx`) | 2 MB | `~/.cache/huggingface/hub` | the same service, which cuts long audio into segments: `tools/voice.py:76` | all four | `$PY -m tools.voice fetch` |
| Kokoro v1.0 (`kokoro-v1.0.onnx`, `voices-v1.0.bin`) | 337 MB | `~/.cache/kokoro-onnx` | Valor's speech out, the present skill's `tts.py`: `tools/voice.py:46-49`, `core/settings.py:555`, passed to turns at `core/__main__.py:305` | all four | `$PY -m tools.voice fetch` |
| granite4.1:3b (Ollama) | 2.1 GB | `~/.ollama/models` | Yudame Tools' editor model: tidies dictation and writes meeting notes. Default at `Sources/YudameToolsKit/LocalModels/Ollama.swift:12`; no Mac overrides `editorModel` | all four | Yudame Tools' Setup page (it downloads without asking on the Cowboy, Captain and Bald, where `approveDownloads` is on; the Pirate waits for Download), or `ollama pull granite4.1:3b` |
| Parakeet 0.6B unified, GGUF Q8 (`parakeet-unified-en-0.6b-Q8_0.gguf`) | 731 MB | `~/Library/Application Support/YudameTools/Models/transcribe` | Yudame Tools' speech model for Dictation and Meetings, the "Accurate" choice every Mac has: `Sources/YudameToolsKit/Dictation/SpeechChoice.swift:27,38`, folder at `Sources/YudameTools/Tools/Dictation/DictationTool.swift:175` | all four | Yudame Tools downloads it on its Setup page |
| SpeakerKit (pyannote segmenter, embedder, clusterer) | 11 MB | `~/Library/Application Support/YudameTools/Models/speakers` | Yudame Tools Meetings, telling speakers apart: `Sources/YudameTools/Tools/Meetings/SpeakerSeparator.swift:16` | all four | Yudame Tools downloads it on its Setup page |

Yudame Tools' paths are in `yudame/yudame-tools`. Valor's own code runs no
Ollama model. Its judgement calls are hosted ([machine.md](machine.md)), and
its Meet calls are heard by Gemini Live.

## Kept: a project's, downloads itself again

Keep these. A project Valor works on loads them, and they download again
on first use if removed.

| Model | Size | Where | Used by | Macs holding it |
|---|---|---|---|---|
| nomic-embed-text (Ollama) | 274 MB | `~/.ollama/models` | popoto's Ollama embedding provider and its tests (`src/popoto/embeddings/ollama.py:60`) | Cowboy, Captain, Pirate |
| all-MiniLM-L6-v2 | 92 MB | `~/.cache/huggingface/hub` | popoto's sentence-transformers provider and benchmarks (`src/popoto/embeddings/sentence_transformers.py:80`) | Cowboy, Bald, Pirate |
| openai-whisper `base.pt` | 145 MB | `~/.cache/whisper` | cuttlefish's local podcast transcription, default size (`apps/podcast/tools/transcribe_only.py:35,49`) | Captain, Bald, Pirate |
| openai-whisper `small.pt` | 484 MB | `~/.cache/whisper` | the same script run with `--model small`; downloaded 2026-11-07 | Captain |

## Kept: not proven unused

- **tev1:0.8b** (Ollama, 811 MB, Cowboy). This is a local 0.75B decision
  model built from a local file (`FROM` a blob, not the registry), so it
  cannot be pulled again. Nothing in any repository names it, and Valor's
  Jev calls go to the hosted API (`core/settings.py:305`). Ask Tom before
  removing it.
- **superwhisper's models** on the Cowboy (978 MB: Canary 1B v2 and Parakeet
  v2 Core ML, pyannote speaker models, `emb-v1.onnx`, `seg-v1.onnx`) and the
  Bald (727 MB: `ggml-small.bin` 488 MB, superdiarize nemotron3 199 MB, the
  same small files), in `~/Library/Application Support/superwhisper`.
  superwhisper is a third-party dictation app, installed in `/Applications`
  on these two Macs. It is not running, and its database was last written on
  2026-10-09 (Cowboy) and 2026-11-02 (Bald). Yudame Tools' Dictation replaced
  it, but the app is still installed, so its models go with a decision to
  uninstall it, which is Tom's. Uninstalling the app and its folder together
  frees them.

## Deleted 2026-11-21

Every model this inventory found unused, deleted on Tom's word ("you should
know which ones are used and can delete the ones that aren't", 2026-11-21).
Each was checked first on its Mac: not loaded, not read since the inventory,
and its app not installed or not set to use it. 28.6 GB freed in all.

| Model | Size | Macs | Why nothing needed it |
|---|---|---|---|
| gemma4:e2b (Ollama) | 7.2 GB each | Cowboy, Captain, Bald | No code read the vault `.env` settings that named it (removed with it). The old system retired it (`config/models.py:184,201` at `old-system`). Ollama's logs show only granite4.1:3b loading. |
| granite3.2-vision (Ollama) | 2.4 GB | Captain | Named nowhere. An earlier cleanup (task 7f96f2f1) removed it from the Cowboy as unused. |
| mlx-community/whisper-large-v3-turbo, whisper-base.en-mlx; Systran/faster-whisper-small.en; pipecat-ai/smart-turn-v3 | 2.25 GB together | Cowboy | No code names them. One-off transcriptions downloaded the Whisper models; Valor's own path is the voice service. Valor's Meet calls used Smart Turn until 2026-11-05, when they moved to Gemini Live. |
| mlx-community/whisper-small.en-mlx | 481 MB | Bald | As above; downloaded by a turn on 2026-11-21. |
| superwhisper's models, `~/Library/Application Support/superwhisper` | 1.0 GB Captain, 562 MB Pirate | Captain, Pirate | The superwhisper app was not installed on these two Macs; the folder was what an uninstall left. |
| u2net (`~/.u2net/u2net.onnx`) | 176 MB | Captain | rembg's background-removal model; no repository on the Captain uses rembg, and rembg downloads it again on first use. |
| Yudame Tools `canary-180m-flash-Q8_0.gguf` | 218 MB | Pirate | The Pirate's Dictation is on Accurate (Parakeet), by default. Choosing Fast downloads Canary again. |

A model found unused later is listed here before it is deleted. Deleting:
`ollama rm NAME` for Ollama; `hf cache rm -y model/ORG/NAME` (the `hf` in
Valor's `.venv`) for a Hugging Face model, whose files sit in the hub's
shared `blobs/` folder, so measure that folder or `df` before and after;
Yudame Tools' Disk Cleanup page's Remove for its own models.
