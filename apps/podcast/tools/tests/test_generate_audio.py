"""Tests for podcast audio generation using Gemini 2.5 Native Audio API.

Run with: pytest tests/test_generate_audio.py -v
Run integration tests: pytest tests/test_generate_audio.py -v -m integration
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from prompts import (
    BEAT_SHEET_SYSTEM_PROMPT,
    GENERATION_SYSTEM_PROMPT,
    PART_1_CONTINUATION,
    PART_2_CONTINUATION,
    PART_3_CONTINUATION,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_report():
    """A minimal report.md for testing."""
    return """# Test Episode: Understanding Sleep

Sleep is essential for health. This report covers the science of sleep.

---

## 1. Scientific Basis

### What Sleep Is
Sleep is a natural state of rest. The brain cycles through stages.

### Why It Matters
- Memory consolidation
- Physical recovery
- Immune function

---

## 2. Evidence

### Key Studies
- Walker et al. (2017): 8 hours optimal for adults
- Study from Stanford: REM sleep critical for learning

### Metrics
| Stage | Duration | Function |
|-------|----------|----------|
| N1 | 5% | Light sleep |
| N2 | 50% | Memory processing |
| N3 | 20% | Deep restoration |
| REM | 25% | Dream sleep |

---

## 3. Practical Protocols

### Sleep Optimization
1. Consistent bedtime (within 30 min)
2. Cool room (65-68°F)
3. No screens 1 hour before bed

### Takeaways
- Prioritize 7-9 hours
- Quality matters as much as quantity
- Track patterns for optimization

---

## Conclusion

