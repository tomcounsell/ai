"""Unit tests for the markitdown converter module.

These tests exercise the converter against the real ``markitdown`` CLI
installed via the ``[knowledge]`` extra. Subprocess-path tests use small
HTML fixtures (no LLM required). The Haiku vision path is covered
separately in ``tests/integration/test_markitdown_haiku_vision.py`` —
that file gates the LLM probe, while these tests gate the subprocess
path, idempotency, hash logic, and loop-prevention.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.knowledge import converter
from tools.knowledge.converter import (
    CONVERTIBLE_EXTENSIONS,
    LLM_BENEFICIAL_EXTENSIONS,
    ConversionError,
    convert_to_sidecar,
)


@pytest.fixture
def html_source(tmp_path: Path) -> Path:
    src = tmp_path / "doc.html"
    src.write_text(
        "<html><body><h1>Title</h1><p>Body text.</p></body></html>",
        encoding="utf-8",
    )
    return src


@pytest.fixture(autouse=True)
def _reset_probe_cache():
    """Isolate tests from each other — force each to re-probe if they touch LLM."""
    converter.reset_llm_probe_cache()
    yield
    converter.reset_llm_probe_cache()


@pytest.mark.unit
class TestConvertibleExtensions:
    def test_image_extensions_present(self):
        """Images must be convertible (C4 Implementation Note)."""
        for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
            assert ext in CONVERTIBLE_EXTENSIONS, ext

    def test_document_extensions_present(self):
        for ext in (".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm", ".msg", ".epub"):
            assert ext in CONVERTIBLE_EXTENSIONS, ext

    def test_audio_formats_excluded(self):
        """Audio is explicitly out of scope (spike-2, Google Web Speech)."""
        for ext in (".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"):
            assert ext not in CONVERTIBLE_EXTENSIONS, ext

    def test_llm_beneficial_subset_of_convertible(self):
        assert LLM_BENEFICIAL_EXTENSIONS.issubset(CONVERTIBLE_EXTENSIONS)


@pytest.mark.unit
class TestSubprocessPath:
    def test_converts_html_to_sidecar(self, html_source: Path):
        sidecar = convert_to_sidecar(html_source)
        assert sidecar is not None
        assert sidecar.name == "doc.html.md"
        assert sidecar.exists()
        body = sidecar.read_text(encoding="utf-8")
        assert body.startswith("---\n")
        assert "generated_by: markitdown" in body
        assert "source_hash:" in body
        assert "llm_model: none" in body
        assert "Title" in body

    def test_non_convertible_extension_returns_none(self, tmp_path: Path):
        src = tmp_path / "data.json"
        src.write_text("{}")
        assert convert_to_sidecar(src) is None

    def test_md_input_returns_none(self, tmp_path: Path):
        """Loop-prevention: any .md input must short-circuit."""
        src = tmp_path / "note.md"
        src.write_text("already markdown")
        assert convert_to_sidecar(src) is None

    def test_double_md_suffix_returns_none(self, tmp_path: Path):
        """`weird.md.md` is still .md; no recursion."""
        src = tmp_path / "weird.md.md"
        src.write_text("stub")
        assert convert_to_sidecar(src) is None

    def test_missing_source_returns_none(self, tmp_path: Path):
        assert convert_to_sidecar(tmp_path / "nope.pdf") is None

    def test_zero_byte_source_returns_none(self, tmp_path: Path):
        src = tmp_path / "empty.html"
        src.write_bytes(b"")
        assert convert_to_sidecar(src) is None
        assert not (tmp_path / "empty.html.md").exists()


@pytest.mark.unit
class TestHashIdempotency:
    def test_same_content_skips_second_call(self, html_source: Path):
        first = convert_to_sidecar(html_source)
        assert first is not None
        mtime_1 = first.stat().st_mtime_ns

        # Small delay to ensure a different mtime would show if rewritten.
        import time

        time.sleep(0.01)
        second = convert_to_sidecar(html_source)
        assert second == first
        assert second.stat().st_mtime_ns == mtime_1

    def test_content_change_triggers_regeneration(self, html_source: Path):
        first = convert_to_sidecar(html_source)
        assert first is not None
        first_fm = first.read_text()
        import re

        generated_at_match = re.search(r"generated_at: (\S+)", first_fm)
        regenerated_at_match = re.search(r"regenerated_at: (\S+)", first_fm)
        assert generated_at_match and regenerated_at_match
        original_generated = generated_at_match.group(1)

        # Wait to ensure a distinct timestamp, then edit the source.
        import time

        time.sleep(1.1)
        html_source.write_text(
            "<html><body><h1>Changed</h1></body></html>",
            encoding="utf-8",
        )
        second = convert_to_sidecar(html_source)
        assert second == first
        second_fm = second.read_text()
        new_generated = re.search(r"generated_at: (\S+)", second_fm).group(1)
        new_regenerated = re.search(r"regenerated_at: (\S+)", second_fm).group(1)
        # generated_at is preserved across regens; regenerated_at advances.
        assert new_generated == original_generated
        assert new_regenerated != original_generated

    def test_force_flag_bypasses_hash_check(self, html_source: Path):
        first = convert_to_sidecar(html_source)
        assert first is not None
        first_mtime = first.stat().st_mtime_ns

        import time

        time.sleep(0.01)
        second = convert_to_sidecar(html_source, force=True)
        assert second == first
        assert second.stat().st_mtime_ns != first_mtime


@pytest.mark.unit
class TestConvertWithStatus:
    """Internal helper exposes write/skip distinction so callers can report counts."""

    def test_first_call_returns_written(self, html_source: Path):
        from tools.knowledge.converter import convert_to_sidecar_with_status

        sidecar, status = convert_to_sidecar_with_status(html_source)
        assert sidecar is not None
        assert sidecar.exists()
        assert status == "written"

    def test_second_call_with_unchanged_hash_returns_skipped_hash(self, html_source: Path):
        from tools.knowledge.converter import convert_to_sidecar_with_status

        first_sidecar, first_status = convert_to_sidecar_with_status(html_source)
        assert first_status == "written"
        second_sidecar, second_status = convert_to_sidecar_with_status(html_source)
        assert second_sidecar == first_sidecar
        assert second_status == "skipped_hash"

    def test_force_flag_returns_written_even_when_hash_matches(self, html_source: Path):
        from tools.knowledge.converter import convert_to_sidecar_with_status

        convert_to_sidecar_with_status(html_source)
        sidecar, status = convert_to_sidecar_with_status(html_source, force=True)
        assert sidecar is not None
        assert status == "written"

    def test_unconvertible_extension_returns_none_other(self, tmp_path: Path):
        from tools.knowledge.converter import convert_to_sidecar_with_status

        src = tmp_path / "data.json"
        src.write_text("{}")
        sidecar, status = convert_to_sidecar_with_status(src)
        assert sidecar is None
        assert status == "skipped_other"


@pytest.mark.unit
class TestImageSizeGuard:
    def test_oversized_image_is_skipped(self, tmp_path: Path, caplog):
        """Images over 20MB must not be routed to markitdown (C4)."""
        big = tmp_path / "huge.png"
        big.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20_000_001)
        with caplog.at_level("WARNING"):
            result = convert_to_sidecar(big)
        assert result is None
        assert any("exceeds" in r.message for r in caplog.records)
        assert not (tmp_path / "huge.png.md").exists()

    def test_undersized_image_attempted(self, tmp_path: Path, monkeypatch):
        """Images at/under 20MB are attempted via the subprocess path."""
        import subprocess

        from tools.knowledge import converter as conv

        small = tmp_path / "tiny.png"
        small.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 128)

        called = []

        def fake_run(cmd, **kwargs):
            called.append(cmd)
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="image markdown body", stderr=""
            )

        monkeypatch.setattr(conv.subprocess, "run", fake_run)
        monkeypatch.setattr(conv, "_resolve_markitdown_binary", lambda: "/fake/markitdown")
        # Ensure LLM path is disabled.
        monkeypatch.delenv("MARKITDOWN_LLM_MODEL", raising=False)

        result = convert_to_sidecar(small)
        assert result is not None
        assert len(called) == 1


@pytest.mark.unit
class TestSubprocessFailure:
    def test_nonzero_exit_raises_conversion_failed(self, tmp_path: Path, monkeypatch):
        import subprocess

        from tools.knowledge import converter as conv

        src = tmp_path / "bad.html"
        src.write_text("<html></html>")

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=1,
                stdout="",
                stderr="x" * 1000,  # over 500-char limit
            )

        monkeypatch.setattr(conv.subprocess, "run", fake_run)
        monkeypatch.setattr(conv, "_resolve_markitdown_binary", lambda: "/fake/markitdown")

        with pytest.raises(ConversionError) as exc_info:
            convert_to_sidecar(src)
        assert "exit 1" in str(exc_info.value)
        # Stderr truncated to 500 chars per C1.
        assert "x" * 500 in str(exc_info.value)
        assert "x" * 501 not in str(exc_info.value)

    def test_binary_missing_raises_conversion_failed(self, tmp_path: Path, monkeypatch):
        from tools.knowledge import converter as conv

        src = tmp_path / "x.html"
        src.write_text("<html></html>")
        monkeypatch.setattr(conv, "_resolve_markitdown_binary", lambda: None)
        with pytest.raises(ConversionError, match="not found"):
            convert_to_sidecar(src)

    def test_empty_stdout_with_stderr_raises(self, tmp_path: Path, monkeypatch):
        import subprocess

        from tools.knowledge import converter as conv

        src = tmp_path / "x.html"
        src.write_text("<html></html>")

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr="warning noise"
            )

        monkeypatch.setattr(conv.subprocess, "run", fake_run)
        monkeypatch.setattr(conv, "_resolve_markitdown_binary", lambda: "/fake/markitdown")

        with pytest.raises(ConversionError, match="empty output"):
            convert_to_sidecar(src)


@pytest.mark.unit
class TestLLMProbeCache:
    def test_probe_not_invoked_when_env_var_unset(self, tmp_path: Path, monkeypatch):
        """Without MARKITDOWN_LLM_MODEL, the probe is never consulted."""
        from tools.knowledge import converter as conv

        monkeypatch.delenv("MARKITDOWN_LLM_MODEL", raising=False)
        probe_calls = []
        monkeypatch.setattr(
            conv,
            "_probe_llm_client",
            lambda: probe_calls.append(1) or True,
        )

        src = tmp_path / "doc.html"
        src.write_text("<html><body>hi</body></html>")
        convert_to_sidecar(src)
        assert probe_calls == []

    def test_probe_failure_routes_to_subprocess(self, tmp_path: Path, monkeypatch, caplog):
        """Failed probe must fall back to subprocess with a single WARNING."""
        from tools.knowledge import converter as conv

        monkeypatch.setenv("MARKITDOWN_LLM_MODEL", conv.HAIKU)
        monkeypatch.setattr(conv, "_probe_llm_client", lambda: False)

        src = tmp_path / "img.png"
        # Write a tiny valid-ish PNG header so the subprocess path runs.
        src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 128)

        subprocess_calls = []

        def fake_run(cmd, **kwargs):
            import subprocess

            subprocess_calls.append(cmd)
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="subprocess body", stderr=""
            )

        monkeypatch.setattr(conv.subprocess, "run", fake_run)
        monkeypatch.setattr(conv, "_resolve_markitdown_binary", lambda: "/fake/markitdown")

        result = convert_to_sidecar(src)
        assert result is not None
        assert len(subprocess_calls) == 1
        # A second call must still use the subprocess path without re-probing.
        src.unlink()  # ensure re-run: hash cache miss
        src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 256)
        convert_to_sidecar(src)
        assert len(subprocess_calls) == 2


@pytest.mark.unit
class TestUnicodePath:
    def test_unicode_filename(self, tmp_path: Path):
        src = tmp_path / "日本語 doc.html"
        src.write_text("<html><body><h1>Hello</h1></body></html>", encoding="utf-8")
        result = convert_to_sidecar(src)
        assert result is not None
        assert result.name == "日本語 doc.html.md"
