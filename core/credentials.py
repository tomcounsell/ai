"""The kernel databases' credential: who holds it and who is refused.

Every role logging into a kernel database (`settings.database` and
`settings.test_database`) needs a password, by three `scram-sha-256` rules
placed ahead of everything else in the cluster's `pg_hba.conf`; other
databases on the cluster keep whatever rules they had. The passwords live in
one libpq password file (`settings.pg_passfile`, mode 600, outside iCloud
and the vault), which both turn sandbox profiles deny (every workspace turn
runs under one; a bare `turn` has no tools), and which libpq reads
for every kernel and owner connection, so no kernel process carries a
password in a string, a DSN, or its environment.

`secure_login` is the only code that touches a role's password, the
password file, or `pg_hba.conf`. It is idempotent, safe to run twice at
once, and recovers a deleted password file by making a new one and setting
new passwords: it connects as the owner to the `postgres` database, which
the rules leave alone, so a lost file never locks the owner out.
"""

import base64
import contextlib
import fcntl
import hashlib
import hmac
import os
import secrets
import shutil
import stat
from pathlib import Path

import psycopg
from psycopg import sql

MARK_BEGIN = (
    "# valor-kernel: every role needs a password on the kernel databases (python -m core secure-login)"
)
MARK_END = "# valor-kernel: end"
METHOD = "scram-sha-256"


class CredentialError(RuntimeError):
    pass


def rules(databases: list[str]) -> list[tuple[str, ...]]:
    """The three rules, as `pg_hba.conf` fields."""
    dbs = ",".join(databases)
    return [
        ("local", dbs, "all", METHOD),
        ("host", dbs, "all", "127.0.0.1/32", METHOD),
        ("host", dbs, "all", "::1/128", METHOD),
    ]


def parse(text: str) -> list[tuple[str, ...]]:
    """The rules of a `pg_hba.conf`, in order, as whitespace-split fields,
    comments and blank lines dropped."""
    out = []
    for line in text.splitlines():
        fields = tuple(line.split("#", 1)[0].split())
        if fields:
            out.append(fields)
    return out


def block(databases: list[str]) -> str:
    lines = [MARK_BEGIN]
    for r in rules(databases):
        if r[0] == "local":
            lines.append(f"{r[0]:<8}{r[1]:<40}{r[2]:<16}{'':<24}{r[3]}")
        else:
            lines.append(f"{r[0]:<8}{r[1]:<40}{r[2]:<16}{r[3]:<24}{r[4]}")
    lines.append(MARK_END)
    return "\n".join(lines) + "\n"


def without_block(text: str) -> str:
    """`text` with every marked block of ours removed."""
    kept, inside = [], False
    for line in text.splitlines(keepends=True):
        if line.rstrip("\n") == MARK_BEGIN:
            inside = True
        elif inside and line.rstrip("\n") == MARK_END:
            inside = False
        elif not inside:
            kept.append(line)
    return "".join(kept)


def scram_verifier(password: str, iterations: int = 4096) -> str:
    """A SCRAM-SHA-256 verifier (RFC 7677), the form Postgres stores, so
    the plain password never reaches the server or its log."""
    salt = os.urandom(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode()
    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def secure_login(
    *,
    host: str,
    port: int,
    passfile: str | Path,
    databases: list[str],
    owner: str,
    kernel_role: str,
) -> dict[str, str]:
    """Make the password file if missing, set both roles' passwords from
    it, and put the rules first in the cluster's `pg_hba.conf`. Every
    argument is explicit: this acts on whichever cluster `host` and `port`
    name. Returns what it did."""
    passfile = Path(passfile)
    passfile.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(f"{passfile}.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        created = _ensure_passfile(passfile, databases, [kernel_role, owner])
        passwords = _read_passfile(passfile, databases, [kernel_role, owner])
        dsn = f"host={host} port={port} dbname=postgres user={owner} passfile={passfile}"
        with psycopg.connect(dsn, autocommit=True) as conn:
            if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (kernel_role,)).fetchone() is None:
                conn.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(kernel_role)))
            for role, password in passwords.items():
                conn.execute(
                    sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                        sql.Identifier(role), sql.Literal(scram_verifier(password))
                    )
                )
            hba = _secure_hba(conn, databases)
    return {"passfile": "created" if created else "kept", "pg_hba.conf": hba}


