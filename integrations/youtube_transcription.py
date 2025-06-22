"""YouTube Transcription Integration using transcribe-anything library.

This module provides comprehensive YouTube video transcription capabilities,
including individual videos, playlist monitoring, and automatic learning
integration with the Valor-Claude AI system.
"""

import asyncio
import hashlib
import json
import os
import subprocess
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union
from urllib.parse import parse_qs, urlparse

import aiohttp
import requests
from utilities.logger import get_logger

logger = get_logger("youtube_transcription")


class YouTubeTranscriptionError(Exception):
    """Custom exception for YouTube transcription errors."""
    pass


class YouTubeTranscriptor:
    """YouTube video transcription manager using transcribe-anything."""

    def __init__(self, device: str = "cpu", storage_dir: Optional[str] = None):
        """
        Initialize YouTube transcriptor.

        Args:
            device: Transcription device mode ("cpu", "insane", "mlx")
            storage_dir: Directory to store transcriptions (default: ai/transcriptions)
        """
        self.device = device

        # Resolve storage directory relative to project root if not provided
        if storage_dir is None:
            # Get the project root (parent of the parent of this file)
            project_root = Path(__file__).parent.parent
            self.storage_dir = project_root / "transcriptions"
        else:
            self.storage_dir = Path(storage_dir)

        self.storage_dir.mkdir(exist_ok=True)

        # Validate device options
        valid_devices = ["cpu", "insane", "mlx"]
        if device not in valid_devices:
            logger.warning(f"⚠️ Invalid device '{device}', using 'cpu'. Valid options: {valid_devices}")
            self.device = "cpu"

        logger.info(f"🚀 YouTube Transcriptor initialized with device: {self.device}")
        logger.info(f"📁 Storage directory: {self.storage_dir.absolute()}")

    def _validate_youtube_url(self, url: str) -> Tuple[bool, str, Optional[str]]:
        """
        Validate and extract information from YouTube URL.

        Returns:
            (is_valid, video_id, playlist_id)
        """
        try:
            parsed = urlparse(url)
            if parsed.hostname not in ['www.youtube.com', 'youtube.com', 'youtu.be', 'm.youtube.com']:
                return False, "", None

            # Extract video ID
            video_id = None
            playlist_id = None

            if parsed.hostname == 'youtu.be':
                video_id = parsed.path[1:]
            elif parsed.path == '/watch':
                query_params = parse_qs(parsed.query)
                video_id = query_params.get('v', [None])[0]
                playlist_id = query_params.get('list', [None])[0]
            elif parsed.path.startswith('/playlist'):
                query_params = parse_qs(parsed.query)
                playlist_id = query_params.get('list', [None])[0]

            if not video_id and not playlist_id:
                return False, "", None

            return True, video_id or "", playlist_id

        except Exception as e:
            logger.error(f"❌ URL validation error: {e}")
            return False, "", None

    def _check_transcribe_anything_installed(self) -> bool:
        """Check if transcribe-anything is installed and available."""
        try:
            result = subprocess.run(
                ["transcribe-anything", "--help"],
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _install_transcribe_anything(self) -> bool:
        """Install transcribe-anything if not available."""
        try:
            logger.info("📦 Installing transcribe-anything...")
            result = subprocess.run(
                ["pip", "install", "transcribe-anything"],
                capture_output=True,
                text=True,
                timeout=300  # 5 minutes timeout
            )

            if result.returncode == 0:
                logger.info("✅ transcribe-anything installed successfully")
                return True
            else:
                logger.error(f"❌ Installation failed: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            logger.error("❌ Installation timeout after 5 minutes")
            return False
        except Exception as e:
            logger.error(f"❌ Installation error: {e}")
            return False

    def _get_video_metadata(self, video_id: str) -> Dict:
        """Extract basic metadata from YouTube video (title, duration, etc.)."""
        try:
            # Use yt-dlp to get metadata without downloading
            result = subprocess.run([
                "yt-dlp",
                "--dump-json",
                "--no-download",
                f"https://www.youtube.com/watch?v={video_id}"
            ], capture_output=True, text=True, timeout=30)

            if result.returncode == 0:
                metadata = json.loads(result.stdout)
                return {
                    "title": metadata.get("title", "Unknown Title"),
                    "duration": metadata.get("duration", 0),
                    "uploader": metadata.get("uploader", "Unknown"),
                    "upload_date": metadata.get("upload_date", "Unknown"),
                    "description": metadata.get("description", "")[:500] + "..."
                        if len(metadata.get("description", "")) > 500 else metadata.get("description", ""),
                    "view_count": metadata.get("view_count", 0),
                    "like_count": metadata.get("like_count", 0)
                }
            else:
                logger.warning(f"⚠️ Could not extract metadata for video {video_id}")
                return {"title": f"Video {video_id}", "duration": 0}

        except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as e:
            logger.warning(f"⚠️ Metadata extraction failed for {video_id}: {e}")
            return {"title": f"Video {video_id}", "duration": 0}

    def transcribe_video(
        self,
        youtube_url: str,
        batch_size: Optional[int] = None,
        verbose: bool = True,
        flash: bool = False,
        save_to_file: bool = True
    ) -> Dict:
        """
        Transcribe a single YouTube video.

        Args:
            youtube_url: YouTube video URL
            batch_size: Batch size for transcription (device dependent)
            verbose: Enable verbose output
            flash: Use flash attention (for compatible devices)
            save_to_file: Save transcription to file

        Returns:
            Dictionary with transcription results and metadata
        """
        logger.info(f"🎥 Starting transcription for: {youtube_url}")

        # Validate URL
        is_valid, video_id, playlist_id = self._validate_youtube_url(youtube_url)
        if not is_valid or not video_id:
            raise YouTubeTranscriptionError(f"Invalid YouTube video URL: {youtube_url}")

        # Check if already transcribed
        cache_file = self.storage_dir / f"{video_id}.json"
        if cache_file.exists():
            logger.info(f"📄 Loading cached transcription for {video_id}")
            with open(cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)

        # Ensure transcribe-anything is available
        if not self._check_transcribe_anything_installed():
            logger.info("📦 transcribe-anything not found, installing...")
            if not self._install_transcribe_anything():
                raise YouTubeTranscriptionError("Failed to install transcribe-anything")

        # Get video metadata
        metadata = self._get_video_metadata(video_id)
        logger.info(f"📹 Video: {metadata.get('title', 'Unknown')} ({metadata.get('duration', 0)}s)")

        # Build transcription command
        cmd = ["transcribe-anything", youtube_url, "--device", self.device]

        if batch_size:
            cmd.extend(["--batch_size", str(batch_size)])
        if verbose:
            cmd.append("--verbose")
        if flash and self.device in ["insane", "mlx"]:
            cmd.extend(["--flash", "True"])

        try:
            logger.info(f"🔄 Running transcription command: {' '.join(cmd)}")

            # Run transcription with progress tracking
            start_time = datetime.now()
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3600  # 1 hour timeout for long videos
            )

            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()

            if result.returncode != 0:
                error_msg = f"Transcription failed: {result.stderr}"
                logger.error(f"❌ {error_msg}")
                raise YouTubeTranscriptionError(error_msg)

            transcription_text = result.stdout.strip()
            if not transcription_text:
                raise YouTubeTranscriptionError("Empty transcription result")

            # Create result object
            transcription_result = {
                "video_id": video_id,
                "url": youtube_url,
                "transcription": transcription_text,
                "metadata": metadata,
                "transcription_info": {
                    "device": self.device,
                    "duration_seconds": duration,
                    "timestamp": datetime.now().isoformat(),
                    "batch_size": batch_size,
                    "flash": flash,
                    "character_count": len(transcription_text),
                    "word_count": len(transcription_text.split())
                }
            }

            # Save to file if requested
            if save_to_file:
                with open(cache_file, 'w', encoding='utf-8') as f:
                    json.dump(transcription_result, f, indent=2, ensure_ascii=False)
                logger.info(f"💾 Transcription saved to: {cache_file}")

            logger.info(f"✅ Transcription completed in {duration:.1f}s")
            logger.info(f"📊 Stats: {len(transcription_text)} chars, {len(transcription_text.split())} words")

            return transcription_result

        except subprocess.TimeoutExpired:
            error_msg = f"Transcription timeout after 1 hour for video {video_id}"
            logger.error(f"❌ {error_msg}")
            raise YouTubeTranscriptionError(error_msg)
        except Exception as e:
            error_msg = f"Transcription error for video {video_id}: {str(e)}"
            logger.error(f"❌ {error_msg}")
            raise YouTubeTranscriptionError(error_msg)

    def transcribe_playlist(
        self,
        playlist_url: str,
        max_videos: Optional[int] = None,
        skip_existing: bool = True,
        batch_size: Optional[int] = None
    ) -> List[Dict]:
        """
        Transcribe all videos in a YouTube playlist.

        Args:
            playlist_url: YouTube playlist URL
            max_videos: Maximum number of videos to process
            skip_existing: Skip videos that are already transcribed
            batch_size: Batch size for transcription

        Returns:
            List of transcription results
        """
        logger.info(f"📝 Starting playlist transcription: {playlist_url}")

        # Validate playlist URL
        is_valid, _, playlist_id = self._validate_youtube_url(playlist_url)
        if not is_valid or not playlist_id:
            raise YouTubeTranscriptionError(f"Invalid YouTube playlist URL: {playlist_url}")

        try:
            # Get playlist video URLs using yt-dlp
            result = subprocess.run([
                "yt-dlp",
                "--flat-playlist",
                "--print", "url",
                playlist_url
            ], capture_output=True, text=True, timeout=60)

            if result.returncode != 0:
                raise YouTubeTranscriptionError(f"Failed to extract playlist URLs: {result.stderr}")

            video_urls = [url.strip() for url in result.stdout.strip().split('\n') if url.strip()]

            if max_videos:
                video_urls = video_urls[:max_videos]

            logger.info(f"🔍 Found {len(video_urls)} videos in playlist")

            # Transcribe each video
            results = []
            for i, video_url in enumerate(video_urls, 1):
                try:
                    logger.info(f"🎬 Processing video {i}/{len(video_urls)}: {video_url}")

                    # Check if already exists
                    _, video_id, _ = self._validate_youtube_url(video_url)
                    cache_file = self.storage_dir / f"{video_id}.json"

                    if skip_existing and cache_file.exists():
                        logger.info(f"⏭️ Skipping existing transcription for {video_id}")
                        with open(cache_file, 'r', encoding='utf-8') as f:
                            results.append(json.load(f))
                        continue

                    # Transcribe video
                    result = self.transcribe_video(
                        video_url,
                        batch_size=batch_size,
                        verbose=False  # Reduce verbosity for batch processing
                    )
                    results.append(result)

                    # Brief pause between videos to avoid rate limiting
                    time.sleep(2)

                except Exception as e:
                    logger.error(f"❌ Failed to transcribe video {i}: {video_url} - {e}")
                    continue

            logger.info(f"✅ Playlist transcription completed: {len(results)} videos processed")
            return results

        except subprocess.TimeoutExpired:
            raise YouTubeTranscriptionError("Playlist extraction timeout")
        except Exception as e:
            raise YouTubeTranscriptionError(f"Playlist transcription error: {str(e)}")

    def get_transcription_summary(self, video_id: str) -> Optional[Dict]:
        """Get a summary of a transcribed video."""
        cache_file = self.storage_dir / f"{video_id}.json"
        if not cache_file.exists():
            return None

        with open(cache_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        return {
            "video_id": data["video_id"],
            "title": data["metadata"].get("title", "Unknown"),
            "duration": data["metadata"].get("duration", 0),
            "uploader": data["metadata"].get("uploader", "Unknown"),
            "transcription_length": data["transcription_info"]["character_count"],
            "word_count": data["transcription_info"]["word_count"],
            "transcribed_at": data["transcription_info"]["timestamp"],
            "device_used": data["transcription_info"]["device"]
        }

    def search_transcriptions(self, query: str, limit: int = 10) -> List[Dict]:
        """Search through stored transcriptions for specific content."""
        results = []

        for cache_file in self.storage_dir.glob("*.json"):
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                # Search in transcription text and title
                transcription = data.get("transcription", "").lower()
                title = data.get("metadata", {}).get("title", "").lower()

                if query.lower() in transcription or query.lower() in title:
                    # Find context around the match
                    context_start = max(0, transcription.find(query.lower()) - 100)
                    context_end = min(len(transcription), context_start + 300)
                    context = transcription[context_start:context_end]

                    results.append({
                        "video_id": data["video_id"],
                        "title": data["metadata"].get("title", "Unknown"),
                        "url": data["url"],
                        "context": context,
                        "relevance_score": transcription.count(query.lower()) + title.count(query.lower())
                    })

            except Exception as e:
                logger.warning(f"⚠️ Error searching {cache_file}: {e}")
                continue

        # Sort by relevance and limit results
        results.sort(key=lambda x: x["relevance_score"], reverse=True)
        return results[:limit]

    def cleanup_old_transcriptions(self, days_old: int = 30) -> int:
        """Clean up transcription files older than specified days."""
        cutoff_date = datetime.now() - timedelta(days=days_old)
        cleaned_count = 0

        for cache_file in self.storage_dir.glob("*.json"):
            try:
                file_time = datetime.fromtimestamp(cache_file.stat().st_mtime)
                if file_time < cutoff_date:
                    cache_file.unlink()
                    cleaned_count += 1
                    logger.info(f"🗑️ Cleaned up old transcription: {cache_file.name}")
            except Exception as e:
                logger.warning(f"⚠️ Error cleaning {cache_file}: {e}")

        logger.info(f"🧹 Cleanup completed: {cleaned_count} old transcriptions removed")
        return cleaned_count


# Convenience functions for easy integration

def transcribe_youtube_video(
    url: str,
    device: str = "cpu",
    save_results: bool = True
) -> Dict:
    """
    Quick function to transcribe a single YouTube video.

    Args:
        url: YouTube video URL
        device: Device mode ("cpu", "insane", "mlx")
        save_results: Whether to save transcription to file

    Returns:
        Transcription result dictionary
    """
    transcriptor = YouTubeTranscriptor(device=device)
    return transcriptor.transcribe_video(url, save_to_file=save_results)


def transcribe_youtube_playlist(
    url: str,
    device: str = "cpu",
    max_videos: Optional[int] = None
) -> List[Dict]:
    """
    Quick function to transcribe a YouTube playlist.

    Args:
        url: YouTube playlist URL
        device: Device mode ("cpu", "insane", "mlx")
        max_videos: Maximum number of videos to process

    Returns:
        List of transcription results
    """
    transcriptor = YouTubeTranscriptor(device=device)
    return transcriptor.transcribe_playlist(url, max_videos=max_videos)


def search_ai_content(query: str, limit: int = 5) -> List[Dict]:
    """
    Search through AI-related transcriptions for specific content.

    Args:
        query: Search query
        limit: Maximum number of results

    Returns:
        List of matching transcription excerpts
    """
    transcriptor = YouTubeTranscriptor()
    return transcriptor.search_transcriptions(query, limit)


# Auto-learning integration for AI best practices
class AIContentLearner:
    """Automatic learning system for AI best practices from YouTube content."""

    def __init__(self, storage_dir: Optional[str] = None):
        self.transcriptor = YouTubeTranscriptor(storage_dir=storage_dir)

        # Use the same storage directory as the transcriptor
        if storage_dir is None:
            project_root = Path(__file__).parent.parent
            storage_path = project_root / "transcriptions"
        else:
            storage_path = Path(storage_dir)

        self.learning_log = storage_path / "learning_log.json"

    def learn_from_video(self, youtube_url: str, tags: List[str] = None) -> Dict:
        """
        Transcribe and categorize an AI-related video for learning.

        Args:
            youtube_url: YouTube video URL
            tags: Optional tags for categorization

        Returns:
            Learning result with transcription and insights
        """
        # Transcribe the video
        result = self.transcriptor.transcribe_video(youtube_url)

        # Add learning metadata
        result["learning_info"] = {
            "tags": tags or [],
            "learned_at": datetime.now().isoformat(),
            "category": self._categorize_content(result["transcription"]),
            "key_concepts": self._extract_key_concepts(result["transcription"])
        }

        # Log the learning activity
        self._log_learning_activity(result)

        return result

    def _categorize_content(self, transcription: str) -> str:
        """Categorize the content based on keywords."""
        categories = {
            "machine_learning": ["machine learning", "ml", "neural network", "training", "model"],
            "ai_ethics": ["ethics", "bias", "fairness", "responsible ai", "alignment"],
            "llm": ["large language model", "llm", "transformer", "gpt", "claude"],
            "prompt_engineering": ["prompt", "prompting", "few-shot", "chain of thought"],
            "ai_tools": ["tool", "integration", "api", "framework", "library"],
            "research": ["paper", "research", "study", "experiment", "evaluation"]
        }

        text_lower = transcription.lower()
        category_scores = {}

        for category, keywords in categories.items():
            score = sum(text_lower.count(keyword) for keyword in keywords)
            category_scores[category] = score

        return max(category_scores, key=category_scores.get) if category_scores else "general"

    def _extract_key_concepts(self, transcription: str) -> List[str]:
        """Extract key AI concepts from the transcription."""
        # This is a simple implementation - could be enhanced with NLP
        key_terms = [
            "attention mechanism", "transformer", "fine-tuning", "rag", "retrieval augmented",
            "prompt engineering", "few-shot learning", "chain of thought", "reasoning",
            "alignment", "rlhf", "constitutional ai", "safety", "interpretability",
            "multimodal", "embeddings", "vector database", "semantic search",
            "agent", "tool use", "function calling", "planning", "memory"
        ]

        text_lower = transcription.lower()
        found_concepts = [term for term in key_terms if term in text_lower]

        return found_concepts[:10]  # Limit to top 10

    def _log_learning_activity(self, result: Dict):
        """Log learning activity to a persistent log."""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "video_id": result["video_id"],
            "title": result["metadata"].get("title", "Unknown"),
            "category": result["learning_info"]["category"],
            "key_concepts": result["learning_info"]["key_concepts"],
            "tags": result["learning_info"]["tags"]
        }

        # Load existing log
        log_data = []
        if self.learning_log.exists():
            with open(self.learning_log, 'r', encoding='utf-8') as f:
                log_data = json.load(f)

        # Add new entry
        log_data.append(log_entry)

        # Save updated log
        with open(self.learning_log, 'w', encoding='utf-8') as f:
            json.dump(log_data, f, indent=2, ensure_ascii=False)
