"""
Architectural constraint tests for the SDLC router oscillation guard.

Asserts the one-way import boundary between tools/ and agent/sdlc_router.py:
  - tools/sdlc_dispatch.py MAY import from agent/sdlc_router.py (CLI wrapper)
  - agent/sdlc_router.py MUST NOT import from tools/sdlc_dispatch.py (cycle prevention)
  - agent/sdlc_router.py MUST NOT import from tools/sdlc_verdict.py (cycle prevention)

The full tools/ -> agent/ direction is accepted (tools/sdlc_stage_query.py,
tools/sdlc_dispatch.py, etc. all import from agent/). The constraint is
specifically that the modules in agent/ which ARE imported by tools/ do not
create a cycle by importing back.
"""

import ast
import os


def _get_imports(filepath: str) -> list[str]:
    """Return all module names imported by the file at filepath."""
    with open(filepath) as fh:
        tree = ast.parse(fh.read(), filename=filepath)

    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
    return imports


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SDLC_ROUTER = os.path.join(REPO_ROOT, "agent", "sdlc_router.py")
SDLC_VERDICT = os.path.join(REPO_ROOT, "tools", "sdlc_verdict.py")
SDLC_DISPATCH = os.path.join(REPO_ROOT, "tools", "sdlc_dispatch.py")


class TestSdlcRouterImportBoundary:
    """
    agent/sdlc_router.py is the ground-truth Python reference for G1-G5 dispatch
    guards.  tools/sdlc_dispatch.py and tools/sdlc_verdict.py both import it.
    If sdlc_router.py were to import either of those tools in return, a circular
    import would occur and all three modules would fail to load.
    """

    def test_sdlc_router_does_not_import_sdlc_dispatch(self):
        """agent/sdlc_router.py must not import tools.sdlc_dispatch (cycle guard)."""
        imports = _get_imports(SDLC_ROUTER)
        assert "tools.sdlc_dispatch" not in imports, (
            "Circular import detected: agent/sdlc_router.py imports tools.sdlc_dispatch. "
            "tools/sdlc_dispatch.py imports agent.sdlc_router, so this creates a cycle."
        )

    def test_sdlc_router_does_not_import_sdlc_verdict(self):
        """agent/sdlc_router.py must not import tools.sdlc_verdict (cycle guard)."""
        imports = _get_imports(SDLC_ROUTER)
        assert "tools.sdlc_verdict" not in imports, (
            "Circular import detected: agent/sdlc_router.py imports tools.sdlc_verdict. "
            "tools/sdlc_verdict.py (and tools/sdlc_dispatch.py) import agent.sdlc_router, "
            "so this creates a cycle."
        )

    def test_sdlc_router_does_not_import_tools_package(self):
        """agent/sdlc_router.py must not import any module from the tools/ package."""
        imports = _get_imports(SDLC_ROUTER)
        tools_imports = [m for m in imports if m.startswith("tools")]
        assert tools_imports == [], (
            f"agent/sdlc_router.py imports from tools/ package: {tools_imports}. "
            "This risks creating circular imports since tools/sdlc_dispatch.py and "
            "tools/sdlc_verdict.py both import from agent.sdlc_router."
        )

    def test_sdlc_dispatch_imports_agent_sdlc_router(self):
        """Positive assertion: tools/sdlc_dispatch.py SHOULD import agent.sdlc_router."""
        imports = _get_imports(SDLC_DISPATCH)
        assert "agent.sdlc_router" in imports, (
            "tools/sdlc_dispatch.py no longer imports agent.sdlc_router. "
            "If the dispatch CLI was restructured, update this test to reflect "
            "the new boundary."
        )

    def test_sdlc_verdict_exists_and_is_parseable(self):
        """Smoke test: tools/sdlc_verdict.py must exist and be valid Python."""
        assert os.path.exists(SDLC_VERDICT), (
            "tools/sdlc_verdict.py does not exist — it is required by the "
            "single-writer invariant for _verdicts in stage_states."
        )
        # If parse fails, ast.parse raises SyntaxError
        with open(SDLC_VERDICT) as fh:
            ast.parse(fh.read(), filename=SDLC_VERDICT)

    def test_no_circular_import_via_runtime(self):
        """Runtime import of agent.sdlc_router must succeed without circular-import error."""
        # This will raise ImportError if a cycle exists
        import importlib

        mod = importlib.import_module("agent.sdlc_router")
        assert mod is not None
        assert hasattr(mod, "decide_next_dispatch"), (
            "agent.sdlc_router.decide_next_dispatch not found — the dispatch function "
            "was renamed or removed."
        )
