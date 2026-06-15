"""Tests for engineer persona load-bearing sections.

The engineer persona (config/personas/engineer.md) owns full SDLC pipelines
and can fan out across multiple issues in parallel. The sections asserted here
are the load-bearing pieces -- if any of them silently disappears, eng sessions
will halt at multi-issue work or get stuck at the merge gate's stale-baseline
guard.

These are prompt-level tests: they validate the persona text contains the
required behavioral instructions, not that infrastructure enforces them.
Two of these substrings (``Mode 3`` and ``merge_authorized``) are also
asserted by the runtime overlay-drift guards in ``agent/sdk_client.py``;
the duplication is intentional -- the tests fail at CI time, the runtime
guards warn at session startup.
"""

import pytest

ENG_PERSONA_PATH = "config/personas/engineer.md"


@pytest.fixture
def eng_persona_text():
    """Load the engineer persona markdown file."""
    with open(ENG_PERSONA_PATH) as f:
        return f.read()


class TestLoadBearingSections:
    """The five sections without which the promoted persona collapses."""

    def test_mode_3_parallel_orchestrator_present(self, eng_persona_text):
        """Mode 3 anchors the multi-issue parallel orchestrator playbook.

        Without this, eng sessions cannot fan out across multiple issues
        and the runtime drift guard at agent/sdk_client.py warns at startup.
        """
        assert "Mode 3" in eng_persona_text

    def test_merge_authorized_bypass_present(self, eng_persona_text):
        """merge_authorized anchors the stale-baseline bypass section.

        Without this, eng sessions halt on Full Suite Gate false positives
        (the gate expects a sentinel file under data/merge_authorized_{N}).
        The runtime drift guard at agent/sdk_client.py warns at startup.
        """
        assert "merge_authorized" in eng_persona_text

    def test_permissions_section_exists(self, eng_persona_text):
        """The persona must declare what the eng session is allowed to do."""
        assert "## Permissions" in eng_persona_text

    def test_modes_of_operation_section_exists(self, eng_persona_text):
        """The persona must enumerate Modes 1/2/3 so the session knows
        which mode applies for the current dispatch."""
        assert "## Modes of Operation" in eng_persona_text

    def test_hard_rules_section_exists(self, eng_persona_text):
        """Hard Rules apply across all modes -- this section is the floor
        of eng-session behavior. It must not be removed by overlay drift."""
        assert "## Hard Rules" in eng_persona_text
