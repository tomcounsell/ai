"""Narrated Deck Video compositor.

Orchestrates Marp PNG export + per-slide ``valor-tts`` synthesis + ffmpeg
compositing into a single narrated MP4 (``deck.mp4``) next to a Marp markdown
deck. Each slide is held on screen for the duration of its narration clip
(narrated slides) or a default hold (silent slides), and the narration audio
is muxed into the final video.

This is approach B (in-house ffmpeg slideshow) from
``docs/plans/narrated_deck_video.md`` -- no external animation engine or cloud
speech provider, just Marp PNG export + ffmpeg, reusing the existing
``valor-tts`` surface for synthesis.

Public entrypoint: :func:`build_deck_video`.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from tools.tts import synthesize

logger = logging.getLogger(__name__)


# --- Errors ------------------------------------------------------------------


class DeckVideoError(Exception):
    """Raised on any unrecoverable failure in the deck-video pipeline."""


# --- Constants ---------------------------------------------------------------

# Default on-screen hold (seconds) for slides with empty/missing narration.
# Grain of salt: this is a provisional, tunable default -- there is no
# authoritative "right" hold for a silent slide. Override via the
# DECK_VIDEO_DEFAULT_HOLD_SECS environment variable if 4s reads too fast/slow.
DECK_VIDEO_DEFAULT_HOLD_SECS: float = float(os.environ.get("DECK_VIDEO_DEFAULT_HOLD_SECS", "4.0"))

# Output frame rate for the composited video. Fixed for broad player
# compatibility; provisional/tunable.
_OUTPUT_FPS = 30

# Marp's NewMessage-style page break is a line of exactly ``---`` (optionally
# surrounded by whitespace) in slide-separator position.
_SLIDE_BREAK_RE = re.compile(r"^\s*---\s*$")

# Per-slide narration carrier: an HTML comment ``<!-- narration: ... -->``.
_NARRATION_RE = re.compile(
    r"<!--\s*narration:\s*(?P<body>.*?)\s*-->",
    re.IGNORECASE | re.DOTALL,
)


# --- Binary resolution (mirrors tools/tts subprocess + missing-binary pattern)


def _require_binary(name: str) -> str:
    """Resolve a binary on PATH or raise a clean, actionable DeckVideoError.

    Mirrors the binary-resolution / missing-binary-error pattern used in
    ``tools/tts/__init__.py`` (``shutil.which`` + descriptive error).
    """
    resolved = shutil.which(name)
    if resolved is None:
        raise DeckVideoError(
            f"required tool not found on PATH: {name!r}. "
            f"Install it and ensure it is reachable before running deck-video."
        )
    return resolved


def _marp_available() -> bool:
    """Return True if the Marp CLI is reachable via ``npx``.

    Marp is invoked through ``npx --yes @marp-team/marp-cli`` (no global
    binary), so we probe ``--version`` rather than ``shutil.which``.
    """
    if shutil.which("npx") is None:
        return False
    try:
        result = subprocess.run(
            ["npx", "--yes", "@marp-team/marp-cli", "--version"],
            capture_output=True,
            timeout=120,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _valor_tts_available() -> bool:
    """Return True if the ``valor-tts`` surface is usable.

    We import ``synthesize`` directly (not the CLI), so availability reduces to
    the module being importable -- which it is by construction here. We still
    expose this as a guard so the CLI can report it uniformly with the other
    prerequisites.
    """
    return synthesize is not None


def check_prerequisites() -> list[str]:
    """Return a list of human-readable messages for any missing prerequisites.

    Empty list means all prerequisites (ffmpeg, ffprobe, Marp CLI, valor-tts)
    are resolvable. Used by both the CLI and the compositor entrypoint to fail
    fast before any export/synthesis work begins.
    """
    missing: list[str] = []
    if shutil.which("ffmpeg") is None:
        missing.append("ffmpeg (required for video encode + audio mux)")
    if shutil.which("ffprobe") is None:
        missing.append("ffprobe (required for per-clip duration measurement)")
    if not _marp_available():
        missing.append(
            "Marp CLI (npx --yes @marp-team/marp-cli --version failed; "
            "required for PNG-per-slide export)"
        )
    if not _valor_tts_available():
        missing.append("valor-tts (required for narration synthesis)")
    return missing


# --- Narration parsing -------------------------------------------------------


def parse_narration_blocks(markdown: str) -> list[str]:
    """Split a Marp source into per-slide narration blocks.

    Produces exactly one narration string per slide (in document order), where
    an un-narrated slide yields an empty string. Whitespace-only narration is
    treated as empty.

    Robustness rules (see plan Implementation Note 1):
      - Skip a leading YAML front-matter block (delimited by ``---`` lines at
        the very top of the file).
      - Recognize a slide separator only when ``---`` sits on its own line in
        slide-separator position (Marp page-break semantics); arbitrary
        mid-paragraph ``---`` is NOT a break.
      - Ignore ``---`` inside fenced code blocks (```` ``` ````).

    Args:
        markdown: Full Marp markdown source.

    Returns:
        One narration block per slide, in document order. The length of this
        list is the canonical ``total_slide_count`` used for parity assertions.
    """
    lines = markdown.splitlines()
    n = len(lines)
    idx = 0

    # Skip leading YAML front-matter: a `---` on line 0, then content, then a
    # closing `---`. Only consume it if it is genuinely the opening fence.
    if idx < n and _SLIDE_BREAK_RE.match(lines[idx]):
        close = None
        for j in range(idx + 1, n):
            if _SLIDE_BREAK_RE.match(lines[j]):
                close = j
                break
        if close is not None:
            idx = close + 1

    # Walk remaining lines, splitting on slide-separator `---` lines while
    # ignoring fenced code blocks.
    slides: list[list[str]] = []
    current: list[str] = []
    in_fence = False
    fence_marker = ""

    for i in range(idx, n):
        line = lines[i]
        stripped = line.lstrip()

        # Track fenced code blocks (``` or ~~~). A fence toggles only when the
        # marker matches the opening marker length/char loosely (``` / ~~~).
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            current.append(line)
            continue

        if not in_fence and _SLIDE_BREAK_RE.match(line):
            slides.append(current)
            current = []
            continue

        current.append(line)

    slides.append(current)

    blocks: list[str] = []
    for slide_lines in slides:
        slide_text = "\n".join(slide_lines)
        match = _NARRATION_RE.search(slide_text)
        if match:
            body = match.group("body").strip()
            blocks.append(body)
        else:
            blocks.append("")

    return blocks


