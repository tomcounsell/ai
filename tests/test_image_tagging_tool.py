"""Tests for image_tagging_tool.py - Image analysis and tagging functionality."""

import pytest
import tempfile
import os
import json
import base64
from pathlib import Path
from unittest.mock import patch, MagicMock
from tools.image_tagging_tool import (
    ImageTag,
    ImageAnalysis,
    TaggingConfig,
    tag_image,
    batch_tag_images,
    extract_simple_tags,
    quick_tag_image,
    detailed_image_analysis,
    content_moderation_tags,
    _is_image_file,
    _analyze_with_openai,
    _analyze_with_anthropic,
    _analyze_with_local_model,
    _basic_image_analysis,
    _build_analysis_prompt,
    _parse_analysis_response,
    _fallback_analysis
)


class TestImageTag:
    """Test ImageTag model validation."""
    
    def test_valid_image_tag(self):
        """Test creating valid image tag."""
        tag = ImageTag(
            tag="person",
            confidence=0.95,
            category="object"
        )
        
        assert tag.tag == "person"
        assert tag.confidence == 0.95
        assert tag.category == "object"
    
    def test_confidence_bounds(self):
        """Test confidence score validation."""
        # Valid boundary values
        tag_low = ImageTag(tag="test", confidence=0.0, category="general")
        tag_high = ImageTag(tag="test", confidence=1.0, category="general")
        
        assert tag_low.confidence == 0.0
        assert tag_high.confidence == 1.0


class TestImageAnalysis:
    """Test ImageAnalysis model validation."""
    
    def test_valid_image_analysis(self):
        """Test creating valid image analysis."""
        tags = [
            ImageTag(tag="person", confidence=0.9, category="object"),
            ImageTag(tag="outdoor", confidence=0.8, category="scene")
        ]
        
        analysis = ImageAnalysis(
            file_path="/path/to/image.jpg",
            tags=tags,
            description="A person standing outdoors",
            primary_objects=["person"],
            scene_type="outdoor",
            dominant_colors=["blue", "green"],
            style_tags=["photography", "natural"],
            mood_sentiment="positive",
            technical_quality={"lighting": "good", "focus": "sharp"},
            ai_confidence=0.85
        )
        
        assert analysis.file_path == "/path/to/image.jpg"
        assert len(analysis.tags) == 2
        assert analysis.scene_type == "outdoor"
        assert analysis.ai_confidence == 0.85
    
    def test_ai_confidence_bounds(self):
        """Test AI confidence validation."""
        analysis = ImageAnalysis(
            file_path="test.jpg",
            tags=[],
            description="test",
            primary_objects=[],
            scene_type="unknown",
            dominant_colors=[],
            style_tags=[],
            mood_sentiment="neutral",
            technical_quality={},
            ai_confidence=0.5
        )
        
        assert analysis.ai_confidence == 0.5


class TestTaggingConfig:
    """Test TaggingConfig model validation."""
    
    def test_default_config(self):
        """Test default configuration values."""
        config = TaggingConfig()
        
        assert config.max_tags == 20
        assert config.min_confidence == 0.3
        assert config.include_technical == True
        assert config.focus_categories is None
        assert config.local_model == False
        assert config.api_provider == "openai"
    
    def test_custom_config(self):
        """Test custom configuration."""
        config = TaggingConfig(
            max_tags=10,
            min_confidence=0.5,
            include_technical=False,
            focus_categories=["object", "scene"],
            local_model=True,
            api_provider="anthropic"
        )
        
        assert config.max_tags == 10
        assert config.min_confidence == 0.5
        assert config.include_technical == False
        assert config.focus_categories == ["object", "scene"]
        assert config.local_model == True
        assert config.api_provider == "anthropic"


# ==================== TEST FIXTURES ====================

@pytest.fixture
def temp_image_file():
    """Create temporary image file (fake)."""
    # Create a simple 1x1 PNG image data
    png_data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChAGAD7TL5gAAAABJRU5ErkJggg=="
    )
    
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
        f.write(png_data)
        temp_path = f.name
    
    yield temp_path
    
    try:
        os.unlink(temp_path)
    except:
        pass


@pytest.fixture
def temp_jpeg_file():
    """Create temporary JPEG file."""
    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
        # Write minimal JPEG header
        f.write(b'\xFF\xD8\xFF\xE0')
        temp_path = f.name
    
    yield temp_path
    
    try:
        os.unlink(temp_path)
    except:
        pass


