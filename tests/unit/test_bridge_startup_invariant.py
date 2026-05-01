"""Unit tests for the bridge startup invariant (#1173 Risk 3).

The invariant: `set(DM_WHITELIST) == set(DM_USER_TO_PROJECT.keys())` at bridge
startup. If they decouple in projects.json, a whitelisted sender_id could pass
should_respond_async (because it's in DM_WHITELIST) yet have no project mapping
(because it's missing from DM_USER_TO_PROJECT) — re-introducing the class of
leak we're fixing.

Note: the bridge module performs the check at import time (module-level), which
makes it tricky to re-import in a test. We replicate the exact check logic here
to verify the invariant behaves correctly. The `test_invariant_lives_in_module`
test additionally asserts the runtime check exists in the bridge source.
"""

from __future__ import annotations

import pathlib

import pytest


def _check_invariant(whitelist: set[int], mapping_keys: set[int]) -> None:
    """Replica of the bridge's startup invariant — keep in sync with
    bridge/telegram_bridge.py around line 609."""
    if set(whitelist) != set(mapping_keys):
        raise RuntimeError(
            "projects.json drift: DM_WHITELIST and DM_USER_TO_PROJECT.keys() must be equal "
            f"(whitelist={whitelist}, mapping_keys={set(mapping_keys)}). "
            "Every dms.whitelist[] entry must reference an active project."
        )


class TestStartupInvariant:
    def test_consistent_sets_pass(self):
        # No raise = pass
        _check_invariant({1, 2, 3}, {1, 2, 3})

    def test_empty_sets_pass(self):
        _check_invariant(set(), set())

    def test_whitelist_has_extra_entry_raises(self):
        with pytest.raises(RuntimeError, match="projects.json drift"):
            _check_invariant({1, 2, 3, 99}, {1, 2, 3})

    def test_mapping_has_extra_entry_raises(self):
        with pytest.raises(RuntimeError, match="projects.json drift"):
            _check_invariant({1, 2, 3}, {1, 2, 3, 99})

    def test_disjoint_sets_raise(self):
        with pytest.raises(RuntimeError, match="projects.json drift"):
            _check_invariant({1, 2}, {3, 4})

    def test_invariant_lives_in_module(self):
        """Sanity: the runtime check must exist in bridge/telegram_bridge.py and
        use raise (not bare `assert`, which is stripped under python -O — #1173 C4)."""
        bridge_src = (
            pathlib.Path(__file__).parents[2] / "bridge" / "telegram_bridge.py"
        ).read_text()
        assert "set(DM_WHITELIST) != set(DM_USER_TO_PROJECT.keys())" in bridge_src, (
            "Startup invariant missing from bridge/telegram_bridge.py — see #1173 Risk 3"
        )
        # C4: must use `raise RuntimeError`, not `assert`, since asserts are stripped
        # under python -O
        idx = bridge_src.index("set(DM_WHITELIST) != set(DM_USER_TO_PROJECT.keys())")
        # Look in a window around the check for either the raise or an assert
        window = bridge_src[max(0, idx - 200) : idx + 400]
        assert "raise RuntimeError" in window, (
            "Startup invariant must use `raise RuntimeError`, not `assert` (#1173 C4)"
        )
        assert "assert set(DM_WHITELIST)" not in bridge_src, (
            "Bare assert detected — assert is stripped under python -O (#1173 C4); "
            "use `raise RuntimeError` instead"
        )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
