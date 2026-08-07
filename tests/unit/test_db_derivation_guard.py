"""Acceptance for the Redis ``db=`` derivation recurrence guard (#2655).

Three things have to be true for this guard to be worth having, and a green run
proves only the first:

1. The tree is clean — every site under ``tests/`` either uses the claim API or
   carries a written disposition.
2. The guard is **non-vacuous** — it goes RED on deliberately-planted offending
   sources, one per shape it claims to catch.
3. The guard is **measuring something** — the candidate count it sees is
   asserted non-zero and the ``ast.Attribute`` branch is asserted load-bearing.
   A guard whose matcher reads only ``node.func.id`` matches ZERO of this tree's
   Redis constructions and reports clean forever; that is the exact failure this
   issue exists to prevent, so it is asserted against directly.

Nothing here claims a pool database or touches db 0. Planted offenders are
written to ``tmp_path`` and PARSED, never executed.
"""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import pytest

from tests.db_derivation_guard import (
    TEST_DB_POOL_MAX,
    TESTS_ROOT,
    Exemption,
    _terminal_name,
    apply_dispositions,
    check_dispositions,
    format_violation,
    scan_source,
    scan_tree,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# 1. The tree is clean
# ---------------------------------------------------------------------------


def test_no_test_derives_its_own_redis_db():
    """The headline assertion: no undispositioned self-derived ``db=`` anywhere."""
    result = scan_tree()
    remaining, _ = apply_dispositions(result)
    assert not remaining, "Self-derived Redis db= found:\n\n" + "\n\n".join(
        format_violation(c) for c in remaining
    )


def test_no_stale_disposition_entries():
    """An exemption that matches nothing must be deleted, not left to cover the next site."""
    _, stale = apply_dispositions(scan_tree())
    assert not stale, (
        "Stale disposition entries (site fixed or moved) — delete them:\n"
        + "\n".join(f"  {e.path} :: {e.expr!r}" for e in stale)
    )


def test_disposition_tables_satisfy_their_own_rules():
    """Invariant + metadata checks on the shipped tables, including deferral expiry."""
    assert check_dispositions() == []


# ---------------------------------------------------------------------------
# 2. The guard is measuring something (anti-vacuity)
# ---------------------------------------------------------------------------


def test_guard_sees_a_non_zero_number_of_candidates(capsys):
    """A guard with nothing to check is indistinguishable from a passing one."""
    result = scan_tree()
    db_kwargs = [c for c in result.candidates if c.kind == "db-kwarg"]
    from_urls = [c for c in result.candidates if c.kind == "from-url"]
    with capsys.disabled():
        print(
            f"\n[db-derivation-guard] candidates: {len(result.candidates)} "
            f"({len(db_kwargs)} db= keyword, {len(from_urls)} from_url)"
        )
    assert len(db_kwargs) >= 15, "the db= walk matched almost nothing — suspect the walker"
    assert len(from_urls) >= 8, "the from_url walk matched almost nothing — suspect the walker"


def test_every_redis_construction_in_the_tree_is_attribute_qualified(capsys):
    """The ``ast.Attribute`` branch carries 100% of this tree; ``ast.Name`` carries 0%.

    This is why :func:`_terminal_name` must read ``.attr`` as well as ``.id``. If
    this ever flips, the mutation evidence in the PR body needs redoing.
    """
    by_kind: dict[str, int] = {}
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            if isinstance(node, ast.Call) and _terminal_name(node.func) == "Redis":
                by_kind[type(node.func).__name__] = by_kind.get(type(node.func).__name__, 0) + 1
    with capsys.disabled():
        print(f"\n[db-derivation-guard] Redis(...) call sites by callee node kind: {by_kind}")
    assert by_kind.get("Attribute", 0) >= 15, (
        "the attribute-qualified Redis constructions vanished — a node.func.id matcher "
        "would now be vacuously green"
    )


def test_terminal_name_covers_both_node_kinds():
    """Both branches, asserted directly rather than inferred from a green tree."""
    assert _terminal_name(ast.parse("claim_test_db", mode="eval").body) == "claim_test_db"
    assert _terminal_name(ast.parse("_db_claim.claim_test_db", mode="eval").body) == "claim_test_db"
    assert _terminal_name(ast.parse("(a + b)", mode="eval").body) is None


# ---------------------------------------------------------------------------
# 3. Demonstrated red — one planted offender per shape
# ---------------------------------------------------------------------------

# Each entry: (id, source, substring the violation message must contain).
PLANTED_OFFENDERS = [
    pytest.param(
        "hardcoded-pool-db",
        "import redis\n\n\ndef test_x():\n    redis.Redis(db=7).ping()\n",
        "db 7",
        id="hardcoded-pool-db",
    ),
    pytest.param(
        "attribute-qualified-callee",
        "import redis\n\n\ndef test_x():\n    redis.Redis(db=some_helper())\n",
        "not a call to claim_test_db",
        id="attribute-qualified-callee",
    ),
    pytest.param(
        "bare-name-callee",
        "from redis import Redis\n\n\ndef test_x():\n    Redis(db=some_helper())\n",
        "not a call to claim_test_db",
        id="bare-name-callee",
    ),
    pytest.param(
        "unknown-constructor-name",
        "def test_x():\n    SomeFutureRedisLike(db=8)\n",
        "db 8",
        id="unknown-constructor-name",
    ),
    pytest.param(
        "connection-kwargs-derivation",
        "import redis\n\n\ndef test_x(other):\n"
        "    kw = other.connection_pool.connection_kwargs\n"
        "    redis.Redis(db=int(kw.get('db', 0) or 0))\n",
        "not a call to claim_test_db",
        id="connection-kwargs-derivation",
    ),
    pytest.param(
        "legacy-worker-derivation",
        "import redis\n\n\ndef test_x(request):\n"
        "    n = int(request.config.workerinput['workerid'][2:]) + 1\n"
        "    redis.Redis(db=n)\n",
        "which is not a claim call",
        id="legacy-worker-derivation",
    ),
    pytest.param(
        "rebound-local",
        "import redis\n\n\ndef test_x():\n"
        "    n = claim_test_db()\n"
        "    n = 3\n"
        "    redis.Redis(db=n)\n",
        "rebound 2 times",
        id="rebound-local",
    ),
    pytest.param(
        "literal-pool-url",
        "import redis\n\n\ndef test_x():\n    redis.Redis.from_url('redis://localhost:6379/9')\n",
        "db 9",
        id="literal-pool-url",
    ),
    pytest.param(
        "unparseable-url",
        "import redis\n\n\ndef test_x(cfg):\n    redis.Redis.from_url(cfg.url)\n",
        "cannot be determined",
        id="unparseable-url",
    ),
]


@pytest.mark.parametrize("name,source,expected", PLANTED_OFFENDERS)
def test_planted_offender_goes_red(tmp_path: Path, name: str, source: str, expected: str):
    """The guard must fail on a deliberately-planted offending site, by name and line.

    A green run over the real tree proves the guard did not fire. Only this
    proves it CAN fire.
    """
    planted = tmp_path / f"test_planted_{name.replace('-', '_')}.py"
    planted.write_text(source)

    result = scan_source(source, planted.name)
    assert result.violations, f"planted offender {name!r} was NOT detected — the guard is vacuous"

    message = "\n".join(format_violation(c) for c in result.violations)
    assert expected in message, f"violation message for {name!r} lacks {expected!r}:\n{message}"
    assert planted.name in message
    assert any(c.lineno > 0 for c in result.violations), "violation must name a line"


def test_planted_offender_is_still_caught_when_scanned_from_disk(tmp_path: Path):
    """End-to-end through :func:`scan_tree`, not just :func:`scan_source`."""
    (tmp_path / "test_planted.py").write_text("import redis\n\n\ndef t():\n    redis.Redis(db=5)\n")
    result = scan_tree(root=tmp_path)
    assert [c.expr for c in result.violations] == ["5"]
    assert result.violations[0].pool_db == 5


def test_sanctioned_shapes_are_accepted(tmp_path: Path):
    """The complement of demonstrated-red: the accepted shapes must NOT fire."""
    source = (
        "import redis\n"
        "from tests.db_claim import claim_test_db, redis_test_url\n"
        "import tests.db_claim as _db_claim\n\n\n"
        "def test_direct():\n"
        "    redis.Redis(db=claim_test_db())\n\n\n"
        "def test_attribute_qualified():\n"
        "    redis.Redis(db=_db_claim.claim_test_db())\n\n\n"
        "def test_one_hop_local():\n"
        "    n = claim_test_db()\n"
        "    redis.Redis(db=n)\n"
        "    redis.Redis(db=n, decode_responses=True)\n\n\n"
        "def test_url_call():\n"
        "    redis.Redis.from_url(redis_test_url())\n\n\n"
        "def test_url_fixture(redis_test_url):\n"
        "    redis.Redis.from_url(redis_test_url, decode_responses=True)\n"
    )
    result = scan_source(source, "test_sanctioned.py")
    # 4 db= keywords (direct, attribute-qualified, and the one-hop local twice)
    # plus 2 from_url arguments (the call form and the fixture-parameter form).
    assert len(result.candidates) == 6
    assert not result.violations, "\n".join(format_violation(c) for c in result.violations)


# ---------------------------------------------------------------------------
# 4. The settled invariant, enforced by a check rather than by review
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("expr", ["1", str(TEST_DB_POOL_MAX), "'redis://localhost:6379/3'"])
def test_allowlist_may_not_name_a_pool_database(expr: str):
    """No ALLOWLIST entry may name a db in [1..TEST_DB_POOL_MAX]. Ever."""
    problems = check_dispositions(
        allowlist=(Exemption(path="x.py", expr=expr, reason="pretend"),), deferred=()
    )
    assert any("claimable pool" in p for p in problems), problems


def test_allowlist_may_name_db_zero():
    assert (
        check_dispositions(
            allowlist=(Exemption(path="x.py", expr="0", reason="db-0 guard's own test"),),
            deferred=(),
        )
        == []
    )


def test_a_pool_naming_site_cannot_be_covered_by_an_allowlist_entry(tmp_path: Path):
    """Second layer: even a matching ALLOWLIST entry does not cover a pool-db site.

    Without this, an author could add ``Exemption(path=..., expr="db_num")`` for a
    site whose literal is hidden behind a local variable and launder a pool slot
    through the allowlist.
    """
    source = "import redis\n\n\ndef t():\n    n = 15 if x else 14\n    redis.Redis(db=n)\n"
    result = scan_source(source, "test_launder.py")
    covered, _ = apply_dispositions(
        result,
        allowlist=(Exemption(path="test_launder.py", expr="n", reason="nope"),),
        deferred=(),
    )
    assert covered, "an ALLOWLIST entry laundered a site that resolves to a pool database"

    # The same site IS coverable by a DEFERRED entry, which is dated and issue-linked.
    covered_deferred, _ = apply_dispositions(
        result,
        allowlist=(),
        deferred=(
            Exemption(
                path="test_launder.py",
                expr="n",
                reason="blocked",
                blocked_on="#2628",
                expires="2099-01-01",
            ),
        ),
    )
    assert not covered_deferred


# ---------------------------------------------------------------------------
# 5. Anti-decay: deferrals expire, entries cannot go stale, parse errors surface
# ---------------------------------------------------------------------------


def test_expired_deferral_fails():
    entry = Exemption(
        path="x.py", expr="n", reason="blocked", blocked_on="#2628", expires="2026-01-01"
    )
    problems = check_dispositions(allowlist=(), deferred=(entry,), today=date(2026, 6, 1))
    assert any("expired on 2026-01-01" in p and "#2628" in p for p in problems), problems


def test_unexpired_deferral_passes():
    entry = Exemption(
        path="x.py", expr="n", reason="blocked", blocked_on="#2628", expires="2026-12-31"
    )
    assert check_dispositions(allowlist=(), deferred=(entry,), today=date(2026, 6, 1)) == []


@pytest.mark.parametrize(
    "entry",
    [
        Exemption(path="x.py", expr="n", reason="r", blocked_on=None, expires="2099-01-01"),
        Exemption(path="x.py", expr="n", reason="r", blocked_on="2628", expires="2099-01-01"),
        Exemption(path="x.py", expr="n", reason="r", blocked_on="#2628", expires=None),
        Exemption(path="x.py", expr="n", reason="r", blocked_on="#2628", expires="not-a-date"),
        Exemption(path="x.py", expr="n", reason="  ", blocked_on="#2628", expires="2099-01-01"),
    ],
)
def test_deferral_metadata_is_mandatory(entry: Exemption):
    assert check_dispositions(allowlist=(), deferred=(entry,), today=date(2026, 6, 1))


def test_stale_entry_is_reported(tmp_path: Path):
    (tmp_path / "test_clean.py").write_text(
        "import redis\nfrom tests.db_claim import claim_test_db\n\n\n"
        "def t():\n    redis.Redis(db=claim_test_db())\n"
    )
    _, stale = apply_dispositions(
        scan_tree(root=tmp_path),
        allowlist=(Exemption(path="test_clean.py", expr="0", reason="was needed once"),),
        deferred=(),
    )
    assert [e.expr for e in stale] == ["0"]


def test_unparseable_source_raises_rather_than_being_skipped():
    """A guard that swallows files it cannot parse reports clean on the worst file."""
    with pytest.raises(SyntaxError):
        scan_source("def broken(:\n", "test_broken.py")
