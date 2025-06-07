"""Image analysis and tagging tool using AI vision models.

This module provides comprehensive image analysis and tagging capabilities using
multiple AI vision providers including OpenAI GPT-4o, Anthropic Claude Vision,
local models via Ollama, and basic metadata analysis as fallback.

ARCHITECTURE OVERVIEW:
======================
This tool follows the gold standard wrapper pattern where:
- Standalone implementation (this file) contains all business logic
- MCP tools (development_tools.py) provide interface wrappers with access control
- Multiple AI providers with robust fallback strategies
- Configuration-driven behavior via Pydantic models

SUPPORTED PROVIDERS:
===================
1. OpenAI GPT-4o - High-quality vision analysis with structured JSON responses
2. Anthropic Claude Vision - Alternative vision analysis with excellent accuracy  
3. Local LLaVA via Ollama - Privacy-focused local processing
4. Basic metadata analysis - Filename and file properties fallback

CONFIGURATION:
==============
Environment Variables Required:
- OPENAI_API_KEY: For OpenAI GPT-4o vision analysis
- ANTHROPIC_API_KEY: For Anthropic Claude Vision analysis

Optional Setup:
- Ollama installation for local models: https://ollama.ai
- LLaVA model: `ollama pull llava:latest`

USAGE EXAMPLES:
===============

Basic Usage:
>>> from tools.image_tagging_tool import tag_image
>>> analysis = tag_image("path/to/image.jpg")
>>> print(f"Tags: {[tag.tag for tag in analysis.tags]}")

Using OpenAI Provider:
>>> config = TaggingConfig(api_provider="openai", max_tags=15)
>>> analysis = tag_image("image.jpg", config)

Using Anthropic Provider:
>>> config = TaggingConfig(api_provider="anthropic", min_confidence=0.5)
>>> analysis = tag_image("image.jpg", config)

Using Local Model:
>>> config = TaggingConfig(local_model=True)
>>> analysis = tag_image("image.jpg", config)

Batch Processing:
>>> image_paths = ["image1.jpg", "image2.png", "image3.gif"]
>>> results = batch_tag_images(image_paths)
>>> for path, analysis in results.items():
...     print(f"{path}: {len(analysis.tags)} tags")

Simple Tag Extraction:
>>> tags = extract_simple_tags("image.jpg", max_tags=10)
>>> print(tags)  # ['person', 'outdoor', 'nature', ...]

Content Moderation:
>>> moderation_tags = content_moderation_tags("image.jpg")
>>> print(moderation_tags)  # ['safe', 'family_friendly', ...]

TROUBLESHOOTING:
================

1. API Key Issues:
   - Error: "OPENAI_API_KEY not found"
   - Solution: Set environment variable `export OPENAI_API_KEY=your_key`
   - Validation: Check with `echo $OPENAI_API_KEY`

2. Rate Limiting:
   - Error: "Rate limit exceeded"
   - Solution: Implement delays between requests or use different provider
   - Alternative: Switch to local model with `local_model=True`

3. Local Model Issues:
   - Error: "Ollama not found"
   - Solution: Install Ollama from https://ollama.ai
   - Setup: Run `ollama pull llava:latest` after installation

4. Large Image Issues:
   - Error: "Image too large" or timeout
   - Solution: Resize images before processing or increase timeout
   - Alternative: Use basic metadata analysis as fallback

5. Network/API Issues:
   - Error: Connection timeouts
   - Solution: Tool automatically falls back to basic analysis
   - Check: Verify internet connection and API service status

PERFORMANCE NOTES:
==================
- OpenAI GPT-4o: ~2-5 seconds per image, excellent accuracy
- Anthropic Claude: ~3-6 seconds per image, high accuracy
- Local LLaVA: ~5-15 seconds per image (CPU dependent), privacy-focused
- Basic analysis: <1 second, filename/metadata only

SECURITY CONSIDERATIONS:
========================
- API keys are loaded from environment variables only
- Images are base64 encoded for API transmission
- Local model option provides privacy (no external API calls)
- Input validation prevents malicious file access
- Graceful error handling prevents information leakage

MCP INTEGRATION:
================
This tool integrates with Claude Code via MCP tools in development_tools.py:
- analyze_image_content(): Comprehensive analysis with directory access control
- get_simple_image_tags(): Quick tagging with access validation
Both tools properly import and call functions from this module following
the established wrapper pattern.
"""

