"""Tests for developer persona load-bearing sections.

The developer persona (config/personas/developer.md) was promoted in PR #1355
from a 34-line single-stage executor to a full SDLC-pipeline owner that can
fan out across multiple issues in parallel. The sections asserted here are
the load-bearing pieces — if any of them silently disappears, dev sessions
will roll back to the pre-promotion behavior and either halt at multi-issue
work or get stuck at the merge gate's stale-baseline guard.

These are prompt-level tests, analogous to ``test_pm_persona_guards.py``:
they validate the persona text contains the required behavioral
instructions, not that infrastructure enforces them. Two of these
substrings (``Mode 3`` and ``merge_authorized``) are also asserted by the
runtime overlay-drift guards in ``agent/sdk_client.py``; the duplication
is intentional — the tests fail at CI time, the runtime guards warn at
session startup.
"""

import pytest

DEV_PERSONA_PATH = "config/personas/developer.md"


@pytest.fixture
def dev_persona_text():
    """Load the developer persona markdown file."""
    with open(DEV_PERSONA_PATH) as f:
        return f.read()


class TestLoadBearingSections:
    """The five sections without which the promoted persona collapses."""

    def test_mode_3_parallel_orchestrator_present(self, dev_persona_text):
        """Mode 3 anchors the multi-issue parallel orchestrator playbook.

        Without this, dev sessions cannot fan out across multiple issues
        and the runtime drift guard at agent/sdk_client.py warns at startup.
        """
        assert "Mode 3" in dev_persona_text

    def test_merge_authorized_bypass_present(self, dev_persona_text):
        """merge_authorized anchors the stale-baseline bypass section.

        Without this, dev sessions halt on Full Suite Gate false positives
        (the gate expects a sentinel file under data/merge_authorized_{N}).
        The runtime drift guard at agent/sdk_client.py warns at startup.
        """
        assert "merge_authorized" in dev_persona_text

    def test_permissions_section_exists(self, dev_persona_text):
        """The persona must declare what the dev session is allowed to do."""
        assert "## Permissions" in dev_persona_text

    def test_modes_of_operation_section_exists(self, dev_persona_text):
        """The persona must enumerate Modes 1/2/3 so the session knows
        which mode applies for the current dispatch."""
        assert "## Modes of Operation" in dev_persona_text

    def test_hard_rules_section_exists(self, dev_persona_text):
        """Hard Rules apply across all modes — this section is the floor
        of dev-session behavior. It must not be removed by overlay drift."""
        assert "## Hard Rules" in dev_persona_text
