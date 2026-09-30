"""Spaces: the manifests, the root capability set each one derives, and the
refusals that keep an objective out of a space that does not exist.
Seams §3.4; architecture §9, §1, §3.1.

One module, no state beyond what `load_all` returns. It takes no advisory
lock: `record_inbound` rests on the unique constraint of `inbound_items`, and
nothing here has a second writer.

The module imports no connector code. `poll_connectors` takes `read_recent`
as an argument so `kernel/spaces.py` never imports `broker/`, which depends
on this plan.
"""

import email.utils
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Sequence, get_args

import psycopg
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from kernel.events import append
from schemas.capability import Capabilities, Capability, CapabilityName
from schemas.ids import SpaceId
from schemas.inbound import InboundItem
from schemas.space import (
    UNASSIGNED_SPACE_ID,
    Connector,
    RoutingRule,
    Space,
    load_space,
)

SPACES_DIR = Path(__file__).parents[1] / "infra" / "spaces"


class SpaceRefused(Exception):
    """A manifest set that cannot be loaded, or a space an objective may not
    open in. The reason is the message."""


def _connector_key(connector: Connector) -> tuple:
    """The identity a rule collides on: kind, account, and the rule itself."""
    return (
        connector.kind,
        connector.account,
        tuple(sorted(connector.route.model_dump().items())),
    )


def load_all(directory: Path = SPACES_DIR) -> dict[SpaceId, Space]:
    """Every `*.yaml` under `directory`, keyed by space id.

    Four refusals. Three are seams §3.4: a duplicate id, the reserved id, and
    an identical `(kind, account, route)` connector in two spaces, which
    would send every matching item to `unassigned` forever and is a mistake
    the manifest is the place to catch.

    Decided in the plan, the fourth: a `gmail` connector whose rule sets no
    `sender_domain`. An empty `RoutingRule` parses and would claim every item
    on its account; a rule setting only `label`, `folder`, or `calendar`
    matches nothing at M0 and would send the whole account to `unassigned` in
    silence; and `broker/gmail.py` composes its query from `sender_domain`,
    so a rule without one has no read either.

    A manifest that does not parse is refused here too, with the file named:
    the reserved id and the id charset are validators on `Space.id`, so this
    is where that refusal reaches the caller.
    """
    spaces: dict[SpaceId, Space] = {}
    origin: dict[SpaceId, Path] = {}
    connectors: dict[tuple, tuple[SpaceId, Path]] = {}

    for path in sorted(directory.glob("*.yaml")):
        try:
            space = load_space(path)
        except ValidationError as exc:
            raise SpaceRefused(f"{path.name} is not a valid manifest: {exc}") from exc

        if space.id in spaces:
            raise SpaceRefused(
                f"space id {space.id!r} is claimed by both "
                f"{origin[space.id].name} and {path.name}"
            )

        for connector in space.connectors:
            if connector.kind == "gmail" and connector.route.sender_domain is None:
                raise SpaceRefused(
                    f"{path.name}: the gmail connector on {connector.account!r} sets "
                    "no sender_domain, so it would claim the whole account or "
                    "nothing at all"
                )
            key = _connector_key(connector)
            if key in connectors:
                _, other_path = connectors[key]
                raise SpaceRefused(
                    f"{path.name} and {other_path.name} declare the same "
                    f"{connector.kind} rule on {connector.account!r}, so every "
                    f"item it matches would land in {UNASSIGNED_SPACE_ID!r}"
                )
            connectors[key] = (space.id, path)

        spaces[space.id] = space
        origin[space.id] = path

    return spaces


def root_capabilities(space: Space) -> Capabilities:
    """Every `CapabilityName` at the manifest's ceiling, scoped to the space
    id (seams §1.2). The supervisor's smaller set is issued from this by the
    tree; nothing else derives from the manifest."""
    return frozenset(
        Capability(name=name, effect_class=space.max_effect_class, scope=space.id)
        for name in get_args(CapabilityName)
    )


def check_may_open(space_id: SpaceId, spaces: dict[SpaceId, Space]) -> None:
    """Raises `SpaceRefused` for the reserved space or an unknown one (seams
    §3.4). One function for the two refusals, so the tree and the supervisor
    call it once and the reason text is the same on every surface. Pure: the
    caller holds the loaded manifests."""
    if space_id == UNASSIGNED_SPACE_ID:
        raise SpaceRefused(
            f"{UNASSIGNED_SPACE_ID!r} holds items no rule claimed; its headers may "
            "be read and nothing else, so no objective opens there"
        )
    if space_id not in spaces:
        raise SpaceRefused(f"no manifest declares the space {space_id!r}")


# ---------------------------------------------------------------------------
# Read tokens: the space filter as a database fact (architecture §1)

# Decided in the plan: a render that needs longer than ten minutes is a bug,
# and a zero or negative ttl mints a token that reads nothing.
MIN_TTL_S, MAX_TTL_S = 1, 600


