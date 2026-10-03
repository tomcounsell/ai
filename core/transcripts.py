"""Transcript copies: each turn's Claude Code session file, and its
subagents' files, kept in the store with a digest.

A workspace turn writes its session under its own config directory
(`<anchor>/claude/projects/<dir>/<session_id>.jsonl`, subagents under
`<session_id>/subagents/agent-*.jsonl`). The anchor (`<task>/state/work`,
or `<task>/checks/<name>`) is the kernel's; everything below it is the
turn's. So the kernel opens the anchor itself, walks `claude` and
`projects` without following a link (`workspace.open_turn_dir`), lists
`projects/` and takes the one child directory holding the session file
the kernel named (`--session-id`), and opens each file with
`workspace.open_turn_file`: a regular file with one link that is not
sparse, or nothing and a reason. A file is named by its path under
`projects/<dir>`.

Copied after the turn's processes are reaped, stopped turns included,
before `turn.ended`. Stored as documents of kind `transcript`, id
`<turn_id>/<name>/<n>`, body `{turn_id, name, offset, chunk, base64}`:
base64 of the raw bytes, `CHUNK` raw bytes per document, no size cap. Each
file is streamed from its descriptor one chunk at a time, every read,
digest, base64 encoding, and JSON dump in a worker thread, so the event
loop never waits on the disk or on a chunk's encoding. A
file the task's last copy covers is stored from where that copy ended,
when its first bytes still hash to that copy's digest; otherwise whole,
with `prefix_changed`. `sha256` and `bytes` are always the whole file's.

The documents are written in their own transaction; when that or the read
fails, nothing is stored and `turn.ended` says `no_transcript`. Nothing
here decides anything: a transcript is a record.
"""

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CHUNK = 64 * 1024 * 1024
KIND = "transcript"


@dataclass(frozen=True)
class Transcript:
    """Where a turn's session file is: `anchor`, the kernel's directory
    holding the config root; `root`, the config root's name in it;
    `session_id`, the id the kernel gave the session."""

    anchor: str
    root: str
    session_id: str


