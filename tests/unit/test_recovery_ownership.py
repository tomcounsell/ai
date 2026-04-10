"""Tests for RECOVERY_OWNERSHIP coverage completeness.

Adding a new non-terminal status without registering its recovery owner
must break this test, forcing the developer to declare which process
is responsible for recovering sessions in that state.
"""

from models.session_lifecycle import NON_TERMINAL_STATUSES, RECOVERY_OWNERSHIP


class TestRecoveryOwnershipCoverage:
    """Assert RECOVERY_OWNERSHIP covers every non-terminal status."""

    def test_keys_match_non_terminal_statuses(self):
        """RECOVERY_OWNERSHIP must have exactly the same keys as NON_TERMINAL_STATUSES."""
        assert set(RECOVERY_OWNERSHIP.keys()) == NON_TERMINAL_STATUSES

    def test_no_empty_owners(self):
        """Every entry must have a non-empty owner string."""
        for status, owner in RECOVERY_OWNERSHIP.items():
            assert isinstance(owner, str) and len(owner) > 0, (
                f"RECOVERY_OWNERSHIP[{status!r}] has empty or non-string owner: {owner!r}"
            )

    def test_owners_are_known_values(self):
        """Owner values must be one of the recognized process names."""
        known_owners = {"worker", "bridge-watchdog", "none"}
        for status, owner in RECOVERY_OWNERSHIP.items():
            assert owner in known_owners, (
                f"RECOVERY_OWNERSHIP[{status!r}] = {owner!r} is not a known owner. "
                f"Known: {known_owners}"
            )
