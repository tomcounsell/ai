"""One poll pass: an idle connection, a read per connector, and the rows and
events of each connector's ingest landing together. Plan 03 task 9; seams
§3.4, §3.10.

The reader is passed in, so these tests hand `poll_connectors` a stub with
the arity of the broker's `read_recent` and one live read through
`broker.gmail`.
"""

import email.utils
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from broker.gmail import recent
from infra.secrets import MissingSecret, read_secret
from kernel.spaces import ingest, load_all, poll_connectors, route
from schemas.inbound import InboundItem
from schemas.space import UNASSIGNED_SPACE_ID, Connector, RoutingRule, Space
from tests.conftest import dsn, requires_postgres

pytestmark = requires_postgres


@pytest.fixture
async def poller():
    """The kernel connection a poll runs on: autocommit and idle, as the
    integration caller opens it."""
    async with await psycopg.AsyncConnection.connect(
        dsn("kernel_rw"), autocommit=True
    ) as conn:
        yield conn


def one_space(space_id: str, account: str, domain: str) -> Space:
    return Space(
        id=space_id,
        kind="client",
        roots=["/repo"],
        max_effect_class="propose",
        connectors=[
            Connector(
                kind="gmail",
                account=account,
                route=RoutingRule(sender_domain=domain),
            )
        ],
    )


def item(sender: str, *, account: str, external_id: str, when=None) -> InboundItem:
    return InboundItem(
        id=uuid.uuid4().hex,
        connector="gmail",
        account=account,
        external_id=external_id,
        headers={"from": sender},
        received_at=when or datetime(2026, 9, 21, tzinfo=UTC),
        space=UNASSIGNED_SPACE_ID,
        routed_by=None,
    )


async def count(conn, sql: str, params) -> int:
    row = await (await conn.execute(sql, params)).fetchone()
    return row[0]


async def test_poll_reads_every_connector_and_ingests(poller):
    """The stub has the broker's arity, asserts the connection is idle when
    it is called, and writes and commits a row of its own mid-poll, as the
    broker's ledger does. The poll's rows and events still land together."""
    accounts = [f"tom+{uuid.uuid4().hex[:8]}@yuda.me" for _ in range(2)]
    ids = sorted(f"space-{uuid.uuid4().hex[:8]}" for _ in range(2))
    spaces = {
        ids[0]: one_space(ids[0], accounts[0], "psyoptimal.com"),
        ids[1]: one_space(ids[1], accounts[1], "acme.com"),
    }
    seen = []
    space_of = dict(zip(accounts, ids))

    async def read_recent(conn, connector: Connector, since: datetime):
        assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
        seen.append((connector.account, since))
        await conn.execute(
            "INSERT INTO events (space_id, type, payload) "
            "VALUES (%s, 'message.sent', %s::jsonb)",
            (space_of[connector.account], f'{{"ledger": "{connector.account}"}}'),
        )
        await conn.commit()
        domain = connector.route.sender_domain
        return [
            item(f"ana@{domain}", account=connector.account, external_id="p-1"),
            item(f"bo@{domain}", account=connector.account, external_id="p-2"),
        ]

    recorded = await poll_connectors(poller, spaces, read_recent)

    assert [account for account, _ in seen] == accounts
    assert {i.space for i in recorded} == set(ids)
    assert len(recorded) == 4

    for account in accounts:
        rows = await count(
            poller, "SELECT count(*) FROM inbound_items WHERE account = %s", (account,)
        )
        events = await count(
            poller,
            "SELECT count(*) FROM events WHERE type LIKE 'inbound.%%' "
            "AND payload->'item'->>'account' = %s",
            (account,),
        )
        assert (rows, events) == (2, 2)


