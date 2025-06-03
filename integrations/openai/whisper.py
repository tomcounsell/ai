"""OpenAI Whisper API client for voice transcription."""

import os
from pathlib import Path
from typing import Optional
import openai


class WhisperClient:
    """Client for OpenAI Whisper API transcription."""
    
    def __init__(self, api_key: Optional[str] = None):
        """Initialize Whisper client with OpenAI API key."""
        self.client = openai.OpenAI(
            api_key=api_key or os.environ.get("OPENAI_API_KEY")
        )
    
    async def transcribe_audio(
        self, 
        file_path: str, 
        language: Optional[str] = None,
        model: str = "whisper-1"
    ) -> str:
        """
        Transcribe audio file using OpenAI Whisper API.
        
        Args:
            file_path: Path to the audio file to transcribe
            language: Optional language code (e.g., 'en', 'es', 'fr')
            model: Whisper model to use (default: whisper-1)
            
        Returns:
            Transcribed text from the audio file
            
        Raises:
            FileNotFoundError: If audio file doesn't exist
            Exception: If transcription fails
        """
        if not Path(file_path).exists():
            raise FileNotFoundError(f"Audio file not found: {file_path}")
        
        try:
            with open(file_path, "rb") as audio_file:
                kwargs = {
                    "model": model,
                    "file": audio_file,
                }
                
                # Add language parameter if specified
                if language:
                    kwargs["language"] = language
                
                transcription = self.client.audio.transcriptions.create(**kwargs)
                return transcription.text.strip()
                
        except Exception as e:
            raise Exception(f"Whisper transcription failed: {str(e)}")
    
    def transcribe_audio_sync(
        self, 
        file_path: str, 
        language: Optional[str] = None,
        model: str = "whisper-1"
    ) -> str:
        """
        Synchronous version of transcribe_audio.
        
        Args:
            file_path: Path to the audio file to transcribe
            language: Optional language code (e.g., 'en', 'es', 'fr')
            model: Whisper model to use (default: whisper-1)
            
        Returns:
            Transcribed text from the audio file
        """
        if not Path(file_path).exists():
            raise FileNotFoundError(f"Audio file not found: {file_path}")
        
        try:
            from utilities.logger import get_logger
            logger = get_logger("openai.whisper_client")
            
            logger.info(f"🎯 OpenAI Whisper API call starting")
            logger.info(f"📁 File: {file_path}")
            logger.info(f"🌍 Language: {language or 'auto-detect'}")
            logger.info(f"🤖 Model: {model}")
            
            with open(file_path, "rb") as audio_file:
                kwargs = {
                    "model": model,
                    "file": audio_file,
                }
                
                if language:
                    kwargs["language"] = language
                
                logger.info("📡 Sending request to OpenAI Whisper API...")
                transcription = self.client.audio.transcriptions.create(**kwargs)
                
                result_text = transcription.text.strip()
                logger.info(f"✅ OpenAI API response received: {len(result_text)} characters")
                logger.debug(f"📝 Response preview: {result_text[:50]}...")
                
                return result_text
                
        except Exception as e:
            from utilities.logger import get_logger
            logger = get_logger("openai.whisper_client")
            logger.error(f"❌ OpenAI Whisper API call failed: {type(e).__name__}: {str(e)}")
            
            # Log specific error types for debugging
            if "api_key" in str(e).lower():
                logger.error("🔑 API Key issue detected - check OPENAI_API_KEY environment variable")
            elif "rate limit" in str(e).lower():
                logger.error("⏰ Rate limit exceeded - too many API calls")
            elif "file" in str(e).lower():
                logger.error("📁 File format or processing issue")
            elif "network" in str(e).lower() or "connection" in str(e).lower():
                logger.error("🌐 Network connectivity issue")
            
            raise Exception(f"Whisper transcription failed: {str(e)}")


# Global instance for easy access
whisper_client = WhisperClient()


def transcribe_voice_message(file_path: str, language: Optional[str] = None) -> str:
    """
    Simple function to transcribe voice message.
    
    Args:
        file_path: Path to the voice/audio file
        language: Optional language code for transcription
        
    Returns:
        Transcribed text
    """
    return whisper_client.transcribe_audio_sync(file_path, language)