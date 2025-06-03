"""Voice transcription tool using OpenAI Whisper API."""

import os
import tempfile
from pathlib import Path
from typing import Optional
from integrations.openai.whisper import transcribe_voice_message


def transcribe_audio_file(
    file_path: str, 
    language: Optional[str] = None,
    cleanup_file: bool = False
) -> str:
    """
    Transcribe an audio file to text using OpenAI Whisper API.
    
    Args:
        file_path: Path to the audio file to transcribe
        language: Optional language code (e.g., 'en', 'es', 'fr') for better accuracy
        cleanup_file: Whether to delete the file after transcription
        
    Returns:
        Transcribed text from the audio file
        
    Raises:
        FileNotFoundError: If the audio file doesn't exist
        Exception: If transcription fails
    """
    try:
        from utilities.logger import get_logger
        logger = get_logger("voice_transcription_tool")
        
        logger.info(f"🔧 Starting transcription for file: {file_path}")
        logger.info(f"🌍 Language: {language or 'auto-detect'}")
        logger.info(f"🗑️ Cleanup after: {cleanup_file}")
        
        # Verify file exists
        if not Path(file_path).exists():
            logger.error(f"❌ File not found: {file_path}")
            raise FileNotFoundError(f"Audio file not found: {file_path}")
        
        # Log file details
        file_size = Path(file_path).stat().st_size
        file_ext = Path(file_path).suffix
        logger.info(f"📁 File details: size={file_size} bytes, extension={file_ext}")
        
        # Transcribe using OpenAI Whisper
        logger.info("🔄 Calling OpenAI Whisper API...")
        transcribed_text = transcribe_voice_message(file_path, language)
        logger.info(f"✅ Transcription completed: {len(transcribed_text)} characters")
        
        # Log first few words for debugging (without exposing sensitive content)
        preview = transcribed_text[:50] + "..." if len(transcribed_text) > 50 else transcribed_text
        logger.debug(f"📝 Transcription preview: {preview}")
        
        # Cleanup temporary file if requested
        if cleanup_file:
            try:
                os.unlink(file_path)
                logger.info(f"🗑️ Cleaned up temporary file: {file_path}")
            except OSError as cleanup_error:
                logger.warning(f"⚠️ Failed to cleanup file {file_path}: {cleanup_error}")
                pass  # Ignore cleanup errors
        
        return transcribed_text
        
    except FileNotFoundError:
        raise
    except Exception as e:
        from utilities.logger import get_logger
        logger = get_logger("voice_transcription_tool")
        logger.error(f"❌ Transcription failed for {file_path}: {type(e).__name__}: {str(e)}")
        
        # Cleanup on error if requested
        if cleanup_file and Path(file_path).exists():
            try:
                os.unlink(file_path)
                logger.info(f"🗑️ Cleaned up file after error: {file_path}")
            except OSError as cleanup_error:
                logger.warning(f"⚠️ Failed to cleanup file after error {file_path}: {cleanup_error}")
                pass
        raise Exception(f"Voice transcription failed: {str(e)}")


def download_and_transcribe_telegram_voice(
    message_file_ref,
    language: Optional[str] = None,
    temp_dir: Optional[str] = None
) -> str:
    """
    Download a Telegram voice/audio message and transcribe it.
    
    Args:
        message_file_ref: Telegram message with voice or audio attachment
        language: Optional language code for transcription
        temp_dir: Directory for temporary files (default: system temp)
        
    Returns:
        Transcribed text from the voice message
    """
    # Create temporary file for download
    if temp_dir is None:
        temp_dir = tempfile.gettempdir()
    
    # Generate unique filename
    temp_file = os.path.join(temp_dir, f"voice_{os.getpid()}_{id(message_file_ref)}.ogg")
    
    try:
        # This will be used by Telegram handlers to download the file
        # The actual download logic is handled by the Telegram client
        # Here we just provide the transcription logic
        
        # Note: message_file_ref.download(temp_file) would be called by the handler
        # We return the function that can be called after download
        def transcribe_downloaded_file():
            return transcribe_audio_file(temp_file, language, cleanup_file=True)
        
        return transcribe_downloaded_file
        
    except Exception as e:
        # Cleanup on error
        if Path(temp_file).exists():
            try:
                os.unlink(temp_file)
            except OSError:
                pass
        raise Exception(f"Failed to prepare voice transcription: {str(e)}")


# Convenience function for direct file transcription
def quick_transcribe(file_path: str, language: Optional[str] = None) -> str:
    """Quick transcription of an audio file."""
    return transcribe_audio_file(file_path, language, cleanup_file=False)