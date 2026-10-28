"""Valor.app (`core/permissions.py`): the launcher every launchd job runs through."""

import importlib
import plistlib
import signal
import subprocess
import time
from pathlib import Path

import pytest

from core import permissions, routines
from core.settings import settings

ROOT = Path(__file__).resolve().parent.parent
JOBS = [
    "core.serve",
    "core.backup",
    "tools.voice",
    "bridges.telegram.__main__",
    "bridges.slack.__main__",
    "bridges.email.__main__",
    "bridges.local.__main__",
    "ui.__main__",
]


@pytest.fixture(scope="module")
def exe(tmp_path_factory):
    exe = tmp_path_factory.mktemp("apps") / "Valor.app" / "Contents" / "MacOS" / "valor-run"
    permissions.build(exe)
    return exe


def test_build_signs_the_bundle_as_valor_and_never_rebuilds(exe):
    signed = subprocess.run(["codesign", "-dv", exe.parents[2]], capture_output=True, text=True, check=False)
    assert f"Identifier={permissions.BUNDLE_ID}" in signed.stderr
    info = plistlib.loads((exe.parents[1] / "Info.plist").read_bytes())
    assert info["CFBundleExecutable"] == exe.name and info["LSUIElement"] is True
    before = exe.stat().st_mtime_ns
    permissions.build(exe)
    assert exe.stat().st_mtime_ns == before


def test_the_launcher_runs_its_command_and_exits_as_it_did(exe):
    done = subprocess.run([exe, "/bin/sh", "-c", 'echo "$0 $PWD"; exit 3', "hi"], cwd="/private/tmp",
                          capture_output=True, text=True, check=False)  # fmt: skip
    assert (done.returncode, done.stdout) == (3, "hi /private/tmp\n")
    assert subprocess.run([exe, "/no/such/program"], capture_output=True, check=False).returncode == 127


def test_the_launcher_forwards_term_and_dies_of_it_as_its_command_did(exe):
    proc = subprocess.Popen([exe, "/bin/sleep", "30"])
    time.sleep(0.5)
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=5) == -signal.SIGTERM


@pytest.mark.parametrize("module", JOBS)
def test_every_launchd_job_runs_through_the_launcher(module):
    job = plistlib.loads(importlib.import_module(module).plist())
    assert job["ProgramArguments"][0] == settings.launcher


def test_every_routine_runs_through_the_launcher():
    job = plistlib.loads(routines.plist(routines.load("verify", ROOT / "routines")))
    assert job["ProgramArguments"][0] == settings.launcher


def test_ensure_puts_every_python_job_on_the_launcher_and_reloads_its_own_last(exe, tmp_path):
    jobs = {
        "com.valor.kernel": ["/opt/homebrew/bin/python3.14", "-m", "core.serve"],
        "com.valor.voice": ["/opt/homebrew/bin/python3.14", "-m", "tools.voice"],
        "com.valor.done": [str(exe), "/opt/homebrew/bin/python3.14", "-m", "core.backup"],
        "com.valor.brew-nightly": ["/bin/bash", "brew-nightly-update.sh"],
    }
    for label, argv in jobs.items():
        (tmp_path / f"{label}.plist").write_bytes(plistlib.dumps({"Label": label, "ProgramArguments": argv}))
    reloaded = []

    def reload(label, plist, own):
        reloaded.append((label, own))
        return "5: Input/output error" if label == "com.valor.voice" else None

    done = permissions.ensure("com.valor.kernel", tmp_path, exe, reload, lambda label: True)
    assert done == {
        "fixed": ["com.valor.voice", "com.valor.kernel"],
        "failed": ["com.valor.voice: 5: Input/output error"],
    }
    assert reloaded == [("com.valor.voice", False), ("com.valor.kernel", True)]
    argv = {p.stem: plistlib.loads(p.read_bytes())["ProgramArguments"] for p in tmp_path.glob("*.plist")}
    assert argv["com.valor.kernel"] == [str(exe), *jobs["com.valor.kernel"]]
    assert argv["com.valor.done"] == jobs["com.valor.done"]
    assert argv["com.valor.brew-nightly"] == jobs["com.valor.brew-nightly"]
    assert permissions.ensure("com.valor.kernel", tmp_path, exe, reload, lambda label: True) == {}