@pytest.fixture
def temp_non_image_file():
    """Create temporary non-image file."""
    with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
        f.write(b"This is not an image file")
        temp_path = f.name
    
    yield temp_path
    
    try:
        os.unlink(temp_path)
    except:
        pass


# ==================== TEST CLASSES ====================


class TestIsImageFile:
    """Test image file validation functionality."""
    
    def test_valid_image_extensions(self):
        """Test valid image file extensions."""
        valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.svg']
        
        for ext in valid_extensions:
            path = Path(f"test{ext}")
            assert _is_image_file(path) == True
            
            # Test uppercase
            path_upper = Path(f"test{ext.upper()}")
            assert _is_image_file(path_upper) == True
    
    def test_invalid_image_extensions(self):
        """Test invalid file extensions."""
        invalid_extensions = ['.txt', '.pdf', '.doc', '.mp4', '.mp3', '.zip']
        
        for ext in invalid_extensions:
            path = Path(f"test{ext}")
            assert _is_image_file(path) == False


class TestBuildAnalysisPrompt:
    """Test analysis prompt building functionality."""
    
    def test_basic_prompt_construction(self):
        """Test basic prompt construction."""
        config = TaggingConfig()
        prompt = _build_analysis_prompt(config)
        
        assert "Analyze this image in detail" in prompt
        assert "JSON format" in prompt
        assert f"up to {config.max_tags} relevant tags" in prompt
        assert f"confidence >= {config.min_confidence}" in prompt
        assert "object|scene|action|style|color|mood|technical" in prompt
    
    def test_focus_categories_inclusion(self):
        """Test that focus categories are included in prompt."""
        config = TaggingConfig(focus_categories=["object", "action"])
        prompt = _build_analysis_prompt(config)
        
        assert "Focus especially on these categories: object, action" in prompt
    
    def test_technical_quality_exclusion(self):
        """Test excluding technical quality analysis."""
        config = TaggingConfig(include_technical=False)
        prompt = _build_analysis_prompt(config)
        
        assert "Skip technical quality analysis" in prompt
    
    def test_custom_limits(self):
        """Test custom tag limits in prompt."""
        config = TaggingConfig(max_tags=15, min_confidence=0.6)
        prompt = _build_analysis_prompt(config)
        
        assert "up to 15 relevant tags" in prompt
        assert "confidence >= 0.6" in prompt


