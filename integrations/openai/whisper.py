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
            with open(file_path, "rb") as audio_file:
                kwargs = {
                    "model": model,
                    "file": audio_file,
                }
                
                if language:
                    kwargs["language"] = language
                
                transcription = self.client.audio.transcriptions.create(**kwargs)
                return transcription.text.strip()
                
        except Exception as e:
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