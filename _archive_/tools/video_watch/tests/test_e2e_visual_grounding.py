"""E2E visual-grounding outcome test (plan Success Criterion, round-2 NIT).

The plan's criterion: a slide-deck / silent-demo fixture where the answer is
ON-SCREEN (not in the audio) → ``valor-video-watch`` emits frames that let the
agent answer, where transcript-only fails.

This test runs the REAL pipeline — real ffprobe duration probe, real ffmpeg
scene-change frame extraction, real Pillow dedup, real ffmpeg audio-track
extraction — against a synthesized silent "slide deck" (three solid-color
slides, hard cuts, silent audio track). Only the two network edges are
patched: yt-dlp download (replaced by the local fixture) and the Whisper
upload (returns ``None``, as it would for silent audio).

The visual-grounding outcome is asserted mechanically: the transcript path
yields nothing (transcript-only fails), while the emitted frames are multiple,
persistent, and pairwise visually DISTINCT — i.e. they carry the on-screen
information (which slide is showing, and when) that the transcript cannot.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.video_watch import pipeline as vw

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH",
)


@pytest.fixture(scope="module")
def slide_deck_mp4(tmp_path_factory) -> Path:
    """Synthesize a 3-second silent 'slide deck': three solid-color slides
    (hard cuts at t=1s and t=2s) plus a silent mono 16 kHz audio track."""
    out = tmp_path_factory.mktemp("e2e_fixture") / "slides.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=red:s=320x240:d=1:r=25",
        "-f",
        "lavfi",
        "-i",
        "color=c=green:s=320x240:d=1:r=25",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=320x240:d=1:r=25",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=16000:cl=mono:d=3",
        "-filter_complex",
        "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
        "-map",
        "[v]",
        "-map",
        "3:a",
        "-shortest",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"fixture synthesis failed: {result.stderr[-400:]}"
    return out


def _mean_abs_diff(path_a: Path, path_b: Path) -> float:
    """16x16 grayscale mean-abs-diff between two images (0..255)."""
    from PIL import Image

    def sig(p: Path) -> list[int]:
        with Image.open(p) as im:
            return list(im.convert("L").resize((16, 16)).tobytes())

    a, b = sig(path_a), sig(path_b)
    return sum(abs(x - y) for x, y in zip(a, b, strict=True)) / len(a)


def test_silent_slide_deck_yields_distinct_persistent_frames(slide_deck_mp4, tmp_path):
    """Transcript-only fails on the silent deck; the watch tier emits multiple
    pairwise-distinct frames covering the slide changes — the 'seeing' win."""

    def fake_download(url, workdir):
        dest = Path(workdir) / "source.mp4"
        shutil.copy(slide_deck_mp4, dest)
        return dest

    async def silent_whisper(path):
        # The ONLY patched extraction step besides download: the network upload.
        # Assert it received the REAL extracted mp3 audio track, not the video.
        assert path.name == "audio.mp3"
        assert path.stat().st_size > 0
        return None  # what Whisper effectively yields for silence

    out_dir = tmp_path / "frames_out"
    with (
        patch.object(vw, "_download_video", side_effect=fake_download),
        patch.object(vw, "transcribe_audio_file", side_effect=silent_whisper),
        patch.object(vw, "fetch_x_context") as mock_grok,
    ):
        result = asyncio.run(vw.watch_video("https://youtu.be/e2e-fixture", output_dir=out_dir))

    # Transcript-only fails (the answer is not in the audio)...
    assert result["transcript"] is None
    assert any("no transcript" in n for n in result["notes"])
    mock_grok.assert_not_called()

    # ...but the watch tier still succeeds with the on-screen content.
    assert result["success"] is True
    frames = result["frames"]
    assert len(frames) >= 2, f"expected >=2 scene frames, got {len(frames)}: {result['notes']}"

    # Opening frame is always kept, timestamped 00:00.
    assert frames[0]["timestamp"] == "00:00"
    assert frames[0]["seconds"] == 0.0

    # Frames persist outside the pipeline's temp work dir (agent Reads them later).
    for fr in frames:
        p = Path(fr["path"])
        assert p.exists() and p.stat().st_size > 0
        assert p.parent == out_dir

    # Consecutive frames are pairwise visually DISTINCT — each carries new
    # on-screen information (different slide), which is the grounding signal.
    for prev, cur in zip(frames, frames[1:], strict=False):
        mad = _mean_abs_diff(Path(prev["path"]), Path(cur["path"]))
        assert mad > vw.VIDEO_WATCH_DEDUP_THRESHOLD, (
            f"frames {prev['timestamp']} and {cur['timestamp']} are near-identical (mad={mad:.1f})"
        )

    # And the timestamps actually advance across the deck (temporal coverage).
    seconds = [fr["seconds"] for fr in frames]
    assert seconds == sorted(seconds)
    assert seconds[-1] >= 1.0, f"no frame captured after the first slide cut: {seconds}"
