"""The SQL. Plan 04, Modules; seams §5.1.

Hand-written and parameterized, under `kernel_rw`, which holds SELECT and
INSERT on the three tables and nothing else (tech stack §3). Every function
takes the caller's connection and runs inside the caller's transaction; the
caller commits. Nothing here opens a connection or holds state.
"""

import hashlib
import json
from dataclasses import dataclass

from psycopg.types.json import Jsonb

from schemas.gateway import Usage

LOG_COLUMNS = (
    "brief_id",
    "generation",
    "space_id",
    "call",
    "event",
    "model",
    "request_sha256",
    "estimated_input",
    "usage",
    "stop_reason",
    "reason",
    "cache_state",
)


@dataclass(frozen=True)
class TokenRow:
    """A row of `gateway_tokens`. Immutable, so a cached copy is never
    stale. `cap_usd_micros` is set for a turn token and null for a Brief's
    (seams v3 §3.9)."""

    token_sha256: str
    brief_id: str
    generation: int
    model_ref: str
    space_id: str
    cap_usd_micros: int | None

    @property
    def is_turn(self) -> bool:
        """Generation 0 is a turn token: no objective, no fence, a cap
        instead of a ledger node. No Brief carries it, since a Brief's
        generation is 1 plus its stops (seams §1.4)."""
        return self.generation == 0


def token_sha256(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def body_sha256(canonical_bytes: bytes) -> str:
    return hashlib.sha256(canonical_bytes).hexdigest()


# ---------------------------------------------------------------------------
# Writes


async def insert_token(
    conn,
    *,
    token_sha256: str,
    brief_id: str,
    generation: int,
    model_ref: str,
    space_id: str,
    cap_usd_micros: int | None,
) -> None:
    await conn.execute(
        "INSERT INTO gateway_tokens (token_sha256, brief_id, generation, model_ref, "
        "space_id, cap_usd_micros) VALUES (%s, %s, %s, %s, %s, %s)",
        (token_sha256, brief_id, generation, model_ref, space_id, cap_usd_micros),
    )


async def insert_log(conn, *, event: str, usage: Usage | None = None, **fields) -> int:
    """One `gateway_log` row. Returns its id. Every column the caller does
    not name is null, which is what a refusal with no Brief needs."""
    values = {k: fields.pop(k, None) for k in LOG_COLUMNS}
    if fields:
        raise TypeError(f"gateway_log has no column {sorted(fields)}")
    values["event"] = event
    if usage is not None:
        values["usage"] = Jsonb(usage.model_dump(mode="json"))
    columns = ", ".join(LOG_COLUMNS)
    placeholders = ", ".join(["%s"] * len(LOG_COLUMNS))
    row = await (
        await conn.execute(
            f"INSERT INTO gateway_log ({columns}) VALUES ({placeholders}) RETURNING id",
            tuple(values[k] for k in LOG_COLUMNS),
        )
    ).fetchone()
    return row[0]


async def insert_body(conn, sha256: str, body: dict) -> None:
    """A body is stored once. `ON CONFLICT DO NOTHING` needs no UPDATE
    privilege, which `kernel_rw` does not hold."""
    await conn.execute(
        "INSERT INTO request_bodies (sha256, body) VALUES (%s, %s) "
        "ON CONFLICT (sha256) DO NOTHING",
        (sha256, Jsonb(body)),
    )


# ---------------------------------------------------------------------------
# Reads


async def revoked_briefs(conn) -> set[str]:
    """Warms the in-memory revoked set at `start()`. Bounded by the number
    of stops."""
    rows = await (
        await conn.execute(
            "SELECT DISTINCT brief_id FROM gateway_log "
            "WHERE event = 'token_revoked' AND brief_id IS NOT NULL"
        )
    ).fetchall()
    return {r[0] for r in rows}


async def last_call(conn, brief_id: str) -> int:
    """The highest call number the log holds for the Brief, so numbering
    continues across a restart (seams §5.1)."""
    row = await (
        await conn.execute(
            "SELECT coalesce(max(call), 0) FROM gateway_log WHERE brief_id = %s",
            (brief_id,),
        )
    ).fetchone()
    return int(row[0])


async def token_row(conn, token_sha256: str) -> TokenRow | None:
    row = await (
        await conn.execute(
            "SELECT token_sha256, brief_id, generation, model_ref, space_id, "
            "cap_usd_micros FROM gateway_tokens WHERE token_sha256 = %s",
            (token_sha256,),
        )
    ).fetchone()
    return None if row is None else TokenRow(*row)


async def newest_token_for_brief(conn, brief_id: str) -> TokenRow | None:
    """`revoke` fills its row's `generation` and `space_id` from here; the
    table is keyed by hash and the revoker holds only the id.

    `generation` breaks the tie before the hash does, because `issued_at`
    defaults to `now()`, which is the transaction's start: two tokens minted
    for one brief in one transaction carry the same timestamp, and ordering
    those by hash picks one of them at random. Generations only ever
    increase for a brief, so the highest is the newest, and two tokens at
    one generation carry the same generation and space anyway, which is all
    `revoke` reads here."""
    row = await (
        await conn.execute(
            "SELECT token_sha256, brief_id, generation, model_ref, space_id, "
            "cap_usd_micros FROM gateway_tokens WHERE brief_id = %s "
            "ORDER BY issued_at DESC, generation DESC, token_sha256 DESC LIMIT 1",
            (brief_id,),
        )
    ).fetchone()
    return None if row is None else TokenRow(*row)


async def spent_usd_micros(conn, brief_id: str) -> int:
    """What the log says a turn has already spent: the sum of `usd_micros`
    over its `response` and `cut` rows. A turn keeps no ledger node, so its
    cap is metered from the gateway's own rows (seams v3 §3.9)."""
    row = await (
        await conn.execute(
            "SELECT coalesce(sum((usage->>'usd_micros')::bigint), 0) FROM gateway_log "
            "WHERE brief_id = %s AND event IN ('response', 'cut')",
            (brief_id,),
        )
    ).fetchone()
    return int(row[0])


async def dangling_requests(
    conn,
) -> list[tuple[str, int, int, str, str, int, dict]]:
    """Every `request` row with no `response`, `cut`, or `upstream_error`
    for the same `(brief_id, call)`, with the stored body so the reconcile
    can read its `max_tokens` (plan 04, Restart; critique 8).

    Returns (brief_id, generation, call, space_id, model, estimated_input,
    body).
    """
    rows = await (
        await conn.execute(
            "SELECT r.brief_id, r.generation, r.call, r.space_id, r.model, "
            "coalesce(r.estimated_input, 0), b.body "
            "FROM gateway_log r "
            "LEFT JOIN request_bodies b ON b.sha256 = r.request_sha256 "
            "WHERE r.event = 'request' AND NOT EXISTS ("
            "  SELECT 1 FROM gateway_log c WHERE c.brief_id = r.brief_id "
            "  AND c.call = r.call AND c.event IN ('response', 'cut', 'upstream_error')"
            ") ORDER BY r.id"
        )
    ).fetchall()
    return [
        (
            r[0],
            r[1],
            r[2],
            r[3],
            r[4],
            r[5],
            r[6] if isinstance(r[6], dict) else json.loads(r[6] or "{}"),
        )
        for r in rows
    ]
