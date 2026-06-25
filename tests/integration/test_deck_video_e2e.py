"""End-to-end integration tests for the narrated deck-video compositor.

Real Marp PNG export + real valor-tts synthesis + real ffmpeg compositing. No
mocks on the happy path (per this repo's testing philosophy). These tests are
slow (TTS + Marp via npx + two ffmpeg passes) and gracefully skip if any
prerequisite tool is unavailable.

Two decks are exercised:
  - MIXED deck (one narrated slide + one silent slide): the output MP4 must be
    playable, have an audio stream, and run for
    ``sum(floored narrated durations) + (count_silent * DEFAULT_HOLD)``.
  - ALL-SILENT deck (every narration block empty): the output MP4 must be
    playable, have NO audio stream, and run for ``count_slides * DEFAULT_HOLD``.

Durations are probed from the actual synthesized clips (TTS length varies), so
the expected total is computed, never hardcoded.
"""

from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest

from tools.deck_video import (
    DECK_VIDEO_DEFAULT_HOLD_SECS,
    build_deck_video,
    check_prerequisites,
)
from tools.deck_video.cli import main as cli_main

pytestmark = [pytest.mark.integration, pytest.mark.slow]


# --- ffprobe helpers ---------------------------------------------------------


def _ffprobe_json(path: Path) -> dict:
    """Return the parsed ffprobe stream+format JSON for a media file."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="ignore")
    return json.loads(result.stdout.decode("utf-8", errors="ignore"))


def _has_audio_stream(probe: dict) -> bool:
    return any(s.get("codec_type") == "audio" for s in probe.get("streams", []))


def _has_video_stream(probe: dict) -> bool:
    return any(s.get("codec_type") == "video" for s in probe.get("streams", []))


def _container_duration(probe: dict) -> float:
    return float(probe["format"]["duration"])


def _clip_duration(path: Path) -> float:
    """Probe a single audio clip's duration in seconds."""
    return _container_duration(_ffprobe_json(path))


# --- Skip guard --------------------------------------------------------------


@pytest.fixture(autouse=True)
def _require_prereqs():
    """Skip the module if Marp/ffmpeg/ffprobe/valor-tts are unavailable."""
    missing = check_prerequisites()
    if missing:
        pytest.skip(f"deck-video prerequisites unavailable: {'; '.join(missing)}")


# --- Fixtures ----------------------------------------------------------------


def _write_deck(tmp_path: Path, name: str, body: str) -> Path:
    deck = tmp_path / name
    deck.write_text(textwrap.dedent(body), encoding="utf-8")
    return deck


# --- MIXED deck E2E ----------------------------------------------------------


def test_mixed_deck_produces_narrated_mp4_with_audio(tmp_path):
    """Mixed deck: narrated + silent slide → playable MP4 with an audio stream.

    Total duration ≈ floored narrated clip duration + DEFAULT_HOLD (one silent
    slide). The narrated clip's actual duration is probed from the synthesized
    OGG to build the expected total, so the assertion tracks real TTS length.
    """
    deck = _write_deck(
        tmp_path,
        "mixed.md",
        """\
        ---
        marp: true
        ---

        # First slide

        <!-- narration: Hello from the first slide. -->

        ---

        # Second slide

        Silent here.
        """,
    )

    out = build_deck_video(deck, output_path=tmp_path / "mixed.mp4")

    assert Path(out).exists()
    probe = _ffprobe_json(out)
    assert _has_video_stream(probe), "expected a video stream"
    assert _has_audio_stream(probe), "mixed deck must have an audio stream"

    # The narrated clip was synthesized during the build under a temp dir that
    # is now gone, so re-synthesize the SAME text once to recover its floored
    # duration for the expected-total computation.
    from tools.tts import synthesize

    probe_clip = tmp_path / "probe_narration.ogg"
    res = synthesize(text="Hello from the first slide.", output_path=str(probe_clip))
    assert not res.get("error"), res.get("error")
    narrated_dur = _clip_duration(probe_clip)
    assert narrated_dur > 0.0

    expected = narrated_dur + DECK_VIDEO_DEFAULT_HOLD_SECS
    actual = _container_duration(probe)
    # Tolerance: TTS re-synthesis is deterministic-ish but ffmpeg padding +
    # frame-rate quantization introduce small drift; allow generous slack.
    assert actual == pytest.approx(expected, abs=1.5), (
        f"mixed-deck duration {actual:.2f}s not ≈ expected {expected:.2f}s "
        f"(narrated={narrated_dur:.2f}, hold={DECK_VIDEO_DEFAULT_HOLD_SECS})"
    )


# --- ALL-SILENT deck E2E -----------------------------------------------------


def test_all_silent_deck_produces_video_only_mp4(tmp_path):
    """All-silent deck → playable VIDEO-ONLY MP4 (no audio stream).

    Total duration ≈ count_slides * DEFAULT_HOLD. Exit must be clean (the
    compositor takes the zero-audio branch and never builds an audio command).
    """
    deck = _write_deck(
        tmp_path,
        "silent.md",
        """\
        ---
        marp: true
        ---

        # Slide one

        Nothing spoken.

        ---

        # Slide two

        Also silent.
        """,
    )

    out = build_deck_video(deck, output_path=tmp_path / "silent.mp4")

    assert Path(out).exists()
    probe = _ffprobe_json(out)
    assert _has_video_stream(probe), "expected a video stream"
    assert not _has_audio_stream(probe), "all-silent deck must have NO audio stream"

    expected = 2 * DECK_VIDEO_DEFAULT_HOLD_SECS
    actual = _container_duration(probe)
    assert actual == pytest.approx(expected, abs=1.0), (
        f"all-silent duration {actual:.2f}s not ≈ expected {expected:.2f}s "
        f"(2 slides * {DECK_VIDEO_DEFAULT_HOLD_SECS})"
    )


# --- CLI path E2E ------------------------------------------------------------


def test_cli_all_silent_warns_and_succeeds(tmp_path, capsys):
    """The CLI builds a video-only MP4 for an all-silent deck and warns once.

    Exit code 0, an MP4 with no audio stream, and a stderr warning that no
    narration blocks were found (keep-and-warn, not drop-and-fail).
    """
    deck = _write_deck(
        tmp_path,
        "cli_silent.md",
        """\
        ---
        marp: true
        ---

        # Only slide

        No narration.
        """,
    )
    out_path = tmp_path / "cli_silent.mp4"

    rc = cli_main([str(deck), "--output", str(out_path)])

    assert rc == 0
    assert out_path.exists()
    err = capsys.readouterr().err
    assert "narration" in err.lower()

    probe = _ffprobe_json(out_path)
    assert _has_video_stream(probe)
    assert not _has_audio_stream(probe)
