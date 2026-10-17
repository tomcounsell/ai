#!/usr/bin/env python3
"""valor-tts TEXT -o FILE

Speaks TEXT in Valor's voice into FILE, through the voice process
(`tools/voice.py` on 127.0.0.1, `$VALOR_VOICE_PORT`). FILE's extension
picks the format: `.ogg` (Opus, which Telegram sends as a voice note) or
`.mp3` (which Slack plays inline). Needs no key. Standard library only,
and Python 3.9 (macOS's own `python3`), so it runs in any workspace.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

FORMATS = {".ogg": "ogg", ".mp3": "mp3"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="valor-tts", description="Speak text into an .ogg or .mp3 file.")
    parser.add_argument("text", help="what to say")
    parser.add_argument("-o", "--output", required=True, help="the file to write, .ogg or .mp3")
    args = parser.parse_args(argv)
    fmt = FORMATS.get(os.path.splitext(args.output)[1].lower())
    if fmt is None:
        print("valor-tts: the output file ends in .ogg or .mp3", file=sys.stderr)
        return 2
    port = os.environ.get("VALOR_VOICE_PORT", "8714")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/speech",
        data=json.dumps({"text": args.text, "format": fmt}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        try:
            said = json.loads(e.read()).get("error")
        except ValueError:
            said = None
        print(f"valor-tts: the voice process answered {e.code}: {said or e.reason}", file=sys.stderr)
        return 1
    except OSError:  # URLError is one
        print(f"valor-tts: the voice process is not answering on 127.0.0.1:{port}", file=sys.stderr)
        return 1
    with open(args.output, "wb") as f:
        f.write(data)
    print(args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
