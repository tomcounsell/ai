"""The shape of a merge's URL.

A merge lands on the URL and target branch the kernel recorded in the
task's Brief at start, from the project spec (`merge_url`, and `branch` or
`target_branch`); no turn changes either. `local` tells a local path from
a remote, which only a remote merge needs the GitHub credential for.

A remote URL is written into a git config file for the merge's header,
so `url_ok` keeps it to `https://host/path` with a plain path: no quote,
backslash, bracket, newline, space, dot segment, port, user info, query,
or fragment can reach that file.
"""

import re
from urllib.parse import urlsplit

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
