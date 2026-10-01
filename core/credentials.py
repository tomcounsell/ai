"""The kernel databases' credential: who holds it and who is refused.

Every role logging into a kernel database (`settings.database` and
`settings.test_database`) needs a password, by three `scram-sha-256` rules
placed ahead of everything else in the cluster's `pg_hba.conf`; other
databases on the cluster keep whatever rules they had. The passwords live in
one libpq password file (`settings.pg_passfile`, mode 600, outside iCloud
and the vault), which both turn sandbox profiles deny, and which libpq reads
for every kernel and owner connection, so no kernel process carries a
password in a string, a DSN, or its environment.

`secure_login` is the only code that touches a role's password, the
password file, or `pg_hba.conf`. It is idempotent, safe to run twice at
once, and recovers a deleted password file by making a new one and setting
new passwords: it connects as the owner to the `postgres` database, which
the rules leave alone, so a lost file never locks the owner out.
"""

import base64
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
