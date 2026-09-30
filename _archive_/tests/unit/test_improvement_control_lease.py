"""Lease protocol conformance and the #3220 hand-off (Decision 1, Task 2).

Uses the autouse ``redis_test_db`` fixture (tests/conftest.py): ``REDIS_URL``
points at a claimed per-worker test DB process-wide, so :class:`CaseLease`'s
raw Redis keys never touch production.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.improvement_control.lease import CaseLease, LeaseProtocol, default_lease
from utils.redis_client import text_redis

PK = "test-3215-lease"


def make_lease() -> CaseLease:
    return CaseLease(text_redis(), default_ttl_seconds=90)


def key(case: str = "c1") -> str:
    return f"improve:{PK}:{case}:lease"


class TestLeaseProtocolConformance:
    def test_acquire_returns_increasing_generations(self):
        lease = make_lease()
        k = key("gens")
        g1 = lease.acquire(k, ttl=90)
        assert lease.release(k, g1)
        g2 = lease.acquire(k, ttl=90)
        assert g1 is not None
        assert g2 is not None
        assert g2 > g1

    def test_lapsed_lease_can_be_reacquired_with_a_higher_generation(self):
        lease = make_lease()
        k = key("lapsed")
        g1 = lease.acquire(k, ttl=0)  # expires immediately (server TIME + 0)
        assert g1 is not None
        g2 = lease.acquire(k, ttl=90)
        assert g2 is not None
        assert g2 > g1

    def test_second_acquire_before_expiry_is_refused(self):
        lease = make_lease()
        k = key("held")
        g1 = lease.acquire(k, ttl=90)
        assert g1 is not None
        g2 = lease.acquire(k, ttl=90)
        assert g2 is None

    def test_stale_renew_returns_false_and_changes_nothing(self):
        lease = make_lease()
        k = key("renew")
        g1 = lease.acquire(k, ttl=90)
        assert lease.release(k, g1)
        g2 = lease.acquire(k, ttl=90)
        assert lease.renew(k, g1) is False
        # The live holder's lease is untouched.
        assert lease.renew(k, g2) is True

    def test_stale_release_returns_false_and_changes_nothing(self):
        lease = make_lease()
        k = key("release")
        g1 = lease.acquire(k, ttl=90)
        assert lease.release(k, g1)
        g2 = lease.acquire(k, ttl=90)
        # A stale releaser presenting the old generation must not clobber
        # the newer holder's lease.
        assert lease.release(k, g1) is False
        assert lease.release(k, g2) is True

    def test_holders_own_renewal_succeeds(self):
        lease = make_lease()
        k = key("own-renew")
        g1 = lease.acquire(k, ttl=90)
        assert lease.renew(k, g1) is True

    def test_default_lease_satisfies_protocol(self):
        lease = default_lease()
        assert isinstance(lease, LeaseProtocol)


class TestControlKeyRestriction:
    def test_refuses_a_key_outside_the_improve_prefix(self):
        lease = make_lease()
        with pytest.raises(ValueError):
            lease.acquire("lease:session:x", ttl=90)
        with pytest.raises(ValueError):
            lease.renew("lease:session:x", 1)
        with pytest.raises(ValueError):
            lease.release("lease:session:x", 1)


class TestInterimLeaseRetirement:
    def test_interim_lease_retired_when_redis_lease_exists(self):
        """The #3220 hand-off trigger.

        Anchored to the repo root via ``parents[2]`` (this file lives at
        ``tests/unit/``) so it fails from any cwd, including a worktree.
        Once ``models/redis_lease.py`` exists, #3220's builder deletes
        ``CaseLease``, repoints ``default_lease()`` at it, and deletes this
        test along with the vacuous lane-3 Verification row.
        """
        redis_lease_path = Path(__file__).resolve().parents[2] / "models" / "redis_lease.py"
        assert not redis_lease_path.exists(), (
            "models/redis_lease.py (#3220) now exists: retire "
            "tools/improvement_control/lease.py's CaseLease, repoint "
            "default_lease() at models.redis_lease, and delete this test."
        )
