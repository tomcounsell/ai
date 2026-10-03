"""The emulator's forced arms: `tests/emulator/replay.py`'s `judged_as` starts the
local judgement upstream for `bare` and `clarify`, points the kernel's leg
endpoints at it for the run, and restores them and stops the upstream
however the run ends; `routed` changes nothing.

No model call. Live spend: none.
"""

import importlib
import os
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

pytestmark = pytest.mark.spend(usd=0)

ROOT = Path(__file__).resolve().parent.parent
KEYS = ("VALOR_JEV_URL", "VALOR_OPEN_WEIGHT_URL")


@pytest.fixture
def replay():
    return importlib.import_module("tests.emulator.replay")


def _listening(url: str) -> bool:
    u = urlparse(url)
    with socket.socket() as s:
        return s.connect_ex((u.hostname, u.port)) == 0


def test_routed_changes_nothing(replay, monkeypatch):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    with replay.judged_as("routed"):
        assert not [k for k in KEYS if k in os.environ]


@pytest.mark.parametrize(("arm", "answer"), [("bare", "precise"), ("clarify", "thin")])
def test_a_forced_arm_points_both_legs_at_a_local_upstream_and_restores_them(
    replay, monkeypatch, arm, answer
):
    monkeypatch.setenv("VALOR_JEV_URL", "http://127.0.0.1:1/before")
    monkeypatch.delenv("VALOR_OPEN_WEIGHT_URL", raising=False)
    with replay.judged_as(arm):
        during = {k: os.environ[k] for k in KEYS}
        assert all(f"/fixed/{answer}/" in v and urlparse(v).hostname == "127.0.0.1" for v in during.values())
        assert _listening(during["VALOR_JEV_URL"])
    assert os.environ["VALOR_JEV_URL"] == "http://127.0.0.1:1/before"
    assert "VALOR_OPEN_WEIGHT_URL" not in os.environ
    assert not _listening(during["VALOR_JEV_URL"])  # the upstream was stopped


def test_a_run_that_raises_still_restores_and_stops(replay, monkeypatch):
    monkeypatch.delenv("VALOR_JEV_URL", raising=False)
    with pytest.raises(RuntimeError), replay.judged_as("bare"):
        url = os.environ["VALOR_JEV_URL"]
        raise RuntimeError("the run failed")
    assert "VALOR_JEV_URL" not in os.environ and not _listening(url)


def test_the_driver_takes_the_routed_arm():
    out = subprocess.run([sys.executable, "-m", "tests.emulator.replay", "--help"], cwd=ROOT, capture_output=True,
                         text=True, check=False)  # fmt: skip
    assert out.returncode == 0 and "{bare,clarify,routed}" in out.stdout
