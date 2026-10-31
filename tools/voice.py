"""The voice process: speech to text, on this Mac.

`python -m tools.voice run | fetch | --plist`.

One process holds the model, so the three bridges share one copy
(Parakeet peaks near 1.7 GB resident). It answers on
`127.0.0.1:settings.voice_port`:

- `POST /transcribe {"path": ...}` -> `{"text": ...}`: ffmpeg decodes any
  audio to 16 kHz mono; Parakeet (`settings.asr_model`, through onnx-asr,
  int8, on the CPU) with onnx-asr's Silero VAD, so any length is cut into
  segments.
- `POST /hear`, a turn of a Meet call as raw float32 samples at 16 kHz ->
  `{"text": ...}`: the same model, on a worker thread of its own, so a
  call is heard while a voice note transcribes (`core/call.py`).

The engine is `listen`: a new model is a setting, a new engine is one
function. The model loads on first use and stays loaded; inference runs on
one thread, one request at a time. Speech out is not here: a turn speaks
with the yudame present skill's `tts.py` (Kokoro, the old system's engine),
from Kokoro's files in `settings.tts_model_dir` in `settings.tts_voice`.
`fetch` downloads Parakeet and those files, the step each Mac runs once.
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

from core import routines, sentry
from core.settings import settings

LABEL = "com.valor.voice"
ROOT = Path(__file__).resolve().parents[1]
PLIST_ENV = ("VALOR_VOICE_PORT", "VALOR_ASR_MODEL")
# The old system's Kokoro files, which the present skill's `tts.py` reads:
# the kokoro-onnx maintainer's release.
KOKORO = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
KOKORO_FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")
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


@functools.cache
def _asr():
    import onnx_asr

    model = onnx_asr.load_model(settings.asr_model, quantization="int8", providers=["CPUExecutionProvider"])
    return model.with_vad(onnx_asr.load_vad("silero"))


def listen(path: str) -> str:
    """The words spoken in the audio file at `path`."""
    return " ".join(t for s in _asr().recognize(decode(path)) if (t := s.text.strip()))


def heard(samples: np.ndarray) -> str:
    """The words spoken in 16 kHz mono samples."""
    return " ".join(t for s in _asr().recognize(samples) if (t := s.text.strip()))


def app(*, listen=listen, heard=heard) -> web.Application:
    pool, calls = ThreadPoolExecutor(max_workers=1), ThreadPoolExecutor(max_workers=1)

    async def transcribe(request: web.Request) -> web.Response:
        body = await request.json()
        text = await asyncio.get_running_loop().run_in_executor(pool, listen, body["path"])
        return web.json_response({"text": text})

    async def hear(request: web.Request) -> web.Response:
        samples = np.frombuffer(await request.read(), dtype=np.float32)
        text = await asyncio.get_running_loop().run_in_executor(calls, heard, samples)
        return web.json_response({"text": text})

    application = web.Application(client_max_size=64 * 1024 * 1024)
    application.router.add_post("/transcribe", transcribe)
    application.router.add_post("/hear", hear)
    application.on_cleanup.append(lambda _: asyncio.to_thread(pool.shutdown))
    application.on_cleanup.append(lambda _: asyncio.to_thread(calls.shutdown))
    return application


def fetch() -> None:
    """Download Parakeet and a Meet call's Smart Turn (into the Hugging
    Face cache) and Kokoro's files (into `settings.tts_model_dir`); a file
    already there is kept."""
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
    from huggingface_hub import hf_hub_download

    from core import call

    hf_hub_download(*call.SMART_TURN)
    print("the models are in place")


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
            "ProgramArguments": [settings.launcher, python or sys.executable, "-m", "tools.voice", "run"],
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
    sub.add_parser("run", help="answer transcription on 127.0.0.1")
    sub.add_parser("fetch", help="download Parakeet and Kokoro's files")
    args = parser.parse_args(argv)
    if args.plist:
        sys.stdout.buffer.write(plist())
        return 0
    if args.verb == "fetch":
        fetch()
        return 0
    if args.verb == "run":
        sentry.init("voice")
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        web.run_app(app(), host="127.0.0.1", port=settings.voice_port, print=None)
        return 1
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
