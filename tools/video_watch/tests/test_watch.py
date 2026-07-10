"""Unit tests for tools.video_watch core pipeline.

No real network or subprocess: yt-dlp/ffmpeg/transcription/Grok are all patched.
Covers source detection, timestamp formatting, subsampling, real Pillow-based
dedup, and watch_video orchestration incl. graceful X-degrade and temp cleanup.
"""

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from tools import video_watch as vw

# --- pure helpers ------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.youtube.com/watch?v=abc", "youtube"),
        ("https://youtu.be/abc", "youtube"),
        ("https://x.com/user/status/123", "x"),
        ("https://twitter.com/user/status/123", "x"),
        ("https://mobile.x.com/user/status/123", "x"),
        ("https://vimeo.com/123", "other"),
        ("", "other"),
        # Regression: `h in host` substring matching used to misclassify any
        # host merely *containing* "x.com" or a youtube host as a substring.
        ("https://netflix.com/watch/123", "other"),
        ("https://max.com/foo", "other"),
    ],
)
def test_detect_source(url, expected):
    assert vw.detect_source(url) == expected


@pytest.mark.parametrize(
    "seconds,expected",
    [(0, "00:00"), (5, "00:05"), (65, "01:05"), (3661, "1:01:01")],
)
def test_fmt_ts(seconds, expected):
    assert vw._fmt_ts(seconds) == expected


def test_subsample_caps_and_preserves_span():
    frames = [(Path(f"f{i}.jpg"), float(i)) for i in range(100)]
    out = vw._subsample(frames, 10)
    assert len(out) == 10
    assert out[0][1] == 0.0  # keeps the first
    # under cap => untouched
    assert vw._subsample(frames[:5], 10) == frames[:5]


def test_dedup_drops_identical_frames(tmp_path):
    from PIL import Image

    # Three identical black frames + one white frame.
    black = Image.new("RGB", (32, 32), (0, 0, 0))
    white = Image.new("RGB", (32, 32), (255, 255, 255))
    paths = []
    for i, img in enumerate([black, black, black, white]):
        p = tmp_path / f"frame_{i}.jpg"
        img.save(p)
        paths.append((p, float(i)))

    kept = vw._dedup_frames(paths)
    # first black kept, next two identical dropped, white kept
    assert len(kept) == 2
    assert kept[0][1] == 0.0
    assert kept[1][1] == 3.0


# --- watch_video orchestration ----------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def test_empty_url_errors():
    result = _run(vw.watch_video("   "))
    assert result["success"] is False
    assert "URL is required" in result["error"]


def test_youtube_happy_path(tmp_path, monkeypatch):
    """yt-dlp + ffmpeg + transcription all succeed for a YouTube URL; no Grok."""

    def fake_download(url, workdir):
        vpath = Path(workdir) / "source.mp4"
        vpath.write_bytes(b"fake-bytes")
        return vpath

    def fake_frames(video_path, workdir):
        # produce two real jpeg frames in workdir/frames
        from PIL import Image

        fdir = Path(workdir) / "frames"
        fdir.mkdir(exist_ok=True)
        out = []
        for i, color in enumerate([(0, 0, 0), (255, 255, 255)]):
            fp = fdir / f"frame_{i}.jpg"
            Image.new("RGB", (16, 16), color).save(fp)
            out.append((fp, float(i * 10)))
        return out

    def fake_extract_audio(video_path, workdir):
        apath = Path(workdir) / "audio.wav"
        apath.write_bytes(b"fake-audio")
        return apath

    async def fake_transcribe(path):
        return "hello world transcript"

    out_dir = tmp_path / "frames_out"
    with (
        patch.object(vw, "_download_video", side_effect=fake_download),
        patch.object(vw, "_extract_scene_frames", side_effect=fake_frames),
        patch.object(vw, "_extract_audio", side_effect=fake_extract_audio),
        patch.object(vw, "_probe_duration", return_value=30.0),
        patch.object(vw, "transcribe_audio_file", side_effect=fake_transcribe),
        patch.object(vw, "fetch_x_context") as mock_grok,
    ):
        result = _run(vw.watch_video("https://youtu.be/abc", output_dir=out_dir))

    assert result["success"] is True
    assert result["source"] == "youtube"
    assert len(result["frames"]) == 2
    # frames persisted to the caller output dir (outlive the temp workdir)
    for fr in result["frames"]:
        assert Path(fr["path"]).exists()
        assert Path(fr["path"]).parent == out_dir
    assert result["transcript"] == "hello world transcript"
    assert result["grok_context"] is None
    mock_grok.assert_not_called()


def test_audio_extracted_before_transcription(tmp_path):
    """transcribe_audio_file must receive the extracted audio path, not the raw video."""

    def fake_download(url, workdir):
        vpath = Path(workdir) / "source.mp4"
        vpath.write_bytes(b"fake-bytes")
        return vpath

    def fake_frames(video_path, workdir):
        return []

    def fake_extract_audio(video_path, workdir):
        apath = Path(workdir) / "audio.wav"
        apath.write_bytes(b"fake-audio")
        return apath

    received_paths = []

    async def fake_transcribe(path):
        received_paths.append(path)
        return "a transcript"

    with (
        patch.object(vw, "_download_video", side_effect=fake_download),
        patch.object(vw, "_extract_scene_frames", side_effect=fake_frames),
        patch.object(vw, "_extract_audio", side_effect=fake_extract_audio) as mock_extract_audio,
        patch.object(vw, "_probe_duration", return_value=10.0),
        patch.object(vw, "transcribe_audio_file", side_effect=fake_transcribe),
    ):
        result = _run(vw.watch_video("https://youtu.be/abc", output_dir=tmp_path))

    assert result["transcript"] == "a transcript"
    mock_extract_audio.assert_called_once()
    assert len(received_paths) == 1
    assert received_paths[0].name == "audio.wav"
    assert received_paths[0].name != "source.mp4"