async def mint_read_token(
    conn: psycopg.AsyncConnection, space: SpaceId, ttl_s: int = 60
) -> str:
    """One row in `read_tokens` on the kernel connection, returning the token.

    The token is `secrets.token_urlsafe(32)` and not an id (seams §3.4): a
    read token is a secret, and a UUIDv7 is time-ordered and so guessable
    from another one.
    """
    if not MIN_TTL_S <= ttl_s <= MAX_TTL_S:
        raise ValueError(
            f"ttl_s must be between {MIN_TTL_S} and {MAX_TTL_S} seconds; "
            f"a render asking for {ttl_s} is a bug"
        )
    token = secrets.token_urlsafe(32)
    await conn.execute(
        "INSERT INTO read_tokens (token, space_id, expires_at) "
        "VALUES (%s, %s, now() + make_interval(secs => %s))",
        (token, space, ttl_s),
    )
    return token


async def bind_read_token(conn_ro: psycopg.AsyncConnection, token: str) -> None:
    """Bind the token for the length of the caller's transaction.

    `set_config(..., true)` is transaction-local, and the render runs inside
    one transaction (seams §3.4), so the scope dies with it and a connection
    handed to the next render carries no space.
    """
    await conn_ro.execute("SELECT set_config('cori.read_token', %s, true)", (token,))


# ---------------------------------------------------------------------------
# Routing: the explicit act, made once in the manifest (architecture §9)

# The order a rule is rendered in, so one rule has one text on every surface.
RULE_FIELDS = ("sender_domain", "label", "folder", "calendar")


def _sender_domain(item: InboundItem) -> str | None:
    """The domain of the address in the `from` header, lowercased.

    `email.utils.parseaddr` strips the display name, so `Ana Ruiz
    <ana@psyoptimal.com>` and `ana@psyoptimal.com` reach the same rule.
    """
    _, address = email.utils.parseaddr(item.headers.get("from", ""))
    local, at, domain = address.rpartition("@")
    # An empty local part is not an address. Without this a `from` header of
    # "@psyoptimal.com" would route a stranger's mail into the client space.
    if not at or not local.strip() or not domain.strip():
        return None
    return domain.strip().lower()


def _rule_text(space_id: SpaceId, connector: Connector) -> str:
    """`<space_id>:<kind>:<account>:<field>=<value>[,...]`, the rule as the
    person reads it on a card and as the event records it."""
    rule = connector.route
    fields = ",".join(
        f"{name}={getattr(rule, name)}"
        for name in RULE_FIELDS
        if getattr(rule, name) is not None
    )
    return f"{space_id}:{connector.kind}:{connector.account}:{fields}"


def _rule_matches(rule: RoutingRule, item: InboundItem) -> bool:
    """A rule matches when every field it sets matches.

    `sender_domain` compares case-insensitively and exactly: a subdomain is
    its own rule, because `mail.psyoptimal.com` is a different sender from
    `psyoptimal.com` and guessing between them is the leak the partition
    exists to prevent. `label`, `folder`, and `calendar` compare against
    headers the M0 reader does not populate, so a rule setting one of them
    matches nothing until a reader does.
    """
    if rule.sender_domain is not None:
        if _sender_domain(item) != rule.sender_domain.strip().lower():
            return False
    for name in ("label", "folder", "calendar"):
        value = getattr(rule, name)
        if value is not None and item.headers.get(name) != value:
            return False
    return True


def matches(
    item: InboundItem, spaces: dict[SpaceId, Space]
) -> list[tuple[SpaceId, str]]:
    """Every `(space_id, rule_text)` that claims the item, in space id order.

    A connector claims an item only when its kind and account are the item's,
    so a rule never reaches across accounts. The order is the space id's, so
    the candidate list on an `inbound.unassigned` event is the same on every
    run (seams §3.4, deterministic).
    """
    claims = []
    for space_id in sorted(spaces):
        for connector in spaces[space_id].connectors:
            if connector.kind != item.connector or connector.account != item.account:
                continue
            if _rule_matches(connector.route, item):
                claims.append((space_id, _rule_text(space_id, connector)))
    return claims


def route(
    item: InboundItem, spaces: dict[SpaceId, Space]
) -> tuple[SpaceId, str | None]:
    """Total and deterministic: the one space that claimed the item, or the
    reserved space on zero or many claims (seams §3.4).

    An item two spaces both claim is never guessed at, because a wrong guess
    between two clients is the leak the partition exists to prevent; `ingest`
    puts the candidates on the event so the person can see why.
    """
    claims = matches(item, spaces)
    if len(claims) == 1:
        return claims[0]
    return (UNASSIGNED_SPACE_ID, None)


# ---------------------------------------------------------------------------
# Recording: one row per item per space, and the event that says why


