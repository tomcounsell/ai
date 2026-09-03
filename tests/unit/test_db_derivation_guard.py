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

# Anti-vacuity floors. These are NOT counts of the tree — they are the point
# below which "the walker matched something" stops being credible. Set well
# under the live totals so ordinary cleanups (deleting a raw client, folding
# two into one) do not fail a test that is not about them; raise them only if
# the live totals grow enough that the current floor stops discriminating.
# Grain of salt: provisional, tunable.
_MIN_DB_KWARG_SITES = 12
_MIN_FROM_URL_SITES = 8
_MIN_ATTRIBUTE_QUALIFIED_SITES = 12


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
    assert len(db_kwargs) >= _MIN_DB_KWARG_SITES, (
        "the db= walk matched almost nothing — suspect the walker"
    )
    assert len(from_urls) >= _MIN_FROM_URL_SITES, (
        "the from_url walk matched almost nothing — suspect the walker"
    )


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
    assert by_kind.get("Attribute", 0) >= _MIN_ATTRIBUTE_QUALIFIED_SITES, (
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
    # A `**` splat parses as a keyword with `arg is None`, so the original
    # `kw.arg != "db"` skip made both of these produce no candidate at all --
    # not a violation, not a pass, simply unseen. Exactly the class of miss the
    # guard exists to close: a shape nobody enumerated. No live site exists
    # today, but the deferred test_notify_isolation.py already works with
    # connection_kwargs dicts, so `redis.Redis(**kw)` is one refactor away.
    pytest.param(
        "db-through-a-dict-literal-splat",
        'import redis\n\n\ndef test_x():\n    redis.Redis(**{"db": 15})\n',
        "db 15",
        id="db-through-a-dict-literal-splat",
    ),
    pytest.param(
        "opaque-splat-into-a-redis-construction",
        "import redis\n\n\ndef test_x(kw):\n    redis.Redis(**kw)\n",
        "may carry a db= the guard cannot see",
        id="opaque-splat-into-a-redis-construction",
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


# ---------------------------------------------------------------------------
# `**` splat handling (#2700 review, Tech Debt 2)
# ---------------------------------------------------------------------------


class TestSplatHandling:
    """The dict-literal leg stays callee-agnostic; the opaque leg cannot.

    Flagging every opaque `**` regardless of callee produced 183 violations
    across 100+ unrelated files on the first attempt — `**kwargs` forwarding is
    how test helpers are written here. A guard that fires on every helper in
    the repo gets deleted rather than fixed, and then the real hole is open
    with no guard at all. So the opaque leg is scoped to Redis constructions
    and the cost is recorded rather than hidden.
    """

    def test_a_dict_literal_splat_is_judged_like_a_written_out_kwarg(self):
        result = scan_source('import redis\ndef t():\n    redis.Redis(**{"db": 15})\n', "t.py")
        assert len(result.violations) == 1
        assert result.violations[0].pool_db == 15

    def test_a_dict_literal_splat_carrying_a_claim_call_passes(self):
        result = scan_source(
            "import redis\nfrom tests.db_claim import claim_test_db\n"
            'def t():\n    redis.Redis(**{"db": claim_test_db()})\n',
            "t.py",
        )
        assert result.violations == []
        assert len(result.candidates) == 1, "the site must still be SEEN, just accepted"

    def test_a_dict_literal_splat_without_a_db_key_or_nested_unpack_is_provably_safe(self):
        result = scan_source('import redis\ndef t():\n    redis.Redis(**{"host": "x"})\n', "t.py")
        assert result.candidates == []

    def test_a_dict_literal_splat_with_a_nested_unpack_falls_through_to_opaque(self):
        """A nested ** unpack inside the dict is invisible to a static scan, so
        the dict cannot be proven safe even absent a literal "db" key."""
        result = scan_source(
            'import redis\ndef t(base_kw):\n    redis.Redis(**{**base_kw, "host": "x"})\n', "t.py"
        )
        assert len(result.violations) == 1
        assert "cannot see" in result.violations[0].detail

    def test_a_dict_literal_splat_with_a_nested_unpack_into_an_unrelated_helper_is_ignored(self):
        """The callee scoping still applies on the fall-through path."""
        result = scan_source(
            'def t(base_kw):\n    make_session(**{**base_kw, "host": "x"})\n', "t.py"
        )
        assert result.candidates == []

    def test_a_dict_literal_splat_with_a_computed_key_falls_through_to_opaque(self):
        """A non-constant key is exactly as invisible to a static scan as a
        nested ** unpack -- it cannot be proven not to be "db" any more than
        an unpacked entry can."""
        result = scan_source("import redis\ndef t(k):\n    redis.Redis(**{k: 7})\n", "t.py")
        assert len(result.violations) == 1
        assert "cannot see" in result.violations[0].detail

    def test_a_dict_literal_splat_with_a_computed_key_into_an_unrelated_helper_is_ignored(self):
        """The callee scoping still applies on the computed-key fall-through."""
        result = scan_source("def t(k):\n    make_session(**{k: 7})\n", "t.py")
        assert result.candidates == []

    def test_an_opaque_entry_after_the_db_key_is_not_blessed_by_it(self):
        """A "db" key that is visible and a claim call is not enough -- a
        later entry in the same dict literal can silently overwrite it at
        runtime, so this must NOT be accepted on the visible value alone."""
        result = scan_source(
            "import redis\nfrom tests.db_claim import claim_test_db\n"
            'def t(overrides):\n    redis.Redis(**{"db": claim_test_db(), **overrides})\n',
            "t.py",
        )
        assert len(result.violations) == 1
        assert "silently overwrite" in result.violations[0].detail

    def test_a_visible_db_with_a_trailing_nested_unpack_is_flagged_for_any_callee(self):
        """Round-4 regression (#2700): a callee not in REDIS_CONSTRUCTORS must
        not make a visible "db" key with a trailing opaque entry disappear.
        Round 3's restructure sent this down the REDIS_CONSTRUCTORS-scoped
        opaque leg, so `Whatever(...)` produced NO candidate at all -- a pool
        slot written in plain sight vanished silently."""
        result = scan_source('def t(ov):\n    Whatever(**{"db": 15, **ov})\n', "t.py")
        assert len(result.violations) == 1
        assert result.violations[0].pool_db == 15
        assert "silently overwrite" in result.violations[0].detail

    def test_a_future_redis_like_constructor_with_a_visible_db_and_overrides_is_flagged(self):
        """The PR body's own headline example of what the inverted polarity
        buys: a constructor the guard does not recognize by name must still
        be caught when the db value is visible."""
        result = scan_source('def t(ov):\n    SomeFutureRedisLike(**{"db": 8, **ov})\n', "t.py")
        assert len(result.violations) == 1
        assert result.violations[0].pool_db == 8

    def test_a_visible_db_followed_by_a_computed_key_is_flagged_regardless_of_callee(self):
        """Same defect, computed-key shape instead of a nested unpack."""
        result = scan_source('def t(k):\n    Whatever(**{"db": 15, k: 1})\n', "t.py")
        assert len(result.violations) == 1
        assert result.violations[0].pool_db == 15

    def test_an_opaque_entry_before_the_db_key_does_not_shadow_it(self):
        """The mirror case: an opaque entry BEFORE the "db" key cannot
        overwrite it -- the literal "db" entry is the one that wins in a
        Python dict literal -- so this is still judged on the visible value."""
        result = scan_source(
            'import redis\ndef t(base_kw):\n    redis.Redis(**{**base_kw, "db": 9})\n', "t.py"
        )
        assert len(result.violations) == 1
        assert result.violations[0].pool_db == 9

    def test_the_dict_literal_leg_ignores_the_callee(self):
        """Polarity preserved where the value is visible."""
        result = scan_source('def t():\n    Whatever(**{"db": 15})\n', "t.py")
        assert len(result.violations) == 1

    def test_an_opaque_splat_into_a_redis_construction_is_undecidable(self):
        result = scan_source("import redis\ndef t(kw):\n    redis.Redis(**kw)\n", "t.py")
        assert len(result.violations) == 1
        assert "cannot see" in result.violations[0].detail

    def test_an_opaque_splat_into_an_unrelated_helper_is_ignored(self):
        """The bounded exception. Without it the guard is unusable, see the docstring."""
        result = scan_source("def t(kw):\n    make_session(**kw)\n", "t.py")
        assert result.candidates == []

    def test_the_real_tree_has_no_splat_violations(self):
        """Scoping claim, measured rather than asserted: 191 `**` sites, zero Redis."""
        remaining, _ = apply_dispositions(scan_tree())
        assert remaining == []
