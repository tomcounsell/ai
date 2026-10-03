"""Transcript copies: each turn's Claude Code session file, and its
subagents' files, kept in the store with a digest.

A workspace turn writes its session under its own config directory
(`<anchor>/claude/projects/<dir>/<session_id>.jsonl`, subagents under
`<session_id>/subagents/agent-*.jsonl`). The anchor (`<task>/state/work`,
or `<task>/checks/<name>`) is the kernel's; everything below it is the
turn's. So the kernel opens the anchor itself, walks `claude` and
`projects` without following a link (`workspace.open_turn_dir`), lists
`projects/` and takes the one child directory holding the session file
the kernel named (`--session-id`), and reads each file with
`workspace.read_turn_file`: a regular file with one link, or nothing and a
reason. A file is named by its path under `projects/<dir>`.

Copied after the turn's processes are reaped, stopped turns included,
before `turn.ended`. Stored as documents of kind `transcript`, id
`<turn_id>/<name>/<n>`, body `{turn_id, name, offset, chunk, base64}`:
base64 of the raw bytes, `CHUNK` raw bytes per document, no size cap. A
file the task's last copy covers is stored from where that copy ended,
when its first bytes still hash to that copy's digest; otherwise whole,
with `prefix_changed`. `sha256` and `bytes` are always the whole file's.

The documents are written in their own transaction; when that or the read
fails, nothing is stored and `turn.ended` says `no_transcript`. Nothing
here decides anything: a transcript is a record.
"""

import base64
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

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


def collect(t: Transcript) -> tuple[list[tuple[str, bytes]], list[dict[str, str]]]:
    """The session's files as (name, bytes), and the files skipped with
    why. Raises `LookupError` when no project directory holds the session
    file."""
    from core import workspace

    anchor = os.open(t.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        projects, why = workspace.open_turn_dir(anchor, f"{t.root}/projects")
        if projects is None:
            raise LookupError(f"no projects directory: {why}")
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
        raise LookupError(why)
    files: list[tuple[str, bytes]] = []
    skipped: list[dict[str, str]] = []
    try:
        data, why = workspace.read_turn_file(fd, session)
        if data is None:
            skipped.append({"name": session, "why": why})
        else:
            files.append((session, data))
        sub_dir = f"{session_id}/subagents"
        sub, why = workspace.open_turn_dir(fd, sub_dir)
        if sub is None:
            if not why.endswith("does not exist"):
                skipped.append({"name": sub_dir, "why": why})
            return files, skipped
        try:
            for entry in sorted(os.listdir(sub)):
                if not (entry.startswith("agent-") and entry.endswith(".jsonl")):
                    continue
                name = f"{sub_dir}/{entry}"
                data, why = workspace.read_turn_file(sub, entry)
                if data is None:
                    skipped.append({"name": name, "why": why})
                else:
                    files.append((name, data))
        finally:
            os.close(sub)
    finally:
        os.close(fd)
    return files, skipped


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


def plan(files: list[tuple[str, bytes]], last: dict[str, dict[str, Any]]):
    """Each file's record for `turn.ended` and its bytes to store."""
    out = []
    for name, data in files:
        prev = last.get(name)
        offset, changed = 0, prev is not None
        if (
            prev is not None
            and len(data) >= prev["bytes"]
            and hashlib.sha256(data[: prev["bytes"]]).hexdigest() == prev["sha256"]
        ):
            offset, changed = prev["bytes"], False
        delta = data[offset:]
        chunks = [delta[i : i + CHUNK] for i in range(0, len(delta), CHUNK)]
        record = {
            "name": name,
            "documents": len(chunks),
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "offset": offset,
            "prefix_changed": changed,
        }
        out.append((record, chunks))
    return out


async def copy(conn, task_id: str, turn_id: str, t: Transcript, *, read=collect) -> dict[str, Any]:
    """Copy the turn's files into the store. Returns what `turn.ended`
    gains: `transcript`, or `no_transcript` with the reason."""
    import asyncio

    try:
        files, skipped = await asyncio.to_thread(read, t)
        planned = plan(files, await _last_copies(conn, task_id))
        async with conn.transaction():
            for record, chunks in planned:
                offset = record["offset"]
                for n, chunk in enumerate(chunks):
                    await conn.execute(
                        "INSERT INTO documents (kind, id, body) VALUES (%s, %s, %s)",
                        (
                            KIND,
                            f"{turn_id}/{record['name']}/{n}",
                            Jsonb(
                                {
                                    "turn_id": turn_id,
                                    "name": record["name"],
                                    "offset": offset,
                                    "chunk": n,
                                    "base64": base64.b64encode(chunk).decode(),
                                }
                            ),
                        ),
                    )
                    offset += len(chunk)
    except Exception as exc:  # noqa: BLE001  a transcript never keeps turn.ended from being written
        return {"no_transcript": f"{type(exc).__name__}: {exc}"[:400]}
    return {"transcript": {"files": [r for r, _ in planned], "skipped": skipped}}


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