def test_ensure_loads_a_job_whose_plist_is_in_place_but_not_loaded(exe, tmp_path):
    """The Cowboy's com.valor.brew-nightly, put back from a turn's sandbox,
    which cannot bootstrap (2026-10-28): loaded as written, not wrapped."""
    argv = [str(exe), "/bin/bash", "brew-nightly-update.sh"]
    for label in ("com.valor.brew-nightly", "com.valor.backup"):
        (tmp_path / f"{label}.plist").write_bytes(plistlib.dumps({"Label": label, "ProgramArguments": argv}))
    up = {"com.valor.backup"}
    reloaded = []

    def reload(label, plist, own):
        reloaded.append((label, own))
        up.add(label)

    done = permissions.ensure("com.valor.kernel", tmp_path, exe, reload, up.__contains__)
    assert done == {"fixed": ["com.valor.brew-nightly: not loaded"]}
    assert reloaded == [("com.valor.brew-nightly", False)]
    plist = plistlib.loads((tmp_path / "com.valor.brew-nightly.plist").read_bytes())
    assert plist["ProgramArguments"] == argv
    assert permissions.ensure("com.valor.kernel", tmp_path, exe, reload, up.__contains__) == {}


# -- Slack on every Mac whose projects list a Slack chat (Tom, 2026-10-25) ----


def slack_mac(tmp_path, chats='["slack:T0ABC/C0DEV"]'):
    """This machine with one project listing `chats`, and its Telegram job,
    whose settings the Slack job takes."""
    from tests import bridges

    projects = tmp_path / "projects"
    projects.mkdir(parents=True)
    (projects / "p.toml").write_text(f'name = "p"\nrepo = "r"\nkind = "plain"\nsuite = "true"\nchats = {chats}\n')
    agents = tmp_path / "agents"
    agents.mkdir()
    env = {"PATH": "/usr/bin", "HOME": "/Users/x", "VALOR_MACHINE": "m-test", "VALOR_DB": "valor_x",
           "VALOR_OPERATOR_SLACK_IDS": "T0ABC/U0TOM", "VALOR_INBOUND": "/tmp/in", "VALOR_SERVE_TICK_S": "7"}  # fmt: skip
    (agents / f"{permissions.TELEGRAM}.plist").write_bytes(plistlib.dumps({"EnvironmentVariables": env}))
    return lambda: bridges.configure(projects_dir=str(projects), machine="m-test", default_machine="m-test"), agents, env


def keys_said(stdout: str, code: int = 0, loaded: bool = True):
    """`python -m bridges.slack keys` answering `stdout`; `--plist` runs for
    real; `launchctl print` finds the Slack job when `loaded`."""
    calls = []

    def run(argv, **kw):
        if argv[0] == "launchctl":
            calls.append((argv[1], None))
            return subprocess.CompletedProcess(argv, 0 if loaded or argv[1] != "print" else 113, "", "")
        calls.append((argv[-1], kw["env"]))
        if argv[-1] == "keys":
            return subprocess.CompletedProcess(argv, code, stdout, "SLACK_USER_TOKEN_M_TEST is missing" if code else "")
        return subprocess.run(argv, check=True, **kw)

    return run, calls


def test_the_update_installs_the_slack_job_with_the_telegram_jobs_settings(tmp_path):
    from bridges.slack import __main__ as slack_main

    config, agents, env = slack_mac(tmp_path)
    run, calls = keys_said("T0ABC_SLACK_APP_TOKEN: kept\nT0ABC_SLACK_USER_TOKEN: written\n")
    reloaded, fixed, failed = [], [], []
    with config():
        permissions.slack(agents, lambda *a: reloaded.append(a[0]), fixed, failed, run)
    job = plistlib.loads((agents / f"{permissions.SLACK}.plist").read_bytes())
    assert job["Label"] == permissions.SLACK == slack_main.LABEL
    assert job["ProgramArguments"][-3:] == ["-m", "bridges.slack", "run"]
    assert {k: job["EnvironmentVariables"][k] for k in env if k in slack_main.PLIST_ENV} == {
        k: v for k, v in env.items() if k in slack_main.PLIST_ENV
    }
    assert [c for c, _ in calls] == ["keys", "--plist", "enable"] and calls[0][1]["VALOR_MACHINE"] == "m-test"
    assert (fixed, failed, reloaded) == ([permissions.SLACK], [], [permissions.SLACK])

    fixed.clear(), reloaded.clear()
    with config():
        permissions.slack(agents, lambda *a: reloaded.append(a[0]), fixed, failed, run)
    assert (fixed, reloaded) == ([f"{permissions.SLACK}: new keys"], [permissions.SLACK])
    run, calls = keys_said("T0ABC_SLACK_APP_TOKEN: kept\nT0ABC_SLACK_USER_TOKEN: kept\n")
    fixed.clear(), reloaded.clear()
    with config():
        permissions.slack(agents, lambda *a: reloaded.append(a[0]), fixed, failed, run)
    assert (fixed, failed, reloaded) == ([], [], []) and [c for c, _ in calls] == ["keys", "print"]