class TestParseAnalysisResponse:
    """Test analysis response parsing functionality."""
    
    def test_valid_json_parsing(self):
        """Test parsing valid JSON response."""
        json_response = json.dumps({
            "description": "A beautiful landscape photo",
            "tags": [
                {"tag": "landscape", "confidence": 0.95, "category": "scene"},
                {"tag": "mountain", "confidence": 0.8, "category": "object"},
                {"tag": "nature", "confidence": 0.9, "category": "scene"}
            ],
            "primary_objects": ["mountain", "trees"],
            "scene_type": "outdoor",
            "dominant_colors": ["green", "blue"],
            "style_tags": ["photography", "landscape"],
            "mood_sentiment": "peaceful",
            "technical_quality": {
                "lighting": "good",
                "composition": "rule_of_thirds",
                "focus": "sharp"
            }
        })
        
        config = TaggingConfig()
        analysis = _parse_analysis_response(json_response, "test.jpg", config)
        
        assert analysis.file_path == "test.jpg"
        assert len(analysis.tags) == 3
        assert analysis.tags[0].tag == "landscape"
        assert analysis.tags[0].confidence == 0.95
        assert analysis.scene_type == "outdoor"
        assert analysis.dominant_colors == ["green", "blue"]
        assert analysis.technical_quality["lighting"] == "good"
    
    def test_confidence_filtering(self):
        """Test filtering tags by confidence threshold."""
        json_response = json.dumps({
            "description": "Test image",
            "tags": [
                {"tag": "high_conf", "confidence": 0.8, "category": "object"},
                {"tag": "low_conf", "confidence": 0.2, "category": "object"},
                {"tag": "medium_conf", "confidence": 0.5, "category": "scene"}
            ],
            "primary_objects": [],
            "scene_type": "unknown",
            "dominant_colors": [],
            "style_tags": [],
            "mood_sentiment": "neutral",
            "technical_quality": {}
        })
        
        config = TaggingConfig(min_confidence=0.4)
        analysis = _parse_analysis_response(json_response, "test.jpg", config)
        
        # Should only include tags with confidence >= 0.4
        assert len(analysis.tags) == 2
        tag_names = [tag.tag for tag in analysis.tags]
        assert "high_conf" in tag_names
        assert "medium_conf" in tag_names
        assert "low_conf" not in tag_names
    
    def test_tag_limit_enforcement(self):
        """Test enforcing maximum tag limit."""
        tags_data = [
            {"tag": f"tag_{i}", "confidence": 0.8, "category": "object"}
            for i in range(25)  # More than default max
        ]
        
        json_response = json.dumps({
            "description": "Image with many tags",
            "tags": tags_data,
            "primary_objects": [],
            "scene_type": "unknown",
            "dominant_colors": [],
            "style_tags": [],
            "mood_sentiment": "neutral",
            "technical_quality": {}
        })
        
        config = TaggingConfig(max_tags=10)
        analysis = _parse_analysis_response(json_response, "test.jpg", config)
        
        assert len(analysis.tags) <= 10
    
    def test_malformed_json_fallback(self):
        """Test fallback parsing for malformed JSON."""
        malformed_response = "This is a beautiful outdoor scene with mountains and trees."
        
        config = TaggingConfig()
        analysis = _parse_analysis_response(malformed_response, "test.jpg", config)
        
        assert analysis.file_path == "test.jpg"
        assert analysis.ai_confidence < 0.5  # Lower confidence for fallback
        assert "text_fallback" in str(analysis.technical_quality)
    
    def test_embedded_json_extraction(self):
        """Test extracting JSON from mixed content."""
        mixed_response = '''
Looking at this image, I can provide the following analysis:

{
    "description": "A cat sitting on a windowsill",
    "tags": [
        {"tag": "cat", "confidence": 0.95, "category": "object"},
        {"tag": "indoor", "confidence": 0.8, "category": "scene"}
    ],
    "primary_objects": ["cat"],
    "scene_type": "indoor",
    "dominant_colors": ["brown", "white"],
    "style_tags": ["photography"],
    "mood_sentiment": "calm",
    "technical_quality": {"lighting": "natural"}
}

Additional observations: The cat appears relaxed.
'''
        
        config = TaggingConfig()
        analysis = _parse_analysis_response(mixed_response, "test.jpg", config)
        
        assert analysis.file_path == "test.jpg"
        assert len(analysis.tags) == 2
        assert analysis.tags[0].tag == "cat"
        assert analysis.scene_type == "indoor"


class TestBasicImageAnalysis:
    """Test basic image analysis functionality."""
    
    def test_filename_based_analysis(self, temp_image_file):
        """Test analysis based on filename."""
        # Rename to include recognizable keywords
        test_path = temp_image_file.replace(".png", "_sunset_landscape_photo.png")
        os.rename(temp_image_file, test_path)
        
        try:
            config = TaggingConfig()
            analysis = _basic_image_analysis(test_path, config)
            
            assert analysis.file_path == test_path
            assert len(analysis.tags) > 0
            
            # Should extract keywords from filename
            tag_names = [tag.tag for tag in analysis.tags]
            assert any(tag in ["sunset", "landscape", "photo"] for tag in tag_names)
            
            assert analysis.ai_confidence < 0.5  # Low confidence for basic analysis
        finally:
            try:
                os.unlink(test_path)
            except:
                pass
    
    def test_file_size_technical_analysis(self, temp_image_file):
        """Test file size based technical analysis."""
        config = TaggingConfig()
        analysis = _basic_image_analysis(temp_image_file, config)
        
        assert "file_size_mb" in analysis.technical_quality
        assert "format" in analysis.technical_quality
        assert "analysis_method" in analysis.technical_quality
        assert analysis.technical_quality["analysis_method"] == "basic_metadata"
    
    def test_png_format_detection(self, temp_image_file):
        """Test PNG format specific tags."""
        # Ensure file has .png extension
        if not temp_image_file.endswith('.png'):
            png_path = temp_image_file.replace(Path(temp_image_file).suffix, '.png')
            os.rename(temp_image_file, png_path)
            temp_image_file = png_path
        
        try:
            config = TaggingConfig()
            analysis = _basic_image_analysis(temp_image_file, config)
            
            tag_names = [tag.tag for tag in analysis.tags]
            assert "transparent_capable" in tag_names
        finally:
            try:
                os.unlink(temp_image_file)
            except:
                pass