async def record_inbound(
    conn: psycopg.AsyncConnection,
    item: InboundItem,
    *,
    candidates: Sequence[SpaceId] = (),
) -> bool:
    """One row in `inbound_items`, and the event that explains it.

    Returns `False` when the row already existed. The key
    `(connector, account, external_id, space_id)` makes a re-read harmless,
    which is what lets `poll_connectors` overlap its window (spike 02's
    lesson about reconciling by key, applied to reads), and it lets an item
    assigned later to a second space get a row there, which row-level
    security needs for that space's render to see it.

    `inbound.routed` carries the item and the rule; `inbound.unassigned`
    carries the item and the candidate space ids (seams §4). Decided in
    build: `candidates` is a keyword argument with an empty default rather
    than something this function recomputes, because the seams §3.4
    signature takes the item alone and `ingest` already holds what `matches`
    returned. An unassigned row stays header-only forever, which is what
    architecture §9 asks.
    """
    cur = await conn.execute(
        "INSERT INTO inbound_items (item_id, space_id, connector, account, "
        "external_id, headers, received_at, routed_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (connector, account, external_id, space_id) DO NOTHING "
        "RETURNING id",
        (
            item.id,
            item.space,
            item.connector,
            item.account,
            item.external_id,
            Jsonb(item.headers),
            item.received_at,
            item.routed_by,
        ),
    )
    if await cur.fetchone() is None:
        return False

    payload = {"item": item.model_dump(mode="json")}
    if item.space == UNASSIGNED_SPACE_ID:
        await append(
            conn,
            space_id=item.space,
            type="inbound.unassigned",
            payload={**payload, "candidates": list(candidates)},
        )
    else:
        await append(
            conn,
            space_id=item.space,
            type="inbound.routed",
            payload={**payload, "rule": item.routed_by},
        )
    return True


async def ingest(
    conn: psycopg.AsyncConnection,
    spaces: dict[SpaceId, Space],
    items: Iterable[InboundItem],
) -> list[InboundItem]:
    """Route each item, stamp it with where it landed, and record it.

    Returns the items that were newly recorded, stamped. A caller uses these
    ids rather than the ones it read with: a second read of the same message
    mints a fresh `id`, and only the first is kept.
    """
    recorded = []
    for item in items:
        space_id, rule = route(item, spaces)
        candidates = [candidate for candidate, _ in matches(item, spaces)]
        routed = item.model_copy(update={"space": space_id, "routed_by": rule})
        if await record_inbound(conn, routed, candidates=candidates):
            recorded.append(routed)
    return recorded


# ---------------------------------------------------------------------------
# Polling: one pass over every connector of every loaded space


async def _since(
    conn: psycopg.AsyncConnection, connector: Connector, lookback_days: int
) -> datetime:
    """Where this connector's next read starts.

    `min(max(received_at), now()) - 1 hour` over the rows already recorded for
    this `(kind, account)`, or `now() - lookback_days` when there are none.
    The clamp to `now()` keeps one future-dated `Date` header from moving the
    window past the present and starving the account. The hour of overlap
    covers delivery delay, and `record_inbound`'s key makes it harmless.
    """
    row = await (
        await conn.execute(
            "SELECT max(received_at), now(), now() - make_interval(days => %s) "
            "FROM inbound_items WHERE connector = %s AND account = %s",
            (lookback_days, connector.kind, connector.account),
        )
    ).fetchone()
    last, now, fallback = row
    if last is None:
        return fallback
    return min(last, now) - timedelta(hours=1)


async def poll_connectors(
    conn: psycopg.AsyncConnection,
    spaces: dict[SpaceId, Space],
    read_recent,
    *,
    lookback_days: int = 7,
) -> list[InboundItem]:
    """Read every connector of every space once and record what came back.

    `read_recent(conn, connector, since)` is passed in rather than imported,
    so this module never imports `broker/`, which depends on it (seams §3.4,
    §3.10).

    The connection is the kernel's, opened with `autocommit=True`, and one
    inside a transaction is refused before any read: the broker writes an
    intent row and commits it before the network call, and a caller's
    enclosing transaction would demote that commit to a savepoint. The read
    manages its own transactions; each connector's `ingest` runs inside
    `conn.transaction()`, so a connector's rows and its events land together
    or not at all.

    At M0 nothing calls this on a timer. Integration's task 3 calls it once by
    hand.
    """
    if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
        raise RuntimeError(
            "poll_connectors needs an idle autocommit connection; the read "
            "commits its own ledger rows before the network call, which an "
            "open transaction would demote to a savepoint"
        )

    recorded: list[InboundItem] = []
    for space_id in sorted(spaces):
        for connector in spaces[space_id].connectors:
            since = await _since(conn, connector, lookback_days)
            items = await read_recent(conn, connector, since)
            async with conn.transaction():
                recorded.extend(await ingest(conn, spaces, items))
    return recorded
