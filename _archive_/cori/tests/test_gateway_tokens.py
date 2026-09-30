"""Minting, revoking, and the two caches. Plan 04 task 3; seams §3.2, §3.9.

Both memories fail closed: nothing is cached at issue, so a token whose
issuing transaction rolled back authenticates nothing; and the revoked set
is warmed from the log at start, so a restart forgets no revocation.
"""

import hashlib
import inspect
import uuid

import psycopg
import pytest
from pydantic import SecretStr

from gateway.core import Gateway, TokenRefused
from kernel.tree import TokenIssuer
from schemas.budget import Budget
from tests.conftest import dsn, requires_postgres

pytestmark = requires_postgres


def connect():
    return psycopg.AsyncConnection.connect(dsn("kernel_rw"))


@pytest.fixture
def gateway() -> Gateway:
    return Gateway(provider_key="not-a-key", connect=connect)


def sha(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


async def rows(conn, sql, params=()):
    return await (await conn.execute(sql, params)).fetchall()


async def test_a_rolled_back_mint_leaves_no_row(gateway, kernel, space):
    brief = uuid.uuid4().hex
    token = await gateway.issue_token(
        kernel, brief_id=brief, generation=1, model_ref="claude-opus-5", space=space
    )
    await kernel.rollback()

    assert (
        await rows(
            kernel,
            "SELECT 1 FROM gateway_tokens WHERE token_sha256 = %s",
            (sha(token.get_secret_value()),),
        )
        == []
    )
    assert (
        await rows(kernel, "SELECT 1 FROM gateway_log WHERE brief_id = %s", (brief,))
        == []
    )


async def test_a_rolled_back_token_is_unknown_on_presentation(gateway, kernel, space):
    """Nothing is cached at issue (critique 1), so the plaintext of a mint
    that never committed authenticates nothing in this same process."""
    token = await gateway.issue_token(
        kernel,
        brief_id=uuid.uuid4().hex,
        generation=1,
        model_ref="claude-opus-5",
        space=space,
    )
    await kernel.rollback()
    assert await gateway.token(kernel, token.get_secret_value()) is None


async def test_a_committed_mint_writes_both_rows(gateway, kernel, space):
    brief = uuid.uuid4().hex
    token = await gateway.issue_token(
        kernel, brief_id=brief, generation=3, model_ref="claude-opus-5", space=space
    )
    await kernel.commit()

    assert token.get_secret_value().startswith("cori-")
    assert await rows(
        kernel,
        "SELECT brief_id, generation, model_ref, space_id, cap_usd_micros "
        "FROM gateway_tokens WHERE token_sha256 = %s",
        (sha(token.get_secret_value()),),
    ) == [(brief, 3, "claude-opus-5", space, None)]
    assert await rows(
        kernel,
        "SELECT event, generation, space_id FROM gateway_log WHERE brief_id = %s",
        (brief,),
    ) == [("token_issued", 3, space)]

    row = await gateway.token(kernel, token.get_secret_value())
    assert row is not None and row.brief_id == brief and not row.is_turn


async def test_a_turn_token_carries_its_cap_and_no_fence(gateway, kernel, space):
    """Generation 0 is a turn id: no objective, no ledger node, a cap
    instead (seams v3 §3.9)."""
    turn = uuid.uuid4().hex
    token = await gateway.issue_token(
        kernel,
        brief_id=turn,
        generation=0,
        model_ref="claude-opus-5",
        space=space,
        cap=Budget(usd_micros=50_000),
    )
    await kernel.commit()

    assert await rows(
        kernel,
        "SELECT generation, cap_usd_micros FROM gateway_tokens WHERE token_sha256 = %s",
        (sha(token.get_secret_value()),),
    ) == [(0, 50_000)]
    row = await gateway.token(kernel, token.get_secret_value())
    assert row.is_turn and row.cap_usd_micros == 50_000


async def test_a_turn_id_without_a_cap_is_refused_at_mint(gateway, kernel, space):
    with pytest.raises(TokenRefused):
        await gateway.issue_token(
            kernel,
            brief_id=uuid.uuid4().hex,
            generation=0,
            model_ref="claude-opus-5",
            space=space,
        )
    await kernel.rollback()


async def test_a_brief_with_a_cap_is_refused_at_mint(gateway, kernel, space):
    """A Brief's budget is the tree's; a cap on it would be a second
    ledger."""
    with pytest.raises(TokenRefused):
        await gateway.issue_token(
            kernel,
            brief_id=uuid.uuid4().hex,
            generation=1,
            model_ref="claude-opus-5",
            space=space,
            cap=Budget(usd_micros=1),
        )
    await kernel.rollback()


async def test_revoke_fills_its_row_from_the_newest_token(gateway, kernel, space):
    brief = uuid.uuid4().hex
    await gateway.issue_token(
        kernel, brief_id=brief, generation=1, model_ref="claude-opus-5", space=space
    )
    await gateway.issue_token(
        kernel, brief_id=brief, generation=2, model_ref="claude-opus-5", space=space
    )
    await kernel.commit()

    await gateway.revoke(kernel, brief)
    await kernel.commit()

    assert await rows(
        kernel,
        "SELECT generation, space_id FROM gateway_log "
        "WHERE brief_id = %s AND event = 'token_revoked'",
        (brief,),
    ) == [(2, space)]
    assert brief in gateway.revoked


async def test_revoke_of_an_id_with_no_token_writes_nothing(gateway, kernel):
    absent = uuid.uuid4().hex
    await gateway.revoke(kernel, absent)
    await kernel.commit()
    assert (
        await rows(kernel, "SELECT 1 FROM gateway_log WHERE brief_id = %s", (absent,))
        == []
    )
    assert absent not in gateway.revoked


async def test_a_second_gateway_warms_the_revoked_set_from_the_log(
    gateway, kernel, space
):
    brief = uuid.uuid4().hex
    await gateway.issue_token(
        kernel, brief_id=brief, generation=1, model_ref="claude-opus-5", space=space
    )
    await gateway.revoke(kernel, brief)
    await kernel.commit()

    restarted = Gateway(provider_key="not-a-key", connect=connect)
    assert brief not in restarted.revoked
    await restarted.start()
    assert brief in restarted.revoked


# ---------------------------------------------------------------------------
# The gateway is the tree's TokenIssuer (seams §3.2; critique 12)


def test_the_two_methods_match_the_token_issuer_protocol():
    """`cap` is the one difference, and it is seams §3.9's own addition:
    keyword-only with a default, so a `TokenIssuer` caller never passes it."""
    for name in ("issue_token", "revoke"):
        ours = inspect.signature(getattr(Gateway, name))
        theirs = inspect.signature(getattr(TokenIssuer, name))
        trimmed = ours.replace(
            parameters=[p for p in ours.parameters.values() if p.name != "cap"]
        )
        assert trimmed == theirs, name

    cap = inspect.signature(Gateway.issue_token).parameters["cap"]
    assert cap.kind is inspect.Parameter.KEYWORD_ONLY and cap.default is None


async def test_a_fake_delegate_gets_a_plaintext_the_table_backs(gateway, kernel, space):
    """What `tree.delegate` does with a `TokenIssuer`: mint on the caller's
    connection, inside the caller's transaction."""

    async def delegate(conn, *, token_issuer: TokenIssuer, brief_id: str) -> SecretStr:
        return await token_issuer.issue_token(
            conn,
            brief_id=brief_id,
            generation=1,
            model_ref="claude-opus-5",
            space=space,
        )

    brief = uuid.uuid4().hex
    token = await delegate(kernel, token_issuer=gateway, brief_id=brief)
    await kernel.commit()
    assert await rows(
        kernel,
        "SELECT brief_id FROM gateway_tokens WHERE token_sha256 = %s",
        (sha(token.get_secret_value()),),
    ) == [(brief,)]
