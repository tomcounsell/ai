"""The one typed settings module: overrides, dated prices, and seats.

Live spend: none.
"""

import asyncio
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from core import db, spending, tasks
from core.settings import PRICES, SEATS, Settings, resolve_model, resolve_seat, settings
from tests.conftest import TEST_DB

pytestmark = pytest.mark.spend(usd=0)

ROOT = Path(__file__).resolve().parent.parent


def test_an_override_reaches_its_value_and_defaults_hold_otherwise(monkeypatch, tmp_path):
    monkeypatch.setenv("VALOR_BACKUP_DIR", str(tmp_path))
    monkeypatch.setenv("VALOR_PGPORT", "6543")
    s = Settings()
    assert s.backup_dir == str(tmp_path) and s.pgport == 6543
    assert s.pg_socket == f"{s.pghost}/.s.PGSQL.6543"
    monkeypatch.delenv("VALOR_BACKUP_DIR")
    assert Settings().backup_dir == "/Volumes//valor_temp"


def test_a_connection_string_names_the_password_file_and_holds_no_password():
    dsn = settings.dsn()
    assert f"passfile={settings.pg_passfile}" in dsn and "password=" not in dsn


def test_every_price_carries_the_day_it_was_checked_and_every_seat_is_priced():
    assert all(isinstance(p.checked, date) for p in PRICES.values())
    for seat, (harness, model) in SEATS.items():
        assert harness in ("claude_code", "pi"), seat
        if harness == "claude_code":
            assert model in PRICES, seat
        assert resolve_seat(seat) == (harness, model) and resolve_model(seat) == model
    assert resolve_model("claude-sonnet-5") == "claude-sonnet-5"
    assert resolve_seat("claude-sonnet-5") == ("claude_code", "claude-sonnet-5")


def test_the_openai_reviewer_seat_is_pi_on_gpt():
    assert resolve_seat("reviewer_openai") == ("pi", "gpt-6.1-sol")


def test_a_dated_model_id_takes_its_undated_entry_and_its_checked_date():
    price = spending.prices("claude-haiku-4-5-20251001")
    assert price["input"] == 1_000_000 and price["output"] == 5_000_000
    assert price["checked"] == PRICES["claude-haiku-4-5"].checked.isoformat()


def test_start_takes_a_seat_and_records_its_pinned_id(dsn):
    out = subprocess.run(
        [sys.executable, "-m", "core", "start", "seat test", "--model", "frontier"],
        cwd=ROOT,
        env={**os.environ, "VALOR_DB": TEST_DB},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    async def brief():
        async with await db.connect(dsn) as conn:
            return await tasks.brief(conn, out)

    assert asyncio.run(brief()).model == SEATS["frontier"][1]


def test_the_settings_command_prints_shell_assignments_bash_can_read():
    out = subprocess.run(
        ["bash", "-c", f'eval "$({sys.executable} -m core.settings)"; printf %s "$SETTING_BACKUP_DIR"'],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert out == settings.backup_dir
