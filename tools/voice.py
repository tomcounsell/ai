"""The voice process: speech to text and text to speech, on this Mac.

`python -m tools.voice run | fetch | --plist`.

One process holds both models, so the three bridges share one copy
(Parakeet alone peaks near 1.7 GB resident). It answers on
`127.0.0.1:settings.voice_port`:

- `POST /transcribe {"path": ...}` -> `{"text": ...}`: ffmpeg decodes any
  audio to 16 kHz mono; Parakeet (`settings.asr_model`, through onnx-asr,
  int8, on the CPU) with onnx-asr's Silero VAD, so any length is cut into
  segments.
- `POST /speech {"text": ..., "format": "ogg" | "mp3"}` -> the audio:
  Kokoro (`settings.tts_model_dir`, voice `settings.tts_voice`), then
  ffmpeg to Opus in Ogg (a Telegram voice note) or MP3.

The engines are `listen` and `speak`: a new voice or model is a setting, a
new engine is one function. Each model loads on first use and stays
loaded; inference runs on one thread, one request at a time.
`fetch` downloads both models, the step each Mac runs once.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import logging
import os
import plistlib
import shutil
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from aiohttp import web

from core import routines
from core.settings import settings

LABEL = "com.valor.voice"
ROOT = Path(__file__).resolve().parents[1]
PLIST_ENV = ("VALOR_VOICE_PORT", "VALOR_ASR_MODEL", "VALOR_TTS_MODEL_DIR", "VALOR_TTS_VOICE")
# The old system's Kokoro files: the kokoro-onnx maintainer's release.
KOKORO = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
KOKORO_FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")
ENCODERS = {
    "ogg": ["-c:a", "libopus", "-b:a", "32k", "-f", "ogg"],
    "mp3": ["-c:a", "libmp3lame", "-f", "mp3"],
}
RATE = 16_000
QUIET = ("-nostdin", "-loglevel", "error")


def ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if not found:
        raise RuntimeError("ffmpeg not found: brew install ffmpeg")
    return found


def decode(path: str | Path) -> np.ndarray:
    """Any audio ffmpeg reads, as 16 kHz mono float32 samples."""
    out = subprocess.run(
        [ffmpeg(), *QUIET, "-i", str(path), *f"-f f32le -ac 1 -ar {RATE} -".split()],
        capture_output=True,
        check=True,
    )
    return np.frombuffer(out.stdout, dtype=np.float32)


def encode(samples: np.ndarray, rate: int, fmt: str) -> bytes:
    """Mono float32 samples as an Ogg/Opus or MP3 file's bytes."""
    out = subprocess.run(
        [ffmpeg(), *QUIET, *f"-f f32le -ar {rate} -ac 1 -i -".split(), *ENCODERS[fmt], "-"],
        input=np.asarray(samples, dtype=np.float32).tobytes(),
        capture_output=True,
        check=True,
    )
    return out.stdout


@functools.cache
def _asr():
    import onnx_asr

    model = onnx_asr.load_model(settings.asr_model, quantization="int8", providers=["CPUExecutionProvider"])
    return model.with_vad(onnx_asr.load_vad("silero"))


@functools.cache
def _tts():
    from kokoro_onnx import Kokoro

    folder = Path(settings.tts_model_dir).expanduser()
    return Kokoro(str(folder / KOKORO_FILES[0]), str(folder / KOKORO_FILES[1]))


def listen(path: str) -> str:
    """The words spoken in the audio file at `path`."""
    return " ".join(t for s in _asr().recognize(decode(path)) if (t := s.text.strip()))


def speak(text: str, fmt: str) -> bytes:
    """`text` spoken in the configured voice, as a `fmt` file's bytes."""
    samples, rate = _tts().create(text, voice=settings.tts_voice)
    return encode(samples, rate, fmt)


def app(*, listen=listen, speak=speak) -> web.Application:
    pool = ThreadPoolExecutor(max_workers=1)

    async def transcribe(request: web.Request) -> web.Response:
        body = await request.json()
        text = await asyncio.get_running_loop().run_in_executor(pool, listen, body["path"])
        return web.json_response({"text": text})

    async def speech(request: web.Request) -> web.Response:
        body = await request.json()
        fmt = body.get("format", "ogg")
        if fmt not in ENCODERS:
            return web.json_response({"error": f"format is ogg or mp3, not {fmt!r}"}, status=400)
        data = await asyncio.get_running_loop().run_in_executor(pool, speak, body["text"], fmt)
        return web.Response(body=data, content_type="audio/ogg" if fmt == "ogg" else "audio/mpeg")

    application = web.Application()
    application.add_routes([web.post("/transcribe", transcribe), web.post("/speech", speech)])
    application.on_cleanup.append(lambda _: asyncio.to_thread(pool.shutdown))
    return application


def fetch() -> None:
    """Download Parakeet (into the Hugging Face cache) and Kokoro's files
    (into `settings.tts_model_dir`); a file already there is kept."""
    folder = Path(settings.tts_model_dir).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    for name in KOKORO_FILES:
        if not (folder / name).exists():
            print(f"fetching {name}")
            tmp = folder / f".{name}.{os.getpid()}"
            urllib.request.urlretrieve(f"{KOKORO}/{name}", tmp)
            tmp.replace(folder / name)
    print(f"fetching {settings.asr_model}")
    _asr()
    print("both models are in place")


def plist(*, python: str | None = None) -> bytes:
    """The launchd job, its PATH the kernel's with ffmpeg's directory."""
    path = routines.launchd_path().split(":")
    if (d := os.path.dirname(ffmpeg())) not in path:
        path.append(d)
    env = {"HOME": str(Path.home()), "PATH": ":".join(path)}
    env.update({k: os.environ[k] for k in PLIST_ENV if k in os.environ})
    log_file = str(Path(settings.log_dir) / "voice.log")
    return plistlib.dumps(
        {
            "Label": LABEL,
            "ProgramArguments": [python or sys.executable, "-m", "tools.voice", "run"],
            "WorkingDirectory": str(ROOT),
            "EnvironmentVariables": env,
            "RunAtLoad": True,
            "KeepAlive": True,
            "StandardOutPath": log_file,
            "StandardErrorPath": log_file,
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.voice")
    parser.add_argument("--plist", action="store_true", help="print the launchd job")
    sub = parser.add_subparsers(dest="verb")
    sub.add_parser("run", help="answer transcription and speech on 127.0.0.1")
    sub.add_parser("fetch", help="download both models")
    args = parser.parse_args(argv)
    if args.plist:
        sys.stdout.buffer.write(plist())
        return 0
    if args.verb == "fetch":
        fetch()
        return 0
    if args.verb == "run":
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        web.run_app(app(), host="127.0.0.1", port=settings.voice_port, print=None)
        return 1
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