# --- Marp PNG export ---------------------------------------------------------


def _export_pngs(deck_path: Path, work_dir: Path) -> list[Path]:
    """Export one PNG per slide via the Marp CLI into ``work_dir``.

    Mirrors the existing Marp invocation shape from
    ``.claude/skills-global/do-presentation/SKILL.md`` (``npx --yes
    @marp-team/marp-cli <deck> --allow-local-files``), adding ``--images png``.
    Marp emits zero-padded sequence filenames (``deck.001.png`` ...). The PNGs
    are sorted NUMERICALLY by the parsed sequence number, not lexicographically.

    Raises:
        DeckVideoError: if Marp exits non-zero (stderr surfaced) or emits no
        PNGs, or if any emitted filename is not zero-padded.
    """
    out_base = work_dir / "deck.png"  # Marp derives deck.001.png, deck.002.png, ...
    cmd = [
        "npx",
        "--yes",
        "@marp-team/marp-cli",
        str(deck_path),
        "--images",
        "png",
        "--allow-local-files",
        "-o",
        str(out_base),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=600)
    except subprocess.TimeoutExpired as e:
        raise DeckVideoError("Marp PNG export timed out") from e
    except FileNotFoundError as e:
        raise DeckVideoError("Marp CLI / npx not found on PATH") from e

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="ignore")
        raise DeckVideoError(f"Marp PNG export failed (exit {result.returncode}): {stderr}")

    pngs = _collect_pngs(work_dir)
    if not pngs:
        raise DeckVideoError(
            "Marp PNG export produced no images "
            f"(searched {work_dir}); the deck may contain zero slides."
        )
    return pngs


