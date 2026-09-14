"""Promotion gate (#3218, lane 6).

Automated promotion is disabled. The gate names the two preconditions that
would have to be met before it could be enabled, checks the one that is
checkable (a human-amended charter naming the reversible surfaces) against the
pinned charter text, and reports the other (evaluator secrets and production
credentials separated from candidate execution) as unmet by construction. No
setting, env key, or file flag reads into the answer; the last test in this
file proves that by setting the flag a builder would reach for and asserting
nothing moves.

Uses the autouse ``redis_test_db`` fixture (tests/conftest.py), which claims a
per-worker test DB; production Redis is never touched. Charter rows are seeded
under a test-scoped ``project_key``.
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime

import pytest

from models import ImprovementCharter, ImprovementRelease
from models.improvement_charter import _CHARTER_PATH
from tools.improvement_release import promotion
from tools.improvement_release.promotion import (
    PRECONDITION_CHARTER_REVERSIBLE_SURFACES,
    PRECONDITION_CREDENTIAL_SEPARATION,
    PromotionDisabled,
    PromotionGate,
    charter_names_reversible_surfaces,
    promote_automatically,
    promotion_gate,
)

PK = "test-3218-promotion-gate"
BOTH = {PRECONDITION_CREDENTIAL_SEPARATION, PRECONDITION_CHARTER_REVERSIBLE_SURFACES}


@pytest.fixture
def real_charter():
    """The charter that ships, pinned in the test partition."""
    row = ImprovementCharter.load_from_file(_CHARTER_PATH, project_key=PK)
    assert row is not None, "the real charter failed to seed"
    return row


@pytest.fixture
def amended_charter(tmp_path):
    """A synthetic charter amendment that names its reversible surfaces.

    Built from the real charter's bytes so the owner check passes and the
    only difference is the section precondition 2 looks for.
    """
    text = _CHARTER_PATH.read_text(encoding="utf-8")
    amended = text + "\n## 13. Reversible surfaces\n\nPrompts under `agent/` and `config/`.\n"
    path = tmp_path / "improvement-charter.md"
    path.write_text(amended, encoding="utf-8")
    row = ImprovementCharter.load_from_file(path, project_key=PK)
    assert row is not None, "the amended charter failed to seed"
    return row


class TestGateOnTheRealCharter:
    def test_gate_refuses_with_both_preconditions_unmet(self, real_charter):
        gate = promotion_gate(PK)

        assert isinstance(gate, PromotionGate)
        assert gate.automated is False
        assert set(gate.unmet) == BOTH
        assert gate.detail[PRECONDITION_CHARTER_REVERSIBLE_SURFACES]["charter_digest"] == (
            real_charter.digest
        )

    def test_gate_refuses_with_no_pinned_charter(self):
        gate = promotion_gate("test-3218-nothing-pinned")

        assert gate.automated is False
        assert set(gate.unmet) == BOTH
        assert gate.detail[PRECONDITION_CHARTER_REVERSIBLE_SURFACES]["charter_digest"] is None

    def test_promote_automatically_raises_naming_both(self, real_charter):
        with pytest.raises(PromotionDisabled) as excinfo:
            promote_automatically("rel-123", project_key=PK)

        message = str(excinfo.value)
        assert PRECONDITION_CREDENTIAL_SEPARATION in message
        assert PRECONDITION_CHARTER_REVERSIBLE_SURFACES in message
        assert "rel-123" in message
        assert set(excinfo.value.gate.unmet) == BOTH

    def test_promote_automatically_never_writes(self, real_charter):
        release = ImprovementRelease.create(
            project_key=PK, created_at=datetime.now(UTC), state="proposed"
        )
        before = len(list(ImprovementRelease.query.filter(project_key=PK)))

        with pytest.raises(PromotionDisabled):
            promote_automatically(release.id, project_key=PK)

        rows = list(ImprovementRelease.query.filter(project_key=PK))
        assert len(rows) == before
        assert all(r.state == "proposed" for r in rows)


class TestPreconditionTwoIsCheckable:
    def test_gate_refuses_on_credential_separation_alone(self, amended_charter):
        """A charter naming its reversible surfaces clears precondition 2 only."""
        gate = promotion_gate(PK)

        assert gate.automated is False
        assert gate.unmet == (PRECONDITION_CREDENTIAL_SEPARATION,)
        detail = gate.detail[PRECONDITION_CHARTER_REVERSIBLE_SURFACES]
        assert detail["met"] is True
        assert detail["charter_digest"] == amended_charter.digest
        assert "reversible surfaces" in detail["heading"].lower()
        with pytest.raises(PromotionDisabled) as excinfo:
            promote_automatically("rel-456", project_key=PK)
        assert excinfo.value.gate.unmet == (PRECONDITION_CREDENTIAL_SEPARATION,)

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("## Reversible surfaces\n", True),
            ("# 13. REVERSIBLE SURFACES\n", True),
            ("### The reversible surfaces of this loop\n", True),
            ("#### Reversible surfaces\n", False),
            ("Reversible surfaces are named below.\n", False),
            ("## Surfaces\n\nreversible surfaces in prose\n", False),
            ("", False),
        ],
    )
    def test_heading_match_is_a_section_heading_of_depth_one_to_three(self, text, expected):
        assert charter_names_reversible_surfaces(text) is expected


class TestNothingReadsIntoTheGate:
    def test_an_env_flag_changes_nothing(self, monkeypatch, real_charter):
        before = promotion_gate(PK)

        monkeypatch.setenv("PROMOTION_ENABLED", "1")
        monkeypatch.setenv("IMPROVEMENT_PROMOTION_ENABLED", "true")
        monkeypatch.setenv("PROMOTE_AUTOMATICALLY", "yes")
        after = promotion_gate(PK)

        assert after == before
        assert after.automated is False
        with pytest.raises(PromotionDisabled):
            promote_automatically("rel-789", project_key=PK)

    def test_the_module_reads_no_environment_settings_or_flag_file(self):
        """Pinned by import graph, so a docstring naming the rule cannot trip it."""
        tree = ast.parse(inspect.getsource(promotion))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        allowed = {
            "__future__",
            "re",
            "dataclasses",
            "models.improvement_charter",
            "tools.improvement_eval.runner",
        }
        assert imported <= allowed, f"promotion.py imports {sorted(imported - allowed)}"
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "open" not in calls, "promotion.py opens a file"