def _ensure_passfile(passfile: Path, databases: list[str], roles: list[str]) -> bool:
    try:
        fd = os.open(passfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as f:
        for role in roles:
            password = secrets.token_urlsafe(32)  # letters, digits, - and _: nothing libpq escapes
            for database in databases:
                f.write(f"*:*:{database}:{role}:{password}\n")
        f.flush()
        os.fsync(f.fileno())
    return True


def _read_passfile(passfile: Path, databases: list[str], roles: list[str]) -> dict[str, str]:
    found: dict[tuple[str, str], str] = {}
    for line in passfile.read_text().splitlines():
        parts = line.split(":")
        if len(parts) == 5:
            found[(parts[2], parts[3])] = parts[4]
    out = {}
    for role in roles:
        values = {found.get((database, role)) for database in databases}
        if None in values or len(values) != 1:
            raise CredentialError(
                f"{passfile} has no single password for {role} on {', '.join(databases)}; "
                "delete it and run `python -m core secure-login` again"
            )
        out[role] = values.pop()
    return out


def _secure_hba(conn: psycopg.Connection, databases: list[str]) -> str:
    """Our rules first, written atomically; restored and not reloaded if
    the server would not parse the result."""
    path = Path(conn.execute("SHOW hba_file").fetchone()[0])
    original = path.read_text()
    if parse(original)[:3] == rules(databases):
        return "unchanged"
    mode = stat.S_IMODE(path.stat().st_mode)
    saved = path.with_name(path.name + ".valor-kernel-original")
    shutil.copy2(path, saved)
    staged = path.with_name(path.name + ".valor-kernel-new")
    fd = os.open(staged, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, mode)
    with os.fdopen(fd, "w") as f:
        f.write(block(databases) + without_block(original))
        f.flush()
        os.fsync(f.fileno())
    os.replace(staged, path)
    errors = conn.execute(
        "SELECT line_number, error FROM pg_hba_file_rules WHERE error IS NOT NULL ORDER BY line_number"
    ).fetchall()
    if errors:
        os.replace(saved, path)
        raise CredentialError(
            f"{path} would not parse ({errors}); the original is back in place and the server was not reloaded"
        )
    conn.execute("SELECT pg_reload_conf()")
    saved.unlink()
    return "written"


# -- the judgement legs' keys ------------------------------------------------------
#
# Kernel-held secrets live in the kernel key directory, the password file's
# directory, which both turn sandbox profiles deny. The judgement keys are
# one `NAME=value` file there (`settings.judgement_keyfile`), written only by
# `copy_keys` and read only by the kernel process, never into an
# environment.


class MissingKey(CredentialError):
    """A key the kernel needs is not in its key file. The message names the
    variable and the file, never a value."""


def _parse_env(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        out[name] = value
    return out


def read_key(keyfile: str | Path, name: str, command: str = "judgement-keys") -> str:
    """One key from a kernel key file, or `MissingKey` naming it and the
    command that writes that file."""
    path = Path(keyfile)
    try:
        found = _parse_env(path.read_text())
    except FileNotFoundError:
        raise MissingKey(
            f"{name}: the key file {path} does not exist; run `python -m core {command}`"
        ) from None
    value = found.get(name)
    if not value:
        raise MissingKey(f"{name} is not in {path}; run `python -m core {command}`")
    return value


# The merge's GitHub credential. The token sits in `settings.github_keyfile`;
# for each git call the merge performer makes against a granted remote, the
# kernel writes a config file holding one pinned header scoped to that URL,
# hands git its path as `GIT_CONFIG_GLOBAL`, and removes it when git exits.

GITHUB_KEY = "GITHUB_PUSH_TOKEN"
HEADER_PREFIX = "github-push-"
HEADER_SUFFIX = ".gitconfig"


@contextlib.contextmanager
def header_file(keyfile: str | Path, url: str, *, loopback: bool = False):
    """A config file, mode 600, in the key file's directory, carrying the
    token as `http.<url>.extraHeader`, with `http.followRedirects=false` so
    git never carries the header to a redirect's target; yields its path and
    removes it on exit. Refuses a URL `targets.url_ok` refuses, so nothing
    the URL holds can add a line. The file is named by the PID of the
    process writing it; leftovers whose process no longer exists are
    removed first (`sweep_headers`)."""
    from core import targets

    if not targets.url_ok(url, loopback=loopback):
        raise CredentialError(f"{url} is not an https://host/path URL the kernel writes into config")
    token = read_key(keyfile, GITHUB_KEY, "github-key")
    directory = Path(keyfile).parent
    sweep_headers(directory)
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    path = directory / f"{HEADER_PREFIX}{os.getpid()}-{secrets.token_hex(16)}{HEADER_SUFFIX}"
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(
                f'[http]\n\tfollowRedirects = false\n[http "{url}"]\n\textraHeader = Authorization: Basic {basic}\n'
            )
        yield path
    finally:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


def sweep_headers(directory: str | Path) -> list[str]:
    """Remove header files whose writing process (the PID in the name) no
    longer exists, and any not named by a PID; returns the names removed.
    A live writer removes its own file when its call ends."""
    removed = []
    for entry in os.scandir(directory):
        name = entry.name
        if not (name.startswith(HEADER_PREFIX) and name.endswith(HEADER_SUFFIX)):
            continue
        pid = name[len(HEADER_PREFIX) : -len(HEADER_SUFFIX)].split("-", 1)[0]
        if pid.isdigit() and _alive(int(pid)):
            continue
        with contextlib.suppress(FileNotFoundError):
            os.unlink(entry.path)
            removed.append(name)
    return removed


def _alive(pid: int) -> bool:
    """Whether a process with this PID exists (signal 0 sends nothing)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def copy_keys(
    vault_env: str | Path, keyfile: str | Path, names: list[str], sources: dict[str, str] | None = None
) -> dict[str, str]:
    """Copy `names` from the vault `.env` into the key file (mode 600, its
    directory 700), atomically, each read from the vault name `sources`
    gives it (its own name by default) and stored under its own, replacing
    any value it had. Returns each name's `written`, `kept`, or `missing`;
    whether a value changed is decided by SHA-256 digest, and no value or
    part of one is returned."""
    sources = sources or {}
    keyfile = Path(keyfile)
    vault = _parse_env(Path(vault_env).read_text())
    keyfile.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with os.fdopen(os.open(f"{keyfile}.lock", os.O_CREAT | os.O_WRONLY, 0o600), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = _parse_env(keyfile.read_text()) if keyfile.exists() else {}
        status: dict[str, str] = {}
        for name in names:
            new = vault.get(sources.get(name, name))
            if not new:
                status[name] = "missing"
                continue
            same = (
                name in current
                and hashlib.sha256(current[name].encode()).digest() == hashlib.sha256(new.encode()).digest()
            )
            status[name] = "kept" if same else "written"
            current[name] = new
        staged = keyfile.with_name(keyfile.name + ".new")
        fd = os.open(staged, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as f:
            for name, value in current.items():
                f.write(f"{name}={value}\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(staged, 0o600)
        os.replace(staged, keyfile)
    return status