# Marp PNG filename: <stem>.<NNN>.png with a numeric, conventionally
# zero-padded sequence suffix.
_PNG_SEQ_RE = re.compile(r"\.(?P<seq>\d+)\.png$", re.IGNORECASE)


def _collect_pngs(work_dir: Path) -> list[Path]:
    """Collect + numerically sort Marp-emitted PNGs; assert zero-padding.

    Sorts by the parsed integer sequence number (NOT lexicographically) so the
    ordering survives past 9 slides even if a future Marp drops zero-padding.
    Still asserts the emitted names are zero-padded as Marp currently does.
    """
    candidates: list[tuple[int, Path]] = []
    pad_widths: set[int] = set()
    for p in work_dir.iterdir():
        if not p.is_file():
            continue
        m = _PNG_SEQ_RE.search(p.name)
        if not m:
            continue
        seq_str = m.group("seq")
        pad_widths.add(len(seq_str))
        candidates.append((int(seq_str), p))

    if not candidates:
        return []

    candidates.sort(key=lambda t: t[0])

    # Zero-padding assertion: Marp emits fixed-width zero-padded sequences. If
    # the max sequence number has more digits than the pad width, padding has
    # been dropped -- fail loudly rather than risk a lexicographic mis-order
    # downstream.
    max_seq = candidates[-1][0]
    min_pad = min(pad_widths)
    if len(str(max_seq)) > min_pad:
        raise DeckVideoError(
            "Marp emitted non-zero-padded PNG filenames "
            f"(pad width {min_pad}, max sequence {max_seq}); refusing to risk "
            "mis-ordered slides."
        )

    return [p for _, p in candidates]


# --- Duration probing --------------------------------------------------------


def _probe_duration(clip_path: str) -> float:
    """Probe an audio clip's duration via ffprobe; 0.0 on any failure.

    A direct re-probe used to floor a ``valor-tts`` duration of ``<= 0.0``.
    """
    if shutil.which("ffprobe") is None:
        return 0.0
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                clip_path,
            ],
            capture_output=True,
            timeout=15,
        )
        if result.returncode != 0:
            return 0.0
        return float(result.stdout.decode("utf-8", errors="ignore").strip() or 0.0)
    except (subprocess.TimeoutExpired, ValueError, FileNotFoundError):
        return 0.0


# --- Per-slide synthesis -----------------------------------------------------


def _synthesize_narrated_slide(slide_index: int, narration: str, clip_path: Path) -> float:
    """Synthesize one narrated slide and return its floored duration.

    Checks ``res.get("error")`` FIRST (synthesize returns ``{"error": ...}``
    with NO path/duration keys on failure -- reading them would raise
    TypeError/KeyError). Only after confirming no error does it read duration
    and apply the zero-length floor (re-probe via ffprobe, raise if still
    ``<= 0.0``).

    Args:
        slide_index: Zero-based document-order index (for error messages).
        narration: Non-empty narration text for this slide.
        clip_path: Destination OGG/Opus path for the synthesized clip.

    Returns:
        The clip's floored duration in seconds (> 0.0).

    Raises:
        DeckVideoError: on synthesis failure or an unrecoverable zero-length
        duration.
    """
    res = synthesize(text=narration, output_path=str(clip_path))

    # Error-first: synthesize() returns {"error": ...} with no path/duration on
    # failure. Reading res["duration"] (None <= 0.0 -> TypeError) or
    # res["path"] (KeyError) before this check would crash opaquely.
    if res.get("error"):
        raise DeckVideoError(f"slide {slide_index}: TTS synthesis failed: {res['error']}")

    duration = res["duration"]
    path = res["path"]

    # Zero-length guard. valor-tts duration is best-effort (0.0 when ffprobe
    # missing/fails). Re-probe directly; if still <= 0.0, refuse to emit a
    # zero-length slide.
    if duration is None or duration <= 0.0:
        reprobed = _probe_duration(path)
        if reprobed <= 0.0:
            raise DeckVideoError(
                f"slide {slide_index}: narrated clip has non-positive duration "
                f"after re-probe: {path}"
            )
        duration = reprobed

    return float(duration)