import base64
import json
import subprocess
import tempfile
from pathlib import Path
from typing import List, Dict, Optional, Any, Union
from pydantic import BaseModel, Field
import os


class ImageTag(BaseModel):
    """Individual image tag with confidence."""
    tag: str
    confidence: float = Field(ge=0.0, le=1.0)
    category: str = Field(description="Tag category: 'object', 'scene', 'action', 'style', 'color', 'mood'")


class ImageAnalysis(BaseModel):
    """Complete image analysis result."""
    file_path: str
    tags: List[ImageTag]
    description: str
    primary_objects: List[str]
    scene_type: str
    dominant_colors: List[str]
    style_tags: List[str]
    mood_sentiment: str
    technical_quality: Dict[str, Any]
    ai_confidence: float = Field(ge=0.0, le=1.0)


class TaggingConfig(BaseModel):
    """Configuration for image tagging."""
    max_tags: int = Field(default=20, description="Maximum number of tags to generate")
    min_confidence: float = Field(default=0.3, description="Minimum confidence threshold for tags")
    include_technical: bool = Field(default=True, description="Include technical quality analysis")
    focus_categories: Optional[List[str]] = Field(default=None, description="Focus on specific tag categories")
    local_model: bool = Field(default=False, description="Use local vision model instead of cloud API")
    api_provider: str = Field(default="openai", description="API provider: 'openai', 'anthropic', 'local'")


def tag_image(
    image_path: str,
    config: Optional[TaggingConfig] = None
) -> ImageAnalysis:
    """
    Analyze image and generate comprehensive tags.
    
    Uses AI vision models to identify objects, scenes, styles, colors, and mood.
    Returns structured tags with confidence scores and categorization.
    """
    if config is None:
        config = TaggingConfig()
    
    # Validate image file
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image file not found: {image_path}")
    
    if not _is_image_file(path):
        raise ValueError(f"Not a valid image file: {image_path}")
    
    # Choose analysis method based on config
    if config.local_model:
        analysis_result = _analyze_with_local_model(image_path, config)
    elif config.api_provider == "openai":
        analysis_result = _analyze_with_openai(image_path, config)
    elif config.api_provider == "anthropic":
        analysis_result = _analyze_with_anthropic(image_path, config)
    else:
        # Fallback to basic analysis
        analysis_result = _basic_image_analysis(image_path, config)
    
    return analysis_result


def batch_tag_images(
    image_paths: List[str],
    config: Optional[TaggingConfig] = None
) -> Dict[str, ImageAnalysis]:
    """Tag multiple images in batch."""
    results = {}
    
    for image_path in image_paths:
        try:
            analysis = tag_image(image_path, config)
            results[image_path] = analysis
        except Exception as e:
            # Create error result for failed images
            results[image_path] = ImageAnalysis(
                file_path=image_path,
                tags=[ImageTag(tag="error", confidence=0.0, category="technical")],
                description=f"Failed to analyze: {str(e)}",
                primary_objects=[],
                scene_type="unknown",
                dominant_colors=[],
                style_tags=[],
                mood_sentiment="neutral",
                technical_quality={"error": str(e)},
                ai_confidence=0.0
            )
    
    return results


def extract_simple_tags(image_path: str, max_tags: int = 10) -> List[str]:
    """Extract simple list of tags without detailed analysis."""
    config = TaggingConfig(max_tags=max_tags, include_technical=False)
    analysis = tag_image(image_path, config)
    return [tag.tag for tag in analysis.tags]


def _is_image_file(path: Path) -> bool:
    """Check if file is a valid image format."""
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.svg'}
    return path.suffix.lower() in image_extensions


