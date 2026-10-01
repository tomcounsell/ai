"""Record one live response from each judgement leg, as the shape the local
upstream (`tests/judgement_upstream.py`) builds its 200 bodies from:
`judgement_jev.json` and `judgement_open_weight.json`.

    VALOR_LIVE=1 .venv/bin/python tests/fixtures/record_judgement.py

Reads the keys from the kernel's key file and sends them only to the legs'
default endpoints. Writes the response bodies as received; no key and no
request is written. Live spend: under $0.001.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import credentials, judgement
from core.judgement_tasks import JUDGE
from core.settings import JEV_KEY, JEV_URL, OPEN_WEIGHT_KEY, OPEN_WEIGHT_URL, settings
from tools.jev import Jev
from tools.open_weight import OpenWeight

HERE = Path(__file__).resolve().parent
INPUTS = {"request": "Add a dark mode toggle to the settings page.", "thread": "", "project": "toy"}


async def main() -> None:
    if os.environ.get("VALOR_LIVE") != "1":
        raise SystemExit("recording spends money; set VALOR_LIVE=1")
    keys = settings.judgement_keyfile
    legs = {
        "judgement_jev.json": Jev(JEV_URL, credentials.read_key(keys, JEV_KEY)),
        "judgement_open_weight.json": OpenWeight(
            OPEN_WEIGHT_URL, credentials.read_key(keys, OPEN_WEIGHT_KEY)
        ),
    }
    for name, leg in legs.items():
        got = await judgement.post(leg.endpoint, leg._key, leg.body(JUDGE, INPUTS), 30)
        if isinstance(got, judgement.LegError) or got[0] != 200:
            raise SystemExit(f"{name}: no 200 ({got if isinstance(got, judgement.LegError) else got[0]})")
        (HERE / name).write_text(json.dumps(json.loads(got[1]), indent=2) + "\n")
        print(f"recorded {name}")


if __name__ == "__main__":
    asyncio.run(main())