# --- ffmpeg compositing ------------------------------------------------------


def _write_concat_list(list_path: Path, holds: list[tuple[Path, float]]) -> None:
    """Write an ffmpeg concat-demuxer list with per-image durations.

    Each PNG is held for its slide's duration; the final image is repeated once
    (concat demuxer ignores the last entry's duration without a trailing repeat
    of the final file).
    """
    lines: list[str] = []
    for png, dur in holds:
        lines.append(f"file '{png.as_posix()}'")
        lines.append(f"duration {dur:.6f}")
    # Repeat the final frame so its duration is honored by the concat demuxer.
    if holds:
        last_png, _ = holds[-1]
        lines.append(f"file '{last_png.as_posix()}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_audio_track(
    work_dir: Path,
    audio_segments: list[tuple[Path | None, float]],
    ffmpeg: str,
) -> Path:
    """Build a single concatenated audio track aligned to the video timeline.

    For each slide, ``audio_segments`` carries either a narrated clip path with
    its hold duration, or ``(None, hold)`` for a silent slide. Narrated clips
    are decoded; silent slides contribute generated silence of their hold
    length so the audio and video timelines stay aligned. The concatenated
    track is re-encoded once to AAC at mux time (caller's job); here we produce
    an intermediate concatenated WAV/M4A.

    Returns the path to the combined audio file.
    """
    # Materialize each segment as a uniform intermediate (mono 44.1k AAC in m4a)
    # so concatenation is homogeneous, then concat via the demuxer.
    seg_paths: list[Path] = []
    for i, (clip, hold) in enumerate(audio_segments):
        seg_out = work_dir / f"aseg.{i:04d}.m4a"
        if clip is not None:
            # Decode the narrated clip and pad with trailing silence to exactly
            # the slide hold so audio never truncates the on-screen video.
            cmd = [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(clip),
                "-af",
                f"apad,atrim=0:{hold:.6f}",
                "-ar",
                "44100",
                "-ac",
                "1",
                "-c:a",
                "aac",
                str(seg_out),
            ]
        else:
            # Generate silence of the hold length for a silent slide.
            cmd = [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=mono:sample_rate=44100",
                "-t",
                f"{hold:.6f}",
                "-c:a",
                "aac",
                str(seg_out),
            ]
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="ignore")
            raise DeckVideoError(
                f"ffmpeg audio segment {i} build failed (exit {result.returncode}): {stderr}"
            )
        seg_paths.append(seg_out)

    # Concatenate the homogeneous segments.
    concat_list = work_dir / "audio_concat.txt"
    concat_list.write_text(
        "\n".join(f"file '{p.as_posix()}'" for p in seg_paths) + "\n",
        encoding="utf-8",
    )
    combined = work_dir / "audio_combined.m4a"
    cmd = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-c",
        "copy",
        str(combined),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=300)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="ignore")
        raise DeckVideoError(f"ffmpeg audio concat failed (exit {result.returncode}): {stderr}")
    return combined


