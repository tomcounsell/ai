"""`python -m bridges.telegram`: keys, the missing-key message, the plist."""

import os
import plistlib
import stat
import subprocess
import sys

import pytest

FAKE_HASH = "0123456789abcdeffedcba9876543210"


@pytest.fixture
def env(tmp_path):
    keys = tmp_path / "valor-kernel"
    vault = tmp_path / "vault.env"
    vault.write_text(f"TELEGRAM_API_ID=12345\nTELEGRAM_API_HASH={FAKE_HASH}\nOTHER=x\n")
    e = {k: v for k, v in os.environ.items() if not k.startswith("VALOR_")}
    e.update(
        VALOR_PG_PASSFILE=str(keys / "pgpass"),
        VALOR_VAULT_ENV=str(vault),
        VALOR_LOG_DIR=str(tmp_path / "logs"),
        VALOR_MACHINE="testbox",
    )
    return e, keys


def cli(env, *args):
    return subprocess.run(
        [sys.executable, "-m", "bridges.telegram", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def leaks(text: str) -> bool:
    return any(FAKE_HASH[i : i + 4] in text for i in range(len(FAKE_HASH) - 3))


def test_keys_copies_the_two_names_at_mode_600_and_prints_no_part_of_them(env):
    e, keys = env
    out = cli(e, "keys")
    assert out.returncode == 0
    assert out.stdout.splitlines() == ["TELEGRAM_API_ID: written", "TELEGRAM_API_HASH: written"]
    assert not leaks(out.stdout + out.stderr)
    keyfile = keys / "telegram-keys"
    assert stat.S_IMODE(keyfile.stat().st_mode) == 0o600
    assert "OTHER" not in keyfile.read_text()
    assert cli(e, "keys").stdout.splitlines() == ["TELEGRAM_API_ID: kept", "TELEGRAM_API_HASH: kept"]


def test_a_missing_key_names_both_variables_and_the_keys_command(env):
    e, _ = env
    out = cli(e, "login")
    assert out.returncode != 0
    assert "TELEGRAM_API_ID or TELEGRAM_API_HASH" in out.stderr
    assert "python -m bridges.telegram keys" in out.stderr


def test_the_plist_runs_the_bridge_and_carries_no_key(env):
    e, _ = env
    cli(e, "keys")
    out = subprocess.run(
        [sys.executable, "-m", "bridges.telegram", "--plist"],
        env=e,
        capture_output=True,
        timeout=60,
        check=True,
    )
    job = plistlib.loads(out.stdout)
    assert job["Label"] == "com.valor.kernel.telegram"
    assert job["ProgramArguments"][1:] == ["-m", "bridges.telegram", "run"]
    assert job["KeepAlive"] is True and job["RunAtLoad"] is True
    assert job["StandardOutPath"].endswith("telegram.log")
    assert job["EnvironmentVariables"]["VALOR_MACHINE"] == "testbox"
    assert not leaks(out.stdout.decode())
