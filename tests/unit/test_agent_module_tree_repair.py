"""The ``agent.*`` module-tree repair must not change module identity (#2558).

``tests/conftest.py``'s autouse ``agent_hooks_consistency_guard`` fires when the
parent-attribute link between ``agent`` and ``agent.hooks`` is severed --
``sys.modules`` reports both loaded while ``getattr(agent, "hooks")`` raises, so
every dotted-string ``mock.patch``/``monkeypatch.setattr`` that walks that path
dies at setup.

It used to repair by evicting every ``agent.*`` key from ``sys.modules``. That
self-heals the attribute walk and introduces a quieter defect in its place. Test
modules bind names at collection time (``from agent.sdk_client import
load_persona_prompt``, the ordinary idiom, in ~50 files here), before any
eviction. Afterwards the bound function still closes over the OLD module's
``__globals__`` while ``patch("agent.sdk_client.X")`` imports and patches a
FRESH module object. The patch lands on a module nothing under test is looking
at. In ``tests/unit/test_persona_substitution.py`` that surfaced as four
failures with no explanation in any diff; a test asserting a mock was CALLED
would instead have read green while measuring nothing.

So the invariant here is narrow and load-bearing: the repair restores the
attribute tree and leaves ``sys.modules`` entries pointing at the same objects
they pointed at before.

Both tests below sever the link themselves and restore it in ``finally``, so
they leave the interpreter exactly as they found it and do not depend on test
ordering.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import agent
import agent.hooks  # noqa: F401 -- ensures the key exists so the guard's predicate can fire
from agent.sdk_client import load_persona_prompt
from tests.conftest import agent_hooks_consistency_guard, repair_agent_module_tree


def _sever_the_link() -> None:
    """Reproduce the corruption the guard exists to catch."""
    delattr(sys.modules["agent"], "hooks")


def _run_the_guard() -> None:
    """Drive the real autouse fixture body, predicate included.

    Calling the fixture rather than ``repair_agent_module_tree`` directly is
    deliberate: it pins the detection condition too, so a repair that stops
    being reached is a failure here rather than a silent no-op.
    """
    generator = agent_hooks_consistency_guard.__wrapped__()
    next(generator)


def test_the_guard_repairs_the_link_without_replacing_the_modules():
    before = {k: v for k, v in sys.modules.items() if k == "agent" or k.startswith("agent.")}
    assert "agent.sdk_client" in before, "precondition: the module under test is cached"

    _sever_the_link()
    try:
        assert not hasattr(agent, "hooks"), "precondition: the link is actually severed"
        _run_the_guard()

        for name, module in before.items():
            assert sys.modules.get(name) is module, (
                f"{name} was evicted or replaced. Every collection-time "
                f"`from {name} import ...` in the suite now closes over an orphaned "
                f'module object, and every `patch("{name}....")` targets a different one'
            )
        assert sys.modules["agent"].hooks is before["agent.hooks"], (
            "the guard did not restore the parent-attribute link"
        )
    finally:
        repair_agent_module_tree()


def test_a_collection_time_binding_still_sees_a_patch_after_a_repair(tmp_path):
    """The symptom, end to end: a patch must reach the object the test holds.

    ``load_persona_prompt`` above is bound at collection time, exactly as
    ``tests/unit/test_persona_substitution.py`` binds it. If the repair swapped
    the module out, these patches would apply to a fresh ``agent.sdk_client``
    and this call would read the real persona tree instead.
    """
    overlay = tmp_path / "customer-service.md"
    overlay.write_text("Hello customer {customer_id}, how can I help?")

    _sever_the_link()
    try:
        _run_the_guard()

        with (
            patch("agent.sdk_client.PERSONAS_OVERLAY_DIR", tmp_path),
            patch("agent.sdk_client.PERSONAS_BASE_DIR", tmp_path),
            patch("agent.sdk_client.load_identity", return_value={}),
            patch("agent.sdk_client._assemble_segments", return_value="base content\n"),
        ):
            result = load_persona_prompt(
                "customer-service", substitutions={"customer_id": "cust-42"}
            )

        assert "cust-42" in result
        assert "base content" in result, (
            "the patched _assemble_segments never ran: the bound function and the patch "
            "target are different module objects"
        )
    finally:
        repair_agent_module_tree()
