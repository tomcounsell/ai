"""The local encoder weights step (#3420): the download script and ``/update``'s wrapper.

``scripts/download_local_encoder_models.py`` verifies every file's sha256
against ``config.models.LOCAL_ENCODER_FILES`` and never leaves an
unverified file in place; ``scripts/update/local_encoder.py::ensure_models``
runs it as a subprocess and surfaces a non-zero exit as a non-fatal
``failed`` result. Both run against a temp models dir
(``LOCAL_ENCODER_MODELS_DIR``) and a stubbed network.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

from config.models import LOCAL_ENCODER_FILES
from scripts.update import local_encoder as step

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "download_local_encoder_models", REPO_ROOT / "scripts" / "download_local_encoder_models.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def models_dir(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "encoder"
    monkeypatch.setenv("LOCAL_ENCODER_MODELS_DIR", str(root))
    return root


def _write_pinned(root: Path, contents: dict[str, bytes]) -> None:
    """Write files and rewrite the pin table to match their digests."""
    for filename, data in contents.items():
        path = root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


class TestDownloadScript:
    def test_mismatch_after_download_deletes_the_part_and_names_both_digests(
        self, models_dir, monkeypatch, capsys
    ):
        script = _load_script()
        filename, expected = next(iter(LOCAL_ENCODER_FILES.items()))

        def _wrong_bytes(url, tmp: Path):
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(b"not the pinned weights")

        monkeypatch.setattr(script, "_download", _wrong_bytes)
        monkeypatch.setattr(script, "LOCAL_ENCODER_FILES", {filename: expected})

        assert script.main([]) == 1

        err = capsys.readouterr().err
        actual = hashlib.sha256(b"not the pinned weights").hexdigest()
        assert f"expected {expected}" in err
        assert f"actual   {actual}" in err
        assert not (models_dir / filename).exists()
        assert not (models_dir / (filename + ".part")).exists()

    def test_verified_file_is_skipped_and_stale_file_is_refetched(
        self, models_dir, monkeypatch, capsys
    ):
        script = _load_script()
        good = b"good weights"
        pins = {"onnx/model_int8.onnx": hashlib.sha256(good).hexdigest()}
        monkeypatch.setattr(script, "LOCAL_ENCODER_FILES", pins)
        fetched: list[str] = []

        def _serve_good(url, tmp: Path):
            fetched.append(url)
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(good)

        monkeypatch.setattr(script, "_download", _serve_good)

        _write_pinned(models_dir, {"onnx/model_int8.onnx": b"stale"})
        assert script.main([]) == 0
        assert len(fetched) == 1
        assert (models_dir / "onnx/model_int8.onnx").read_bytes() == good
        assert "[stale]" in capsys.readouterr().out

        assert script.main([]) == 0
        assert len(fetched) == 1, "a verified file is never re-fetched"
        assert "[skip]" in capsys.readouterr().out

    def test_source_url_pins_the_revision(self):
        script = _load_script()
        from config.models import LOCAL_ENCODER_MODEL, LOCAL_ENCODER_REVISION

        url = script.source_url("tokenizer.json")
        assert url == (
            f"https://huggingface.co/{LOCAL_ENCODER_MODEL}/resolve/"
            f"{LOCAL_ENCODER_REVISION}/tokenizer.json"
        )


class TestEnsureModels:
    def _fake_project(self, tmp_path: Path, body: str) -> Path:
        project = tmp_path / "project"
        (project / "scripts").mkdir(parents=True)
        (project / "scripts" / "download_local_encoder_models.py").write_text(body)
        return project

    def test_failed_script_exit_is_a_non_fatal_failed_result(self, models_dir, tmp_path):
        """Failure Path, last row: the update step surfaces the script's exit 1 as a warning."""
        project = self._fake_project(
            tmp_path,
            "import sys\n"
            "print('[fail] onnx/model_int8.onnx: sha256 mismatch after download\\n"
            "       expected aaaa\\n       actual   bbbb', file=sys.stderr)\n"
            "sys.exit(1)\n",
        )

        result = step.ensure_models(project)

        assert result.success is False
        assert result.action == "failed"
        assert result.models_dir == str(models_dir)
        assert "expected aaaa" in result.error and "actual   bbbb" in result.error

    def test_verified_weights_skip_the_script(self, models_dir, tmp_path, monkeypatch):
        contents = {name: name.encode() for name in LOCAL_ENCODER_FILES}
        pins = {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}
        monkeypatch.setattr(step, "LOCAL_ENCODER_FILES", pins)
        _write_pinned(models_dir, contents)
        project = self._fake_project(tmp_path, "import sys\nsys.exit(1)\n")

        result = step.ensure_models(project)

        assert result.success is True and result.action == "skipped"

    def test_script_that_exits_zero_without_valid_weights_is_a_failure(self, models_dir, tmp_path):
        project = self._fake_project(tmp_path, "import sys\nsys.exit(0)\n")

        result = step.ensure_models(project)

        assert result.success is False
        assert "sha256" in result.error

    def test_missing_script_is_a_failure(self, models_dir, tmp_path):
        result = step.ensure_models(tmp_path / "nowhere")

        assert result.success is False
        assert "not found" in result.error

    def test_runs_the_script_with_this_interpreter(self, models_dir, tmp_path, monkeypatch):
        seen: dict = {}

        class _Done:
            returncode = 0
            stdout = stderr = ""

        def _run(argv, **kwargs):
            seen["argv"] = argv
            seen["cwd"] = kwargs.get("cwd")
            return _Done()

        monkeypatch.setattr(step.subprocess, "run", _run)
        project = self._fake_project(tmp_path, "")

        step.ensure_models(project)

        assert seen["argv"][0] == sys.executable
        assert seen["argv"][1].endswith("download_local_encoder_models.py")
        assert seen["cwd"] == project