def _composite(
    work_dir: Path,
    holds: list[tuple[Path, float]],
    audio_segments: list[tuple[Path | None, float]],
    has_audio: bool,
    output_path: Path,
    ffmpeg: str,
    total_runtime: float,
) -> None:
    """Composite PNGs (+ optional audio) into the final MP4.

    Two-pass video pipeline (see Risk 2 in the plan):
      1. Concat the per-slide PNGs via the concat demuxer with explicit
         per-image ``duration`` directives and a trailing final-frame repeat,
         WITHOUT a framerate flag, into an intermediate MP4. Letting the concat
         demuxer drive timing here yields a stream whose PTS exactly match the
         summed per-slide holds.
      2. Normalize that intermediate to a fixed ``_OUTPUT_FPS`` CFR stream (via
         the ``fps`` filter) in the final encode/mux pass, capped to the
         authoritative ``total_runtime`` via ``-t``.

    Applying ``-r``/``fps`` directly to the concat demuxer's image stream makes
    ffmpeg over-count the trailing repeated frame (the last slide's hold gets
    counted twice), inflating the runtime and desyncing audio from video. The
    two-pass split keeps the concat-demuxer timing correct in step 1; the
    ``fps`` filter in step 2 still holds the final repeated frame for an extra
    cycle, so step 2 is explicitly capped at ``total_runtime`` (the summed
    per-slide holds the compositor already computed) to trim that tail. The
    padded audio track is built to the same timeline, keeping A/V aligned.

    Branches on ``has_audio``:
      - No audio (all-silent deck): emit a VIDEO-ONLY MP4 (``-c:v libx264
        -pix_fmt yuv420p``, NO ``-c:a``, NO ``-shortest``).
      - Audio present: build the combined audio track and mux it
        (``-c:v libx264 -pix_fmt yuv420p -c:a aac``). Audio is padded to the
        video timeline (see :func:`_build_audio_track`), so ``-shortest`` is
        not relied upon to honor on-screen holds.
    """
    concat_list = work_dir / "video_concat.txt"
    _write_concat_list(concat_list, holds)

    # Pass 1: concat images into an intermediate with concat-demuxer-driven
    # timing (no framerate flag -> correct PTS, no trailing-frame over-count).
    intermediate = work_dir / "video_intermediate.mp4"
    cmd = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(intermediate),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=600)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="ignore")
        raise DeckVideoError(
            f"ffmpeg video concat (pass 1) failed (exit {result.returncode}): {stderr}"
        )

    if not has_audio:
        # Pass 2: normalize to CFR _OUTPUT_FPS, video-only.
        cmd = [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(intermediate),
            "-vf",
            f"fps={_OUTPUT_FPS}",
            "-t",
            f"{total_runtime:.6f}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=600)
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="ignore")
            raise DeckVideoError(
                f"ffmpeg video-only composite failed (exit {result.returncode}): {stderr}"
            )
        return

    combined_audio = _build_audio_track(work_dir, audio_segments, ffmpeg)
    # Pass 2: normalize video to CFR _OUTPUT_FPS and mux the padded audio track.
    cmd = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(intermediate),
        "-i",
        str(combined_audio),
        "-vf",
        f"fps={_OUTPUT_FPS}",
        "-t",
        f"{total_runtime:.6f}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=600)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="ignore")
        raise DeckVideoError(f"ffmpeg mux composite failed (exit {result.returncode}): {stderr}")


# --- Public entrypoint -------------------------------------------------------


