"""Unit tests for the valor-ingest CLI (``tools.valor_ingest``)."""

from __future__ import annotations

import urllib.error
from pathlib import Path

import pytest

from tools import valor_ingest


@pytest.fixture
def html_source(tmp_path: Path) -> Path:
    src = tmp_path / "doc.html"
    src.write_text(
        "<html><body><h1>Title</h1></body></html>",
        encoding="utf-8",
    )
    return src


@pytest.mark.unit
class TestArgparseEdgeCases:
    def test_no_args_is_error(self, capsys):
        with pytest.raises(SystemExit) as exc:
            valor_ingest.main([])
        assert exc.value.code == 2
        captured = capsys.readouterr()
        assert "required" in captured.err.lower()

    def test_scan_and_source_mutually_exclusive(self, tmp_path: Path):
        with pytest.raises(SystemExit) as exc:
            valor_ingest.main([str(tmp_path / "f.pdf"), "--scan", str(tmp_path)])
        assert exc.value.code == 2

    def test_scan_with_vault_subdir_rejected(self, tmp_path: Path, capsys):
        with pytest.raises(SystemExit) as exc:
            valor_ingest.main(["--scan", str(tmp_path), "--vault-subdir", "Consulting"])
        assert exc.value.code == 2
        assert "cannot be combined" in capsys.readouterr().err

    def test_scan_with_output_rejected(self, tmp_path: Path, capsys):
        with pytest.raises(SystemExit) as exc:
            valor_ingest.main(["--scan", str(tmp_path), "--output", "/tmp/foo.md"])
        assert exc.value.code == 2
        assert "cannot be combined" in capsys.readouterr().err


@pytest.mark.unit
class TestSingleSource:
    def test_local_source_produces_sidecar(self, html_source: Path, capsys):
        rc = valor_ingest.main([str(html_source)])
        assert rc == 0
        expected = html_source.with_name(html_source.name + ".md")
        assert expected.exists()
        out = capsys.readouterr().out
        assert str(expected) in out

    def test_missing_source_returns_1(self, tmp_path: Path, capsys):
        rc = valor_ingest.main([str(tmp_path / "missing.pdf")])
        assert rc == 1
        assert "source not found" in capsys.readouterr().err

    def test_unconvertible_extension_exits_zero_with_notice(self, tmp_path: Path, capsys):
        src = tmp_path / "data.json"
        src.write_text("{}")
        rc = valor_ingest.main([str(src)])
        assert rc == 0
        err = capsys.readouterr().err
        assert "no sidecar generated" in err

    def test_force_flag_passed_through(self, html_source: Path, monkeypatch):
        seen = []

        def fake_convert(path, *, force: bool = False):
            seen.append(force)
            return path.with_name(path.name + ".md")

        monkeypatch.setattr(valor_ingest, "convert_to_sidecar", fake_convert)
        # Prime the sidecar so the CLI doesn't print a missing message.
        html_source.with_name(html_source.name + ".md").write_text("stub")

        valor_ingest.main([str(html_source), "--force"])
        assert seen == [True]


@pytest.mark.unit
class TestScanMode:
    def test_scan_converts_recursively(self, tmp_path: Path, capsys):
        (tmp_path / "top.html").write_text("<html><body>A</body></html>")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "nested.html").write_text("<html><body>B</body></html>")

        rc = valor_ingest.main(["--scan", str(tmp_path)])
        assert rc == 0
        assert (tmp_path / "top.html.md").exists()
        assert (sub / "nested.html.md").exists()
        assert "2 converted" in capsys.readouterr().out

    def test_scan_empty_dir(self, tmp_path: Path, capsys):
        rc = valor_ingest.main(["--scan", str(tmp_path)])
        assert rc == 0
        assert "0 converted" in capsys.readouterr().out

    def test_scan_skips_hidden_dirs(self, tmp_path: Path):
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / "a.html").write_text("<html></html>")
        (tmp_path / "visible.html").write_text("<html><body>visible</body></html>")
        valor_ingest.main(["--scan", str(tmp_path)])
        assert (tmp_path / "visible.html.md").exists()
        assert not (hidden / "a.html.md").exists()

    def test_scan_skips_archive_dirs(self, tmp_path: Path):
        arch = tmp_path / "_archive_"
        arch.mkdir()
        (arch / "old.html").write_text("<html></html>")
        valor_ingest.main(["--scan", str(tmp_path)])
        assert not (arch / "old.html.md").exists()

    def test_scan_ignores_non_convertible_extensions(self, tmp_path: Path, capsys):
        (tmp_path / "readme.txt").write_text("not convertible via markitdown")
        (tmp_path / "notes.md").write_text("already md")
        rc = valor_ingest.main(["--scan", str(tmp_path)])
        assert rc == 0
        # No sidecars produced.
        assert not (tmp_path / "readme.txt.md").exists()
        assert not (tmp_path / "notes.md.md").exists()
        assert "0 converted" in capsys.readouterr().out

    def test_scan_missing_dir_returns_1(self, tmp_path: Path, capsys):
        rc = valor_ingest.main(["--scan", str(tmp_path / "nope")])
        assert rc == 1
        assert "not a directory" in capsys.readouterr().err


@pytest.mark.unit
class TestUrlDispatch:
    def test_youtube_url_detected(self):
        assert valor_ingest._is_youtube_url("https://www.youtube.com/watch?v=abc123")
        assert valor_ingest._is_youtube_url("https://youtu.be/abc123")
        assert not valor_ingest._is_youtube_url("https://example.com/watch?v=abc")

    def test_url_vs_path_classification(self):
        assert valor_ingest._looks_like_url("https://example.com/x.pdf")
        assert valor_ingest._looks_like_url("http://localhost:8000/doc")
        assert not valor_ingest._looks_like_url("/absolute/path/to/file.pdf")
        assert not valor_ingest._looks_like_url("./relative/file.html")

    def test_offline_url_clean_error(self, monkeypatch, capsys):
        """Offline URL fetch raises URLError; main() should print a clean
        stderr message and return exit code 1 — not a Python traceback.
        """

        def fake_download(url, *, dest_dir):
            raise urllib.error.URLError("Network is unreachable")

        monkeypatch.setattr(valor_ingest, "_download_url_to_tempfile", fake_download)

        rc = valor_ingest.main(["https://example.com/somefile.pdf"])
        assert rc == 1

        captured = capsys.readouterr()
        # Clean error message on stderr.
        assert "Network error" in captured.err
        assert "https://example.com/somefile.pdf" in captured.err
        assert "Network is unreachable" in captured.err
        # No Python traceback.
        assert "Traceback" not in captured.err
        assert "URLError" not in captured.err

    def test_offline_url_http_error_clean_message(self, monkeypatch, capsys):
        """HTTPError (subclass of URLError) gets a clearer message that
        includes the HTTP status code, with no traceback.
        """

        def fake_download(url, *, dest_dir):
            raise urllib.error.HTTPError(
                url=url,
                code=404,
                msg="Not Found",
                hdrs=None,
                fp=None,
            )

        monkeypatch.setattr(valor_ingest, "_download_url_to_tempfile", fake_download)

        rc = valor_ingest.main(["https://example.com/missing.pdf"])
        assert rc == 1

        captured = capsys.readouterr()
        assert "HTTP error" in captured.err
        assert "404" in captured.err
        assert "Traceback" not in captured.err
