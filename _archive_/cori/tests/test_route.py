"""Routing is the manifest's explicit act applied by the kernel: total,
deterministic, never a guess between two clients. Plan 03 task 7; seams
§3.4; architecture §9."""

import uuid
from datetime import UTC, datetime

from hypothesis import given, settings, strategies as st

from kernel.spaces import load_all, matches, route
from schemas.inbound import InboundItem
from schemas.space import UNASSIGNED_SPACE_ID, Connector, RoutingRule, Space

ACCOUNT = "tom@yuda.me"
SPACES = load_all()


def item(sender: str, *, account: str = ACCOUNT, connector="gmail", **headers):
    return InboundItem(
        id=uuid.uuid4().hex,
        connector=connector,
        account=account,
        external_id=uuid.uuid4().hex,
        headers={"from": sender, **headers},
        received_at=datetime(2026, 9, 21, tzinfo=UTC),
        space=UNASSIGNED_SPACE_ID,
        routed_by=None,
    )


def space(space_id: str, *connectors: Connector) -> Space:
    return Space(
        id=space_id,
        kind="client",
        roots=["/repo"],
        max_effect_class="propose",
        connectors=list(connectors),
    )


def gmail(account: str, **rule) -> Connector:
    return Connector(kind="gmail", account=account, route=RoutingRule(**rule))


# ---------------------------------------------------------------------------
# The live manifest


def test_psyoptimal_mail_routes_to_psyoptimal():
    space_id, rule = route(item("ana@psyoptimal.com"), SPACES)
    assert space_id == "psyoptimal"
    assert rule == "psyoptimal:gmail:tom@yuda.me:sender_domain=psyoptimal.com"


def test_other_domain_routes_to_unassigned():
    assert route(item("someone@acme.com"), SPACES) == (UNASSIGNED_SPACE_ID, None)
    assert matches(item("someone@acme.com"), SPACES) == []


def test_display_name_and_case_do_not_matter():
    for sender in (
        "Ana Ruiz <ana@psyoptimal.com>",
        "ANA@PSYOPTIMAL.COM",
        '"Ruiz, Ana" <Ana@PsyOptimal.com>',
    ):
        assert route(item(sender), SPACES)[0] == "psyoptimal"


def test_subdomain_does_not_match():
    """A subdomain is its own rule: `mail.psyoptimal.com` is a different
    sender, and guessing between the two is what the partition prevents."""
    for sender in ("ana@mail.psyoptimal.com", "ana@psyoptimal.com.acme.net"):
        assert route(item(sender), SPACES) == (UNASSIGNED_SPACE_ID, None)


def test_a_missing_or_malformed_from_header_is_unassigned():
    for sender in ("", "not an address", "@psyoptimal.com"):
        assert route(item(sender), SPACES) == (UNASSIGNED_SPACE_ID, None)


def test_a_rule_the_m0_reader_cannot_evaluate_matches_nothing():
    """A label rule matches only an item whose reader populated the header."""
    spaces = {"alpha": space("alpha", gmail(ACCOUNT, label="clients"))}
    assert route(item("ana@psyoptimal.com"), spaces) == (UNASSIGNED_SPACE_ID, None)
    assert route(item("ana@psyoptimal.com", label="clients"), spaces)[0] == "alpha"


# ---------------------------------------------------------------------------
# Properties

IDS = st.from_regex(r"\A[a-z0-9][a-z0-9-]{0,10}\Z").filter(
    lambda s: s != UNASSIGNED_SPACE_ID
)
DOMAINS = st.sampled_from(["psyoptimal.com", "acme.com", "example.org", "yuda.me"])
ACCOUNTS = st.sampled_from(["tom@yuda.me", "ops@yuda.me", "ana@yuda.me"])
SENDERS = st.sampled_from(
    [
        "ana@psyoptimal.com",
        "Bo <bo@acme.com>",
        "c@example.org",
        "d@mail.yuda.me",
        "",
    ]
)


@st.composite
def manifests(draw):
    """One to four spaces, each with zero to three gmail connectors on
    distinct accounts, every rule setting a sender_domain so the set is one
    `load_all` would accept."""
    ids = draw(st.lists(IDS, min_size=1, max_size=4, unique=True))
    spaces = {}
    for space_id in ids:
        accounts = draw(st.lists(ACCOUNTS, max_size=3, unique=True))
        connectors = [
            gmail(account, sender_domain=draw(DOMAINS)) for account in accounts
        ]
        spaces[space_id] = space(space_id, *connectors)
    return spaces


@st.composite
def manifest_and_item(draw):
    """A manifest set and an item aimed at it often enough that the
    properties below are not vacuous: half the items borrow an account and a
    sender domain the manifest declares."""
    spaces = draw(manifests())
    connectors = [c for s in spaces.values() for c in s.connectors]
    if connectors and draw(st.booleans()):
        connector = draw(st.sampled_from(connectors))
        account = connector.account
        sender = f"ana@{connector.route.sender_domain}"
    else:
        account, sender = draw(ACCOUNTS), draw(SENDERS)
    kind = draw(st.sampled_from(["gmail", "gmail", "calendar", "messaging"]))
    return spaces, item(sender, account=account, connector=kind)


@settings(max_examples=25, deadline=None)
@given(pair=manifest_and_item())
def test_route_is_total_and_deterministic(pair):
    spaces, built = pair
    space_id, rule = route(built, spaces)
    assert space_id in set(spaces) | {UNASSIGNED_SPACE_ID}
    assert route(built, spaces) == (space_id, rule)
    assert (rule is None) == (space_id == UNASSIGNED_SPACE_ID)


@settings(max_examples=25, deadline=None)
@given(pair=manifest_and_item())
def test_route_never_claims_across_accounts(pair):
    spaces, built = pair
    # An implication rather than a filter: an item no rule claims is a fact
    # about routing too, and filtering them out would discard most examples.
    for space_id, _ in matches(built, spaces):
        assert any(
            c.kind == built.connector and c.account == built.account
            for c in spaces[space_id].connectors
        )


@settings(max_examples=25, deadline=None)
@given(
    ids=st.lists(IDS, min_size=2, max_size=4, unique=True),
    domain=DOMAINS,
    account=ACCOUNTS,
)
def test_overlapping_rules_never_guess(ids, domain, account):
    """Every space claims the item, each by a rule of its own, so the set is
    one `load_all` would accept. Route still refuses to pick, and `matches`
    lists every candidate."""
    extra = {"label": "clients", "folder": "2026", "calendar": "work"}
    narrowers = [{}] + [{name: value} for name, value in extra.items()]
    spaces = {
        space_id: space(space_id, gmail(account, sender_domain=domain, **narrower))
        for space_id, narrower in zip(ids, narrowers)
    }
    built = item(f"ana@{domain}", account=account, **extra)

    assert route(built, spaces) == (UNASSIGNED_SPACE_ID, None)
    assert [s for s, _ in matches(built, spaces)] == sorted(ids)
    # The set the test built is one the manifest loader accepts.
    assert len(
        {c.route.model_dump_json() for s in spaces.values() for c in s.connectors}
    ) == len(ids)