def test_oversized_duration_skips_transcription_with_exact_note(tmp_path):
    """Duration beyond VIDEO_WATCH_MAX_DURATION skips transcription entirely and
    emits the plan-committed exact note string."""

    def fake_download(url, workdir):
        vpath = Path(workdir) / "source.mp4"
        vpath.write_bytes(b"fake-bytes")
        return vpath

    def fake_frames(video_path, workdir):
        return []

    with (
        patch.object(vw, "_download_video", side_effect=fake_download),
        patch.object(vw, "_extract_scene_frames", side_effect=fake_frames),
        patch.object(vw, "_probe_duration", return_value=vw.VIDEO_WATCH_MAX_DURATION + 100),
        patch.object(vw, "_extract_audio") as mock_extract_audio,
        patch.object(vw, "transcribe_audio_file") as mock_transcribe,
    ):
        result = _run(vw.watch_video("https://youtu.be/abc", output_dir=tmp_path))

    assert result["transcript"] is None
    assert "[audio too long to transcribe — frames only]" in result["notes"]
    mock_extract_audio.assert_not_called()
    mock_transcribe.assert_not_called()


def test_x_download_fails_falls_back_to_grok(tmp_path, monkeypatch):
    """X URL where yt-dlp fails but Grok returns context => degraded success."""

    def fake_download(url, workdir):
        raise vw.VideoWatchError("yt-dlp download failed: protected media")

    with (
        patch.object(vw, "_download_video", side_effect=fake_download),
        patch.object(vw, "fetch_x_context", return_value="@user: chart demo") as mock_grok,
    ):
        result = _run(vw.watch_video("https://x.com/user/status/1", output_dir=tmp_path))

    assert result["success"] is True
    assert result["source"] == "x"
    assert result["frames"] == []
    assert result["transcript"] is None
    assert result["grok_context"] == "@user: chart demo"
    mock_grok.assert_called_once()
    assert any("media acquisition failed" in n for n in result["notes"])


def test_x_download_fails_and_grok_unavailable(tmp_path):
    """X URL where both yt-dlp AND Grok fail => hard failure, no crash."""
    with (
        patch.object(vw, "_download_video", side_effect=vw.VideoWatchError("boom")),
        patch.object(vw, "fetch_x_context", return_value=None),
    ):
        result = _run(vw.watch_video("https://x.com/user/status/1", output_dir=tmp_path))

    assert result["success"] is False
    assert result["error"]
    assert result["frames"] == []


def test_silent_video_emits_frames_without_transcript(tmp_path):
    """Video with frames but no transcript still succeeds and notes the absence."""

    def fake_download(url, workdir):
        vpath = Path(workdir) / "source.mp4"
        vpath.write_bytes(b"x")
        return vpath

    def fake_frames(video_path, workdir):
        from PIL import Image

        fdir = Path(workdir) / "frames"
        fdir.mkdir(exist_ok=True)
        fp = fdir / "frame_0.jpg"
        Image.new("RGB", (16, 16), (10, 20, 30)).save(fp)
        return [(fp, 0.0)]

    def fake_extract_audio(video_path, workdir):
        apath = Path(workdir) / "audio.wav"
        apath.write_bytes(b"fake-audio")
        return apath

    async def no_transcript(path):
        return None

    with (
        patch.object(vw, "_download_video", side_effect=fake_download),
        patch.object(vw, "_extract_scene_frames", side_effect=fake_frames),
        patch.object(vw, "_extract_audio", side_effect=fake_extract_audio),
        patch.object(vw, "_probe_duration", return_value=5.0),
        patch.object(vw, "transcribe_audio_file", side_effect=no_transcript),
    ):
        result = _run(vw.watch_video("https://youtu.be/abc", output_dir=tmp_path))

    assert result["success"] is True
    assert len(result["frames"]) == 1
    assert result["transcript"] is None
    assert any("no transcript" in n for n in result["notes"])


# --- reap_stale_frame_dirs ----------------------------------------------------


def test_reap_stale_frame_dirs_removes_old_dirs_keeps_fresh(tmp_path, monkeypatch):
    import os
    import time

    monkeypatch.setattr(vw.tempfile, "gettempdir", lambda: str(tmp_path))

    stale = tmp_path / "video_watch_frames_stale"
    stale.mkdir()
    fresh = tmp_path / "video_watch_frames_fresh"
    fresh.mkdir()

    old_time = time.time() - 1000
    os.utime(stale, (old_time, old_time))

    removed = vw.reap_stale_frame_dirs(max_age_seconds=500)

    assert removed == 1
    assert not stale.exists()
    assert fresh.exists()
