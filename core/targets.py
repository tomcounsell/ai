"""Where a merge may land: the merge-target list.

A merge to a remote (anything but a local path) lands only on a (URL,
branch) pair Tom granted. Grants and revocations are rows on the global
stream `merge_targets`, `merge_target.granted` and `merge_target.revoked`,
each `{url, branch, note, provenance}`; a pair is granted when its latest
row is a grant. Rows never change.

`python -m core merge-target add` writes a grant, always by Tom;
`merge-target remove` writes a revocation and may be run by anyone, since
it takes authority away. `check` reads only the ledger, so it runs inside
the request's and the release's transaction under the task lock.

A granted URL is written into a git config file for the merge's header,
so `url_ok` keeps it to `https://host/path` with a plain path: no quote,
backslash, bracket, newline, space, dot segment, port, user info, query,
or fragment can reach that file.
"""

import re
from typing import Any
from urllib.parse import urlsplit

from core import ledger

STREAM = "merge_targets"
GRANTED = "merge_target.granted"
REVOKED = "merge_target.revoked"

_HOST = re.compile(r"[A-Za-z0-9.-]+")
_PATH = re.compile(r"/[A-Za-z0-9._/-]+")
_LOOPBACK = "127.0.0.1"


def local(url: str) -> bool:
    """A local path: no scheme and no `host:path` form."""
    return "://" not in url and not re.match(r"^[^/]+:", url)


def url_ok(url: str, *, loopback: bool = False) -> bool:
    """`https://<host>/<path>` with a plain host and path. `loopback` also
    lets `http://127.0.0.1:<port>/<path>` through, for tests only."""
    if any(c.isspace() or not c.isprintable() for c in url):
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    if parts.query or parts.fragment or "?" in url or "#" in url or "@" in parts.netloc:
        return False
    if not _PATH.fullmatch(parts.path) or any(seg in (".", "..") for seg in parts.path.split("/")):
        return False
    host = parts.hostname or ""
    if loopback and parts.scheme == "http" and host == _LOOPBACK and port is not None:
        return parts.netloc == f"{_LOOPBACK}:{port}"
    return parts.scheme == "https" and port is None and parts.netloc == host and bool(_HOST.fullmatch(host))


async def grant(conn, url: str, branch: str, note: str, *, via: str = "the command line") -> None:
    """Tom's grant of one pair."""
    await ledger.append(
        conn,
        STREAM,
        GRANTED,
        {"url": url, "branch": branch, "note": note, "provenance": ledger.provenance("tom", via, False)},
    )


async def revoke(conn, url: str, branch: str, note: str, *, by: str, via: str = "the command line") -> None:
    await ledger.append(
        conn,
        STREAM,
        REVOKED,
        {"url": url, "branch": branch, "note": note, "provenance": ledger.provenance(by, via, False)},
    )


async def granted(conn) -> list[dict[str, Any]]:
    """Every pair whose latest row is a grant, with that grant's note."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in await ledger.read(conn, STREAM):
        p = row["payload"]
        latest[(p["url"], p["branch"])] = {**p, "type": row["type"]}
    return [p for p in latest.values() if p["type"] == GRANTED]


async def check(conn, url: str | None, branch: str | None) -> str | None:
    """Why a merge to (url, branch) may not land, or None. A local path
    needs no grant."""
    if not url or not branch:
        return "a merge names its URL and target branch"
    if local(url):
        return None
    row = await (
        await conn.execute(
            "SELECT type FROM events WHERE task_id = %s AND type IN (%s, %s) "
            "AND payload->>'url' = %s AND payload->>'branch' = %s ORDER BY id DESC LIMIT 1",
            (STREAM, GRANTED, REVOKED, url, branch),
        )
    ).fetchone()
    if row is None or row[0] != GRANTED:
        return (
            f"{url} {branch} is not a granted merge target; Tom grants it with "
            f"`python -m core merge-target add {url} {branch} --note TEXT`"
        )
    return None