class TestAnalysisWithAPIs:
    """Test analysis with different API providers using mocks."""
    
    @patch('openai.OpenAI')
    def test_openai_analysis_success(self, mock_openai_class, temp_image_file):
        """Test successful OpenAI analysis."""
        # Mock OpenAI client and response
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        
        mock_response = MagicMock()
        mock_response.choices[0].message.content = json.dumps({
            "description": "A test image",
            "tags": [
                {"tag": "test", "confidence": 0.9, "category": "object"}
            ],
            "primary_objects": ["test"],
            "scene_type": "indoor",
            "dominant_colors": ["blue"],
            "style_tags": ["digital"],
            "mood_sentiment": "neutral",
            "technical_quality": {"quality": "good"}
        })
        mock_client.chat.completions.create.return_value = mock_response
        
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test_key'}):
            config = TaggingConfig(api_provider="openai")
            analysis = _analyze_with_openai(temp_image_file, config)
            
            assert analysis.file_path == temp_image_file
            assert len(analysis.tags) == 1
            assert analysis.tags[0].tag == "test"
            assert analysis.ai_confidence == 0.8  # High confidence for AI analysis
    
    @patch('anthropic.Anthropic')
    def test_anthropic_analysis_success(self, mock_anthropic_class, temp_image_file):
        """Test successful Anthropic analysis."""
        # Mock Anthropic client and response
        mock_client = MagicMock()
        mock_anthropic_class.return_value = mock_client
        
        mock_response = MagicMock()
        mock_response.content[0].text = json.dumps({
            "description": "Analysis from Claude",
            "tags": [
                {"tag": "analysis", "confidence": 0.85, "category": "general"}
            ],
            "primary_objects": ["object"],
            "scene_type": "unknown",
            "dominant_colors": ["gray"],
            "style_tags": ["ai_generated"],
            "mood_sentiment": "neutral",
            "technical_quality": {"analysis": "complete"}
        })
        mock_client.messages.create.return_value = mock_response
        
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test_key'}):
            config = TaggingConfig(api_provider="anthropic")
            analysis = _analyze_with_anthropic(temp_image_file, config)
            
            assert analysis.file_path == temp_image_file
            assert len(analysis.tags) == 1
            assert analysis.tags[0].tag == "analysis"
    
    def test_missing_api_key_error(self, temp_image_file):
        """Test error handling for missing API key."""
        with patch.dict(os.environ, {}, clear=True):
            config = TaggingConfig(api_provider="openai")
            
            # Should handle missing API key gracefully
            analysis = _analyze_with_openai(temp_image_file, config)
            assert analysis.ai_confidence == 0.0
            assert "OPENAI_API_KEY" in analysis.technical_quality["error"] or "API key" in analysis.technical_quality["error"]
    
    @patch('tools.image_tagging_tool.subprocess.run')
    def test_local_model_analysis(self, mock_run, temp_image_file):
        """Test local model analysis with Ollama."""
        # Mock successful Ollama response
        mock_response = json.dumps({
            "description": "Local model analysis",
            "tags": [
                {"tag": "local", "confidence": 0.7, "category": "general"}
            ],
            "primary_objects": ["item"],
            "scene_type": "unknown",
            "dominant_colors": ["various"],
            "style_tags": ["local_analysis"],
            "mood_sentiment": "neutral",
            "technical_quality": {"model": "local"}
        })
        
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=mock_response.encode('utf-8')
        )
        
        config = TaggingConfig(local_model=True)
        analysis = _analyze_with_local_model(temp_image_file, config)
        
        assert analysis.file_path == temp_image_file
        assert len(analysis.tags) == 1
        assert analysis.tags[0].tag == "local"
        
        # Verify ollama was called
        assert mock_run.called
        call_args = mock_run.call_args[0][0]
        assert "ollama" in call_args
        assert "llava:latest" in call_args
    
    @patch('tools.image_tagging_tool.subprocess.run')
    def test_local_model_not_found(self, mock_run, temp_image_file):
        """Test handling when Ollama is not installed."""
        mock_run.side_effect = FileNotFoundError("ollama not found")
        
        config = TaggingConfig(local_model=True)
        analysis = _analyze_with_local_model(temp_image_file, config)
        
        assert analysis.ai_confidence == 0.0
        assert "Ollama not found" in analysis.technical_quality["error"]


