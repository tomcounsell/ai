"""`python -m ui`: the status page, on 127.0.0.1 at `settings.ui_port`.

`python -m ui --plist` prints its launchd job, `com.valor.ui`: the
kernel's job (`core.serve.plist`) with its own label, arguments and log,
kept alive. A kernel start that ends a rollout kicks it
(`core.serve.restart_bridges`), so the page runs the code just rolled.
"""

import argparse
import plistlib
import sys
from pathlib import Path

from aiohttp import web

from core import rollout, serve
from core.settings import settings
from ui.app import make_app


def plist() -> bytes:
    job = plistlib.loads(serve.plist())
    log = str(Path(settings.log_dir) / "ui.log")
    job.pop("ProcessType")
    job |= {"Label": rollout.UI, "ProgramArguments": [job["ProgramArguments"][0], "-m", "ui"]}
    job |= {"StandardOutPath": log, "StandardErrorPath": log}
    return plistlib.dumps(job)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m ui")
    parser.add_argument("--plist", action="store_true", help="print the launchd job and exit")
    if parser.parse_args().plist:
        sys.stdout.write(plist().decode())
    else:
        web.run_app(make_app(), host="127.0.0.1", port=settings.ui_port, print=None)