async def test_poll_refuses_a_connection_inside_a_transaction(kernel, space):
    """The broker commits its intent row before the network call, which an
    enclosing transaction would demote to a savepoint."""
    called = []

    async def read_recent(conn, connector, since):
        called.append(connector)
        return []

    await kernel.execute("SELECT 1")
    assert kernel.info.transaction_status != psycopg.pq.TransactionStatus.IDLE

    spaces = {space: one_space(space, "tom@yuda.me", "psyoptimal.com")}
    with pytest.raises(RuntimeError, match="idle"):
        await poll_connectors(kernel, spaces, read_recent)
    assert called == []
    await kernel.rollback()


async def test_since_is_the_last_received_at(poller):
    """Three cases: nothing recorded, a past item, and a future-dated one."""
    account = f"tom+{uuid.uuid4().hex[:8]}@yuda.me"
    space_id = f"space-{uuid.uuid4().hex[:8]}"
    spaces = {space_id: one_space(space_id, account, "psyoptimal.com")}
    seen = []

    async def read_recent(conn, connector, since):
        seen.append(since)
        return []

    now = datetime.now(UTC)
    await poll_connectors(poller, spaces, read_recent)
    assert abs(seen[-1] - (now - timedelta(days=7))) < timedelta(minutes=1)
    assert abs(seen[-1] - (now - timedelta(days=3))) > timedelta(minutes=1)

    past = now - timedelta(days=2)
    async with poller.transaction():
        await ingest(
            poller,
            spaces,
            [item("ana@psyoptimal.com", account=account, external_id="s-1", when=past)],
        )
    await poll_connectors(poller, spaces, read_recent)
    assert abs(seen[-1] - (past - timedelta(hours=1))) < timedelta(seconds=1)

    ahead = now + timedelta(days=1)
    async with poller.transaction():
        await ingest(
            poller,
            spaces,
            [
                item(
                    "ana@psyoptimal.com",
                    account=account,
                    external_id="s-2",
                    when=ahead,
                )
            ],
        )
    await poll_connectors(poller, spaces, read_recent)
    assert seen[-1] <= datetime.now(UTC) - timedelta(hours=1)
    assert seen[-1] > now - timedelta(hours=1, minutes=1)


# ---------------------------------------------------------------------------
# The live read


def live_items(rows: list[dict]) -> list[InboundItem]:
    """The Gmail headers as inbound items. `recent` returns `id`, `From`,
    `Subject`, and `Date`, so the keys are lowercased here and `Date` is
    parsed; the Gmail id is the external id, which is what makes a second
    read of the same message a no-op."""
    items = []
    for row in rows:
        headers = {k.lower(): v for k, v in row.items() if k != "id"}
        items.append(
            InboundItem(
                id=uuid.uuid4().hex,
                connector="gmail",
                account="tom@yuda.me",
                external_id=row["id"],
                headers=headers,
                received_at=email.utils.parsedate_to_datetime(headers["date"]),
                space=UNASSIGNED_SPACE_ID,
                routed_by=None,
            )
        )
    return items


async def test_live_psyoptimal_mail_routes_to_psyoptimal(kernel):
    """Real mail, the real manifest: every message the rule's own query
    returns routes to `psyoptimal`, and a second ingest writes nothing."""
    try:
        read_secret("gmail_refresh_token")
    except MissingSecret:
        pytest.skip("gmail_refresh_token is not in the Keychain")

    spaces = load_all()
    items = live_items(recent("psyoptimal.com", 10))
    assert items

    for built in items:
        space_id, rule = route(built, spaces)
        assert space_id == "psyoptimal"
        assert rule == "psyoptimal:gmail:tom@yuda.me:sender_domain=psyoptimal.com"

    await ingest(kernel, spaces, items)
    await kernel.commit()

    external_ids = [i.external_id for i in items]
    rows = await (
        await kernel.execute(
            "SELECT DISTINCT space_id FROM inbound_items WHERE external_id = ANY(%s)",
            (external_ids,),
        )
    ).fetchall()
    assert rows == [("psyoptimal",)]

    assert await ingest(kernel, spaces, live_items(recent("psyoptimal.com", 10))) == []
    await kernel.commit()