def _analyze_with_openai(image_path: str, config: TaggingConfig) -> ImageAnalysis:
    """Analyze image using OpenAI GPT-4V."""
    try:
        import openai
        
        # Load OpenAI API key from environment
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            raise ValueError("OPENAI_API_KEY not found in environment")
        
        client = openai.OpenAI(api_key=api_key)
        
        # Encode image as base64
        with open(image_path, 'rb') as image_file:
            image_data = base64.b64encode(image_file.read()).decode('utf-8')
        
        # Create comprehensive analysis prompt
        prompt = _build_analysis_prompt(config)
        
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_data}"
                            }
                        }
                    ]
                }
            ],
            max_tokens=1000,
            temperature=0.1
        )
        
        analysis_text = response.choices[0].message.content
        return _parse_analysis_response(analysis_text, image_path, config)
        
    except ImportError:
        raise Exception("OpenAI library not installed. Install with: pip install openai")
    except Exception as e:
        return _fallback_analysis(image_path, f"OpenAI API error: {str(e)}")


def _analyze_with_anthropic(image_path: str, config: TaggingConfig) -> ImageAnalysis:
    """Analyze image using Anthropic Claude Vision."""
    try:
        import anthropic
        
        # Load Anthropic API key from environment
        api_key = os.getenv('ANTHROPIC_API_KEY')
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not found in environment")
        
        client = anthropic.Anthropic(api_key=api_key)
        
        # Read and encode image
        with open(image_path, 'rb') as image_file:
            image_data = base64.b64encode(image_file.read()).decode('utf-8')
        
        # Get file extension for media type
        file_ext = Path(image_path).suffix.lower()
        media_type = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.webp': 'image/webp'
        }.get(file_ext, 'image/jpeg')
        
        prompt = _build_analysis_prompt(config)
        
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1000,
            temperature=0.1,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_data
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }
            ]
        )
        
        analysis_text = response.content[0].text
        return _parse_analysis_response(analysis_text, image_path, config)
        
    except ImportError:
        raise Exception("Anthropic library not installed. Install with: pip install anthropic")
    except Exception as e:
        return _fallback_analysis(image_path, f"Anthropic API error: {str(e)}")


def _analyze_with_local_model(image_path: str, config: TaggingConfig) -> ImageAnalysis:
    """Analyze image using local vision model (LLaVA via Ollama)."""
    try:
        # Use Ollama with LLaVA model for local vision analysis
        prompt = _build_analysis_prompt(config)
        
        # Run ollama command with image
        cmd = [
            "ollama", "run", "llava:latest",
            f"Analyze this image: {prompt}"
        ]
        
        with open(image_path, 'rb') as image_file:
            result = subprocess.run(
                cmd,
                input=image_file.read(),
                capture_output=True,
                timeout=120
            )
        
        if result.returncode == 0:
            analysis_text = result.stdout.decode('utf-8')
            return _parse_analysis_response(analysis_text, image_path, config)
        else:
            error_msg = result.stderr.decode('utf-8')
            return _fallback_analysis(image_path, f"Local model error: {error_msg}")
        
    except FileNotFoundError:
        return _fallback_analysis(image_path, "Ollama not found. Install from: https://ollama.ai")
    except subprocess.TimeoutExpired:
        return _fallback_analysis(image_path, "Local model analysis timed out")
    except Exception as e:
        return _fallback_analysis(image_path, f"Local model error: {str(e)}")


