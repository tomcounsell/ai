"""Unit tests for the narrated deck-video compositor (``tools/deck_video``).

Fast, pure-logic tests plus monkeypatched failure-injection paths. The happy
path (real Marp + ffmpeg + valor-tts) lives in
``tests/integration/test_deck_video_e2e.py``.

These tests pin the contract described in ``docs/plans/narrated_deck_video.md``:
the narration parser, PNG-count parity (slide-count parity, NOT clip parity),
numeric PNG ordering past index 9, the synthesis-error guard (clean
``DeckVideoError``, never ``TypeError``/``KeyError``), the zero-length duration
floor, the zero-slide error, temp-dir cleanup on mid-pipeline failure, and the
missing-binary message.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

import tools.deck_video as dv
from tools.deck_video import (
    DECK_VIDEO_DEFAULT_HOLD_SECS,
    DeckVideoError,
    build_deck_video,
    compute_total_runtime,
    parse_narration_blocks,
)

pytestmark = pytest.mark.unit


# --- Narration parsing -------------------------------------------------------


def test_parse_handles_frontmatter_fence_and_real_break():
    """Front-matter + a ``---`` inside a fenced code block + a real page break.

    The leading YAML front-matter ``---`` pair must be skipped, the ``---``
    inside the fenced code block must NOT be treated as a slide break, and the
    single real ``---`` page break splits the deck into exactly two slides.
    """
    markdown = textwrap.dedent(
        """\
        ---
        marp: true
        theme: default
        ---

        # Slide one

        <!-- narration: First slide narration -->

        ```yaml
        ---
        key: value
        ---
        ```

        ---

        # Slide two

        <!-- narration: Second slide narration -->
        """
    )

    blocks = parse_narration_blocks(markdown)

    assert len(blocks) == 2
    assert blocks[0] == "First slide narration"
    assert blocks[1] == "Second slide narration"


def test_parse_silent_slide_yields_empty_string():
    """A slide with no narration comment yields an empty narration block."""
    markdown = textwrap.dedent(
        """\
        ---
        marp: true
        ---

        # Narrated

        <!-- narration: only this one talks -->

        ---

        # Silent slide with no narration comment
        """
    )

    blocks = parse_narration_blocks(markdown)

    assert len(blocks) == 2
    assert blocks[0] == "only this one talks"
    assert blocks[1] == ""


def test_parse_whitespace_only_narration_is_empty():
    """Whitespace-only narration text is treated as empty (silent slide)."""
    markdown = textwrap.dedent(
        """\
        # Slide

        <!-- narration:    \t   -->
        """
    )

    blocks = parse_narration_blocks(markdown)

    assert len(blocks) == 1
    assert blocks[0] == ""


def test_parse_single_slide_no_frontmatter():
    """A deck with no front-matter and one slide parses to one block."""
    markdown = "# Only slide\n\n<!-- narration: hello world -->\n"

    blocks = parse_narration_blocks(markdown)

    assert blocks == ["hello world"]


# --- Default-hold runtime computation ----------------------------------------


def test_empty_narration_uses_default_hold_in_runtime():
    """A silent slide contributes exactly DECK_VIDEO_DEFAULT_HOLD_SECS.

    Asserted via the runtime-computation helper (no full render). One narrated
    slide (duration supplied) + one silent slide → narrated + one default hold.
    """
    markdown = textwrap.dedent(
        """\
        # A

        <!-- narration: talk -->

        ---

        # B
        """
    )

    total = compute_total_runtime(markdown, narrated_durations=[2.5])

    assert total == pytest.approx(2.5 + DECK_VIDEO_DEFAULT_HOLD_SECS)


def test_all_silent_runtime_is_count_times_default_hold():
    """An all-silent deck's runtime is count_slides * default hold."""
    markdown = textwrap.dedent(
        """\
        # A

        ---

        # B

        ---

        # C
        """
    )

    total = compute_total_runtime(markdown, narrated_durations=[])

    assert total == pytest.approx(3 * DECK_VIDEO_DEFAULT_HOLD_SECS)


