"""The kernel databases' credential, on scratch clusters only.

`secure_login` runs here against throwaway clusters that trust every local
login, the way the machine cluster does before the rollout, and each test
first checks that the connection it will secure is the scratch cluster's.
The machine cluster's `pg_hba.conf`, roles, and password file are only read
(`test_migrate_touches_no_credential_on_the_machine_cluster`).

Live spend: none.
"""

import os
import subprocess
import sys
import threading
from pathlib import Path

import psycopg
import pytest

from core import backup, credentials, db
from core.settings import settings
from harnesses import claude_code

pytestmark = pytest.mark.spend(usd=0)

ROOT = Path(__file__).resolve().parent.parent
KERNEL_DBS = ["valor_rebuild", "valor_rebuild_test"]
OWNER = settings.owner_role
KERNEL = settings.kernel_role


def _is_scratch(cluster: backup.Cluster) -> None:
    with psycopg.connect(cluster.dsn(), autocommit=True) as conn:
        assert Path(conn.execute("SHOW data_directory").fetchone()[0]).resolve() == cluster.data.resolve()


def _secure(cluster: backup.Cluster, passfile: Path) -> dict:
    _is_scratch(cluster)
    return credentials.secure_login(
        host=cluster.host,
        port=cluster.port,
        passfile=passfile,
        databases=KERNEL_DBS,
        owner=OWNER,
        kernel_role=KERNEL,
    )


def _migrate(cluster: backup.Cluster, passfile: Path) -> None:
    _is_scratch(cluster)
    for name in KERNEL_DBS:
        db.migrate(name, host=cluster.host, port=cluster.port, passfile=str(passfile))


def _connect(cluster, host, database, user, **kw):
    return psycopg.connect(host=host, port=cluster.port, dbname=database, user=user, connect_timeout=5, **kw)


def _refused_28p01(cluster: backup.Cluster, host, database, user, **kw) -> None:
    """The login fails, and the server's log records it as SQLSTATE 28P01
    (psycopg leaves `sqlstate` unset on a failed connection)."""
    log = cluster.root / "postgres.log"
    start = log.stat().st_size
    with pytest.raises(psycopg.OperationalError, match="password authentication failed"):
        _connect(cluster, host, database, user, **kw)
    with open(log) as f:
        f.seek(start)
        logged = f.read()
    assert f'28P01 FATAL:  password authentication failed for user "{user}"' in logged


def _hba(cluster: backup.Cluster) -> Path:
    return cluster.data / "pg_hba.conf"


@pytest.fixture(scope="module")
def secured(tmp_path_factory):
    passfile = tmp_path_factory.mktemp("kernel") / "valor-kernel" / "pgpass"
    with backup.scratch_cluster(tcp=True) as cluster:
        _migrate(cluster, passfile)
        first = _secure(cluster, passfile)
        yield cluster, passfile, first


@pytest.fixture
def fresh(tmp_path):
    with backup.scratch_cluster() as cluster:
        passfile = tmp_path / "valor-kernel" / "pgpass"
        _migrate(cluster, passfile)
        yield cluster, passfile


def test_the_first_run_makes_a_private_password_file_and_writes_the_rules(secured):
    cluster, passfile, first = secured
    assert first == {"passfile": "created", "pg_hba.conf": "written"}
    assert passfile.stat().st_mode & 0o777 == 0o600
    assert passfile.parent.stat().st_mode & 0o777 == 0o700
    assert credentials.parse(_hba(cluster).read_text())[:3] == credentials.rules(KERNEL_DBS)


