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
import inspect
from datetime import date
from pathlib import Path

import pytest
import redis

from tests.db_derivation_guard import (
    ALLOWLIST,
    DEFERRED,
    REDIS_DB_POSITIONAL_INDEX,
    TEST_DB_POOL_MAX,
    TESTS_ROOT,
    Candidate,
    Exemption,
    _matches,
    _parameter_names_without_defaults,
    _rebound_names,
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
    """A guard with nothing to check is indistinguishable from a passing one.

    ``db-positional``'s floor is 0, deliberately, not an oversight: the live
    tree has zero positional ``db`` sites (#2764's positional leg is scoped to
    ``Redis``/``StrictRedis`` by terminal name, and nothing under ``tests/``
    constructs either one that way today). Non-vacuity for this kind comes
    from the planted offenders in ``PLANTED_OFFENDERS`` and the labeled rows
    in ``test_shadowing_and_rebinding_shapes``, never from a floor asserted
    against the live tree here. A future reader must not "fix" this floor by
    raising it above 0 -- there is nothing in the tree to raise it against
    until a real positional site is written.
    """
    result = scan_tree()
    db_kwargs = [c for c in result.candidates if c.kind == "db-kwarg"]
    from_urls = [c for c in result.candidates if c.kind == "from-url"]
    db_positionals = [c for c in result.candidates if c.kind == "db-positional"]
    with capsys.disabled():
        print(
            f"\n[db-derivation-guard] candidates: {len(result.candidates)} "
            f"({len(db_kwargs)} db= keyword, {len(from_urls)} from_url, "
            f"{len(db_positionals)} db-positional)"
        )
    assert len(db_kwargs) >= _MIN_DB_KWARG_SITES, (
        "the db= walk matched almost nothing — suspect the walker"
    )
    assert len(from_urls) >= _MIN_FROM_URL_SITES, (
        "the from_url walk matched almost nothing — suspect the walker"
    )
    assert len(db_positionals) == 0, (
        "a positional db site appeared in the live tree — confirm it is a "
        "genuine site needing a disposition, not a walker regression, before "
        "raising this floor"
    )


def test_no_disposition_entry_is_silently_absorbed_by_a_new_kind():
    """Sibling to ``test_no_stale_disposition_entries``: that test catches an
    ALLOWLIST/DEFERRED entry matching NOTHING (orphaned); this one catches the
    mirror-image failure of matching the WRONG thing. ``_matches`` covers a
    candidate by ``(path, expr)`` only, never by ``kind`` (#2764's positional
    and keyword legs add two new candidate-producing shapes), so a new-kind
    candidate landing on the same file and expression as an existing entry
    would silently cover it -- the entry stops being stale, for the wrong
    reason, and the site the entry actually described could go unmonitored.
    Assert every currently-dispositioned candidate is still the db-kwarg/
    from-url kind the four ``ALLOWLIST`` entries were written for.
    """
    result = scan_tree()
    covered = [
        cand for cand in result.violations if any(_matches(e, cand) for e in ALLOWLIST + DEFERRED)
    ]
    # Mirror of test_no_stale_disposition_entries' orphan check: every entry
    # must still cover at least one candidate.
    for entry in ALLOWLIST + DEFERRED:
        assert any(_matches(entry, c) for c in covered), (
            f"disposition entry {entry.path} :: {entry.expr!r} covers no candidate"
        )
    # The absorption direction: none of the candidates an entry covers may be
    # a kind #2764 introduced (db-positional). All four ALLOWLIST entries
    # were written for db-kwarg/from-url sites; a db-positional candidate
    # sharing one's (path, expr) would be covered by coincidence, not intent.
    absorbed = [c for c in covered if c.kind not in ("db-kwarg", "from-url")]
    assert not absorbed, (
        "a disposition entry was silently absorbed by a new candidate kind: "
        + ", ".join(f"{c.path}:{c.lineno} ({c.kind})" for c in absorbed)
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
    # #2764: positional `db` and keyword `url=` produced NO candidate at all --
    # not a violation, not a pass, simply unseen. Each row here was shown red
    # against pre-#2764 `main` before this file's fix landed.
    pytest.param(
        "positional-db-redis",
        "import redis\n\n\ndef test_x():\n    redis.Redis('localhost', 6379, 7)\n",
        "db 7",
        id="positional-db-redis",
    ),
    pytest.param(
        "positional-db-strictredis",
        "import redis\n\n\ndef test_x():\n    redis.StrictRedis('localhost', 6379, 7)\n",
        "db 7",
        id="positional-db-strictredis",
    ),
    pytest.param(
        "url-keyword-pool-literal",
        "import redis\n\n\ndef test_x():\n    redis.Redis.from_url(url='redis://localhost:6379/9')\n",
        "db 9",
        id="url-keyword-pool-literal",
    ),
    pytest.param(
        "url-keyword-unparseable",
        "import redis\n\n\ndef test_x(cfg):\n    redis.Redis.from_url(url=cfg.url)\n",
        "cannot be determined",
        id="url-keyword-unparseable",
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
        "    redis.Redis.from_url(redis_test_url, decode_responses=True)\n\n\n"
        # #2764: the positional claim call, the keyword-form sanctioned URL,
        # and the direct (unaliased) fixture parameter -- the last of these
        # was red before #2764 landed, refused with "no local binding in the
        # enclosing function" despite being the most obvious correct spelling.
        "def test_positional_claim_call():\n"
        "    redis.Redis('localhost', 6379, claim_test_db())\n\n\n"
        "def test_url_keyword_claim_call():\n"
        "    redis.Redis.from_url(url=redis_test_url())\n\n\n"
        "def test_direct_fixture_parameter(scratch_test_db):\n"
        "    redis.Redis(db=scratch_test_db)\n"
    )
    result = scan_source(source, "test_sanctioned.py")
    # 4 db= keywords (direct, attribute-qualified, the one-hop local twice)
    # plus 1 direct fixture parameter, plus 2 from_url arguments (call form,
    # fixture-parameter form) plus 1 url= keyword call, plus 1 positional
    # claim call.
    assert len(result.candidates) == 9
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

    def test_the_positional_leg_and_the_splat_leg_compose(self):
        """#2764: a positional `db` alongside a `**` splat must yield BOTH
        violations, not one that swallows the other -- they are unrelated
        legs judging unrelated arguments on the same call."""
        result = scan_source(
            "import redis\ndef t(kw):\n    redis.Redis('h', 6379, 7, **kw)\n", "t.py"
        )
        kinds = sorted(c.kind for c in result.violations)
        assert kinds == ["db-kwarg", "db-positional"]
        assert len(result.violations) == 2

    def test_starred_positional_yields_no_candidate(self):
        """`redis.Redis("h", 6379, *rest)` puts an `ast.Starred` at the db
        index and passes the length guard -- the only shape where the
        suppression branch is actually reachable."""
        result = scan_source(
            "import redis\ndef t(rest):\n    redis.Redis('h', 6379, *rest)\n", "t.py"
        )
        assert result.candidates == []

    def test_starred_positional_strictredis_mirror_yields_no_candidate(self):
        result = scan_source(
            "import redis\ndef t(rest):\n    redis.StrictRedis('h', 6379, *rest)\n", "t.py"
        )
        assert result.candidates == []

    def test_a_starred_arg_before_the_index_is_blind(self):
        """#3193, gap 5(b): a starred unpack BEFORE the db index shifts every
        later argument, so `node.args` is `[Starred, Constant(7)]` -- length 2
        -- and the fixed-index length guard short-circuits before the leg reads
        anything. A db literal in plain sight yields no candidate at all, by a
        different mechanism than the Starred suppression above: that branch is
        never reached here. Pins the disclosed behavior so a change to the
        length guard cannot silently move it."""
        result = scan_source("import redis\ndef t(hp):\n    redis.Redis(*hp, 7)\n", "t.py")
        assert result.candidates == []


# ---------------------------------------------------------------------------
# #2764: positional `db` and keyword `from_url(url=...)` produced no
# candidate at all, and route 2's bare-name leg laundered a shadowed
# identifier to green. Every row below was shown to reproduce against
# pre-#2764 `main` before this file's fix landed.
# ---------------------------------------------------------------------------


def test_positional_db_on_redis_goes_red():
    result = scan_source("import redis\ndef t():\n    redis.Redis('h', 6379, 7)\n", "x.py")
    assert len(result.violations) == 1
    assert result.violations[0].kind == "db-positional"
    assert result.violations[0].pool_db == 7


def test_positional_db_on_strictredis_goes_red():
    result = scan_source("import redis\ndef t():\n    redis.StrictRedis('h', 6379, 7)\n", "x.py")
    assert len(result.violations) == 1


def test_positional_claim_call_is_accepted():
    result = scan_source(
        "import redis\nfrom tests.db_claim import claim_test_db\n"
        "def t():\n    redis.Redis('h', 6379, claim_test_db())\n",
        "x.py",
    )
    assert result.violations == []
    assert len(result.candidates) == 1


def test_positional_db_alongside_an_explicit_db_keyword_emits_once():
    """`Redis("h", 6379, 7, db=8)` is a TypeError at runtime and cannot be a
    live site; the positional leg must not double-report it."""
    result = scan_source("import redis\ndef t():\n    redis.Redis('h', 6379, 7, db=8)\n", "x.py")
    assert len(result.violations) == 1
    assert result.violations[0].kind == "db-kwarg"


@pytest.mark.parametrize(
    "source",
    [
        "import redis\ndef t():\n    redis.Redis()\n",
        "import redis\ndef t():\n    redis.Redis('h')\n",
        "import redis\ndef t():\n    redis.Redis('h', 6379)\n",
        "import redis\ndef t(args):\n    redis.Redis(*args)\n",
    ],
    ids=["zero-args", "one-arg", "two-args", "single-starred-arg"],
)
def test_short_and_starred_positional_calls_raise_nothing(source: str):
    """`Redis(*args)` short-circuits on the length guard -- one argument --
    and proves nothing about the Starred-suppression branch itself."""
    assert scan_source(source, "x.py").candidates == []


def test_keyword_url_on_from_url_goes_red():
    result = scan_source(
        "import redis\ndef t():\n    redis.Redis.from_url(url='redis://localhost:6379/9')\n",
        "x.py",
    )
    assert len(result.violations) == 1
    assert result.violations[0].pool_db == 9


def test_keyword_sanctioned_url_is_accepted():
    result = scan_source(
        "import redis\nfrom tests.db_claim import redis_test_url\n"
        "def t():\n    redis.Redis.from_url(url=redis_test_url())\n",
        "x.py",
    )
    assert result.violations == []


def test_from_url_with_neither_positional_nor_keyword_yields_no_candidate():
    result = scan_source("import redis\ndef t():\n    redis.Redis.from_url()\n", "x.py")
    assert result.candidates == []


def test_from_url_with_only_an_opaque_splat_yields_no_from_url_candidate():
    """No visible `url` -- the splat leg is what fires (unchanged), not route 2."""
    result = scan_source("import redis\ndef t(kw):\n    redis.Redis.from_url(**kw)\n", "x.py")
    assert all(c.kind != "from-url" for c in result.candidates)


def test_direct_unshadowed_fixture_parameter_route1():
    result = scan_source(
        "import redis\ndef t(scratch_test_db):\n    redis.Redis(db=scratch_test_db)\n", "x.py"
    )
    assert result.violations == []


def test_direct_unshadowed_fixture_parameter_route2():
    result = scan_source(
        "import redis\ndef t(redis_test_url):\n    redis.Redis.from_url(redis_test_url)\n", "x.py"
    )
    assert result.violations == []


@pytest.mark.parametrize(
    "fn_src",
    [
        "def t(scratch_test_db):\n    redis.Redis(db=scratch_test_db)\n",
        "def t(*, scratch_test_db):\n    redis.Redis(db=scratch_test_db)\n",
        "def t(scratch_test_db, /):\n    redis.Redis(db=scratch_test_db)\n",
        "async def t(scratch_test_db):\n    redis.Redis(db=scratch_test_db)\n",
    ],
    ids=["plain", "keyword-only", "positional-only", "async-def"],
)
def test_the_genuine_fixture_parameter_passes_in_every_spelling_route1(fn_src: str):
    result = scan_source(f"import redis\n{fn_src}", "x.py")
    assert result.violations == []


@pytest.mark.parametrize(
    "fn_src",
    [
        "def t(redis_test_url):\n    redis.Redis.from_url(redis_test_url)\n",
        "def t(*, redis_test_url):\n    redis.Redis.from_url(redis_test_url)\n",
        "def t(redis_test_url, /):\n    redis.Redis.from_url(redis_test_url)\n",
        "async def t(redis_test_url):\n    redis.Redis.from_url(redis_test_url)\n",
    ],
    ids=["plain", "keyword-only", "positional-only", "async-def"],
)
def test_the_genuine_fixture_parameter_passes_in_every_spelling_route2(fn_src: str):
    result = scan_source(f"import redis\n{fn_src}", "x.py")
    assert result.violations == []


def test_a_defaulted_fixture_parameter_stays_red_route1():
    result = scan_source(
        "import redis\ndef t(scratch_test_db=7):\n    redis.Redis(db=scratch_test_db)\n", "x.py"
    )
    assert len(result.violations) == 1


def test_a_defaulted_fixture_parameter_stays_red_route2():
    result = scan_source(
        "import redis\ndef t(redis_test_url='redis://localhost:6379/9'):\n"
        "    redis.Redis.from_url(redis_test_url)\n",
        "x.py",
    )
    assert len(result.violations) == 1


# Leg-2 evidence (route 1) -- the sanctioned identifier is never an
# unshadowed parameter at all, so leg 2 refuses these on its own and
# `_rebound_names` is never consulted.
LEG2_ROWS = [
    ("LAUNDER1", "def t():\n    scratch_test_db = 7\n    redis.Redis(db=scratch_test_db)\n", 1),
    ("LAUNDER2", "def t():\n    test_db = 7\n    redis.Redis(db=test_db)\n", 1),
    (
        "LDEFAULT",
        "def t(scratch_test_db=7):\n    redis.Redis(db=scratch_test_db)\n",
        1,
    ),
    (
        "LMODULE",
        "scratch_test_db = 7\ndef t():\n    redis.Redis(db=scratch_test_db)\n",
        1,
    ),
    (
        "LFOR",
        "def t(xs):\n    for scratch_test_db in xs:\n        redis.Redis(db=scratch_test_db)\n",
        1,
    ),
    (
        "LWALRUS",
        "def t():\n    if (scratch_test_db := 7):\n        redis.Redis(db=scratch_test_db)\n",
        1,
    ),
    (
        "LWITH",
        "def t(c):\n    with c as scratch_test_db:\n        redis.Redis(db=scratch_test_db)\n",
        1,
    ),
]

# Leg-3 evidence (route 1) -- the sanctioned name IS a parameter with no
# default (legs 1 and 2 both pass), so only `_rebound_names` can refuse it.
# One row per binding form.
LEG3_ROWS = [
    (
        "L3ASSIGN",
        "def t(scratch_test_db):\n    scratch_test_db = 7\n    redis.Redis(db=scratch_test_db)\n",
        1,
    ),
    (
        "L3ANNASSIGN",
        "def t(scratch_test_db):\n"
        "    scratch_test_db: int = 7\n    redis.Redis(db=scratch_test_db)\n",
        1,
    ),
    (
        "L3AUG",
        "def t(scratch_test_db):\n    scratch_test_db += 1\n    redis.Redis(db=scratch_test_db)\n",
        1,
    ),
    (
        "L3FOR",
        "def t(scratch_test_db, xs):\n"
        "    for scratch_test_db in xs:\n        redis.Redis(db=scratch_test_db)\n",
        1,
    ),
    (
        "L3ASYNCFOR",
        "async def t(scratch_test_db, xs):\n"
        "    async for scratch_test_db in xs:\n        redis.Redis(db=scratch_test_db)\n",
        1,
    ),
    (
        "L3WALRUS",
        "def t(scratch_test_db):\n"
        "    if (scratch_test_db := 7):\n        redis.Redis(db=scratch_test_db)\n",
        1,
    ),
    (
        "L3WITH",
        "def t(scratch_test_db, c):\n"
        "    with c as scratch_test_db:\n        redis.Redis(db=scratch_test_db)\n",
        1,
    ),
    (
        "L3ASYNCWITH",
        "async def t(scratch_test_db, c):\n"
        "    async with c as scratch_test_db:\n        redis.Redis(db=scratch_test_db)\n",
        1,
    ),
    (
        "L3COMPWALRUS",
        "def t(scratch_test_db, xs):\n"
        "    [scratch_test_db := y for y in xs]\n    redis.Redis(db=scratch_test_db)\n",
        1,
    ),
    (
        "L3EXCEPT",
        "def t(scratch_test_db):\n"
        "    try:\n        pass\n    except ValueError as scratch_test_db:\n"
        "        redis.Redis(db=scratch_test_db)\n",
        1,
    ),
    (
        "L3IMPORT",
        "def t(scratch_test_db):\n"
        "    import os as scratch_test_db\n    redis.Redis(db=scratch_test_db)\n",
        1,
    ),
    (
        "L3DEF",
        "def t(scratch_test_db):\n"
        "    def scratch_test_db():\n        return 7\n    redis.Redis(db=scratch_test_db)\n",
        1,
    ),
    (
        "L3CLASS",
        "def t(scratch_test_db):\n"
        "    class scratch_test_db:\n        pass\n    redis.Redis(db=scratch_test_db)\n",
        1,
    ),
    (
        "L3MATCH",
        "def t(scratch_test_db, m):\n"
        "    match m:\n        case scratch_test_db:\n"
        "            redis.Redis(db=scratch_test_db)\n",
        1,
    ),
    (
        "L3MATCHSTAR",
        "def t(scratch_test_db, m):\n"
        "    match m:\n        case [*scratch_test_db]:\n"
        "            redis.Redis(db=scratch_test_db)\n",
        1,
    ),
    (
        "L3MATCHMAP",
        "def t(scratch_test_db, m):\n"
        '    match m:\n        case {"a": 1, **scratch_test_db}:\n'
        "            redis.Redis(db=scratch_test_db)\n",
        1,
    ),
    (
        "L3NONLOCAL",
        "def t(scratch_test_db):\n"
        "    def inner():\n        nonlocal scratch_test_db\n        scratch_test_db = 7\n"
        "    inner()\n    redis.Redis(db=scratch_test_db)\n",
        1,
    ),
    (
        "L3GLOBAL",
        "def t(scratch_test_db):\n"
        "    def inner():\n        global scratch_test_db\n        scratch_test_db = 7\n"
        "    inner()\n    redis.Redis(db=scratch_test_db)\n",
        1,
    ),
    (
        "L3NESTEDDEFAULT",
        "def t(scratch_test_db):\n"
        "    def inner(x=(scratch_test_db := 7)):\n        return x\n"
        "    redis.Redis(db=scratch_test_db)\n",
        1,
    ),
    (
        "L3LAMBDADEFAULT",
        "def t(scratch_test_db):\n"
        "    f = lambda x=(scratch_test_db := 99): x\n"
        "    redis.Redis(db=scratch_test_db)\n",
        1,
    ),
]

# Leg-3 over-refusal -- the sanctioned parameter is genuinely unshadowed;
# each is scope-local in Python 3 and leaves the outer parameter intact.
LEG3_OVERREFUSAL_ROWS = [
    (
        "L3COMP",
        "def t(scratch_test_db, xs):\n"
        "    ys = [scratch_test_db for scratch_test_db in xs]\n"
        "    redis.Redis(db=scratch_test_db)\n",
        0,
    ),
    (
        "L3LAMBDA",
        "def t(scratch_test_db):\n"
        "    f = lambda scratch_test_db: scratch_test_db\n"
        "    redis.Redis(db=scratch_test_db)\n",
        0,
    ),
    (
        "L3NESTASSIGN",
        "def t(scratch_test_db):\n"
        "    def inner():\n        scratch_test_db = 7\n        return scratch_test_db\n"
        "    redis.Redis(db=scratch_test_db)\n",
        0,
    ),
]

# Route-2 mirrors: the leg's bare-name accept performed NO scope check before
# #2764, so every one of these was green when it should have been red.
ROUTE2_ROWS = [
    (
        "URLLAUNDER",
        "def t():\n"
        "    redis_test_url = 'redis://localhost:6379/9'\n"
        "    redis.Redis.from_url(redis_test_url)\n",
        1,
    ),
    (
        "URLDEFAULT",
        "def t(redis_test_url='redis://localhost:6379/9'):\n"
        "    redis.Redis.from_url(redis_test_url)\n",
        1,
    ),
    (
        "URLL3ASSIGN",
        "def t(redis_test_url):\n"
        "    redis_test_url = 'redis://localhost:6379/9'\n"
        "    redis.Redis.from_url(redis_test_url)\n",
        1,
    ),
    (
        "URLL3FOR",
        "def t(redis_test_url, xs):\n"
        "    for redis_test_url in xs:\n        redis.Redis.from_url(redis_test_url)\n",
        1,
    ),
    (
        "URLL3EXCEPT",
        "def t(redis_test_url):\n"
        "    try:\n        pass\n    except ValueError as redis_test_url:\n"
        "        redis.Redis.from_url(redis_test_url)\n",
        1,
    ),
    (
        "URLL3MATCH",
        "def t(redis_test_url, m):\n"
        "    match m:\n        case redis_test_url:\n"
        "            redis.Redis.from_url(redis_test_url)\n",
        1,
    ),
    (
        "URLL3NONLOCAL",
        "def t(redis_test_url):\n"
        "    def inner():\n        nonlocal redis_test_url\n"
        "        redis_test_url = 'redis://localhost:6379/9'\n"
        "    inner()\n    redis.Redis.from_url(redis_test_url)\n",
        1,
    ),
    (
        "URLL3COMPWALRUS",
        "def t(redis_test_url, xs):\n"
        "    [redis_test_url := y for y in xs]\n    redis.Redis.from_url(redis_test_url)\n",
        1,
    ),
    (
        "URLL3NESTEDDEFAULT",
        "def t(redis_test_url):\n"
        '    def inner(x=(redis_test_url := "redis://localhost:6379/9")):\n        return x\n'
        "    redis.Redis.from_url(redis_test_url)\n",
        1,
    ),
]

ROUTE2_OVERREFUSAL_ROWS = [
    (
        "URLL3COMP",
        "def t(redis_test_url, xs):\n"
        "    ys = [redis_test_url for redis_test_url in xs]\n"
        "    redis.Redis.from_url(redis_test_url)\n",
        0,
    ),
    (
        "URLL3NESTASSIGN",
        "def t(redis_test_url):\n"
        "    def inner():\n        redis_test_url = 'redis://localhost:6379/9'\n"
        "        return redis_test_url\n"
        "    redis.Redis.from_url(redis_test_url)\n",
        0,
    ),
]

ALL_LABELED_ROWS = (
    LEG2_ROWS + LEG3_ROWS + LEG3_OVERREFUSAL_ROWS + ROUTE2_ROWS + ROUTE2_OVERREFUSAL_ROWS
)


@pytest.mark.parametrize(
    "label,body,expected",
    ALL_LABELED_ROWS,
    ids=[row[0] for row in ALL_LABELED_ROWS],
)
def test_shadowing_and_rebinding_shapes(label: str, body: str, expected: int):
    result = scan_source(f"import redis\n{body}", "x.py")
    assert len(result.violations) == expected, (
        f"{label}: expected {expected} violation(s), got {len(result.violations)}"
    )


def test_route2_one_hop_alias_stays_a_disclosed_false_positive():
    """URLHOP paired with DBHOP pins residual gap 6: route 2 has no one-hop
    alias leg, so the shape route 1 accepts is a violation here."""
    url_hop = scan_source(
        "import redis\nfrom tests.db_claim import redis_test_url\n"
        "def t():\n    url = redis_test_url()\n    redis.Redis.from_url(url)\n",
        "x.py",
    )
    db_hop = scan_source(
        "import redis\nfrom tests.db_claim import claim_test_db\n"
        "def t():\n    d = claim_test_db()\n    redis.Redis(db=d)\n",
        "x.py",
    )
    assert len(url_hop.violations) == 1
    assert db_hop.violations == []


# ---------------------------------------------------------------------------
# `_parameter_names_without_defaults` and `_rebound_names`, asserted
# directly. A helper that over-collects and a leg-2 refusal produce the same
# violation COUNT through `scan_source`; asserting the helper's return set
# is what tells them apart.
# ---------------------------------------------------------------------------


def _parse_fn(src: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    module = ast.parse(src)
    fn = module.body[0]
    assert isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
    return fn


class TestParameterNamesWithoutDefaults:
    def test_plain_parameters_with_no_defaults(self):
        assert _parameter_names_without_defaults(_parse_fn("def t(a, b):\n    pass\n")) == {
            "a",
            "b",
        }

    def test_right_to_left_default_alignment(self):
        fn = _parse_fn("def t(a, b, c=1, d=2):\n    pass\n")
        assert _parameter_names_without_defaults(fn) == {"a", "b"}

    def test_positional_only_and_keyword_only(self):
        fn = _parse_fn("def t(a, /, b, *, c, d=1):\n    pass\n")
        assert _parameter_names_without_defaults(fn) == {"a", "b", "c"}

    def test_vararg_and_kwarg_are_excluded(self):
        fn = _parse_fn("def t(a, *args, **kw):\n    pass\n")
        assert _parameter_names_without_defaults(fn) == {"a"}

    def test_async_def_is_supported(self):
        fn = _parse_fn("async def t(a, b=1):\n    pass\n")
        assert _parameter_names_without_defaults(fn) == {"a"}

    def test_non_function_node_returns_empty(self):
        assert _parameter_names_without_defaults(ast.parse("x = 1").body[0]) == set()


class TestReboundNames:
    def test_plain_assign(self):
        fn = _parse_fn("def t(p):\n    p = 7\n")
        assert "p" in _rebound_names(fn)

    def test_augassign(self):
        fn = _parse_fn("def t(p):\n    p += 1\n")
        assert "p" in _rebound_names(fn)

    def test_comprehension_target_does_not_rebind(self):
        fn = _parse_fn("def t(p, xs):\n    ys = [p for p in xs]\n")
        assert "p" not in _rebound_names(fn)

    def test_walrus_inside_comprehension_rebinds(self):
        fn = _parse_fn("def t(p, xs):\n    [p := y for y in xs]\n")
        assert "p" in _rebound_names(fn)

    def test_lambda_parameter_does_not_rebind(self):
        fn = _parse_fn("def t(p):\n    f = lambda p: p\n")
        assert "p" not in _rebound_names(fn)

    def test_assignment_inside_nested_def_does_not_rebind(self):
        fn = _parse_fn("def t(p):\n    def inner():\n        p = 7\n        return p\n")
        assert "p" not in _rebound_names(fn)

    def test_nested_def_of_the_same_name_rebinds(self):
        fn = _parse_fn("def t(p):\n    def p():\n        return 7\n")
        assert "p" in _rebound_names(fn)

    def test_nested_def_default_walrus_rebinds(self):
        """A nested def's default evaluates in the ENCLOSING scope at
        definition time, not inside the new scope its body introduces
        (#2764's accept-direction gap)."""
        fn = _parse_fn("def t(p):\n    def inner(x=(p := 7)):\n        return x\n")
        assert "p" in _rebound_names(fn)

    def test_lambda_default_walrus_rebinds(self):
        """A lambda's parameter default evaluates in the ENCLOSING scope at
        definition time, exactly like a nested def's does (#3192)."""
        fn = _parse_fn("def t(p):\n    f = lambda x=(p := 99): x\n")
        assert "p" in _rebound_names(fn)

    def test_lambda_keyword_only_default_walrus_rebinds(self):
        fn = _parse_fn("def t(p):\n    f = lambda *, x=(p := 99): x\n")
        assert "p" in _rebound_names(fn)

    def test_lambda_body_walrus_does_not_rebind(self):
        """The body is the lambda's own scope, so a walrus there binds
        locally and leaves the enclosing parameter intact."""
        fn = _parse_fn("def t(p):\n    f = lambda y: (p := y)\n")
        assert "p" not in _rebound_names(fn)

    def test_nested_def_decorator_walrus_rebinds(self):
        fn = _parse_fn("def t(p):\n    @deco(p := 7)\n    def inner():\n        pass\n")
        assert "p" in _rebound_names(fn)

    def test_nested_class_base_walrus_rebinds(self):
        fn = _parse_fn("def t(p):\n    class C((p := 7) and object):\n        pass\n")
        assert "p" in _rebound_names(fn)

    def test_nested_class_keyword_walrus_rebinds(self):
        fn = _parse_fn("def t(p):\n    class C(metaclass=(p := type)):\n        pass\n")
        assert "p" in _rebound_names(fn)

    def test_nested_nonlocal_rebinds(self):
        fn = _parse_fn("def t(p):\n    def inner():\n        nonlocal p\n        p = 1\n")
        assert "p" in _rebound_names(fn)

    def test_nested_global_rebinds_conservatively(self):
        fn = _parse_fn("def t(p):\n    def inner():\n        global p\n        p = 1\n")
        assert "p" in _rebound_names(fn)

    def test_except_handler_name(self):
        fn = _parse_fn(
            "def t(p):\n    try:\n        pass\n    except ValueError as p:\n        pass\n"
        )
        assert "p" in _rebound_names(fn)

    def test_import_as(self):
        fn = _parse_fn("def t(p):\n    import os as p\n")
        assert "p" in _rebound_names(fn)

    def test_non_function_node_returns_empty(self):
        assert _rebound_names(ast.parse("x = 1").body[0]) == set()


# ---------------------------------------------------------------------------
# Per-kind violation messages (#2768's unpinned-message item; spike-3's
# measured defect that a non-`db-kwarg` kind rendered as `from_url(...)`).
# ---------------------------------------------------------------------------


def test_positional_message_is_not_mislabeled_as_from_url():
    v = scan_source("import redis\ndef t():\n    redis.Redis('h', 6379, 7)\n", "x.py").violations
    message = format_violation(v[0])
    assert "takes db=7" in message
    assert not message.startswith("tests/x.py:3: from_url(")


@pytest.mark.parametrize("kind", ["db-kwarg", "from-url", "db-positional"])
def test_every_kind_names_the_remedial_api(kind: str):
    cand = Candidate(
        path="x.py", lineno=1, kind=kind, expr="7", callee="Redis", ok=False, detail="d", pool_db=7
    )
    message = format_violation(cand)
    for token in ("claim_test_db()", "redis_test_url()", "ALLOWLIST", "DEFERRED"):
        assert token in message, f"{kind} message missing {token!r}:\n{message}"


def test_an_unrecognized_kind_renders_its_own_name_rather_than_impersonating_from_url():
    cand = Candidate(
        path="x.py",
        lineno=1,
        kind="some-future-kind",
        expr="7",
        callee="Redis",
        ok=False,
        detail="d",
        pool_db=None,
    )
    message = format_violation(cand)
    assert "some-future-kind(7)" in message
    assert "from_url(" not in message


# ---------------------------------------------------------------------------
# Fix 6: the signature tripwire. Only THIS file imports `redis`; the guard
# module stays pure-AST.
# ---------------------------------------------------------------------------


def test_positional_index_still_names_db():
    params = list(inspect.signature(redis.Redis.__init__).parameters)
    assert params[REDIS_DB_POSITIONAL_INDEX + 1] == "db"  # +1 skips `self`


def test_strictredis_is_redis():
    assert redis.StrictRedis is redis.Redis


def test_guard_module_imports_no_redis():
    source = (TESTS_ROOT / "db_derivation_guard.py").read_text()
    assert not any(
        line.startswith("import redis") or line.startswith("from redis")
        for line in source.splitlines()
    )


# ---------------------------------------------------------------------------
# Residual-gap disclosure (fix 5, #2768's second folded-in item).
# ---------------------------------------------------------------------------


def test_residual_gaps_are_disclosed_in_the_module_docstring():
    import tests.db_derivation_guard as guard_module

    doc = guard_module.__doc__ or ""
    assert "What this guard still cannot see" in doc
    assert "REDIS_CONSTRUCTORS" in doc
