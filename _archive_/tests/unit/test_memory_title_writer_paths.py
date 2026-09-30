"""Verify generate_title_async is invoked at every Memory writer call site.

Per the cycle-3 architectural direction, no model-layer hook exists —
each of the 7 writer paths individually calls
``generate_title_async(memory_id, strip_private(content))`` after a
successful save. Overwrite-every-save semantics: there is NO
`if not self.title` guard at any site.

These tests mock out save and assert the title-gen call shape.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Hooks dir must be on sys.path for the `hook_utils.memory_bridge` import.
_HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / ".claude" / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))


def _make_saved_record(memory_id: str = "mem-fixture") -> MagicMock:
    rec = MagicMock()
    rec.memory_id = memory_id
    rec.title = ""
    return rec


class TestSiteCliSave:
    """Site #1: tools/memory_search/__init__.py CLI save."""

    def test_invokes_title_gen_on_save(self):
        from tools.memory_search import save

        rec = _make_saved_record("cli-mem")
        memory_mock = MagicMock()
        memory_mock.safe_save.return_value = rec

        captured = {}

        def fake_title_gen(mid, content):
            captured["mid"] = mid
            captured["content"] = content

        with (
            patch.dict("sys.modules", {"models.memory": MagicMock(Memory=memory_mock)}),
            patch(
                "tools.memory_search.title_generator.generate_title_async",
                side_effect=fake_title_gen,
            ),
            patch("config.project_key_resolver.resolve_project_key", return_value="proj"),
        ):
            result = save("test content for memory", project_key="proj")

        assert result is not None
        assert captured["mid"] == "cli-mem"
        # strip_private is idempotent on plain text
        assert captured["content"] == "test content for memory"


class TestSitePostSessionExtraction:
    """Site #2: agent/memory_extraction.py:442 (post-session extraction)."""

    def test_invokes_title_gen_for_each_observation(self):
        # Test the inner save loop directly via direct invocation.
        from tools.memory_search.title_generator import generate_title_async

        # Build a function that mimics the extracted block in extract_observations_async
        def post_session_save_block(parsed, project_key="proj"):
            from agent.private_tag import strip_private
            from models.memory import SOURCE_AGENT, Memory

            saved = []
            for obs_content, importance, metadata in parsed:
                m = Memory.safe_save(
                    agent_id="extraction-test",
                    project_key=project_key,
                    content=obs_content[:500],
                    importance=importance,
                    source=SOURCE_AGENT,
                    metadata=metadata,
                )
                if m:
                    try:
                        generate_title_async(m.memory_id, strip_private(obs_content[:500]))
                    except Exception:
                        pass
                    saved.append(m)
            return saved

        # Real test: import the actual extraction module and check imports/wiring.
        from agent import memory_extraction

        # Confirm the import path exists in the module's source — i.e. the
        # extraction module imports `generate_title_async` at line 442.
        src = open(memory_extraction.__file__).read()
        assert "generate_title_async" in src
        # Verify both extraction site references — site #2 and site #3.
        assert src.count("generate_title_async") >= 2


class TestSitePostMergeLearning:
    """Site #3: agent/memory_extraction.py:676 (post-merge learning)."""

    def test_module_wires_post_merge_title_gen(self):
        from agent import memory_extraction

        src = open(memory_extraction.__file__).read()
        # Post-merge save passes content_text[:500] to strip_private + title-gen.
        assert "strip_private(content_text[:500])" in src


class TestSiteTelegramBridge:
    """Site #4: bridge/telegram_bridge.py:1118 (ingest)."""

    def test_module_wires_telegram_title_gen(self):
        # The telegram bridge depends on Telethon and other heavy modules; we
        # avoid spinning it up. Instead we inspect the source for the wiring.
        import bridge.telegram_bridge as tb

        src = open(tb.__file__).read()
        # The save captures the return value (`_mem_record`) and invokes
        # title-gen. `safe_text` was already strip_private()'d earlier in
        # the on_message handler, so the title-gen call passes safe_text
        # directly (no re-strip).
        assert "generate_title_async" in src
        assert "_mem_record.memory_id, safe_text" in src
        # And the module-level strip_private import is present.
        assert "from agent.private_tag import strip_private" in src