@pytest.mark.parametrize("host", ["socket", "127.0.0.1", "::1"])
@pytest.mark.parametrize("user", [KERNEL, OWNER])
def test_without_the_credential_every_role_is_refused_on_the_kernel_databases(secured, host, user):
    cluster, passfile, _ = secured
    host = cluster.host if host == "socket" else host
    for database in KERNEL_DBS:
        _refused_28p01(cluster, host, database, user, password="not-the-password", passfile="/dev/null")
        for kw in ({"passfile": "/dev/null"}, {"password": "", "passfile": "/dev/null"}):
            # libpq raises "no password supplied" only when the server asked
            # for one; the same login to `postgres` (left on trust) connects,
            # so the refusal is the rule, not a dead socket.
            with pytest.raises(psycopg.OperationalError, match="no password supplied"):
                _connect(cluster, host, database, user, **kw)
            _connect(cluster, host, "postgres", user, **kw).close()
        with _connect(cluster, host, database, user, passfile=str(passfile)) as conn:
            assert conn.execute("SELECT current_user").fetchone()[0] == user


def test_a_second_run_changes_neither_file(secured):
    cluster, passfile, _ = secured
    hba, pw = _hba(cluster).read_bytes(), passfile.read_bytes()
    assert _secure(cluster, passfile) == {"passfile": "kept", "pg_hba.conf": "unchanged"}
    assert _hba(cluster).read_bytes() == hba and passfile.read_bytes() == pw


def test_our_rules_go_back_first_when_a_rule_lands_above_them(fresh):
    cluster, passfile = fresh
    _secure(cluster, passfile)
    hba = _hba(cluster)
    hba.write_text("local   all   all   trust\n" + hba.read_text())
    assert _secure(cluster, passfile)["pg_hba.conf"] == "written"
    text = hba.read_text()
    assert credentials.parse(text)[:4] == [*credentials.rules(KERNEL_DBS), ("local", "all", "all", "trust")]
    assert text.count(credentials.MARK_BEGIN) == 1
    with pytest.raises(psycopg.OperationalError, match="no password supplied"):
        _connect(cluster, cluster.host, KERNEL_DBS[0], KERNEL, passfile="/dev/null")


