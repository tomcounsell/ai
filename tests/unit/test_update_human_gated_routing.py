"""Unit tests for the human-gated warn_state routing of the persona-drift and
OAuth-sync producers in scripts/update/run.py (#2893).

Both conditions are permanently unresolvable by any `/update` run — a standing
per-machine vault persona customization, and a per-machine credential only a
human can provision — so both must collapse to one emission per state
transition rather than re-emitting on every 30-minute cron cycle.

The blocks are lifted out of `run_update` by AST and executed, following
`test_update_append_warning.py`'s idiom: a test that re-types the routing in
its own body cannot observe a producer that stops routing through
`warn_state`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from scripts.update import run as run_module
from scripts.update.run import UpdateResult

pytestmark = pytest.mark.unit


def _block_by_test_source(test_source: str) -> ast.stmt:
    """The `if` statement inside `run_update` whose test renders as ``test_source``.

    Located by its condition rather than by line number, so these tests survive
    edits above the block in `run_update`.
    """
    source = Path(run_module.__file__).read_text()
    tree = ast.parse(source)
    run_update = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run_update"),
        None,
    )
    assert run_update is not None, "run_update not found as a top-level sync def in run.py"
    for node in ast.walk(run_update):
        if isinstance(node, ast.If) and (ast.get_source_segment(source, node.test) or "") == (
            test_source
        ):
            return node
    raise AssertionError(f"block with test `{test_source}` not found in run_update")


def _run_block(block: ast.stmt, project_dir: Path, **bindings) -> UpdateResult:
    """Execute one lifted block against a real warn_state file under ``project_dir``."""
    result = UpdateResult()
    namespace = dict(vars(run_module))
    namespace.update(
        {
            "result": result,
            "project_dir": project_dir,
            "v": False,
            "log": lambda *a, **k: None,
        }
    )
    namespace.update(bindings)
    exec(compile(ast.Module(body=[block], type_ignores=[]), "<block>", "exec"), namespace)
    return result


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    return tmp_path


DRIFT_WARNING = "PM persona overlay drift: 28 lines differ. Run 'diff a b' to review."


class TestPersonaDriftRouting:
    def test_first_run_warns_and_records_key(self, project_dir: Path) -> None:
        block = _block_by_test_source("_persona_warnings")
        result = _run_block(block, project_dir, _persona_warnings=[DRIFT_WARNING])

        assert result.warnings == [DRIFT_WARNING]
        assert "persona-drift" in result.warn_keys_emitted

    def test_second_identical_run_is_suppressed(self, project_dir: Path) -> None:
        block = _block_by_test_source("_persona_warnings")
        _run_block(block, project_dir, _persona_warnings=[DRIFT_WARNING])
        second = _run_block(block, project_dir, _persona_warnings=[DRIFT_WARNING])

        assert second.warnings == []
        assert second.warn_keys_emitted == set()

    def test_changed_drift_size_warns_again(self, project_dir: Path) -> None:
        block = _block_by_test_source("_persona_warnings")
        _run_block(block, project_dir, _persona_warnings=[DRIFT_WARNING])
        changed = _run_block(
            block,
            project_dir,
            _persona_warnings=["PM persona overlay drift: 31 lines differ. Run 'diff a b'."],
        )

        assert len(changed.warnings) == 1

    def test_suppressed_condition_is_retrievable(self, project_dir: Path) -> None:
        block = _block_by_test_source("_persona_warnings")
        _run_block(block, project_dir, _persona_warnings=[DRIFT_WARNING])

        assert "persona-drift" in run_module.warn_state.active(project_dir)

    def test_resolution_clears_state_and_notes_once(self, project_dir: Path) -> None:
        drift_block = _block_by_test_source("_persona_warnings")
        _run_block(drift_block, project_dir, _persona_warnings=[DRIFT_WARNING])

        resolved = _run_block(drift_block, project_dir, _persona_warnings=[])
        assert "persona-drift" in resolved.warn_keys_emitted
        assert resolved.warnings == []
        assert run_module.warn_state.active(project_dir) == {}

        # A second in-sync run stays silent.
        again = _run_block(drift_block, project_dir, _persona_warnings=[])
        assert again.warn_keys_emitted == set()


NO_SOURCE = "No source credentials at ~/Desktop/Valor/claude_oauth_config.json"


class TestOAuthSyncRouting:
    def test_first_run_warns_and_records_key(self, project_dir: Path) -> None:
        block = _block_by_test_source('oauth_sync.get("synced")')
        result = _run_block(block, project_dir, oauth_sync={"synced": False, "reason": NO_SOURCE})

        assert result.warnings == [f"OAuth sync: {NO_SOURCE}"]
        assert "oauth-sync" in result.warn_keys_emitted

    def test_second_identical_run_is_suppressed(self, project_dir: Path) -> None:
        block = _block_by_test_source('oauth_sync.get("synced")')
        payload = {"synced": False, "reason": NO_SOURCE}
        _run_block(block, project_dir, oauth_sync=dict(payload))
        second = _run_block(block, project_dir, oauth_sync=dict(payload))

        assert second.warnings == []
        assert second.warn_keys_emitted == set()

    def test_changed_reason_warns_again(self, project_dir: Path) -> None:
        block = _block_by_test_source('oauth_sync.get("synced")')
        _run_block(block, project_dir, oauth_sync={"synced": False, "reason": NO_SOURCE})
        changed = _run_block(
            block, project_dir, oauth_sync={"synced": False, "reason": "source file unreadable"}
        )

        assert len(changed.warnings) == 1

    def test_suppressed_condition_is_retrievable(self, project_dir: Path) -> None:
        block = _block_by_test_source('oauth_sync.get("synced")')
        _run_block(block, project_dir, oauth_sync={"synced": False, "reason": NO_SOURCE})

        assert "oauth-sync" in run_module.warn_state.active(project_dir)

    def test_resolution_clears_state_and_notes_once(self, project_dir: Path) -> None:
        block = _block_by_test_source('oauth_sync.get("synced")')
        _run_block(block, project_dir, oauth_sync={"synced": False, "reason": NO_SOURCE})

        resolved = _run_block(
            block, project_dir, oauth_sync={"synced": True, "reason": "credentials in sync"}
        )
        assert "oauth-sync" in resolved.warn_keys_emitted
        assert resolved.warnings == []
        assert run_module.warn_state.active(project_dir) == {}

        again = _run_block(
            block, project_dir, oauth_sync={"synced": True, "reason": "credentials in sync"}
        )
        assert again.warn_keys_emitted == set()
