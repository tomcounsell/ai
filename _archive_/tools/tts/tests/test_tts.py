"""Tests for tools.tts.

Mock-based unit tests for backend selection, availability caching, voice
fallback remapping, dispatch, and error paths. No real synthesis runs.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Configuration tests
# ---------------------------------------------------------------------------


class TestConfiguration:
    def test_manifest_exists(self):
        manifest_path = Path(__file__).parent.parent / "manifest.json"
        assert manifest_path.exists()

    def test_manifest_valid(self):
        manifest_path = Path(__file__).parent.parent / "manifest.json"
        with open(manifest_path) as f:
            manifest = json.load(f)
        assert manifest["name"] == "tts"
        assert "synthesize" in manifest["capabilities"]
        backend_names = {b["name"] for b in manifest["backends"]}
        assert "kokoro" in backend_names
        assert "openai-tts" in backend_names

    def test_readme_exists(self):
        readme = Path(__file__).parent.parent / "README.md"
        assert readme.exists()


# ---------------------------------------------------------------------------
# Public API: input validation
# ---------------------------------------------------------------------------


class TestSynthesizeValidation:
    def test_import(self):
        from tools.tts import synthesize

        assert callable(synthesize)

    def test_empty_text(self):
        from tools.tts import synthesize

        result = synthesize("", "/tmp/out.ogg")
        assert result.get("error") and "empty" in result["error"]

    def test_whitespace_text(self):
        from tools.tts import synthesize

        result = synthesize("   \n\t  ", "/tmp/out.ogg")
        assert result.get("error") and "empty" in result["error"]

    def test_text_too_long(self):
        from tools.tts import synthesize

        too_long = "a" * 5000
        result = synthesize(too_long, "/tmp/out.ogg")
        assert result.get("error") and "too long" in result["error"]

    def test_unsupported_format(self):
        from tools.tts import synthesize

        result = synthesize("hello", "/tmp/out.wav", format="wav")
        assert result.get("error") and "unsupported format" in result["error"]


# ---------------------------------------------------------------------------
# Voice resolution + fallback remap
# ---------------------------------------------------------------------------


class TestVoiceResolution:
    def test_default_resolves_canonical_kokoro(self):
        from tools.tts import _resolve_voice

        voice, err = _resolve_voice("default", "kokoro")
        assert err is None
        assert voice == "am_michael"

    def test_default_resolves_canonical_cloud(self):
        from tools.tts import _resolve_voice

        voice, err = _resolve_voice("default", "cloud")
        assert err is None
        assert voice == "nova"

    def test_native_kokoro_voice_passthrough(self):
        from tools.tts import _resolve_voice

        voice, err = _resolve_voice("af_bella", "kokoro")
        assert err is None
        assert voice == "af_bella"

    def test_native_openai_voice_passthrough(self):
        from tools.tts import _resolve_voice

        voice, err = _resolve_voice("nova", "cloud")
        assert err is None
        assert voice == "nova"

    def test_kokoro_voice_remapped_to_cloud(self):
        from tools.tts import _resolve_voice

        voice, err = _resolve_voice("af_bella", "cloud")
        assert err is None
        assert voice == "nova"  # per _VOICE_FALLBACK_MAP

    def test_openai_voice_remapped_to_kokoro(self):
        from tools.tts import _resolve_voice

        voice, err = _resolve_voice("onyx", "kokoro")
        assert err is None
        assert voice == "am_adam"

    def test_unknown_voice_returns_error(self):
        from tools.tts import _resolve_voice

        voice, err = _resolve_voice("xyzzy", "cloud")
        assert voice is None
        assert err and "unknown voice" in err["error"]


# ---------------------------------------------------------------------------
# Backend selection + dispatch + observability
# ---------------------------------------------------------------------------


class TestDispatch:
    def setup_method(self):
        # Reset module-level state between tests.
        import tools.tts as tts

        tts._kokoro_available_cache["timestamp"] = 0.0
        tts._kokoro_available_cache["available"] = False
        tts._kokoro_available_cache["reason"] = None
        tts._kokoro_warn_emitted = False

    def test_force_cloud_skips_kokoro(self, caplog):
        from tools.tts import synthesize

        caplog.set_level(logging.INFO, logger="tools.tts")
        with (
            patch("tools.tts._is_kokoro_available", return_value=True),
            patch(
                "tools.tts._synthesize_openai",
                return_value={
                    "path": "/tmp/x.ogg",
                    "duration": 0.5,
                    "backend": "cloud",
                    "voice": "nova",
                    "format": "opus",
                    "error": None,
                },
            ) as mock_cloud,
        ):
            result = synthesize("hello", "/tmp/x.ogg", force_cloud=True)
        assert result["backend"] == "cloud"
        assert mock_cloud.called
        assert any(
            "backend_selected" in r.message and "force_cloud" in r.message for r in caplog.records
        )

    def test_kokoro_unavailable_falls_back_to_cloud(self, caplog):
        from tools.tts import synthesize

        caplog.set_level(logging.INFO, logger="tools.tts")
        with (
            patch("tools.tts._is_kokoro_available", return_value=False),
            patch(
                "tools.tts._synthesize_openai",
                return_value={
                    "path": "/tmp/x.ogg",
                    "duration": 0.5,
                    "backend": "cloud",
                    "voice": "nova",
                    "format": "opus",
                    "error": None,
                },
            ),
        ):
            result = synthesize("hello", "/tmp/x.ogg")
        assert result["backend"] == "cloud"
        assert any("kokoro_unavailable" in r.message for r in caplog.records)

    def test_kokoro_synth_error_falls_back_to_cloud(self, caplog):
        from tools.tts import synthesize

        caplog.set_level(logging.INFO, logger="tools.tts")
        with (
            patch("tools.tts._is_kokoro_available", return_value=True),
            patch(
                "tools.tts._synthesize_kokoro",
                return_value={"error": "ffmpeg failed: boom"},
            ),
            patch(
                "tools.tts._synthesize_openai",
                return_value={
                    "path": "/tmp/x.ogg",
                    "duration": 0.5,
                    "backend": "cloud",
                    "voice": "nova",
                    "format": "opus",
                    "error": None,
                },
            ) as mock_cloud,
        ):
            result = synthesize("hello", "/tmp/x.ogg")
        assert result["backend"] == "cloud"
        assert mock_cloud.called
        assert any("kokoro_synth_error" in r.message for r in caplog.records)

    def test_kokoro_path_success(self):
        from tools.tts import synthesize

        ok = {
            "path": "/tmp/x.ogg",
            "duration": 1.2,
            "backend": "kokoro",
            "voice": "af_bella",
            "format": "opus",
            "error": None,
        }
        with (
            patch("tools.tts._is_kokoro_available", return_value=True),
            patch("tools.tts._synthesize_kokoro", return_value=ok),
        ):
            result = synthesize("hello", "/tmp/x.ogg")
        assert result["backend"] == "kokoro"
        assert result["voice"] == "af_bella"


# ---------------------------------------------------------------------------
# Availability cache
# ---------------------------------------------------------------------------


class TestAvailabilityCache:
    def setup_method(self):
        import tools.tts as tts

        tts._kokoro_available_cache["timestamp"] = 0.0
        tts._kokoro_available_cache["available"] = False
        tts._kokoro_available_cache["reason"] = None

    def test_cache_hit_skips_check(self):
        import time

        import tools.tts as tts

        tts._kokoro_available_cache["timestamp"] = time.time()
        tts._kokoro_available_cache["available"] = True
        with patch("tools.tts._kokoro_static_check") as mock_static:
            assert tts._is_kokoro_available() is True
            assert not mock_static.called

    def test_cache_miss_runs_check(self):
        import tools.tts as tts

        with patch(
            "tools.tts._kokoro_static_check",
            return_value=(False, "missing model file"),
        ) as mock_static:
            result = tts._is_kokoro_available()
        assert result is False
        assert mock_static.called
        assert tts._kokoro_available_cache["reason"] == "missing model file"

    def test_dynamic_probe_failure_marks_unavailable(self):
        import tools.tts as tts

        with (
            patch("tools.tts._kokoro_static_check", return_value=(True, None)),
            patch(
                "tools.tts._synthesize_kokoro_raw",
                side_effect=RuntimeError("ABI mismatch"),
            ),
        ):
            result = tts._is_kokoro_available()
        assert result is False
        assert "dynamic probe raised" in (tts._kokoro_available_cache["reason"] or "")

    def test_dynamic_probe_empty_bytes_marks_unavailable(self):
        import tools.tts as tts

        with (
            patch("tools.tts._kokoro_static_check", return_value=(True, None)),
            patch("tools.tts._synthesize_kokoro_raw", return_value=b""),
        ):
            result = tts._is_kokoro_available()
        assert result is False
        assert "dynamic probe returned empty bytes" in (tts._kokoro_available_cache["reason"] or "")


# ---------------------------------------------------------------------------
# Backend implementations: failure paths
# ---------------------------------------------------------------------------


class TestKokoroBackend:
    def test_kokoro_synth_raises_returns_error_dict(self):
        from tools.tts import _synthesize_kokoro

        with patch(
            "tools.tts._synthesize_kokoro_raw",
            side_effect=RuntimeError("model corrupt"),
        ):
            result = _synthesize_kokoro("hello", "af_bella", "/tmp/x.ogg")
        assert result.get("error") and "kokoro synthesis failed" in result["error"]

    def test_ffmpeg_failure_returns_error_dict(self, tmp_path):
        from tools.tts import _synthesize_kokoro

        wav_bytes = b"RIFF\x00\x00\x00\x00WAVEfmt "
        out = str(tmp_path / "out.ogg")

        class FakeProc:
            returncode = 1
            stderr = b"ffmpeg: bad input"

        with (
            patch("tools.tts._synthesize_kokoro_raw", return_value=wav_bytes),
            patch("tools.tts.subprocess.run", return_value=FakeProc()),
        ):
            result = _synthesize_kokoro("hello", "af_bella", out)
        assert result.get("error") and "ffmpeg failed" in result["error"]


class TestOpenAIBackend:
    def test_no_api_key_returns_error_dict(self):
        from tools.tts import _synthesize_openai

        with patch.dict(os.environ, {}, clear=True):
            result = _synthesize_openai("hello", "nova", "/tmp/x.ogg")
        assert result.get("error") and "OPENAI_API_KEY" in result["error"]


# ---------------------------------------------------------------------------
# Duration helper
# ---------------------------------------------------------------------------


class TestDurationHelper:
    def test_missing_ffprobe_returns_zero(self):
        from tools.tts import _compute_duration_opus

        with patch("tools.tts.shutil.which", return_value=None):
            assert _compute_duration_opus("/tmp/anything.ogg") == 0.0

    def test_ffprobe_success(self):
        from tools.tts import _compute_duration_opus

        class FakeProc:
            returncode = 0
            stdout = b"12.345\n"

        with (
            patch("tools.tts.shutil.which", return_value="/usr/bin/ffprobe"),
            patch("tools.tts.subprocess.run", return_value=FakeProc()),
        ):
            assert _compute_duration_opus("/tmp/x.ogg") == 12.345


# ---------------------------------------------------------------------------
# Observability: WARN-only-once on first cloud fallback
# ---------------------------------------------------------------------------


class TestObservability:
    def setup_method(self):
        import tools.tts as tts

        tts._kokoro_available_cache["timestamp"] = 0.0
        tts._kokoro_available_cache["available"] = False
        tts._kokoro_available_cache["reason"] = "missing model file"
        tts._kokoro_warn_emitted = False

    def test_first_cloud_fallback_emits_warn(self, caplog):
        from tools.tts import synthesize

        caplog.set_level(logging.WARNING, logger="tools.tts")
        with (
            patch("tools.tts._is_kokoro_available", return_value=False),
            patch(
                "tools.tts._synthesize_openai",
                return_value={
                    "path": "/tmp/x.ogg",
                    "duration": 0.5,
                    "backend": "cloud",
                    "voice": "nova",
                    "format": "opus",
                    "error": None,
                },
            ),
        ):
            synthesize("hello", "/tmp/x.ogg")
        warns = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("kokoro_unavailable" in r.message for r in warns)

    def test_subsequent_fallbacks_dont_re_warn(self, caplog):
        from tools.tts import synthesize

        caplog.set_level(logging.WARNING, logger="tools.tts")
        with (
            patch("tools.tts._is_kokoro_available", return_value=False),
            patch(
                "tools.tts._synthesize_openai",
                return_value={
                    "path": "/tmp/x.ogg",
                    "duration": 0.5,
                    "backend": "cloud",
                    "voice": "nova",
                    "format": "opus",
                    "error": None,
                },
            ),
        ):
            synthesize("hello", "/tmp/x.ogg")
            caplog.clear()
            synthesize("hello again", "/tmp/x.ogg")
        warns = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert not any("kokoro_unavailable" in r.message for r in warns)