def test_two_runs_at_once_leave_one_password_file_that_works(fresh):
    cluster, passfile = fresh
    _is_scratch(cluster)
    errors = []

    def one():
        try:
            _secure(cluster, passfile)
        except Exception as exc:  # noqa: BLE001  reported below
            errors.append(exc)

    threads = [threading.Thread(target=one) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(passfile.read_text().splitlines()) == 2 * len(KERNEL_DBS)
    assert _hba(cluster).read_text().count(credentials.MARK_BEGIN) == 1
    for user in (KERNEL, OWNER):
        _connect(cluster, cluster.host, KERNEL_DBS[0], user, passfile=str(passfile)).close()


def test_a_deleted_password_file_is_recovered_by_running_again(fresh):
    cluster, passfile = fresh
    _secure(cluster, passfile)
    old = passfile.with_name("old")
    passfile.rename(old)
    assert _secure(cluster, passfile)["passfile"] == "created"
    _connect(cluster, cluster.host, KERNEL_DBS[0], KERNEL, passfile=str(passfile)).close()
    _refused_28p01(cluster, cluster.host, KERNEL_DBS[0], KERNEL, passfile=str(old))


def test_a_password_file_missing_a_role_is_refused_and_named(fresh):
    cluster, passfile = fresh
    passfile.parent.mkdir(mode=0o700)
    passfile.write_text(f"*:*:valor_rebuild:{KERNEL}:x\n")
    with pytest.raises(credentials.CredentialError, match="delete it and run"):
        _secure(cluster, passfile)


def test_a_password_file_with_a_different_password_per_database_is_refused(fresh):
    cluster, passfile = fresh
    passfile.parent.mkdir(mode=0o700)
    passfile.write_text(
        "".join(f"*:*:{d}:{role}:{role}-{d}\n" for role in (KERNEL, OWNER) for d in KERNEL_DBS)
    )
    with pytest.raises(credentials.CredentialError, match="no single password"):
        _secure(cluster, passfile)


def test_a_pg_hba_that_would_not_parse_is_put_back_and_never_loaded(fresh):
    cluster, passfile = fresh
    hba = _hba(cluster)
    broken = hba.read_text() + "this is not a rule\n"
    hba.write_text(broken)
    with pytest.raises(credentials.CredentialError, match="would not parse"):
        _secure(cluster, passfile)
    assert hba.read_text() == broken
    assert not list(cluster.data.glob("pg_hba.conf.valor-kernel-*"))
    # The server still runs on the rules it loaded at start: trust.
    _connect(cluster, cluster.host, KERNEL_DBS[0], KERNEL, passfile="/dev/null").close()


# Reads `pg_authid` and the hba file as the machine cluster's owner, a
# superuser on the Mac; the VM's owner role is not one.
@pytest.mark.macos
def test_migrate_touches_no_credential_on_the_machine_cluster(dsn):
    """`db.migrate`, which every test session runs, leaves the machine
    cluster's role passwords, `pg_hba.conf`, and the password file as they
    were."""

    def snapshot():
        with psycopg.connect(settings.dsn(owner=True, database="postgres"), autocommit=True) as conn:
            secrets = conn.execute(
                "SELECT rolname, rolpassword FROM pg_authid WHERE rolname IN (%s, %s) ORDER BY rolname",
                (KERNEL, OWNER),
            ).fetchall()
            hba = Path(conn.execute("SHOW hba_file").fetchone()[0]).read_bytes()
        pf = Path(settings.pg_passfile)
        return secrets, hba, pf.read_bytes() if pf.exists() else None

    before = snapshot()
    db.migrate(settings.test_database)
    assert snapshot() == before


def test_the_cli_secures_a_cluster_and_prints_no_password(tmp_path):
    passfile = tmp_path / "valor-kernel" / "pgpass"
    with backup.scratch_cluster() as cluster:
        _is_scratch(cluster)
        env = {
            **os.environ,
            "VALOR_PGHOST": cluster.host,
            "VALOR_PGPORT": str(cluster.port),
            "VALOR_PG_PASSFILE": str(passfile),
            "VALOR_DB": KERNEL_DBS[0],
            "VALOR_TEST_DB": KERNEL_DBS[1],
        }
        out = ""
        for args in (["migrate"], ["settings"], ["secure-login"]):
            out += subprocess.run(
                [sys.executable, "-m", "core", *args],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        passwords = {line.rsplit(":", 1)[1] for line in passfile.read_text().splitlines()}
        assert len(passwords) == 2 and not any(p in out for p in passwords)
        assert '"passfile": "created"' in out and '"passfile": "kept"' in out
        with pytest.raises(psycopg.OperationalError, match="no password supplied"):
            _connect(cluster, cluster.host, KERNEL_DBS[0], KERNEL, passfile="/dev/null")


@pytest.mark.macos
def test_no_turn_environment_carries_the_credential(monkeypatch, tmp_path):
    """Tom exports `PGPASSFILE` for his own psql; neither harness builder
    passes it, or any other libpq or `VALOR_PG*` variable, to a turn."""
    monkeypatch.setenv("PGPASSFILE", settings.pg_passfile)
    monkeypatch.setenv("PGPASSWORD", "sekrit-value")
    monkeypatch.setenv("VALOR_PG_PASSFILE", settings.pg_passfile)
    built = [
        claude_code.turn("hi", cwd=str(tmp_path))("http://127.0.0.1:1/t/x", "brief", "t1"),
        claude_code.workspace_turn("hi", cwd=str(tmp_path), harness={"sandbox_profile": "/p.sb"})(
            "http://127.0.0.1:1/t/x", "brief", "t1"
        ),
    ]
    for command in built:
        assert not [k for k in command.env if k.startswith(("PG", "VALOR_PG"))]
        assert not [v for v in command.env.values() if "sekrit-value" in v or settings.pg_passfile in v]
        assert settings.pg_passfile not in " ".join(command.argv)
