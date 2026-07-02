"""Unit tests for the Stop-hook fidelity gate's wait logic (plan #1688, Task 0).

The live probe (``tests/granite_faults/hook_fidelity.py``) spawns a real
ollama-backed ``claude`` — these tests cover only its deterministic wait
logic, which caused a wait-budget flake in gate run 2: the harness observed
``SubagentStop`` but gave up before the slow qwen parent turn emitted its
``Stop`` (parent turns continue 2-6 min after the subagent ends).

Covered here, sub-second, no spawn:

- ``_gate_satisfied`` — pass condition is a parent ``Stop`` WITH
  ``transcript_path`` AND a ``SubagentStop`` WITH ``agent_id``/``agent_type``.
- ``_rearm_deadline`` — observing ``SubagentStop`` re-arms the wait so the
  parent turn gets its own budget after the subagent ends.
- Budget constants — overall wait accommodates both observed gate runs and
  matches the production ``hook_turn_end_wait_s`` default (600s).
"""

from __future__ import annotations

import unittest

from tests.granite_faults.hook_fidelity import (
    SUBAGENT_REARM_S,
    TURN_WAIT_S,
    _gate_satisfied,
    _rearm_deadline,
)

_STOP = {"hook_event_name": "Stop", "transcript_path": "/tmp/t.jsonl"}
_STOP_NO_TRANSCRIPT = {"hook_event_name": "Stop"}
_SUBAGENT = {
    "hook_event_name": "SubagentStop",
    "agent_id": "ab743b66aef3675dc",
    "agent_type": "general-purpose",
}
_SUBAGENT_NO_ID = {"hook_event_name": "SubagentStop"}


class TestGateSatisfied(unittest.TestCase):
    def test_both_events_with_required_fields_pass(self) -> None:
        self.assertTrue(_gate_satisfied([_SUBAGENT, _STOP]))

    def test_order_independent(self) -> None:
        self.assertTrue(_gate_satisfied([_STOP, _SUBAGENT]))

    def test_stop_alone_is_not_enough(self) -> None:
        self.assertFalse(_gate_satisfied([_STOP]))

    def test_subagent_alone_is_not_enough(self) -> None:
        # Exactly gate run 2: SubagentStop landed, parent Stop never observed.
        self.assertFalse(_gate_satisfied([_SUBAGENT]))

    def test_stop_without_transcript_path_does_not_count(self) -> None:
        self.assertFalse(_gate_satisfied([_STOP_NO_TRANSCRIPT, _SUBAGENT]))

    def test_subagent_without_agent_fields_does_not_count(self) -> None:
        self.assertFalse(_gate_satisfied([_STOP, _SUBAGENT_NO_ID]))

    def test_empty_is_false(self) -> None:
        self.assertFalse(_gate_satisfied([]))


class TestRearmDeadline(unittest.TestCase):
    def test_no_subagent_seen_keeps_deadline(self) -> None:
        self.assertEqual(_rearm_deadline(100.0, None), 100.0)

    def test_subagent_seen_extends_past_base_deadline(self) -> None:
        # SubagentStop lands 1s before the base deadline: the parent turn must
        # still get a full re-armed window (the gate run 2 flake).
        rearmed = _rearm_deadline(100.0, 99.0)
        self.assertEqual(rearmed, 99.0 + SUBAGENT_REARM_S)
        self.assertGreater(rearmed, 100.0)

    def test_rearm_never_shrinks_the_deadline(self) -> None:
        # Early SubagentStop with a generous base budget: keep the larger one.
        self.assertEqual(_rearm_deadline(1000.0, 10.0), 1000.0)


class TestBudgetConstants(unittest.TestCase):
    def test_overall_budget_matches_production_default(self) -> None:
        # Consistent with GraniteSettings.hook_turn_end_wait_s default and
        # large enough for both observed gate runs (126.2s and >308.9s).
        self.assertEqual(TURN_WAIT_S, 600.0)

    def test_rearm_covers_slow_qwen_parent_turns(self) -> None:
        # Parent turns run 2-6 min after the subagent ends.
        self.assertGreaterEqual(SUBAGENT_REARM_S, 360.0)


if __name__ == "__main__":
    unittest.main()
