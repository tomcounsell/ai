"""The Gmail connector: the query never widens past the routing rule, an
unassigned item stays header-only, a worker may name one message and may not
search, and every read writes its own ledger pair. Plan 07 task 6;
architecture §9.
"""

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from psycopg.types.json import Jsonb

from broker import gmail
from broker.errors import EffectRefused
from broker.manifest import space_for
from infra.secrets import MissingSecret, read_secret
from schemas.effect import ConnectorRead
from schemas.ids import new_id
from schemas.inbound import InboundItem
from schemas.space import UNASSIGNED_SPACE_ID, Connector, RoutingRule
from tests.conftest import requires_postgres

ACCOUNT = "tom@yuda.me"
DOMAIN = "psyoptimal.com"
SINCE = datetime(2026, 9, 1, tzinfo=timezone.utc)


@pytest.fixture
def no_http(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("an HTTP client was constructed")

    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    monkeypatch.setattr(httpx, "post", forbidden)


def item(**over) -> InboundItem:
    fields = {
        "id": new_id(),
        "connector": "gmail",
        "account": ACCOUNT,
        "external_id": uuid.uuid4().hex[:16],
        "headers": {"from": f"Ana <ana@{DOMAIN}>", "subject": "hello"},
        "received_at": datetime.now(timezone.utc),
        "space": UNASSIGNED_SPACE_ID,
        "routed_by": None,
    }
    fields.update(over)
    return InboundItem(**fields)


# ---------------------------------------------------------------------------
# compose_query


def test_the_query_always_carries_the_sender_domain():
    query = gmail.compose_query(RoutingRule(sender_domain=DOMAIN), SINCE)
    assert f"from:@{DOMAIN}" in query
    assert f"after:{int(SINCE.timestamp())}" in query


def test_a_rule_with_no_sender_domain_has_no_query():
    for rule in (
        RoutingRule(),
        RoutingRule(label="clients"),
        RoutingRule(folder="inbox", calendar="work"),
    ):
        with pytest.raises(EffectRefused):
            gmail.compose_query(rule, SINCE)


def test_the_poll_key_carries_the_time_so_two_polls_are_two_effects():
    query = gmail.compose_query(RoutingRule(sender_domain=DOMAIN), SINCE)
    first = gmail._key(ACCOUNT, query)
    assert first.startswith(f"gmail:{ACCOUNT}:{query}:")
    assert (
        ConnectorRead(
            space="psyoptimal",
            objective_id=None,
            brief_id=None,
            connector="gmail",
            account=ACCOUNT,
            query=query,
            idempotency_key=first,
        ).idempotency_key
        == first
    )


# ---------------------------------------------------------------------------
# The worker's query


@pytest.mark.parametrize(
    "query",
    [
        f"from:@{DOMAIN}",
        "in:inbox",
        "id:",
        "id:NOTHEX",
        "id:abc def",
        "id:abc OR from:@other.com",
        "",
    ],
)
def test_a_worker_query_that_is_not_one_message_id_is_refused(query):
    action = ConnectorRead(
        space="psyoptimal",
        objective_id=uuid.uuid4().hex,
        brief_id=uuid.uuid4().hex,
        connector="gmail",
        account=ACCOUNT,
        query=query,
    )
    with pytest.raises(EffectRefused):
        gmail.check(action)


def test_one_message_id_is_accepted():
    gmail.check(
        ConnectorRead(
            space="psyoptimal",
            objective_id=uuid.uuid4().hex,
            brief_id=uuid.uuid4().hex,
            connector="gmail",
            account=ACCOUNT,
            query="id:18f0c0ffee",
        )
    )


async def test_the_query_answers_absent_by_rule():
    """A read leaves nothing on the target, so reconcile re-runs it rather
    than calling it recovered without its data."""
    assert await gmail.query(None, None, None) == "absent"


# ---------------------------------------------------------------------------
# fetch_body refuses before any HTTP


async def test_an_unassigned_item_has_no_body(no_http):
    with pytest.raises(EffectRefused) as info:
        await gmail.fetch_body(None, item())
    assert "header-only" in str(info.value)


async def test_a_space_with_no_connector_on_the_account_is_refused(no_http):
    with pytest.raises(EffectRefused) as info:
        await gmail.fetch_body(
            None, item(space="psyoptimal", account="someone@example.com")
        )
    assert "connector" in str(info.value)


def test_a_message_whose_sender_fails_the_rule_is_refused():
    connector = Connector(
        kind="gmail", account=ACCOUNT, route=RoutingRule(sender_domain=DOMAIN)
    )
    gmail._refuse_off_rule(connector, item(space="psyoptimal"))
    for headers in (
        {"from": "someone@other.com"},
        {"from": f"@{DOMAIN}"},
        {"from": f"ana@mail.{DOMAIN}"},
        {},
    ):
        with pytest.raises(EffectRefused):
            gmail._refuse_off_rule(connector, item(space="psyoptimal", headers=headers))


# ---------------------------------------------------------------------------
# run against a seeded row


async def seed(conn, space_id: str, external_id: str) -> None:
    await conn.execute(
        "INSERT INTO inbound_items (item_id, space_id, connector, account, "
        "external_id, headers, received_at, routed_by) "
        "VALUES (%s, %s, 'gmail', %s, %s, %s, now(), 'seeded')",
        (
            new_id(),
            space_id,
            ACCOUNT,
            external_id,
            Jsonb({"from": f"ana@{DOMAIN}", "subject": "hello"}),
        ),
    )
    await conn.commit()


def read_action(space_id: str, external_id: str) -> ConnectorRead:
    return ConnectorRead(
        space=space_id,
        objective_id=uuid.uuid4().hex,
        brief_id=uuid.uuid4().hex,
        connector="gmail",
        account=ACCOUNT,
        query=f"id:{external_id}",
    )


@requires_postgres
async def test_run_refuses_an_item_in_another_space_before_any_http(
    kernel, space, no_http
):
    external_id = uuid.uuid4().hex[:16]
    await seed(kernel, space + "-other", external_id)
    with pytest.raises(EffectRefused) as info:
        await gmail.run(kernel, read_action(space, external_id), None)
    assert "item_not_in_space" in str(info.value)


@requires_postgres
async def test_run_reads_the_row_in_its_own_space_and_calls_fetch_body(
    kernel, space, monkeypatch
):
    external_id = uuid.uuid4().hex[:16]
    await seed(kernel, space, external_id)
    seen = {}

    async def stub(conn, item):
        seen["external_id"] = item.external_id
        seen["space"] = item.space
        return "the body"

    monkeypatch.setattr(gmail, "fetch_body", stub)
    result = await gmail.run(kernel, read_action(space, external_id), None)
    assert result["body"] == "the body"
    assert result["headers"]["from"] == f"ana@{DOMAIN}"
    assert seen == {"external_id": external_id, "space": space}


# ---------------------------------------------------------------------------
# The ledger pair


@requires_postgres
async def test_two_reads_with_one_since_write_two_intents_with_distinct_keys(
    kernel, monkeypatch
):
    """The poll time is in the key, so an unchanged `since` still advances."""
    connector = Connector(
        kind="gmail",
        account=ACCOUNT,
        route=RoutingRule(sender_domain=f"{uuid.uuid4().hex[:8]}.example.com"),
    )
    monkeypatch.setattr(gmail, "credential_for", lambda *a: None)
    monkeypatch.setattr(gmail, "space_for_connector", lambda c: space_for("psyoptimal"))
    stamps = iter([1758412800, 1758412801])
    monkeypatch.setattr(
        gmail, "_now", lambda: datetime.fromtimestamp(next(stamps), tz=timezone.utc)
    )

    async def stub(credential, account, query):
        return [item()]

    monkeypatch.setattr(gmail, "_read_headers", stub)
    await kernel.set_autocommit(True)
    first = await gmail.read_recent(kernel, connector, SINCE)
    second = await gmail.read_recent(kernel, connector, SINCE)
    assert len(first) == 1 and len(second) == 1
    assert first[0].space == UNASSIGNED_SPACE_ID and first[0].routed_by is None

    query = gmail.compose_query(connector.route, SINCE)
    keys = await (
        await kernel.execute(
            "SELECT DISTINCT idempotency_key FROM effect_ledger "
            "WHERE action_type = 'connector_read' AND idempotency_key LIKE %s",
            (f"gmail:{ACCOUNT}:{query}:%",),
        )
    ).fetchall()
    assert sorted(k[0] for k in keys) == [
        f"gmail:{ACCOUNT}:{query}:1758412800",
        f"gmail:{ACCOUNT}:{query}:1758412801",
    ]
    rows = await (
        await kernel.execute(
            "SELECT event, objective_id, brief_id, generation FROM effect_ledger "
            "WHERE idempotency_key = %s ORDER BY id",
            (f"gmail:{ACCOUNT}:{query}:1758412800",),
        )
    ).fetchall()
    assert [r[0] for r in rows] == ["intent", "outcome"]
    assert all(r[1:] == (None, None, None) for r in rows)


# ---------------------------------------------------------------------------
# Live


@requires_postgres
async def test_live_read_of_ten_headers_and_one_body(kernel):
    try:
        read_secret("gmail_refresh_token")
    except MissingSecret:
        pytest.skip("gmail_refresh_token is not in the Keychain")
    connector = Connector(
        kind="gmail", account=ACCOUNT, route=RoutingRule(sender_domain=DOMAIN)
    )
    await kernel.set_autocommit(True)
    started = datetime.now(timezone.utc)
    since = datetime.now(timezone.utc) - timedelta(days=3650)
    items = await gmail.read_recent(kernel, connector, since)
    assert items, "the psyoptimal.com rule matched nothing at all"
    assert len(items) >= 10
    for got in items[:10]:
        assert gmail._sender_domain(got) == DOMAIN
        assert got.space == UNASSIGNED_SPACE_ID and got.routed_by is None
        assert got.external_id

    routed = items[0].model_copy(update={"space": "psyoptimal", "routed_by": "live"})
    body = await gmail.fetch_body(kernel, routed)
    assert isinstance(body, str)

    rows = await (
        await kernel.execute(
            "SELECT count(*) FROM effect_ledger WHERE action_type = 'connector_read' "
            "AND idempotency_key LIKE %s AND at >= %s",
            (f"gmail:{ACCOUNT}:id:{routed.external_id}:%", started),
        )
    ).fetchone()
    assert rows[0] == 2