def test_the_update_enables_and_loads_a_slack_job_that_is_written_but_not_loaded(tmp_path):
    """The Pirate's job was disabled from 2026-10-16, so its bootstrap
    failed, and every later update saw the plist and did nothing."""
    config, agents, _ = slack_mac(tmp_path)
    (agents / f"{permissions.SLACK}.plist").write_bytes(plistlib.dumps({"Label": permissions.SLACK}))
    run, calls = keys_said("T0ABC_SLACK_APP_TOKEN: kept\nT0ABC_SLACK_USER_TOKEN: kept\n", loaded=False)
    reloaded, fixed, failed = [], [], []
    with config():
        permissions.slack(agents, lambda *a: reloaded.append(a[0]), fixed, failed, run)
    assert [c for c, _ in calls] == ["keys", "print", "enable"]
    assert (fixed, failed, reloaded) == ([f"{permissions.SLACK}: not loaded"], [], [permissions.SLACK])


def test_the_update_starts_no_slack_job_without_a_slack_chat_or_its_own_keys(tmp_path):
    config, agents, _ = slack_mac(tmp_path / "none", chats='["telegram:-1001"]')
    run, calls = keys_said("")
    fixed, failed = [], []
    with config():
        permissions.slack(agents, lambda *a: None, fixed, failed, run)
    assert (calls, fixed, failed) == ([], [], [])

    config, agents, _ = slack_mac(tmp_path / "unkeyed")
    run, calls = keys_said("", code=1)
    with config():
        permissions.slack(agents, lambda *a: None, fixed, failed, run)
    assert [c for c, _ in calls] == ["keys"] and not (agents / f"{permissions.SLACK}.plist").exists()
    assert (fixed, failed) == ([], ["slack: no keys (SLACK_USER_TOKEN_M_TEST is missing)"])


def test_keys_copies_this_machines_own_pair_and_never_anothers(tmp_path, monkeypatch):
    """Each Mac has its own app, so one Socket Mode token never runs on two
    bridges; the unsuffixed names are no Mac's."""
    from bridges.slack import __main__ as slack_main
    from bridges.slack import wire
    from core.credentials import _parse_env

    vault = tmp_path / ".env"
    vault.write_text(
        "SLACK_APP_TOKEN=xapp-old\nSLACK_USER_TOKEN=xoxp-old\n"
        "SLACK_APP_TOKEN_VALOR_THE_COWBOY=xapp-cowboy\nSLACK_USER_TOKEN_VALOR_THE_COWBOY=xoxp-cowboy\n"
        "SLACK_APP_TOKEN_VALOR_THE_PIRATE=xapp-pirate\nSLACK_USER_TOKEN_VALOR_THE_PIRATE=xoxp-pirate\n"
    )
    asked = []
    monkeypatch.setattr(wire, "team_of", lambda token: asked.append(token) or "T0ABC")
    keyfile = tmp_path / "slack-keys"
    assert slack_main.copy(vault, keyfile, "Valor the Pirate") == {
        "T0ABC_SLACK_APP_TOKEN": "written", "T0ABC_SLACK_USER_TOKEN": "written",
    }  # fmt: skip
    assert _parse_env(keyfile.read_text()) == {"T0ABC_SLACK_APP_TOKEN": "xapp-pirate", "T0ABC_SLACK_USER_TOKEN": "xoxp-pirate"}
    assert asked == ["xoxp-pirate"]
    with pytest.raises(SystemExit, match="SLACK_USER_TOKEN_VALOR_THE_BALD is missing"):
        slack_main.copy(vault, keyfile, "Valor the Bald")
    assert "groups:write" in slack_main.manifest()["oauth_config"]["scopes"]["user"]  # creating the dev channels


def test_granted_reads_the_databases_there_are_and_skips_one_that_is_absent(tmp_path, monkeypatch):
    """A Mac without a user TCC database (the Bald, 2026-10-22) still reports
    the grants the system database holds."""
    import sqlite3

    service, target, _, name = permissions.GRANTS[0]
    system = tmp_path / "TCC.db"
    with sqlite3.connect(system) as conn:
        conn.execute("CREATE TABLE access (service, client, indirect_object_identifier, auth_value)")
        conn.execute("INSERT INTO access VALUES (?, ?, ?, 2)", (service, permissions.BUNDLE_ID, target))
    monkeypatch.setattr(permissions, "DATABASES", (system, tmp_path / "absent" / "TCC.db"))
    state = permissions.granted()
    assert state[name] is True
    assert all(v is None for n, v in state.items() if n != name)