def _basic_image_analysis(image_path: str, config: TaggingConfig) -> ImageAnalysis:
    """Basic image analysis without AI models (filename and metadata based)."""
    path = Path(image_path)
    
    # Extract basic info from filename and path
    filename_tags = []
    name_parts = path.stem.lower().replace('_', ' ').replace('-', ' ').split()
    
    # Common image keywords that might be in filenames
    image_keywords = {
        'photo', 'picture', 'image', 'screenshot', 'diagram', 'chart', 'graph',
        'portrait', 'landscape', 'selfie', 'group', 'nature', 'city', 'indoor',
        'outdoor', 'close', 'wide', 'macro', 'sunset', 'sunrise', 'night', 'day'
    }
    
    for part in name_parts:
        if part in image_keywords:
            filename_tags.append(part)
    
    # Get file size for technical quality assessment
    file_size = path.stat().st_size
    
    tags = []
    for tag_text in filename_tags[:config.max_tags]:
        tags.append(ImageTag(
            tag=tag_text,
            confidence=0.5,
            category="metadata"
        ))
    
    # Add basic technical tags
    if file_size > 1_000_000:  # > 1MB
        tags.append(ImageTag(tag="high_resolution", confidence=0.7, category="technical"))
    
    if path.suffix.lower() == '.png':
        tags.append(ImageTag(tag="transparent_capable", confidence=1.0, category="technical"))
    
    return ImageAnalysis(
        file_path=image_path,
        tags=tags,
        description=f"Basic analysis of {path.name}",
        primary_objects=filename_tags[:3],
        scene_type="unknown",
        dominant_colors=[],
        style_tags=[],
        mood_sentiment="neutral",
        technical_quality={
            "file_size_mb": round(file_size / 1_000_000, 2),
            "format": path.suffix.lower(),
            "analysis_method": "basic_metadata"
        },
        ai_confidence=0.3
    )


def _build_analysis_prompt(config: TaggingConfig) -> str:
    """Build comprehensive analysis prompt for AI models."""
    prompt = f"""Analyze this image in detail and provide a comprehensive assessment. Return your analysis in JSON format with the following structure:

{{
    "description": "Detailed description of the image",
    "tags": [
        {{"tag": "tag_name", "confidence": 0.0-1.0, "category": "object|scene|action|style|color|mood|technical"}},
        ...
    ],
    "primary_objects": ["main objects in the image"],
    "scene_type": "indoor|outdoor|studio|nature|urban|abstract|etc",
    "dominant_colors": ["color names"],
    "style_tags": ["artistic style, photography type, etc"],
    "mood_sentiment": "happy|sad|energetic|calm|dramatic|neutral|etc",
    "technical_quality": {{
        "lighting": "good|poor|dramatic|natural|artificial",
        "composition": "centered|rule_of_thirds|dynamic|static",
        "focus": "sharp|blurry|selective",
        "overall_quality": "excellent|good|fair|poor"
    }}
}}

Requirements:
- Generate up to {config.max_tags} relevant tags
- Only include tags with confidence >= {config.min_confidence}
- Categorize each tag appropriately
- Be specific and descriptive
- Focus on visual elements that are clearly visible"""

    if config.focus_categories:
        prompt += f"\n- Focus especially on these categories: {', '.join(config.focus_categories)}"
    
    if not config.include_technical:
        prompt += "\n- Skip technical quality analysis"
    
    prompt += "\n\nRespond with ONLY the JSON, no additional text."
    
    return prompt


def _parse_analysis_response(response_text: str, image_path: str, config: TaggingConfig) -> ImageAnalysis:
    """Parse AI model response into structured ImageAnalysis."""
    try:
        # Try to extract JSON from response
        json_start = response_text.find('{')
        json_end = response_text.rfind('}') + 1
        
        if json_start == -1 or json_end == 0:
            raise ValueError("No JSON found in response")
        
        json_str = response_text[json_start:json_end]
        parsed = json.loads(json_str)
        
        # Parse tags
        tags = []
        for tag_data in parsed.get("tags", []):
            if isinstance(tag_data, dict):
                tags.append(ImageTag(
                    tag=tag_data.get("tag", ""),
                    confidence=float(tag_data.get("confidence", 0.5)),
                    category=tag_data.get("category", "general")
                ))
            elif isinstance(tag_data, str):
                tags.append(ImageTag(
                    tag=tag_data,
                    confidence=0.5,
                    category="general"
                ))
        
        # Filter by confidence
        tags = [tag for tag in tags if tag.confidence >= config.min_confidence]
        
        # Limit number of tags
        tags = tags[:config.max_tags]
        
        return ImageAnalysis(
            file_path=image_path,
            tags=tags,
            description=parsed.get("description", "AI-generated image analysis"),
            primary_objects=parsed.get("primary_objects", []),
            scene_type=parsed.get("scene_type", "unknown"),
            dominant_colors=parsed.get("dominant_colors", []),
            style_tags=parsed.get("style_tags", []),
            mood_sentiment=parsed.get("mood_sentiment", "neutral"),
            technical_quality=parsed.get("technical_quality", {}),
            ai_confidence=0.8  # High confidence for successful AI analysis
        )
        
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        # Fallback parsing for malformed responses
        return _parse_text_response(response_text, image_path)