class TestTagImage:
    """Test main tag_image functionality."""
    
    def test_file_not_found_error(self):
        """Test error handling for non-existent file."""
        with pytest.raises(FileNotFoundError):
            tag_image("/nonexistent/image.jpg")
    
    def test_invalid_image_file_error(self, temp_non_image_file):
        """Test error handling for non-image file."""
        with pytest.raises(ValueError):
            tag_image(temp_non_image_file)
    
    @patch('tools.image_tagging_tool._basic_image_analysis')
    def test_successful_tagging(self, mock_basic_analysis, temp_image_file):
        """Test successful image tagging with basic analysis."""
        # Mock basic analysis return
        mock_analysis = ImageAnalysis(
            file_path=temp_image_file,
            tags=[
                ImageTag(tag="image", confidence=0.9, category="general"),
                ImageTag(tag="test", confidence=0.8, category="object")
            ],
            description="Test image analysis",
            primary_objects=["test"],
            scene_type="unknown",
            dominant_colors=["gray"],
            style_tags=["test"],
            mood_sentiment="neutral",
            technical_quality={"basic": True},
            ai_confidence=0.7
        )
        mock_basic_analysis.return_value = mock_analysis
        
        config = TaggingConfig(api_provider="basic")  # Force basic analysis
        result = tag_image(temp_image_file, config)
        
        assert isinstance(result, ImageAnalysis)
        assert result.file_path == temp_image_file
        assert len(result.tags) == 2
        assert mock_basic_analysis.called


class TestBatchTagImages:
    """Test batch image tagging functionality."""
    
    @patch('tools.image_tagging_tool.tag_image')
    def test_successful_batch_processing(self, mock_tag_image, temp_image_file, temp_jpeg_file):
        """Test successful batch processing of multiple images."""
        # Mock individual tag_image calls
        mock_analyses = [
            ImageAnalysis(
                file_path=temp_image_file,
                tags=[ImageTag(tag="png", confidence=0.9, category="technical")],
                description="PNG image",
                primary_objects=[],
                scene_type="unknown",
                dominant_colors=[],
                style_tags=[],
                mood_sentiment="neutral",
                technical_quality={},
                ai_confidence=0.8
            ),
            ImageAnalysis(
                file_path=temp_jpeg_file,
                tags=[ImageTag(tag="jpeg", confidence=0.9, category="technical")],
                description="JPEG image",
                primary_objects=[],
                scene_type="unknown",
                dominant_colors=[],
                style_tags=[],
                mood_sentiment="neutral",
                technical_quality={},
                ai_confidence=0.8
            )
        ]
        
        mock_tag_image.side_effect = mock_analyses
        
        image_paths = [temp_image_file, temp_jpeg_file]
        results = batch_tag_images(image_paths)
        
        assert len(results) == 2
        assert temp_image_file in results
        assert temp_jpeg_file in results
        assert results[temp_image_file].tags[0].tag == "png"
        assert results[temp_jpeg_file].tags[0].tag == "jpeg"
        assert mock_tag_image.call_count == 2
    
    @patch('tools.image_tagging_tool.tag_image')
    def test_batch_with_errors(self, mock_tag_image, temp_image_file):
        """Test batch processing with some failed images."""
        # Mock one successful, one failed
        mock_tag_image.side_effect = [
            ImageAnalysis(
                file_path=temp_image_file,
                tags=[ImageTag(tag="success", confidence=0.9, category="general")],
                description="Successful analysis",
                primary_objects=[],
                scene_type="unknown",
                dominant_colors=[],
                style_tags=[],
                mood_sentiment="neutral",
                technical_quality={},
                ai_confidence=0.8
            ),
            Exception("Failed to process image")
        ]
        
        image_paths = [temp_image_file, "/nonexistent/image.jpg"]
        results = batch_tag_images(image_paths)
        
        assert len(results) == 2
        
        # Successful image
        assert results[temp_image_file].tags[0].tag == "success"
        
        # Failed image should have error result
        error_result = results["/nonexistent/image.jpg"]
        assert error_result.tags[0].tag == "error"
        assert "Failed to analyze" in error_result.description


