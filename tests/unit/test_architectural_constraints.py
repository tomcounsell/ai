"""
Architectural constraint tests: import boundaries that no other test defends.

Two independent families live here.

**The SDLC router oscillation guard.**
Asserts the one-way import boundary between tools/ and agent/sdlc_router.py:
  - tools/sdlc_dispatch.py MAY import from agent/sdlc_router.py (CLI wrapper)
  - agent/sdlc_router.py MUST NOT import from tools/sdlc_dispatch.py (cycle prevention)
  - agent/sdlc_router.py MUST NOT import from tools/sdlc_verdict.py (cycle prevention)

The full tools/ -> agent/ direction is accepted (tools/sdlc_stage_query.py,
tools/sdlc_dispatch.py, etc. all import from agent/). The constraint is
specifically that the modules in agent/ which ARE imported by tools/ do not
create a cycle by importing back.

**The standalone tool package boundary (#2867).**
Asserts that tools/selfie, tools/sms_reader, and tools/test_scheduler import no
harness package at all, and that utils/__init__.py stays import-free so that
importing utils.utc cannot drag the harness in behind it.
"""

import ast
import os


def _iter_imports(filepath: str) -> list[tuple[str, int, int]]:
    """Return (module, lineno, level) for every import in the file at filepath.

    ``level`` is the relative-import depth: 0 for absolute imports, >0 for
    ``from .x import y``. Uses ``ast.walk``, so function-local imports are
    caught alongside top-level ones.
    """
    with open(filepath) as fh:
        tree = ast.parse(fh.read(), filename=filepath)

    imports: list[tuple[str, int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.append((node.module, node.lineno, node.level))
        elif isinstance(node, ast.Import):
            imports.extend((alias.name, node.lineno, 0) for alias in node.names)
    return imports


def _get_imports(filepath: str) -> list[str]:
    """Return all module names imported by the file at filepath."""
    return [module for module, _lineno, _level in _iter_imports(filepath)]


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SDLC_ROUTER = os.path.join(REPO_ROOT, "agent", "sdlc_router.py")
SDLC_VERDICT = os.path.join(REPO_ROOT, "tools", "sdlc_verdict.py")
SDLC_DISPATCH = os.path.join(REPO_ROOT, "tools", "sdlc_dispatch.py")

# The three tools/ packages that #2867 detached from the harness entirely.
# Deliberately NOT widened to tools/image_gen or tools/telegram_history: both
# legitimately carry cross-package dependencies this guard does not police.
STANDALONE_TOOL_PACKAGES = ("tools/selfie", "tools/sms_reader", "tools/test_scheduler")

# First path segments a standalone tool package must never import.
#
# ``config`` is the load-bearing entry. It is absent from issue #2867's own list
# and is here on measured evidence: importing any config submodule executes
# config/__init__.py, which pulls 214 modules in 94 ms against utils's 2 in 1 ms.
# That measurement is the entire reason utils/ was chosen as utc.py's home, so a
# guard permitting ``from config.paths import ...`` here would wave through the
# precise coupling the move exists to remove.
FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "bridge",
        "agent",
        "worker",
        "models",
        "config",
        "monitoring",
        "reflections",
        "analytics",
        "ui",
    }
)

UTILS_INIT = os.path.join(REPO_ROOT, "utils", "__init__.py")


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


class TestStandaloneToolPackageBoundaries:
    """
    tools/selfie, tools/sms_reader, and tools/test_scheduler are standalone CLI
    utilities. Before #2867 each had exactly one cross-package import in its whole
    source tree, and it was the shared UTC helper, which then lived in the bridge
    package — three self-contained tools chained to the Telegram I/O layer by a
    call to datetime.now().

    Moving that helper to utils/utc.py removed those edges. Nothing else keeps them removed:
    utils/__init__.py is empty by accident, not by contract, and one harness import
    added there later would silently re-couple all three packages with no test
    failing. That is what these two assertions defend.
    """

    def test_standalone_tool_packages_have_no_harness_imports(self):
        """No file in the three standalone packages may import a harness package."""
        offenders: list[str] = []
        scanned = 0

        for package in STANDALONE_TOOL_PACKAGES:
            package_dir = os.path.join(REPO_ROOT, package)
            assert os.path.isdir(package_dir), (
                f"{package} does not exist. If it was renamed or removed, update "
                "STANDALONE_TOOL_PACKAGES — do not let this guard silently scan nothing."
            )
            # os.walk covers each package's tests/ directory too, which is where a
            # harness import is most likely to sneak back in.
            for dirpath, _dirnames, filenames in os.walk(package_dir):
                for filename in sorted(filenames):
                    if not filename.endswith(".py"):
                        continue
                    filepath = os.path.join(dirpath, filename)
                    scanned += 1
                    for module, lineno, level in _iter_imports(filepath):
                        if level:
                            continue  # relative import — cannot reach another package
                        if module.split(".")[0] in FORBIDDEN_IMPORT_ROOTS:
                            rel = os.path.relpath(filepath, REPO_ROOT)
                            offenders.append(f"{rel}:{lineno} imports {module}")

        assert scanned, "Scanned zero files — the guard is measuring nothing."
        assert not offenders, (
            "Standalone tool packages must not import harness packages "
            f"({', '.join(sorted(FORBIDDEN_IMPORT_ROOTS))}):\n  "
            + "\n  ".join(offenders)
            + "\n\nThese CLIs are meant to stand alone (#2867). Move the shared helper "
            "into utils/ instead of importing the harness from here."
        )

    def test_utils_package_init_imports_nothing(self):
        """utils/__init__.py must stay import-free, by contract rather than by luck."""
        imports = [
            f"line {lineno}: {module}" for module, lineno, _level in _iter_imports(UTILS_INIT)
        ]
        assert not imports, (
            "utils/__init__.py imports something:\n  "
            + "\n  ".join(imports)
            + "\n\nutils/__init__.py executes on every ``import utils.<anything>``, so an "
            "import here is inherited by every consumer of utils.utc — silently "
            "re-coupling tools/selfie, tools/sms_reader, and tools/test_scheduler to "
            "the harness that #2867 detached them from."
        )
