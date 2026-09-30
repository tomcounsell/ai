"""Guards the frozen lease-helper binding hazard (#2469 root cause, #2637).

``resolve_ledger_lease`` / ``revalidate_ledger_lease`` live in
``tools._sdlc_utils``. When a consumer pulls them in with a module-level
``from tools._sdlc_utils import resolve_ledger_lease``, Python snapshots the
function object into that consumer's globals at import time. If the
consumer's FIRST import happens while some other test file holds
``tools._sdlc_utils.resolve_ledger_lease`` patched, the ``MagicMock`` is
frozen into those globals for the remaining life of the process, and every
lease assertion routed through that consumer silently inverts. That is the
#2469 failure, and it cost a full debugging session because the symptom
(a lease check passing when it should refuse) looks like Redis state.

The fix is structural: consumers call through the module object
(``_sdlc_utils.resolve_ledger_lease(...)``) so the name is resolved at call
time and there is no snapshot to freeze. These tests hold that shape:

1. ``test_lease_helper_is_not_snapshotted_into_consumer_globals`` — the
   consumer module must not own a binding for the helper at all.
2. ``test_no_module_level_from_import_of_lease_helpers`` — repo-wide AST
   sweep, so a NEW consumer cannot reopen the hazard.
3. ``TestLatePatchIsHonored`` — behavioral: a patch applied AFTER the
   consumer is imported must reach the consumer's write path. This is the
   property the structural checks exist to protect, asserted directly.

Function-local imports inside a call (``tools/sdlc_verdict.py``,
``tools/sdlc_review_finalize.py``, ``agent/pipeline_state.py``) re-resolve
on every call and are not part of the hazard.
"""

from __future__ import annotations

import argparse
import ast
import importlib
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

LEASE_HELPERS = ("resolve_ledger_lease", "revalidate_ledger_lease")

#: Modules that call the lease helpers on a write path.
LEASE_CONSUMERS = (
    "tools.sdlc_dispatch",
    "tools.sdlc_meta_set",
    "tools.sdlc_stage_marker",
)

_SEARCH_ROOTS = ("agent", "bridge", "models", "tools", "worker")


@pytest.mark.parametrize("module_name", LEASE_CONSUMERS)
@pytest.mark.parametrize("helper", LEASE_HELPERS)
def test_lease_helper_is_not_snapshotted_into_consumer_globals(module_name: str, helper: str):
    """The consumer must own no binding for the helper (#2469, #2637)."""
    module = importlib.import_module(module_name)

    assert helper not in vars(module), (
        f"{module_name} has its own `{helper}` binding, which means it was "
        f"pulled in with a module-level `from tools._sdlc_utils import "
        f"{helper}`. That snapshot freezes whatever object was live at first "
        f"import — a MagicMock if any patch was active — for the life of the "
        f"process (#2469). Import the module instead: "
        f"`from tools import _sdlc_utils` and call "
        f"`_sdlc_utils.{helper}(...)`."
    )


def _module_level_lease_imports(path: Path) -> list[tuple[int, str]]:
    """Return (lineno, helper) for module-level `from`-imports of the helpers."""
    tree = ast.parse(path.read_text(), filename=str(path))
    offenders: list[tuple[int, str]] = []
    for node in tree.body:  # module level ONLY — function-local imports are safe
        if not isinstance(node, ast.ImportFrom) or node.module != "tools._sdlc_utils":
            continue
        for alias in node.names:
            if alias.name in LEASE_HELPERS:
                offenders.append((node.lineno, alias.name))
    return offenders


def test_no_module_level_from_import_of_lease_helpers():
    """Repo-wide: nobody may re-open the hazard in a new module."""
    offenders: list[str] = []
    for root in _SEARCH_ROOTS:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            for lineno, helper in _module_level_lease_imports(path):
                offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{lineno} ({helper})")

    assert not offenders, (
        "These modules snapshot a lease helper into their globals with a "
        "module-level `from tools._sdlc_utils import ...`, re-opening the "
        "#2469 frozen-mock hazard:\n  " + "\n  ".join(offenders) + "\n"
        "Use `from tools import _sdlc_utils` and call "
        "`_sdlc_utils.<helper>(...)` so the name resolves at call time."
    )


class TestLatePatchIsHonored:
    """A patch applied after import must reach the consumer's write path.

    Each test patches ``tools._sdlc_utils.resolve_ledger_lease`` to return a
    sentinel the real helper can never produce, then asserts the sentinel
    surfaces in the consumer's return value. On the pre-#2637 snapshot
    binding the patch is invisible to the consumer and the real helper runs
    instead, so the sentinel never appears.
    """

    SENTINEL = {"reason": "LEASE_ABSENT", "sentinel_2637": "late-patch-honored"}

    def test_dispatch_record_honors_late_patch(self):
        from tools import sdlc_dispatch

        args = argparse.Namespace(
            issue_number=999999,
            run_id="a" * 32,
            skill="/do-build",
            pr_number=None,
        )
        with patch(
            "tools._sdlc_utils.resolve_ledger_lease", return_value=(None, dict(self.SENTINEL))
        ):
            result = sdlc_dispatch._cli_record(args)

        assert result.get("sentinel_2637") == "late-patch-honored", (
            f"tools.sdlc_dispatch ignored a late patch of resolve_ledger_lease; got {result!r}"
        )

    def test_meta_set_honors_late_patch(self):
        from tools import sdlc_meta_set

        with patch(
            "tools._sdlc_utils.resolve_ledger_lease", return_value=(None, dict(self.SENTINEL))
        ):
            result = sdlc_meta_set.write_meta(
                key="plan_revising",
                value="true",
                issue_number=999999,
                run_id="a" * 32,
            )

        assert result.get("sentinel_2637") == "late-patch-honored", (
            f"tools.sdlc_meta_set ignored a late patch of resolve_ledger_lease; got {result!r}"
        )

    def test_stage_marker_honors_late_patch(self):
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch(
                "tools._sdlc_utils.resolve_ledger_lease",
                return_value=(None, dict(self.SENTINEL)),
            ),
        ):
            result, code = write_marker(
                stage="PLAN", status="completed", issue_number=999999, run_id="a" * 32
            )

        assert code == 1
        assert result.get("sentinel_2637") == "late-patch-honored", (
            f"tools.sdlc_stage_marker ignored a late patch of resolve_ledger_lease; got {result!r}"
        )