class TestConvenienceFunctions:
    """Test convenience functions for common use cases."""
    
    @patch('tools.image_tagging_tool.tag_image')
    def test_extract_simple_tags(self, mock_tag_image, temp_image_file):
        """Test extracting simple tag list."""
        mock_analysis = ImageAnalysis(
            file_path=temp_image_file,
            tags=[
                ImageTag(tag="person", confidence=0.9, category="object"),
                ImageTag(tag="outdoor", confidence=0.8, category="scene"),
                ImageTag(tag="photography", confidence=0.7, category="style")
            ],
            description="Test",
            primary_objects=[],
            scene_type="outdoor",
            dominant_colors=[],
            style_tags=[],
            mood_sentiment="neutral",
            technical_quality={},
            ai_confidence=0.8
        )
        mock_tag_image.return_value = mock_analysis
        
        tags = extract_simple_tags(temp_image_file, max_tags=5)
        
        assert isinstance(tags, list)
        assert len(tags) == 3
        assert "person" in tags
        assert "outdoor" in tags
        assert "photography" in tags
    
    @patch('tools.image_tagging_tool.tag_image')
    def test_quick_tag_image(self, mock_tag_image, temp_image_file):
        """Test quick tagging function."""
        mock_analysis = ImageAnalysis(
            file_path=temp_image_file,
            tags=[
                ImageTag(tag="quick", confidence=0.8, category="general"),
                ImageTag(tag="test", confidence=0.6, category="object")
            ],
            description="Quick test",
            primary_objects=[],
            scene_type="unknown",
            dominant_colors=[],
            style_tags=[],
            mood_sentiment="neutral",
            technical_quality={},
            ai_confidence=0.7
        )
        mock_tag_image.return_value = mock_analysis
        
        tags = quick_tag_image(temp_image_file)
        
        assert isinstance(tags, list)
        assert "quick" in tags
        assert "test" in tags
    
    @patch('tools.image_tagging_tool.tag_image')
    def test_detailed_image_analysis(self, mock_tag_image, temp_image_file):
        """Test detailed analysis function."""
        mock_analysis = ImageAnalysis(
            file_path=temp_image_file,
            tags=[ImageTag(tag="detailed", confidence=0.9, category="analysis")],
            description="Detailed analysis",
            primary_objects=["object"],
            scene_type="indoor",
            dominant_colors=["blue", "red"],
            style_tags=["detailed", "comprehensive"],
            mood_sentiment="analytical",
            technical_quality={
                "lighting": "excellent",
                "composition": "rule_of_thirds",
                "focus": "sharp",
                "overall_quality": "excellent"
            },
            ai_confidence=0.95
        )
        mock_tag_image.return_value = mock_analysis
        
        analysis = detailed_image_analysis(temp_image_file)
        
        assert isinstance(analysis, ImageAnalysis)
        assert len(analysis.technical_quality) > 0
        assert analysis.ai_confidence > 0.9
        
        # Check that detailed config was used
        call_args = mock_tag_image.call_args
        config = call_args[0][1]  # Second argument should be config
        assert config.max_tags == 30
        assert config.min_confidence == 0.2
        assert config.include_technical == True
    
    @patch('tools.image_tagging_tool.tag_image')
    def test_content_moderation_tags(self, mock_tag_image, temp_image_file):
        """Test content moderation tag extraction."""
        mock_analysis = ImageAnalysis(
            file_path=temp_image_file,
            tags=[
                ImageTag(tag="safe_content", confidence=0.9, category="mood"),
                ImageTag(tag="family_friendly", confidence=0.8, category="mood"),
                ImageTag(tag="professional", confidence=0.7, category="style"),
                ImageTag(tag="unrelated_tag", confidence=0.6, category="object")
            ],
            description="Safe family content",
            primary_objects=[],
            scene_type="indoor",
            dominant_colors=[],
            style_tags=[],
            mood_sentiment="safe",
            technical_quality={},
            ai_confidence=0.8
        )
        mock_tag_image.return_value = mock_analysis
        
        moderation_tags = content_moderation_tags(temp_image_file)
        
        assert isinstance(moderation_tags, list)
        assert "safe_content" in moderation_tags
        assert "family_friendly" in moderation_tags
        assert "professional" in moderation_tags
        assert "unrelated_tag" not in moderation_tags  # Should filter irrelevant tags