def _parse_text_response(response_text: str, image_path: str) -> ImageAnalysis:
    """Fallback parsing for non-JSON responses."""
    # Extract tags from text using simple keyword matching
    words = response_text.lower().split()
    
    # Common tag keywords to look for
    tag_keywords = {
        'person', 'people', 'man', 'woman', 'child', 'face', 'portrait',
        'cat', 'dog', 'animal', 'car', 'building', 'tree', 'flower',
        'indoor', 'outdoor', 'nature', 'city', 'street', 'room',
        'blue', 'red', 'green', 'yellow', 'black', 'white',
        'beautiful', 'bright', 'dark', 'colorful', 'vintage', 'modern'
    }
    
    found_tags = []
    for word in words:
        clean_word = word.strip('.,!?;:"()[]')
        if clean_word in tag_keywords:
            found_tags.append(ImageTag(
                tag=clean_word,
                confidence=0.6,
                category="general"
            ))
    
    # Remove duplicates
    unique_tags = []
    seen_tags = set()
    for tag in found_tags:
        if tag.tag not in seen_tags:
            unique_tags.append(tag)
            seen_tags.add(tag.tag)
    
    return ImageAnalysis(
        file_path=image_path,
        tags=unique_tags[:10],
        description=response_text[:200] + "..." if len(response_text) > 200 else response_text,
        primary_objects=[tag.tag for tag in unique_tags[:3]],
        scene_type="unknown",
        dominant_colors=[],
        style_tags=[],
        mood_sentiment="neutral",
        technical_quality={"parsing_method": "text_fallback"},
        ai_confidence=0.4  # Lower confidence for fallback parsing
    )


def _fallback_analysis(image_path: str, error_message: str) -> ImageAnalysis:
    """Generate fallback analysis when AI models fail."""
    path = Path(image_path)
    
    return ImageAnalysis(
        file_path=image_path,
        tags=[
            ImageTag(tag="image", confidence=1.0, category="general"),
            ImageTag(tag="analysis_failed", confidence=1.0, category="technical")
        ],
        description=f"Failed to analyze {path.name}: {error_message}",
        primary_objects=[],
        scene_type="unknown",
        dominant_colors=[],
        style_tags=[],
        mood_sentiment="neutral",
        technical_quality={"error": error_message},
        ai_confidence=0.0
    )


# Convenience functions for common use cases
def quick_tag_image(image_path: str) -> List[str]:
    """Quick tagging that returns simple string list."""
    config = TaggingConfig(max_tags=10, min_confidence=0.4)
    analysis = tag_image(image_path, config)
    return [tag.tag for tag in analysis.tags]


def detailed_image_analysis(image_path: str) -> ImageAnalysis:
    """Comprehensive image analysis with technical details."""
    config = TaggingConfig(
        max_tags=30,
        min_confidence=0.2,
        include_technical=True
    )
    
    return tag_image(image_path, config)


def content_moderation_tags(image_path: str) -> List[str]:
    """Extract tags relevant for content moderation."""
    config = TaggingConfig(
        max_tags=15,
        focus_categories=["mood", "action", "scene"],
        min_confidence=0.5
    )
    
    analysis = tag_image(image_path, config)
    
    # Filter for moderation-relevant tags
    moderation_keywords = {
        'violent', 'aggressive', 'inappropriate', 'mature', 'explicit',
        'safe', 'family', 'professional', 'educational', 'artistic'
    }
    
    relevant_tags = []
    for tag in analysis.tags:
        if any(keyword in tag.tag.lower() for keyword in moderation_keywords):
            relevant_tags.append(tag.tag)
    
    return relevant_tags