Sleep is the foundation of health. Optimize it first.
"""


@pytest.fixture
def temp_episode_dir(sample_report):
    """Create a temporary episode directory with report.md."""
    with tempfile.TemporaryDirectory() as tmpdir:
        episode_path = Path(tmpdir) / "2025-01-01-test-episode"
        episode_path.mkdir()

        # Create report.md
        report_path = episode_path / "report.md"
        report_path.write_text(sample_report)

        # Create required subdirectories
        (episode_path / "tmp").mkdir()
        (episode_path / "research").mkdir()

        yield episode_path


# =============================================================================
# Unit Tests: prompts.py
# =============================================================================


class TestPrompts:
    """Test the prompts module."""

    def test_beat_sheet_prompt_exists(self):
        """Verify BEAT_SHEET_SYSTEM_PROMPT is defined and non-empty."""
        assert BEAT_SHEET_SYSTEM_PROMPT is not None
        assert len(BEAT_SHEET_SYSTEM_PROMPT) > 1000
        assert "beat sheet" in BEAT_SHEET_SYSTEM_PROMPT.lower()

    def test_generation_prompt_exists(self):
        """Verify GENERATION_SYSTEM_PROMPT is defined and non-empty."""
        assert GENERATION_SYSTEM_PROMPT is not None
        assert len(GENERATION_SYSTEM_PROMPT) > 1000
        assert "yudame research" in GENERATION_SYSTEM_PROMPT.lower()

    def test_continuation_prompts_exist(self):
        """Verify all continuation prompts are defined."""
        assert PART_1_CONTINUATION is not None
        assert PART_2_CONTINUATION is not None
        assert PART_3_CONTINUATION is not None

    def test_beat_sheet_prompt_contains_required_elements(self):
        """Verify beat sheet prompt has key structural elements."""
        prompt = BEAT_SHEET_SYSTEM_PROMPT
        required_elements = [
            "CONTENT EXTRACTION",
            "NARRATIVE ARC",
            "BEAT STRUCTURE",
            "15 beats",
            "3 parts",
            "JSON",
        ]
        for element in required_elements:
            assert element in prompt, f"Missing '{element}' in beat sheet prompt"

    def test_generation_prompt_contains_voice_identity(self):
        """Verify generation prompt includes voice identity elements."""
        prompt = GENERATION_SYSTEM_PROMPT
        # Note: Voice name (Alnilam) is in AudioConfig, not the prompt
        voice_elements = [
            "baritone",
            "Yudame Research",
            "research dot yuda dot me",
            "Austrian",
        ]
        for element in voice_elements:
            assert (
                element.lower() in prompt.lower()
            ), f"Missing '{element}' in generation prompt"

    def test_part_2_has_transcript_placeholder(self):
        """Verify Part 2 continuation has transcript placeholder."""
        assert "{part_1_transcript}" in PART_2_CONTINUATION

    def test_part_3_has_transcripts_placeholder(self):
        """Verify Part 3 continuation has transcripts placeholder."""
        assert "{previous_transcripts}" in PART_3_CONTINUATION


# =============================================================================
# Unit Tests: generate_audio.py
# =============================================================================


@pytest.mark.skip(reason="Module not yet implemented: generate_audio.py")
class TestAudioConfig:
    """Test AudioConfig dataclass."""

    def test_default_values(self):
        """Verify default configuration values."""
        from generate_audio import AudioConfig

        config = AudioConfig()
        assert config.model == "gemini-2.5-flash-native-audio-latest"
        assert config.beat_sheet_model == "gemini-2.5-pro"
        assert config.voice == "Alnilam"
        assert config.temperature == 1.3
        assert config.sample_rate == 24000
        assert config.channels == 1
        assert config.sample_width == 2
        assert config.output_bitrate == "128k"
        assert config.target_duration_per_part == 720  # 12 minutes

    def test_custom_values(self):
        """Verify custom configuration values."""
        from generate_audio import AudioConfig

        config = AudioConfig(
            voice="CustomVoice", temperature=1.5, target_duration_per_part=600
        )
        assert config.voice == "CustomVoice"
        assert config.temperature == 1.5
        assert config.target_duration_per_part == 600


@pytest.mark.skip(reason="Module not yet implemented: generate_audio.py")
class TestGenerationMetrics:
    """Test GenerationMetrics dataclass."""

    def test_metrics_initialization(self):
        """Verify metrics can be initialized."""
        from generate_audio import GenerationMetrics

        metrics = GenerationMetrics(episode_slug="test-episode")
        assert metrics.episode_slug == "test-episode"
        assert metrics.part_durations == []
        assert metrics.total_duration_seconds == 0

    def test_metrics_to_dict(self):
        """Verify metrics can be converted to dict."""
        from datetime import datetime

        from generate_audio import GenerationMetrics

        metrics = GenerationMetrics(episode_slug="test-episode")
        metrics.total_duration_seconds = 2160  # 36 minutes
        metrics.final_file_size_bytes = 30 * 1024 * 1024  # 30 MB
        metrics.end_time = datetime.now()

        result = metrics.to_dict()
        assert result["episode_slug"] == "test-episode"
        assert result["duration_minutes"] == 36.0
        assert result["file_size_mb"] == 30.0


@pytest.mark.skip(reason="Module not yet implemented: generate_audio.py")
class TestAudioHelpers:
    """Test audio helper functions."""

    def test_generate_room_tone(self):
        """Verify room tone generation produces audio data."""
        from generate_audio import AudioConfig, generate_room_tone

        config = AudioConfig()
        room_tone = generate_room_tone(config)

        # Should produce 0.5 seconds of 24kHz 16-bit mono audio
        expected_bytes = int(
            config.sample_rate * config.room_tone_duration * config.sample_width
        )
        assert len(room_tone) == expected_bytes

    def test_pcm_to_wav(self):
        """Verify PCM to WAV conversion works."""
        import wave

        from generate_audio import AudioConfig, generate_room_tone, pcm_to_wav

        config = AudioConfig()
        pcm_data = generate_room_tone(config)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = Path(f.name)

        try:
            pcm_to_wav(pcm_data, config, wav_path)

            # Verify WAV file is valid
            assert wav_path.exists()
            with wave.open(str(wav_path), "rb") as wav_file:
                assert wav_file.getnchannels() == config.channels
                assert wav_file.getsampwidth() == config.sample_width
                assert wav_file.getframerate() == config.sample_rate
        finally:
            wav_path.unlink(missing_ok=True)


# =============================================================================
# Integration Tests (require API key)
# =============================================================================


@pytest.mark.skip(reason="Module not yet implemented: generate_audio.py")
@pytest.mark.integration
class TestGeminiIntegration:
    """Integration tests that call real Gemini API."""

    @pytest.fixture(autouse=True)
    def check_api_key(self):
        """Skip tests if GOOGLE_API_KEY not set."""
        if not os.environ.get("GOOGLE_API_KEY"):
            pytest.skip("GOOGLE_API_KEY not set")

    def test_import_dependencies(self):
        """Verify all dependencies can be imported."""
        from generate_audio import import_dependencies

        import_dependencies()

        # After import, these should be available
        from generate_audio import AudioSegment, genai, whisper

        assert genai is not None
        assert AudioSegment is not None
        assert whisper is not None

    @pytest.mark.asyncio
    async def test_beat_sheet_generation(self, sample_report):
        """Test beat sheet generation with real API."""
        from generate_audio import AudioConfig, generate_beat_sheet, import_dependencies

        import_dependencies()

        from generate_audio import genai

        client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
        config = AudioConfig()

        beat_sheet = await generate_beat_sheet(client, sample_report, config)

        # Verify structure
        assert "episode" in beat_sheet
        assert "parts" in beat_sheet
        assert len(beat_sheet["parts"]) == 3

        # Verify each part has beats
        for part in beat_sheet["parts"]:
            assert "part_number" in part
            assert "beats" in part
            assert len(part["beats"]) > 0

        print(
            f"\nGenerated beat sheet with {sum(len(p['beats']) for p in beat_sheet['parts'])} beats"
        )

    @pytest.mark.asyncio
    async def test_short_audio_generation(self):
        """Test audio generation with a very short prompt."""
        from generate_audio import (
            AudioConfig,
            generate_audio_segment,
            import_dependencies,
        )

        import_dependencies()

        from generate_audio import genai

        client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
        config = AudioConfig()

        # Very short prompt for quick testing
        prompt = """
        Say exactly this in a warm, professional voice:
        "Welcome to Yudame Research. Today we explore the science of sleep.
        Sleep is essential for health."
        Keep it under 15 seconds.
        """

        audio_data = await generate_audio_segment(client, prompt, config)

        # Should produce some audio
        assert len(audio_data) > 0

        # Calculate duration
        duration = len(audio_data) / (
            config.sample_rate * config.sample_width * config.channels
        )
        print(f"\nGenerated {duration:.1f}s of audio ({len(audio_data):,} bytes)")

        # Should be reasonably short (under 60 seconds for this test)
        assert duration < 60

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_full_episode_generation(self, temp_episode_dir):
        """Full end-to-end test (slow, uses significant API credits)."""
        from generate_audio import generate_episode

        output = await generate_episode(str(temp_episode_dir), verbose=True)

        # Should produce output file
        assert output is not None
        assert output.exists()
        assert output.suffix == ".mp3"

        # Check file size (should be reasonable for audio)
        file_size_mb = output.stat().st_size / (1024 * 1024)
        print(f"\nGenerated MP3: {file_size_mb:.1f} MB")

        # Check transcript was created
        transcript_path = temp_episode_dir / "transcript.txt"
        assert transcript_path.exists()

        transcript_words = len(transcript_path.read_text().split())
        print(f"Transcript: {transcript_words} words")

        # Check metrics were saved
        metrics_path = temp_episode_dir / "tmp" / "generation_metrics.json"
        assert metrics_path.exists()

        metrics = json.loads(metrics_path.read_text())
        print(f"Duration: {metrics['duration_minutes']:.1f} minutes")


# =============================================================================
# Test CLI
# =============================================================================


@pytest.mark.skip(reason="Module not yet implemented: generate_audio.py")
class TestCLI:
    """Test command-line interface."""

    def test_help_message(self):
        """Verify help message works."""
        import subprocess

        result = subprocess.run(
            [sys.executable, "generate_audio.py", "--help"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )

        assert result.returncode == 0
        assert "Generate podcast audio" in result.stdout
        assert "episode_dir" in result.stdout

    def test_missing_report_error(self):
        """Verify proper error when report.md is missing."""
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create episode directory without report.md
            episode_dir = Path(tmpdir) / "test-episode"
            episode_dir.mkdir()

            result = subprocess.run(
                [sys.executable, "generate_audio.py", str(episode_dir)],
                capture_output=True,
                text=True,
                cwd=Path(__file__).parent.parent,
            )

            assert result.returncode == 1
            assert "report.md" in result.stdout or "report.md" in result.stderr