class TestFallbackAnalysis:
    """Test fallback analysis functionality."""
    
    def test_fallback_analysis_creation(self):
        """Test creating fallback analysis for errors."""
        error_msg = "API connection failed"
        analysis = _fallback_analysis("test.jpg", error_msg)
        
        assert analysis.file_path == "test.jpg"
        assert analysis.ai_confidence == 0.0
        assert len(analysis.tags) == 2  # Should have "image" and "analysis_failed"
        assert any(tag.tag == "analysis_failed" for tag in analysis.tags)
        assert error_msg in analysis.description
        assert error_msg in analysis.technical_quality["error"]


class TestEdgeCases:
    """Test edge cases and error conditions."""
    
    def test_empty_tag_list(self, temp_image_file):
        """Test handling of empty tag list."""
        with patch('tools.image_tagging_tool._basic_image_analysis') as mock_analysis:
            mock_analysis.return_value = ImageAnalysis(
                file_path=temp_image_file,
                tags=[],  # Empty tag list
                description="No tags found",
                primary_objects=[],
                scene_type="unknown",
                dominant_colors=[],
                style_tags=[],
                mood_sentiment="neutral",
                technical_quality={},
                ai_confidence=0.3
            )
            
            # Force using basic analysis by setting api_provider to unsupported value
            config = TaggingConfig(api_provider="basic")
            result = tag_image(temp_image_file, config)
            assert len(result.tags) == 0
    
    def test_very_large_image_path(self):
        """Test handling of very long file paths."""
        long_path = "/very/long/path/" + "a" * 200 + "/image.jpg"
        
        with patch('tools.image_tagging_tool._basic_image_analysis') as mock_analysis:
            mock_analysis.return_value = ImageAnalysis(
                file_path=long_path,
                tags=[ImageTag(tag="test", confidence=0.5, category="general")],
                description="Long path test",
                primary_objects=[],
                scene_type="unknown", 
                dominant_colors=[],
                style_tags=[],
                mood_sentiment="neutral",
                technical_quality={},
                ai_confidence=0.5
            )
            
            with patch('tools.image_tagging_tool.Path.exists', return_value=True):
                with patch('tools.image_tagging_tool._is_image_file', return_value=True):
                    result = tag_image(long_path)
                    assert result.file_path == long_path


class TestIntegration:
    """Integration tests for the complete image tagging workflow."""
    
    def test_end_to_end_workflow_basic(self, temp_image_file):
        """Test complete end-to-end workflow with basic analysis."""
        # Use basic analysis to avoid external dependencies
        config = TaggingConfig(
            max_tags=15,
            min_confidence=0.4,
            include_technical=True,
            api_provider="basic"  # Force basic analysis
        )
        
        with patch('tools.image_tagging_tool._basic_image_analysis') as mock_basic:
            mock_basic.return_value = ImageAnalysis(
                file_path=temp_image_file,
                tags=[
                    ImageTag(tag="image", confidence=0.9, category="general"),
                    ImageTag(tag="digital", confidence=0.8, category="technical"),
                    ImageTag(tag="test", confidence=0.7, category="object")
                ],
                description="Digital test image file",
                primary_objects=["test"],
                scene_type="digital",
                dominant_colors=["gray"],
                style_tags=["digital", "test"],
                mood_sentiment="neutral",
                technical_quality={
                    "file_size_mb": 0.001,
                    "format": ".png",
                    "analysis_method": "basic_metadata"
                },
                ai_confidence=0.6
            )
            
            # Test main tagging function
            analysis = tag_image(temp_image_file, config)
            
            # Validate complete workflow
            assert isinstance(analysis, ImageAnalysis)
            assert analysis.file_path == temp_image_file
            assert len(analysis.tags) == 3
            assert all(tag.confidence >= config.min_confidence for tag in analysis.tags)
            assert len(analysis.tags) <= config.max_tags
            
            # Test convenience functions
            simple_tags = extract_simple_tags(temp_image_file, max_tags=5)
            assert isinstance(simple_tags, list)
            assert len(simple_tags) <= 5
            
            # Test batch processing
            batch_results = batch_tag_images([temp_image_file])
            assert len(batch_results) == 1
            assert temp_image_file in batch_results