def collect(t: Transcript) -> tuple[list[tuple[str, int]], list[dict[str, str]]]:
    """The session's files as (name, open descriptor), the caller closing
    each, and the files skipped with why. Raises `LookupError` when no
    project directory holds the session file."""
    from core import workspace

    anchor = os.open(t.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        projects, why = workspace.open_turn_dir(anchor, f"{t.root}/projects")
        if projects is None:
            raise LookupError(f"no projects directory: {why or f'{t.root}/projects does not exist'}")
        try:
            return _collect(projects, t.session_id)
        finally:
            os.close(projects)
    finally:
        os.close(anchor)


def _collect(projects: int, session_id: str):
    from core import workspace

    session = f"{session_id}.jsonl"
    holding, refused = [], []
    for entry in sorted(os.listdir(projects)):
        fd, why = workspace.open_turn_dir(projects, entry)
        if fd is None:
            if why is not None:
                refused.append(why)
            continue
        try:
            os.stat(session, dir_fd=fd, follow_symlinks=False)
            holding.append(entry)
        except FileNotFoundError:
            pass
        finally:
            os.close(fd)
    if len(holding) != 1:
        found = "no project directory holds" if not holding else f"{len(holding)} project directories hold"
        raise LookupError(f"{found} {session}" + (f" ({'; '.join(refused)})" if refused else ""))
    fd, why = workspace.open_turn_dir(projects, holding[0])
    if fd is None:
        raise LookupError(why or f"{holding[0]} does not exist")
    files: list[tuple[str, int]] = []
    skipped: list[dict[str, str]] = []

    def take(name: str, dir_fd: int, entry: str) -> None:
        opened, why = workspace.open_turn_file(dir_fd, entry)
        if opened is not None:
            files.append((name, opened))
        elif why is not None:
            skipped.append({"name": name, "why": why})

    try:
        take(session, fd, session)
        sub_dir = f"{session_id}/subagents"
        sub, why = workspace.open_turn_dir(fd, sub_dir)
        if sub is None:
            if why is not None:
                skipped.append({"name": sub_dir, "why": why})
            return files, skipped
        try:
            for entry in sorted(os.listdir(sub)):
                if entry.startswith("agent-") and entry.endswith(".jsonl"):
                    take(f"{sub_dir}/{entry}", sub, entry)
        finally:
            os.close(sub)
    except BaseException:
        _close(files)
        raise
    finally:
        os.close(fd)
    return files, skipped


def _close(files: list[tuple[str, int]]) -> None:
    for _, fd in files:
        with contextlib.suppress(OSError):
            os.close(fd)


async def _last_copies(conn, task_id: str) -> dict[str, dict[str, Any]]:
    """Each name's entry in the task's latest `turn.ended` that recorded it."""
    rows = await (
        await conn.execute(
            "SELECT payload->'transcript'->'files' FROM events WHERE task_id = %s "
            "AND type = 'turn.ended' AND payload ? 'transcript' ORDER BY id",
            (task_id,),
        )
    ).fetchall()
    last: dict[str, dict[str, Any]] = {}
    for (files,) in rows:
        for f in files or []:
            last[f["name"]] = f
    return last


def _hash_prefix(fd: int, length: int):
    """The digest of the file's first `length` bytes, read in pieces."""
    h, pos = hashlib.sha256(), 0
    while pos < length:
        piece = os.pread(fd, min(1 << 20, length - pos), pos)
        if not piece:
            break
        h.update(piece)
        pos += len(piece)
    return h, pos


def _read_chunk(fd: int, pos: int, n: int, whole, turn_id: str, name: str) -> tuple[int, str | None]:
    """Read, digest, base64-encode, and serialize one chunk, all in the
    caller's worker thread: its length, and its document body as JSON text
    (None at the end of the file)."""
    chunk = os.pread(fd, CHUNK, pos)
    if not chunk:
        return 0, None
    whole.update(chunk)
    body = {
        "turn_id": turn_id,
        "name": name,
        "offset": pos,
        "chunk": n,
        "base64": base64.b64encode(chunk).decode(),
    }
    return len(chunk), json.dumps(body)


async def _store(conn, turn_id: str, name: str, fd: int, prev: dict[str, Any] | None) -> dict[str, Any]:
    """Stream one file into documents; returns its `turn.ended` record."""
    size = (await asyncio.to_thread(os.fstat, fd)).st_size
    whole, offset, changed = hashlib.sha256(), 0, prev is not None
    if prev is not None and size >= prev["bytes"]:
        prefix, read = await asyncio.to_thread(_hash_prefix, fd, prev["bytes"])
        if read == prev["bytes"] and prefix.hexdigest() == prev["sha256"]:
            whole, offset, changed = prefix, prev["bytes"], False
    pos, n = offset, 0
    while True:
        length, body = await asyncio.to_thread(_read_chunk, fd, pos, n, whole, turn_id, name)
        if body is None:
            break
        await conn.execute(
            "INSERT INTO documents (kind, id, body) VALUES (%s, %s, %s::jsonb)",
            (KIND, f"{turn_id}/{name}/{n}", body),
        )
        pos += length
        n += 1
    return {
        "name": name,
        "documents": n,
        "sha256": whole.hexdigest(),
        "bytes": pos,
        "offset": offset,
        "prefix_changed": changed,
    }


async def _opened(read, t: Transcript):
    """`read(t)` in a worker thread that owns the descriptors it opens: it
    hands them to the caller only while the caller still awaits, and
    closes them in its own `finally` otherwise. A caller cancelled after
    the hand-over closes them itself."""
    gate = threading.Lock()
    state: dict[str, Any] = {"cancelled": False, "files": []}

    def run():
        files, skipped = read(t)
        handed = False
        try:
            with gate:
                if not state["cancelled"]:
                    state["files"], handed = files, True
        finally:
            if not handed:
                _close(files)
        return files, skipped

    try:
        return await asyncio.to_thread(run)
    except BaseException:
        with gate:
            state["cancelled"] = True
            _close(state["files"])
        raise


async def copy(conn, task_id: str, turn_id: str, t: Transcript, *, read=collect) -> dict[str, Any]:
    """Copy the turn's files into the store. Returns what `turn.ended`
    gains: `transcript`, or `no_transcript` with the reason."""
    opened: list[tuple[str, int]] = []
    try:
        opened, skipped = await _opened(read, t)
        last = await _last_copies(conn, task_id)
        records = []
        async with conn.transaction():
            for name, fd in opened:
                records.append(await _store(conn, turn_id, name, fd, last.get(name)))
    except Exception as exc:  # noqa: BLE001  a transcript never keeps turn.ended from being written
        return {"no_transcript": f"{type(exc).__name__}: {exc}"}
    finally:
        _close(opened)
    return {"transcript": {"files": records, "skipped": skipped}}


async def joined(conn, turn_id: str, name: str) -> bytes:
    """The bytes one turn stored for one name, its chunks joined."""
    rows = await (
        await conn.execute(
            "SELECT body FROM documents WHERE kind = %s AND body->>'turn_id' = %s AND body->>'name' = %s "
            "ORDER BY (body->>'chunk')::int",
            (KIND, turn_id, name),
        )
    ).fetchall()
    return b"".join(base64.b64decode(r[0]["base64"]) for r in rows)


def anchored(config_dir: str | None, session_id: str) -> Transcript | None:
    """The transcript source of a turn whose config directory is
    `config_dir`, or None when it has none."""
    if not config_dir:
        return None
    path = Path(config_dir)
    return Transcript(str(path.parent), path.name, session_id)