def build_deck_video(deck_path: str | Path, output_path: str | Path | None = None) -> Path:
    """Build a narrated MP4 from a Marp deck with per-slide narration blocks.

    Pipeline:
      1. Parse per-slide ``<!-- narration: ... -->`` blocks (one per slide).
      2. Export one PNG per slide via the Marp CLI.
      3. Synthesize one ``valor-tts`` clip per NARRATED slide; floor each
         clip's duration against the zero-length guard.
      4. Assert ``len(pngs) == total_slide_count == len(narration_blocks)``.
      5. Composite via ffmpeg: hold each PNG for its narrated duration (or
         ``DECK_VIDEO_DEFAULT_HOLD_SECS`` for silent slides), mux the narration
         audio (or emit a video-only MP4 if no slide is narrated).

    All intermediate artifacts live under one temp directory removed in a
    ``try/finally`` on both success and failure; only the MP4 survives.

    Args:
        deck_path: Path to the Marp markdown deck.
        output_path: Destination MP4. Defaults to ``<deck>.mp4`` next to the
            deck.

    Returns:
        Path to the written MP4.

    Raises:
        DeckVideoError: on any prerequisite, export, synthesis, or compositing
        failure. The temp directory is always cleaned up first.
    """
    deck_path = Path(deck_path).expanduser().resolve()
    if not deck_path.is_file():
        raise DeckVideoError(f"deck not found: {deck_path}")

    missing = check_prerequisites()
    if missing:
        raise DeckVideoError("missing prerequisites: " + "; ".join(missing))

    ffmpeg = _require_binary("ffmpeg")
    _require_binary("ffprobe")

    if output_path is None:
        output_path = deck_path.with_suffix(".mp4")
    output_path = Path(output_path).expanduser().resolve()

    markdown = deck_path.read_text(encoding="utf-8")
    narration_blocks = parse_narration_blocks(markdown)
    total_slide_count = len(narration_blocks)

    tmp = tempfile.mkdtemp(prefix="deck_video_")
    work_dir = Path(tmp)
    try:
        pngs = _export_pngs(deck_path, work_dir)

        # Slide-count parity: one PNG and one narration block per slide.
        # len(pngs) > len(narration_clips) is EXPECTED for mixed decks (only
        # narrated slides get a clip) -- that is NOT a mismatch.
        if not (len(pngs) == total_slide_count == len(narration_blocks)):
            raise DeckVideoError(
                "slide-count parity violation: "
                f"len(pngs)={len(pngs)}, total_slide_count={total_slide_count}, "
                f"len(narration_blocks)={len(narration_blocks)}"
            )

        holds: list[tuple[Path, float]] = []
        audio_segments: list[tuple[Path | None, float]] = []
        narrated_total = 0.0
        silent_count = 0

        for i, (png, narration) in enumerate(zip(pngs, narration_blocks, strict=True)):
            if narration:  # non-empty (whitespace already stripped by parser)
                clip_path = work_dir / f"clip.{i:04d}.ogg"
                duration = _synthesize_narrated_slide(i, narration, clip_path)
                holds.append((png, duration))
                audio_segments.append((clip_path, duration))
                narrated_total += duration
            else:
                hold = DECK_VIDEO_DEFAULT_HOLD_SECS
                holds.append((png, hold))
                audio_segments.append((None, hold))
                silent_count += 1

        has_audio = any(clip is not None for clip, _ in audio_segments)
        total_runtime = narrated_total + silent_count * DECK_VIDEO_DEFAULT_HOLD_SECS

        _composite(work_dir, holds, audio_segments, has_audio, output_path, ffmpeg, total_runtime)

        logger.info(
            "deck_video.built slides=%d narrated=%d silent=%d runtime=%.2fs -> %s",
            total_slide_count,
            total_slide_count - silent_count,
            silent_count,
            total_runtime,
            output_path,
        )
        return output_path
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def compute_total_runtime(markdown: str, narrated_durations: list[float]) -> float:
    """Compute expected total runtime for a deck.

    ``total_runtime = sum(floored narrated durations) +
    (count_silent * DECK_VIDEO_DEFAULT_HOLD_SECS)``.

    Args:
        markdown: Marp source (to derive slide/narration counts).
        narrated_durations: The floored durations of the narrated clips, in
            document order.

    Returns:
        Expected total runtime in seconds.
    """
    blocks = parse_narration_blocks(markdown)
    silent_count = sum(1 for b in blocks if not b)
    return sum(narrated_durations) + silent_count * DECK_VIDEO_DEFAULT_HOLD_SECS