# --- PNG-count parity --------------------------------------------------------


class _DeckVideoHarness:
    """Reusable monkeypatch harness to drive build_deck_video without Marp.

    Stubs out the binary/prerequisite guards and the Marp export so the parity
    check, synthesis guard, and cleanup logic can be exercised deterministically
    in a unit test. ``synthesize`` and ``_probe_duration`` are stubbed by default
    to succeed; individual tests override them.
    """

    def __init__(self, monkeypatch, tmp_path: Path, png_count: int):
        self.monkeypatch = monkeypatch
        self.tmp_path = tmp_path
        self.png_count = png_count
        self.composited = False
        self.created_work_dirs: list[Path] = []

        monkeypatch.setattr(dv, "check_prerequisites", lambda: [])
        monkeypatch.setattr(dv, "_require_binary", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(dv, "_export_pngs", self._export_pngs)
        monkeypatch.setattr(dv, "_composite", self._composite)
        monkeypatch.setattr(dv, "synthesize", self._synthesize)
        # Track temp dirs so cleanup can be asserted.
        real_mkdtemp = dv.tempfile.mkdtemp

        def tracking_mkdtemp(*args, **kwargs):
            path = real_mkdtemp(*args, **kwargs)
            self.created_work_dirs.append(Path(path))
            return path

        monkeypatch.setattr(dv.tempfile, "mkdtemp", tracking_mkdtemp)

    def _export_pngs(self, deck_path, work_dir):
        pngs = []
        for i in range(self.png_count):
            p = Path(work_dir) / f"deck.{i + 1:03d}.png"
            p.write_bytes(b"fakepng")
            pngs.append(p)
        return pngs

    def _composite(self, work_dir, holds, audio_segments, has_audio, output_path, ffmpeg):
        self.composited = True
        Path(output_path).write_bytes(b"fakemp4")

    def _synthesize(self, text, output_path, **kwargs):
        Path(output_path).write_bytes(b"fakeaudio")
        return {"path": output_path, "duration": 1.5, "error": None}


def _write_deck(tmp_path: Path, slide_blocks: list[str]) -> Path:
    """Build a Marp deck from a list of per-slide narration strings.

    An empty string yields a silent slide (no narration comment).
    """
    parts = ["---", "marp: true", "---", ""]
    sections = []
    for i, narration in enumerate(slide_blocks):
        body = [f"# Slide {i}"]
        if narration:
            body.append(f"<!-- narration: {narration} -->")
        sections.append("\n\n".join(body))
    parts.append("\n\n---\n\n".join(sections))
    deck = tmp_path / "deck.md"
    deck.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return deck


def test_png_count_mismatch_raises(monkeypatch, tmp_path):
    """len(pngs) != total_slide_count must fail loudly with DeckVideoError."""
    deck = _write_deck(tmp_path, ["one", "two"])  # 2 slides
    # Export 3 PNGs for a 2-slide deck → parity violation.
    _DeckVideoHarness(monkeypatch, tmp_path, png_count=3)

    with pytest.raises(DeckVideoError) as exc:
        build_deck_video(deck, output_path=tmp_path / "out.mp4")

    assert "parity" in str(exc.value).lower()


def test_mixed_deck_more_pngs_than_clips_passes(monkeypatch, tmp_path):
    """A mixed deck (silent slide present) must NOT trip the parity check.

    len(pngs) == total_slide_count == len(narration_blocks) holds even though
    len(pngs) > len(narration_clips) (only the narrated slide gets a clip).
    """
    deck = _write_deck(tmp_path, ["narrated", ""])  # 1 narrated + 1 silent
    harness = _DeckVideoHarness(monkeypatch, tmp_path, png_count=2)

    out = build_deck_video(deck, output_path=tmp_path / "out.mp4")

    assert harness.composited is True
    assert Path(out).exists()


# --- Ordering past index 9 ---------------------------------------------------


def test_collect_pngs_numeric_order_past_index_nine(tmp_path):
    """Zero-padded PNG filenames are sorted numerically, not lexicographically.

    A lexicographic sort would order deck.010.png before deck.009.png only if
    padding were dropped; here we confirm 12 slides stay in 1..12 numeric order
    (deck.002.png must precede deck.012.png, deck.012.png last).
    """
    for i in range(1, 13):
        (tmp_path / f"deck.{i:03d}.png").write_bytes(b"x")

    pngs = dv._collect_pngs(tmp_path)

    names = [p.name for p in pngs]
    assert names[0] == "deck.001.png"
    assert names[1] == "deck.002.png"
    assert names[9] == "deck.010.png"
    assert names[-1] == "deck.012.png"
    # Numeric, not lexicographic: deck.010.png comes AFTER deck.009.png.
    assert names.index("deck.009.png") < names.index("deck.010.png")


def test_collect_pngs_rejects_dropped_padding(tmp_path):
    """If Marp ever drops zero-padding, _collect_pngs fails loudly.

    Mixed pad widths where the max sequence has more digits than the min pad
    width must raise rather than risk a mis-ordered slideshow.
    """
    (tmp_path / "deck.1.png").write_bytes(b"x")
    (tmp_path / "deck.10.png").write_bytes(b"x")

    with pytest.raises(DeckVideoError) as exc:
        dv._collect_pngs(tmp_path)

    assert "padd" in str(exc.value).lower()


# --- Synthesis-error guard ---------------------------------------------------


def test_synthesis_error_dict_raises_clean_deck_video_error(monkeypatch, tmp_path):
    """synthesize() returning {"error": ...} → clean DeckVideoError, not a crash.

    The error dict has no path/duration keys; reading them would raise
    TypeError (None <= 0.0) or KeyError (res["path"]). The compositor must check
    res.get("error") first and raise DeckVideoError naming the slide index.
    """
    deck = _write_deck(tmp_path, ["", "boom narration"])  # slide 1 is narrated
    harness = _DeckVideoHarness(monkeypatch, tmp_path, png_count=2)

    def failing_synthesize(text, output_path, **kwargs):
        return {"error": "boom"}

    monkeypatch.setattr(dv, "synthesize", failing_synthesize)

    with pytest.raises(DeckVideoError) as exc:
        build_deck_video(deck, output_path=tmp_path / "out.mp4")

    # Slide index (1) surfaced in the message.
    assert "1" in str(exc.value)
    assert "boom" in str(exc.value)
    # CRITICAL: the propagating exception is DeckVideoError, not the opaque
    # TypeError/KeyError that reading an error dict's missing keys would raise.
    assert exc.type is DeckVideoError
    assert not issubclass(exc.type, TypeError)
    assert not issubclass(exc.type, KeyError)
    # Composite never ran (failed before muxing).
    assert harness.composited is False


# --- Duration floor ----------------------------------------------------------


def test_zero_duration_reprobe_still_zero_raises(monkeypatch, tmp_path):
    """A narrated clip probed at <= 0.0, still <= 0.0 after re-probe → raise.

    synthesize reports duration 0.0; the direct ffprobe re-probe also returns
    0.0; the compositor must refuse to emit a zero-length slide and raise with
    the clip path + slide index.
    """
    deck = _write_deck(tmp_path, ["talky"])  # single narrated slide
    harness = _DeckVideoHarness(monkeypatch, tmp_path, png_count=1)

    def zero_synthesize(text, output_path, **kwargs):
        Path(output_path).write_bytes(b"fakeaudio")
        return {"path": output_path, "duration": 0.0, "error": None}

    monkeypatch.setattr(dv, "synthesize", zero_synthesize)
    monkeypatch.setattr(dv, "_probe_duration", lambda path: 0.0)

    with pytest.raises(DeckVideoError) as exc:
        build_deck_video(deck, output_path=tmp_path / "out.mp4")

    msg = str(exc.value)
    assert "0" in msg  # slide index 0
    assert "duration" in msg.lower()
    assert harness.composited is False


def test_zero_duration_reprobe_recovers(monkeypatch, tmp_path):
    """synthesize reports 0.0 but the re-probe recovers a positive duration.

    The compositor should floor against the re-probed value and proceed (no
    raise, composite runs).
    """
    deck = _write_deck(tmp_path, ["talky"])
    harness = _DeckVideoHarness(monkeypatch, tmp_path, png_count=1)

    def zero_synthesize(text, output_path, **kwargs):
        Path(output_path).write_bytes(b"fakeaudio")
        return {"path": output_path, "duration": 0.0, "error": None}

    monkeypatch.setattr(dv, "synthesize", zero_synthesize)
    monkeypatch.setattr(dv, "_probe_duration", lambda path: 3.2)

    out = build_deck_video(deck, output_path=tmp_path / "out.mp4")

    assert harness.composited is True
    assert Path(out).exists()


# --- Zero-slide deck ---------------------------------------------------------


def test_zero_slide_deck_raises(monkeypatch, tmp_path):
    """A deck that exports zero PNGs → clear DeckVideoError, no partial MP4."""
    deck = tmp_path / "empty.md"
    deck.write_text("---\nmarp: true\n---\n", encoding="utf-8")

    monkeypatch.setattr(dv, "check_prerequisites", lambda: [])
    monkeypatch.setattr(dv, "_require_binary", lambda name: f"/usr/bin/{name}")

    def no_pngs(deck_path, work_dir):
        raise DeckVideoError("Marp PNG export produced no images; zero slides.")

    monkeypatch.setattr(dv, "_export_pngs", no_pngs)

    out_path = tmp_path / "out.mp4"
    with pytest.raises(DeckVideoError):
        build_deck_video(deck, output_path=out_path)

    assert not out_path.exists()


# --- Temp file cleanup -------------------------------------------------------


def test_temp_dir_cleaned_up_on_midpipeline_failure(monkeypatch, tmp_path):
    """A mid-pipeline ffmpeg failure removes the temp dir, leaves no MP4."""
    deck = _write_deck(tmp_path, ["narrated"])
    harness = _DeckVideoHarness(monkeypatch, tmp_path, png_count=1)

    def failing_composite(work_dir, holds, audio_segments, has_audio, output_path, ffmpeg):
        raise DeckVideoError("ffmpeg mux composite failed (exit 1)")

    monkeypatch.setattr(dv, "_composite", failing_composite)

    out_path = tmp_path / "out.mp4"
    with pytest.raises(DeckVideoError):
        build_deck_video(deck, output_path=out_path)

    # The dedicated temp dir was removed in the finally block.
    assert harness.created_work_dirs, "expected a temp dir to have been created"
    for work_dir in harness.created_work_dirs:
        assert not work_dir.exists(), f"orphaned temp dir left behind: {work_dir}"
    # No partial MP4 survives.
    assert not out_path.exists()


# --- Missing binary ----------------------------------------------------------


def test_missing_binary_entrypoint_raises_actionable(monkeypatch, tmp_path):
    """A missing prerequisite makes build_deck_video raise an actionable error."""
    deck = _write_deck(tmp_path, ["talk"])
    monkeypatch.setattr(
        dv,
        "check_prerequisites",
        lambda: ["ffmpeg (required for video encode + audio mux)"],
    )

    with pytest.raises(DeckVideoError) as exc:
        build_deck_video(deck, output_path=tmp_path / "out.mp4")

    assert "ffmpeg" in str(exc.value)
    assert "missing prerequisites" in str(exc.value).lower()


def test_missing_binary_cli_exits_nonzero(monkeypatch, tmp_path, capsys):
    """The CLI emits an actionable message naming the missing tool, exits 1."""
    from tools.deck_video import cli

    deck = _write_deck(tmp_path, ["talk"])
    monkeypatch.setattr(
        cli,
        "check_prerequisites",
        lambda: ["ffprobe (required for per-clip duration measurement)"],
    )

    rc = cli.main([str(deck)])

    assert rc == 1
    err = capsys.readouterr().err
    assert "ffprobe" in err
    assert "missing required tool" in err.lower()
