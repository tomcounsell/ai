"""Tests for the code impact finder tool."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# TestChunkPython — AST-based Python chunking
# ---------------------------------------------------------------------------


class TestChunkPython:
    def test_chunk_python_functions(self):
        """Top-level functions become individual chunks."""
        from tools.code_impact_finder import _chunk_python

        code = "def foo():\n    return 1\n\ndef bar(x):\n    return x + 1\n"
        chunks = _chunk_python(code, "example.py")

        # Should have preamble (empty, but still counted if no preamble code)
        # plus two function chunks
        func_chunks = [c for c in chunks if c["section"].startswith("def ")]
        assert len(func_chunks) == 2

        sections = {c["section"] for c in func_chunks}
        assert "def foo" in sections
        assert "def bar" in sections

        for c in chunks:
            assert c["path"] == "example.py"
            assert "content_hash" in c
            assert len(c["content_hash"]) == 64  # SHA-256 hex

    def test_chunk_python_classes(self):
        """Class gets full-body chunk plus per-method chunks."""
        from tools.code_impact_finder import _chunk_python

        code = (
            "class MyClass:\n"
            "    def method_a(self):\n"
            "        pass\n"
            "\n"
            "    def method_b(self):\n"
            "        return 42\n"
        )
        chunks = _chunk_python(code, "cls.py")

        # Should have: class MyClass (full body) + class MyClass.method_a + class MyClass.method_b
        class_chunks = [c for c in chunks if "MyClass" in c["section"]]
        assert len(class_chunks) == 3

        sections = {c["section"] for c in class_chunks}
        assert "class MyClass" in sections
        assert "class MyClass.method_a" in sections
        assert "class MyClass.method_b" in sections

    def test_chunk_python_preamble(self):
        """Imports and constants before first def/class go into preamble chunk."""
        from tools.code_impact_finder import _chunk_python

        code = "import os\nimport sys\n\nCONSTANT = 42\n\ndef main():\n    pass\n"
        chunks = _chunk_python(code, "mod.py")

        preamble = [c for c in chunks if c["section"] == ""]
        assert len(preamble) == 1
        assert "import os" in preamble[0]["content"]
        assert "CONSTANT = 42" in preamble[0]["content"]

    def test_chunk_python_syntax_error(self):
        """Syntax errors fall back to single chunk."""
        from tools.code_impact_finder import _chunk_python

        code = "def broken(\n    # missing close paren and colon"
        chunks = _chunk_python(code, "broken.py")

        assert len(chunks) == 1
        assert chunks[0]["section"] == ""
        assert chunks[0]["path"] == "broken.py"
        assert "def broken" in chunks[0]["content"]

    def test_chunk_python_decorators(self):
        """Decorators are included with their function."""
        from tools.code_impact_finder import _chunk_python

        code = "@staticmethod\n@some_decorator\ndef decorated():\n    pass\n"
        chunks = _chunk_python(code, "deco.py")

        func_chunks = [c for c in chunks if c["section"] == "def decorated"]
        assert len(func_chunks) == 1
        assert "@staticmethod" in func_chunks[0]["content"]
        assert "@some_decorator" in func_chunks[0]["content"]


# ---------------------------------------------------------------------------
# TestChunkConfig — config and shell chunking
# ---------------------------------------------------------------------------


class TestChunkConfig:
    def test_chunk_small_json(self):
        """Small config file produces single chunk."""
        from tools.code_impact_finder import _chunk_config

        content = '{"key": "value", "another": 123}'
        chunks = _chunk_config(content, "config/small.json")

        assert len(chunks) == 1
        assert chunks[0]["path"] == "config/small.json"
        assert '"key"' in chunks[0]["content"]

    def test_chunk_shell_functions(self):
        """Shell functions are split into individual chunks."""
        from tools.code_impact_finder import _chunk_shell

        content = (
            "#!/bin/bash\n"
            "set -e\n"
            "\n"
            "start() {\n"
            "    echo 'starting'\n"
            "}\n"
            "\n"
            "stop() {\n"
            "    echo 'stopping'\n"
            "}\n"
        )
        chunks = _chunk_shell(content, "scripts/service.sh")

        assert len(chunks) >= 2  # non-function preamble + functions
        sections = {c["section"] for c in chunks}
        assert "start" in sections
        assert "stop" in sections


# ---------------------------------------------------------------------------
# TestDiscovery — file discovery
# ---------------------------------------------------------------------------


class TestDiscovery:
    def test_discover_excludes_venv(self, tmp_path):
        """Files under .venv are excluded."""
        from tools.code_impact_finder import _discover_code_files

        # Create files
        (tmp_path / "main.py").write_text("print('hello')")
        venv = tmp_path / ".venv" / "lib"
        venv.mkdir(parents=True)
        (venv / "something.py").write_text("import os")

        files = _discover_code_files(tmp_path)
        file_strs = [str(f) for f in files]

        assert any("main.py" in f for f in file_strs)
        assert not any(".venv" in f for f in file_strs)

    def test_discover_finds_python(self, tmp_path):
        """.py files are discovered."""
        from tools.code_impact_finder import _discover_code_files

        (tmp_path / "app.py").write_text("x = 1")
        sub = tmp_path / "pkg"
        sub.mkdir()
        (sub / "mod.py").write_text("y = 2")

        files = _discover_code_files(tmp_path)
        names = [f.name for f in files]

        assert "app.py" in names
        assert "mod.py" in names


# ---------------------------------------------------------------------------
# TestAffectedCode — output model and impact type classification
# ---------------------------------------------------------------------------


class TestAffectedCode:
    def test_impact_type_classification(self):
        """Verify test/config/docs/modify classification based on file path."""
        from tools.code_impact_finder import _classify_impact_type

        assert _classify_impact_type("tests/test_something.py") == "test"
        assert _classify_impact_type("config/settings.json") == "config"
        assert _classify_impact_type(".mcp.json") == "config"
        assert _classify_impact_type("pyproject.toml") == "config"
        assert _classify_impact_type("docs/features/auth.md") == "docs"
        assert _classify_impact_type("bridge/telegram_bridge.py") == "modify"
        assert _classify_impact_type("tools/impact_finder_core.py") == "modify"

    def test_affected_code_model(self):
        """AffectedCode model validates and stores fields correctly."""
        from tools.code_impact_finder import AffectedCode

        item = AffectedCode(
            path="bridge/telegram_bridge.py",
            section="def handle_message",
            relevance=0.8,
            impact_type="modify",
            reason="Reads session_id which is being restructured",
        )
        assert item.path == "bridge/telegram_bridge.py"
        assert item.section == "def handle_message"
        assert item.relevance == 0.8
        assert item.impact_type == "modify"

    def test_haiku_impact_type_overrides_path_classification(self):
        """Haiku-provided impact_type takes precedence over path-based classification."""
        from tools.code_impact_finder import _build_affected_code

        results = [
            (
                8.0,
                "Shares state with changed module",
                {
                    "path": "tools/some_tool.py",
                    "section": "def helper",
                    "haiku_impact_type": "dependency",
                },
            ),
            (
                7.0,
                "Directly modified",
                {
                    "path": "tools/another.py",
                    "section": "def main",
                },
            ),
        ]
        affected = _build_affected_code(results)
        assert affected[0].impact_type == "dependency"
        assert affected[1].impact_type == "modify"

    def test_invalid_haiku_impact_type_falls_back(self):
        """Invalid Haiku impact_type falls back to path-based classification."""
        from tools.code_impact_finder import _build_affected_code

        results = [
            (
                8.0,
                "Some reason",
                {
                    "path": "tests/test_foo.py",
                    "section": "def test_bar",
                    "haiku_impact_type": "banana",
                },
            ),
        ]
        affected = _build_affected_code(results)
        assert affected[0].impact_type == "test"  # path-based fallback


# ---------------------------------------------------------------------------
# TestCodeFinderPipeline — full pipeline with mocked APIs
# ---------------------------------------------------------------------------


class TestCodeFinderPipeline:
    def test_end_to_end_with_mocked_apis(self, tmp_path):
        """Full pipeline: index_code -> find_affected_code with mocked APIs."""
        from tools.code_impact_finder import (
            AffectedCode,
            find_affected_code,
            index_code,
        )

        # Create test files
        (tmp_path / "main.py").write_text(
            "import os\n\n"
            "def start():\n"
            "    print('starting')\n\n"
            "def stop():\n"
            "    print('stopping')\n"
        )
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_main.py").write_text("def test_start():\n    assert True\n")

        # Deterministic fake embeddings
        def fake_embed(texts):
            embeddings = []
            for text in texts:
                h = hashlib.md5(text.encode()).hexdigest()
                vec = [int(c, 16) / 15.0 for c in h]
                embeddings.append(vec)
            return embeddings

        # Mock Haiku reranker — patches the core single-candidate reranker
        def mock_rerank(client, prompt, chunk):
            return (8.0, "Relevant to the change", chunk)

        # Step 1: Index code with mocked embeddings
        with patch.dict("os.environ", {"OPENAI_API_KEY": "fake-key"}, clear=False):
            with patch("tools.impact_finder_core._embed_openai", side_effect=fake_embed):
                index = index_code(repo_root=tmp_path)

        assert index["version"] == 1
        assert len(index["chunks"]) > 0
        assert index["model"] == "text-embedding-3-small"

        # Step 2: Find affected code with mocked embeddings + reranker
        with patch.dict("os.environ", {"OPENAI_API_KEY": "fake-key"}, clear=False):
            with patch("tools.impact_finder_core._embed_openai", side_effect=fake_embed):
                with patch(
                    "tools.impact_finder_core._rerank_single_candidate",
                    side_effect=mock_rerank,
                ):
                    results = find_affected_code(
                        "Changed the start function to accept arguments",
                        repo_root=tmp_path,
                    )

        assert isinstance(results, list)
        for r in results:
            assert isinstance(r, AffectedCode)
            assert r.relevance > 0
            assert r.impact_type in ("modify", "test", "config", "docs", "dependency")
            assert len(r.reason) > 0

    def test_graceful_degradation_no_api_key(self):
        """With no embedding keys, find_affected_code returns empty list."""
        from tools.code_impact_finder import find_affected_code

        env = {
            k: v
            for k, v in __import__("os").environ.items()
            if k not in ("OPENAI_API_KEY", "VOYAGE_API_KEY", "ANTHROPIC_API_KEY")
        }
        with patch.dict("os.environ", env, clear=True):
            result = find_affected_code(
                "Some change",
                repo_root=Path("/nonexistent"),
            )
            assert result == []


# ---------------------------------------------------------------------------
# TestChunkCodeFile — routing by extension
# ---------------------------------------------------------------------------


class TestChunkCodeFile:
    def test_routes_python(self):
        """Python files are routed to _chunk_python."""
        from tools.code_impact_finder import chunk_code_file

        code = "def hello():\n    pass\n"
        chunks = chunk_code_file(code, "app.py")
        # Should use AST-based chunking
        func_chunks = [c for c in chunks if "hello" in c.get("section", "")]
        assert len(func_chunks) >= 1

    def test_routes_markdown(self):
        """Markdown files are routed to chunk_markdown."""
        from tools.code_impact_finder import chunk_code_file

        md = "# Title\n\n## Section A\n\nContent A\n\n## Section B\n\nContent B\n"
        chunks = chunk_code_file(md, "docs/readme.md")
        assert len(chunks) == 3  # preamble + 2 sections

    def test_routes_json(self):
        """JSON files are routed to _chunk_config."""
        from tools.code_impact_finder import chunk_code_file

        content = '{"key": "value"}'
        chunks = chunk_code_file(content, "config/settings.json")
        assert len(chunks) >= 1
        assert chunks[0]["path"] == "config/settings.json"

    def test_routes_unknown_extension(self):
        """Unknown extensions produce a single chunk."""
        from tools.code_impact_finder import chunk_code_file

        content = "some random content here"
        chunks = chunk_code_file(content, "Makefile")
        assert len(chunks) == 1
        assert chunks[0]["path"] == "Makefile"
        assert chunks[0]["section"] == ""