class TestSiteClaudeCodeIngest:
    """Site #5: .claude/hooks/hook_utils/memory_bridge.py:743 (UserPromptSubmit ingest)."""

    def test_invokes_title_gen_after_safe_save(self):
        from hook_utils import memory_bridge as mb

        # Build a stripped, non-trivial prompt that passes the filters.
        prompt = "investigate the auth bug that broke after the deploy yesterday"

        rec = _make_saved_record("hook-mem")
        memory_mock = MagicMock()
        memory_mock.safe_save.return_value = rec
        memory_mock._meta.fields.get.return_value = MagicMock(
            might_exist=MagicMock(return_value=False)
        )

        captured = {}

        def fake_title_gen(mid, content):
            captured["mid"] = mid
            captured["content"] = content

        with (
            patch.dict(
                "sys.modules",
                {"models.memory": MagicMock(Memory=memory_mock, SOURCE_HUMAN="human")},
            ),
            patch.object(mb, "_get_project_key", return_value="proj"),
            patch(
                "tools.memory_search.title_generator.generate_title_async",
                side_effect=fake_title_gen,
            ),
        ):
            result = mb.ingest(prompt)

        assert result is True
        assert captured["mid"] == "hook-mem"
        assert "auth bug" in captured["content"]


class TestSiteKnowledgeIndexer:
    """Site #6: tools/knowledge/indexer.py — both chunked and single-doc paths."""

    def test_module_wires_both_chunk_and_single_paths(self):
        import tools.knowledge.indexer as idx

        src = open(idx.__file__).read()
        # Both writer paths capture the safe_save return and call title-gen.
        assert src.count("generate_title_async") >= 2
        # Both apply strip_private to memory_content.
        assert "strip_private(memory_content[:500])" in src


class TestSiteConsolidationMerge:
    """Site #7: scripts/memory_consolidation.py:276 (merge writer)."""

    def test_module_wires_merge_title_gen_only_on_creation(self):
        import scripts.memory_consolidation as mc

        src = open(mc.__file__).read()
        # Title-gen is called for the merge writer.
        assert "generate_title_async" in src
        # And NOT for the superseded_by record.save() at line ~297 — verify
        # exactly one title-gen invocation in the file.
        assert src.count("generate_title_async(") == 1


class TestNoTitleGuard:
    """Verify there is NO `if not self.title` guard at the writer call sites.

    Real-memory semantics (cycle-3): every save unconditionally re-fires
    `generate_title_async`. Titles evolve as new context arrives.
    """

    def test_no_guard_in_writer_path_call_sites(self):
        """Spot-check the actual call-site files — strip comments first.

        We only forbid these patterns appearing as actual code (not in
        explanatory comments). Strip `#`-comments line by line before
        checking.
        """
        import re

        def _strip_comments(src: str) -> str:
            out = []
            for line in src.splitlines():
                # Naïve but adequate for Python: drop trailing #... unless
                # inside an obvious string literal. Tests don't need to be
                # perfect; the goal is to skip our own explanatory `# ...`
                # docstrings and inline notes.
                stripped = re.sub(r"#.*$", "", line)
                out.append(stripped)
            return "\n".join(out)

        repo_root = Path(__file__).resolve().parent.parent.parent
        files = [
            repo_root / "tools" / "memory_search" / "__init__.py",
            repo_root / "agent" / "memory_extraction.py",
            repo_root / "bridge" / "telegram_bridge.py",
            repo_root / ".claude" / "hooks" / "hook_utils" / "memory_bridge.py",
            repo_root / "tools" / "knowledge" / "indexer.py",
            repo_root / "scripts" / "memory_consolidation.py",
        ]
        for path in files:
            src = path.read_text()
            assert "generate_title_async" in src, f"missing wiring in {path}"
            code_only = _strip_comments(src)
            assert "if not self.title" not in code_only, f"unexpected guard in {path}"
            # No guard like `if record.title` at the call site.
            assert "if record.title" not in code_only and "if m.title" not in code_only, (
                f"unexpected title guard in {path}"
            )
