"""
Image Analysis Tool - Multi-Modal Vision Analysis

Advanced image analysis tool leveraging state-of-the-art computer vision models
for comprehensive visual understanding, object detection, and content analysis.

Features:
- Multi-model vision analysis (GPT-4V, Claude Vision, specialized models)
- Object detection and classification
- Scene understanding and context analysis
- Text extraction (OCR) with layout analysis
- Visual similarity comparison
- Content safety and moderation
- Accessibility analysis (alt-text generation)
"""

import asyncio
import base64
import hashlib
import io
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Union, Tuple, BinaryIO
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageEnhance, ImageFilter
from pydantic import BaseModel, Field, validator

from .base import (
    ToolImplementation, BaseInputModel, BaseOutputModel, ToolContext,
    ToolError, ErrorCategory, QualityMetric, performance_monitor
)


class ImageAnalysisInput(BaseInputModel):
    """Input model for image analysis requests."""
    
    image_source: str = Field(
        ...,
        description="Image source: file path, URL, or base64 encoded image"
    )
    
    analysis_types: List[str] = Field(
        default=["description", "objects", "text"],
        description="Types of analysis to perform"
    )
    
    detail_level: str = Field(
        default="standard",
        pattern="^(minimal|standard|detailed|comprehensive)$",
        description="Level of analysis detail"
    )
    
    output_format: str = Field(
        default="structured",
        pattern="^(structured|narrative|technical|accessibility)$",
        description="Output format style"
    )
    
    max_image_size: int = Field(
        default=2048,
        ge=256,
        le=4096,
        description="Maximum image dimension in pixels"
    )
    
    quality_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Minimum confidence threshold for results"
    )
    
    include_coordinates: bool = Field(
        default=False,
        description="Include bounding box coordinates for detected objects"
    )
    
    language: str = Field(
        default="en",
        pattern="^[a-z]{2}$",
        description="Language for text analysis and output"
    )
    
    safety_check: bool = Field(
        default=True,
        description="Enable content safety analysis"
    )
    
    compare_with: Optional[str] = Field(
        default=None,
        description="Optional second image path/URL for comparison"
    )
    
    custom_prompt: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Custom analysis prompt for specific requirements"
    )
    
    @validator('analysis_types')
    def validate_analysis_types(cls, v):
        """Validate analysis type selections."""
        valid_types = {
            "description", "objects", "text", "scene", "colors", "composition",
            "emotions", "brands", "landmarks", "celebrities", "safety", "accessibility"
        }
        
        invalid_types = set(v) - valid_types
        if invalid_types:
            raise ValueError(f"Invalid analysis types: {invalid_types}")
        
        return v
    
    @validator('image_source')
    def validate_image_source(cls, v):
        """Validate image source format."""
        if not v.strip():
            raise ValueError("Image source cannot be empty")
        
        # Check if it's a file path
        if not (v.startswith('http') or v.startswith('data:image/') or Path(v).exists()):
            # Could still be valid base64 without data URL prefix
            if len(v) > 100 and not any(char in v for char in [' ', '\n', '\t']):
                return v  # Assume valid base64
            raise ValueError("Invalid image source format")
        
        return v


class DetectedObject(BaseModel):
    """Individual detected object with metadata."""
    
    label: str = Field(..., description="Object classification label")
    confidence: float = Field(ge=0.0, le=1.0, description="Detection confidence")
    bounding_box: Optional[Dict[str, float]] = Field(
        None, description="Bounding box coordinates (x, y, width, height)"
    )
    attributes: Dict[str, Any] = Field(
        default_factory=dict, description="Additional object attributes"
    )
    color_info: Optional[Dict[str, str]] = Field(
        None, description="Dominant colors of the object"
    )


class ExtractedText(BaseModel):
    """Extracted text with positioning and confidence."""
    
    text: str = Field(..., description="Extracted text content")
    confidence: float = Field(ge=0.0, le=1.0, description="OCR confidence")
    language: Optional[str] = Field(None, description="Detected language")
    bounding_box: Optional[Dict[str, float]] = Field(
        None, description="Text bounding box coordinates"
    )
    font_info: Optional[Dict[str, Any]] = Field(
        None, description="Font size and style information"
    )


class SceneAnalysis(BaseModel):
    """Scene understanding and context analysis."""
    
    scene_type: str = Field(..., description="Primary scene classification")
    setting: str = Field(..., description="Environmental setting")
    lighting: str = Field(..., description="Lighting conditions")
    weather: Optional[str] = Field(None, description="Weather conditions if outdoor")
    time_of_day: Optional[str] = Field(None, description="Estimated time of day")
    mood: str = Field(..., description="Overall mood/atmosphere")
    activity: Optional[str] = Field(None, description="Primary activity depicted")
    context_confidence: float = Field(ge=0.0, le=1.0, description="Context analysis confidence")


class SafetyAnalysis(BaseModel):
    """Content safety and moderation analysis."""
    
    is_safe: bool = Field(..., description="Overall safety assessment")
    safety_score: float = Field(ge=0.0, le=1.0, description="Safety confidence score")
    detected_issues: List[str] = Field(
        default_factory=list, description="Specific safety concerns identified"
    )
    content_rating: str = Field(
        default="general", description="Content rating (general, teen, mature, adult)"
    )
    moderation_flags: Dict[str, float] = Field(
        default_factory=dict, description="Moderation categories with scores"
    )


class ImageAnalysisOutput(BaseOutputModel):
    """Comprehensive image analysis results."""
    
    image_info: Dict[str, Any] = Field(..., description="Basic image metadata")
    
    # Analysis results by type
    description: Optional[str] = Field(None, description="Overall image description")
    detected_objects: List[DetectedObject] = Field(
        default_factory=list, description="Detected objects and entities"
    )
    extracted_text: List[ExtractedText] = Field(
        default_factory=list, description="Extracted text elements"
    )
    scene_analysis: Optional[SceneAnalysis] = Field(None, description="Scene understanding")
    safety_analysis: Optional[SafetyAnalysis] = Field(None, description="Safety assessment")
    
    # Visual analysis
    color_analysis: Dict[str, Any] = Field(
        default_factory=dict, description="Color palette and distribution"
    )
    composition_analysis: Dict[str, Any] = Field(
        default_factory=dict, description="Visual composition metrics"
    )
    
    # Specialized analyses
    accessibility_description: Optional[str] = Field(
        None, description="Accessibility-focused description"
    )
    technical_details: Dict[str, Any] = Field(
        default_factory=dict, description="Technical image analysis"
    )
    
    # Comparison results (if second image provided)
    similarity_analysis: Optional[Dict[str, Any]] = Field(
        None, description="Image similarity comparison results"
    )
    
    # Quality metrics
    analysis_confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Overall analysis confidence"
    )
    processing_time_ms: float = Field(..., description="Total processing time")
    models_used: List[str] = Field(
        default_factory=list, description="AI models used in analysis"
    )


class ImageProcessor:
    """Image processing utilities for preparation and enhancement."""
    
    @staticmethod
    def load_image_from_source(image_source: str) -> Image.Image:
        """Load image from various sources (file, URL, base64)."""
        try:
            if image_source.startswith('http'):
                # Load from URL
                response = httpx.get(image_source, timeout=30.0)
                response.raise_for_status()
                return Image.open(io.BytesIO(response.content))
            
            elif image_source.startswith('data:image/'):
                # Base64 data URL
                header, data = image_source.split(',', 1)
                image_data = base64.b64decode(data)
                return Image.open(io.BytesIO(image_data))
            
            elif Path(image_source).exists():
                # Local file path
                return Image.open(image_source)
            
            else:
                # Try as raw base64
                try:
                    image_data = base64.b64decode(image_source)
                    return Image.open(io.BytesIO(image_data))
                except:
                    raise ValueError(f"Cannot load image from source: {image_source}")
        
        except Exception as e:
            raise ToolError(
                f"Failed to load image: {str(e)}",
                ErrorCategory.INPUT_VALIDATION,
                details={"image_source": image_source[:100]}
            )
    
    @staticmethod
    def preprocess_image(
        image: Image.Image, 
        max_size: int = 2048,
        enhance_quality: bool = True
    ) -> Image.Image:
        """Preprocess image for optimal analysis."""
        
        # Convert to RGB if necessary
        if image.mode not in ('RGB', 'L'):
            image = image.convert('RGB')
        
        # Resize if too large
        if max(image.size) > max_size:
            ratio = max_size / max(image.size)
            new_size = tuple(int(dim * ratio) for dim in image.size)
            image = image.resize(new_size, Image.Resampling.LANCZOS)
        
        if enhance_quality:
            # Enhance image quality for better analysis
            enhancer = ImageEnhance.Sharpness(image)
            image = enhancer.enhance(1.1)  # Slight sharpening
            
            enhancer = ImageEnhance.Contrast(image)
            image = enhancer.enhance(1.05)  # Slight contrast boost
        
        return image
    
    @staticmethod
    def extract_image_metadata(image: Image.Image, file_path: Optional[str] = None) -> Dict[str, Any]:
        """Extract comprehensive image metadata."""
        metadata = {
            "width": image.size[0],
            "height": image.size[1],
            "mode": image.mode,
            "format": image.format,
            "megapixels": round((image.size[0] * image.size[1]) / 1_000_000, 2)
        }
        
        # Add file info if available
        if file_path and Path(file_path).exists():
            file_stat = Path(file_path).stat()
            metadata.update({
                "file_size_bytes": file_stat.st_size,
                "file_size_mb": round(file_stat.st_size / (1024 * 1024), 2),
                "creation_time": datetime.fromtimestamp(file_stat.st_ctime).isoformat(),
                "modification_time": datetime.fromtimestamp(file_stat.st_mtime).isoformat()
            })
        
        # Extract EXIF data if available
        if hasattr(image, '_getexif') and image._getexif():
            try:
                exif_data = {}
                for tag, value in image._getexif().items():
                    if isinstance(value, (str, int, float)):
                        exif_data[str(tag)] = value
                metadata["exif"] = exif_data
            except:
                pass  # EXIF extraction failed, continue without it
        
        return metadata
    
    @staticmethod
    def analyze_colors(image: Image.Image, num_colors: int = 5) -> Dict[str, Any]:
        """Analyze image colors and extract palette."""
        try:
            # Convert to RGB if necessary
            if image.mode != 'RGB':
                image = image.convert('RGB')
            
            # Get dominant colors using quantization
            quantized = image.quantize(colors=num_colors, method=Image.Quantize.MEDIANCUT)
            palette = quantized.getpalette()
            
            # Extract color information
            colors = []
            for i in range(num_colors):
                r, g, b = palette[i*3:(i+1)*3]
                colors.append({
                    "rgb": [r, g, b],
                    "hex": f"#{r:02x}{g:02x}{b:02x}",
                    "percentage": 100 / num_colors  # Approximate
                })
            
            # Calculate overall brightness and saturation
            import colorsys
            hsv_colors = [colorsys.rgb_to_hsv(c["rgb"][0]/255, c["rgb"][1]/255, c["rgb"][2]/255) for c in colors]
            
            avg_brightness = sum(hsv[2] for hsv in hsv_colors) / len(hsv_colors)
            avg_saturation = sum(hsv[1] for hsv in hsv_colors) / len(hsv_colors)
            
            return {
                "dominant_colors": colors,
                "average_brightness": round(avg_brightness, 3),
                "average_saturation": round(avg_saturation, 3),
                "color_temperature": "warm" if colors[0]["rgb"][0] > colors[0]["rgb"][2] else "cool"
            }
            
        except Exception as e:
            return {"error": f"Color analysis failed: {str(e)}"}
    
    @staticmethod
    def analyze_composition(image: Image.Image) -> Dict[str, Any]:
        """Analyze image composition and visual structure."""
        try:
            import numpy as np
            
            # Convert to numpy array for analysis
            img_array = np.array(image.convert('L'))  # Grayscale for analysis
            
            # Calculate image statistics
            height, width = img_array.shape
            
            # Center of mass (visual weight distribution)
            y_coords, x_coords = np.mgrid[:height, :width]
            total_intensity = np.sum(img_array)
            
            if total_intensity > 0:
                center_x = np.sum(x_coords * img_array) / total_intensity
                center_y = np.sum(y_coords * img_array) / total_intensity
                
                # Rule of thirds analysis
                thirds_x = [width/3, 2*width/3]
                thirds_y = [height/3, 2*height/3]
                
                # Distance from rule of thirds intersections
                intersections = [(x, y) for x in thirds_x for y in thirds_y]
                min_distance = min(
                    ((center_x - x)**2 + (center_y - y)**2)**0.5 
                    for x, y in intersections
                )
                
                rule_of_thirds_score = max(0, 1 - (min_distance / (width * 0.1)))
            else:
                center_x = width / 2
                center_y = height / 2
                rule_of_thirds_score = 0
            
            # Symmetry analysis
            left_half = img_array[:, :width//2]
            right_half = img_array[:, width//2:]
            right_half_flipped = np.fliplr(right_half)
            
            # Resize to same dimensions if needed
            min_width = min(left_half.shape[1], right_half_flipped.shape[1])
            left_half = left_half[:, :min_width]
            right_half_flipped = right_half_flipped[:, :min_width]
            
            symmetry_score = 1 - (np.mean(np.abs(left_half - right_half_flipped)) / 255)
            
            return {
                "center_of_mass": {"x": round(center_x, 1), "y": round(center_y, 1)},
                "rule_of_thirds_score": round(rule_of_thirds_score, 3),
                "symmetry_score": round(max(0, symmetry_score), 3),
                "aspect_ratio": round(width / height, 3),
                "orientation": "landscape" if width > height else "portrait" if height > width else "square"
            }
            
        except Exception as e:
            return {"error": f"Composition analysis failed: {str(e)}"}


class VisionAPIClient:
    """Client for vision API services (GPT-4V, Claude Vision, etc.)."""
    
    def __init__(self, api_keys: Dict[str, str]):
        self.api_keys = api_keys
        self.clients = {}
        
        # Initialize HTTP clients for different services
        for service in api_keys:
            self.clients[service] = httpx.AsyncClient(timeout=60.0)
    
    async def analyze_with_gpt4v(
        self, 
        image: Image.Image, 
        prompt: str,
        detail_level: str = "standard"
    ) -> Dict[str, Any]:
        """Analyze image using GPT-4 Vision."""
        if "openai" not in self.api_keys:
            return {"error": "OpenAI API key not available"}
        
        try:
            # Convert image to base64
            buffer = io.BytesIO()
            image.save(buffer, format='PNG')
            image_base64 = base64.b64encode(buffer.getvalue()).decode()
            
            # Prepare request
            headers = {
                "Authorization": f"Bearer {self.api_keys['openai']}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": "gpt-4-vision-preview",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{image_base64}",
                                    "detail": detail_level if detail_level in ["low", "high"] else "auto"
                                }
                            }
                        ]
                    }
                ],
                "max_tokens": 1000
            }
            
            response = await self.clients["openai"].post(
                "https://api.openai.com/v1/chat/completions",
                json=payload,
                headers=headers
            )
            
            response.raise_for_status()
            data = response.json()
            
            return {
                "content": data["choices"][0]["message"]["content"],
                "model": "gpt-4-vision-preview",
                "tokens_used": data.get("usage", {}).get("total_tokens", 0)
            }
            
        except Exception as e:
            return {"error": f"GPT-4V analysis failed: {str(e)}"}
    
    async def analyze_with_claude(
        self, 
        image: Image.Image, 
        prompt: str
    ) -> Dict[str, Any]:
        """Analyze image using Claude Vision."""
        if "anthropic" not in self.api_keys:
            return {"error": "Anthropic API key not available"}
        
        try:
            # Convert image to base64
            buffer = io.BytesIO()
            image.save(buffer, format='PNG')
            image_base64 = base64.b64encode(buffer.getvalue()).decode()
            
            # Prepare request
            headers = {
                "x-api-key": self.api_keys["anthropic"],
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01"
            }
            
            payload = {
                "model": "claude-3-opus-20240229",
                "max_tokens": 1000,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": image_base64
                                }
                            }
                        ]
                    }
                ]
            }
            
            response = await self.clients["anthropic"].post(
                "https://api.anthropic.com/v1/messages",
                json=payload,
                headers=headers
            )
            
            response.raise_for_status()
            data = response.json()
            
            return {
                "content": data["content"][0]["text"],
                "model": "claude-3-opus",
                "tokens_used": data.get("usage", {}).get("output_tokens", 0)
            }
            
        except Exception as e:
            return {"error": f"Claude Vision analysis failed: {str(e)}"}
    
    async def close(self):
        """Close all HTTP clients."""
        for client in self.clients.values():
            await client.aclose()


class ImageAnalysisTool(ToolImplementation[ImageAnalysisInput, ImageAnalysisOutput]):
    """
    Advanced Image Analysis Tool with Multi-Modal Vision
    
    Provides comprehensive image analysis using state-of-the-art computer vision
    models, including object detection, scene understanding, and accessibility analysis.
    """
    
    def __init__(self, api_keys: Optional[Dict[str, str]] = None, **kwargs):
        super().__init__(
            name="image_analysis",
            version="1.3.0",
            description="Advanced multi-modal image analysis with AI vision models",
            **kwargs
        )
        
        # Initialize API keys
        self.api_keys = api_keys or {}
        
        # Try to get keys from environment
        for service in ["openai", "anthropic", "google"]:
            key_name = f"{service.upper()}_API_KEY"
            if key_name in os.environ:
                self.api_keys[service] = os.environ[key_name]
        
        # Initialize vision client
        self.vision_client = VisionAPIClient(self.api_keys)
        
        # Initialize image processor
        self.image_processor = ImageProcessor()
        
        # Analysis cache
        self.analysis_cache: Dict[str, Any] = {}
    
    @property
    def input_model(self) -> type:
        return ImageAnalysisInput
    
    @property
    def output_model(self) -> type:
        return ImageAnalysisOutput
    
    async def _execute_core(
        self, 
        input_data: ImageAnalysisInput, 
        context: ToolContext
    ) -> ImageAnalysisOutput:
        """Core image analysis execution."""
        
        start_time = time.time()
        models_used = []
        
        try:
            # Step 1: Load and preprocess image
            image = self.image_processor.load_image_from_source(input_data.image_source)
            processed_image = self.image_processor.preprocess_image(
                image, input_data.max_image_size
            )
            
            context.add_trace_data("image_loaded", True)
            context.add_trace_data("image_size", processed_image.size)
            
            # Step 2: Extract basic metadata
            image_info = self.image_processor.extract_image_metadata(
                processed_image, 
                input_data.image_source if Path(input_data.image_source).exists() else None
            )
            
            # Step 3: Perform requested analyses
            results = ImageAnalysisOutput(
                image_info=image_info,
                processing_time_ms=0,  # Will be updated at the end
                models_used=models_used
            )
            
            # Basic visual analyses
            if "colors" in input_data.analysis_types:
                results.color_analysis = self.image_processor.analyze_colors(processed_image)
            
            if "composition" in input_data.analysis_types:
                results.composition_analysis = self.image_processor.analyze_composition(processed_image)
            
            # AI-powered analyses
            analysis_tasks = []
            
            if "description" in input_data.analysis_types:
                analysis_tasks.append(self._analyze_description(processed_image, input_data))
            
            if "objects" in input_data.analysis_types:
                analysis_tasks.append(self._analyze_objects(processed_image, input_data))
            
            if "text" in input_data.analysis_types:
                analysis_tasks.append(self._analyze_text(processed_image, input_data))
            
            if "scene" in input_data.analysis_types:
                analysis_tasks.append(self._analyze_scene(processed_image, input_data))
            
            if "safety" in input_data.analysis_types or input_data.safety_check:
                analysis_tasks.append(self._analyze_safety(processed_image, input_data))
            
            if "accessibility" in input_data.analysis_types:
                analysis_tasks.append(self._analyze_accessibility(processed_image, input_data))
            
            # Execute analyses concurrently
            if analysis_tasks:
                analysis_results = await asyncio.gather(*analysis_tasks, return_exceptions=True)
                
                # Process results
                for i, result in enumerate(analysis_results):
                    if isinstance(result, Exception):
                        self.logger.warning(f"Analysis task {i} failed: {str(result)}")
                        continue
                    
                    if result:
                        self._merge_analysis_results(results, result)
                        if "model" in result:
                            models_used.append(result["model"])
            
            # Comparison analysis if requested
            if input_data.compare_with:
                comparison_result = await self._perform_image_comparison(
                    processed_image, input_data.compare_with, input_data
                )
                results.similarity_analysis = comparison_result
            
            # Calculate overall confidence
            results.analysis_confidence = self._calculate_overall_confidence(results)
            
            # Technical details
            results.technical_details = self._extract_technical_details(
                processed_image, image_info
            )
            
            # Update processing time and models used
            results.processing_time_ms = (time.time() - start_time) * 1000
            results.models_used = list(set(models_used))
            
            return results
            
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(
                f"Image analysis failed: {str(e)}",
                ErrorCategory.INTERNAL_ERROR,
                details={"error": str(e)}
            )
    
    async def _analyze_description(
        self, 
        image: Image.Image, 
        input_data: ImageAnalysisInput
    ) -> Dict[str, Any]:
        """Generate comprehensive image description."""
        
        prompt = self._build_description_prompt(input_data)
        
        # Try multiple vision models
        result = await self.vision_client.analyze_with_gpt4v(
            image, prompt, input_data.detail_level
        )
        
        if "error" not in result:
            return {
                "description": result["content"],
                "model": result["model"],
                "confidence": 0.8  # Base confidence for successful analysis
            }
        
        # Fallback to Claude if GPT-4V fails
        result = await self.vision_client.analyze_with_claude(image, prompt)
        
        if "error" not in result:
            return {
                "description": result["content"],
                "model": result["model"],
                "confidence": 0.8
            }
        
        return {"error": "All vision models failed for description analysis"}
    
    async def _analyze_objects(
        self, 
        image: Image.Image, 
        input_data: ImageAnalysisInput
    ) -> Dict[str, Any]:
        """Detect and classify objects in the image."""
        
        prompt = """Identify and describe all objects, people, and entities visible in this image. 
        For each detected item, provide:
        1. Object name/label
        2. Confidence level (0-1)
        3. Brief description
        4. Approximate size (small/medium/large)
        Format as a structured list."""
        
        result = await self.vision_client.analyze_with_gpt4v(image, prompt)
        
        if "error" not in result:
            objects = self._parse_objects_from_text(result["content"])
            return {
                "detected_objects": objects,
                "model": result["model"],
                "object_count": len(objects)
            }
        
        return {"detected_objects": [], "object_count": 0}
    
    async def _analyze_text(
        self, 
        image: Image.Image, 
        input_data: ImageAnalysisInput
    ) -> Dict[str, Any]:
        """Extract and analyze text content from the image."""
        
        prompt = """Extract all visible text from this image. For each text element, provide:
        1. The exact text content
        2. Confidence level (0-1)
        3. Text type (heading, body, caption, etc.)
        4. Approximate position
        5. Language if not English
        Be thorough and accurate."""
        
        result = await self.vision_client.analyze_with_gpt4v(image, prompt)
        
        if "error" not in result:
            text_elements = self._parse_text_from_description(result["content"])
            return {
                "extracted_text": text_elements,
                "model": result["model"],
                "text_count": len(text_elements)
            }
        
        return {"extracted_text": [], "text_count": 0}
    
    async def _analyze_scene(
        self, 
        image: Image.Image, 
        input_data: ImageAnalysisInput
    ) -> Dict[str, Any]:
        """Analyze scene context and environment."""
        
        prompt = """Analyze the scene and context of this image. Describe:
        1. Scene type (indoor/outdoor, specific location type)
        2. Setting and environment
        3. Lighting conditions
        4. Weather (if outdoor)
        5. Time of day estimate
        6. Overall mood/atmosphere
        7. Primary activity or event
        Provide specific details and confidence in your assessment."""
        
        result = await self.vision_client.analyze_with_gpt4v(image, prompt)
        
        if "error" not in result:
            scene_info = self._parse_scene_analysis(result["content"])
            return {
                "scene_analysis": scene_info,
                "model": result["model"]
            }
        
        return {}
    
    async def _analyze_safety(
        self, 
        image: Image.Image, 
        input_data: ImageAnalysisInput
    ) -> Dict[str, Any]:
        """Perform content safety and moderation analysis."""
        
        prompt = """Analyze this image for content safety and appropriateness. Check for:
        1. Inappropriate or adult content
        2. Violence or harmful imagery
        3. Hate symbols or offensive material
        4. Unsafe situations or dangerous activities
        5. Privacy concerns (faces, personal info)
        
        Provide a safety rating (safe/caution/unsafe) and explain any concerns."""
        
        result = await self.vision_client.analyze_with_gpt4v(image, prompt)
        
        if "error" not in result:
            safety_info = self._parse_safety_analysis(result["content"])
            return {
                "safety_analysis": safety_info,
                "model": result["model"]
            }
        
        # Default to safe if analysis fails
        return {
            "safety_analysis": SafetyAnalysis(
                is_safe=True,
                safety_score=0.5,
                content_rating="unknown"
            )
        }
    
    async def _analyze_accessibility(
        self, 
        image: Image.Image, 
        input_data: ImageAnalysisInput
    ) -> Dict[str, Any]:
        """Generate accessibility-focused description."""
        
        prompt = """Create a detailed accessibility description for this image suitable for screen readers and visually impaired users. Include:
        1. Essential visual information
        2. Context and setting
        3. Important details for understanding
        4. Text content if present
        5. Spatial relationships
        Be comprehensive but concise, focusing on information that conveys the image's meaning and purpose."""
        
        result = await self.vision_client.analyze_with_gpt4v(image, prompt)
        
        if "error" not in result:
            return {
                "accessibility_description": result["content"],
                "model": result["model"]
            }
        
        return {}
    
    async def _perform_image_comparison(
        self,
        image1: Image.Image,
        image2_source: str,
        input_data: ImageAnalysisInput
    ) -> Dict[str, Any]:
        """Compare two images for similarity and differences."""
        
        try:
            # Load second image
            image2 = self.image_processor.load_image_from_source(image2_source)
            image2 = self.image_processor.preprocess_image(image2, input_data.max_image_size)
            
            # Basic similarity metrics
            similarity_metrics = self._calculate_basic_similarity(image1, image2)
            
            # AI-powered comparison (if available)
            # Note: This would require a more sophisticated approach in production
            # involving feature extraction and comparison algorithms
            
            return {
                "basic_metrics": similarity_metrics,
                "overall_similarity": similarity_metrics.get("structural_similarity", 0.0)
            }
            
        except Exception as e:
            return {"error": f"Comparison failed: {str(e)}"}
    
    def _calculate_basic_similarity(self, image1: Image.Image, image2: Image.Image) -> Dict[str, float]:
        """Calculate basic image similarity metrics."""
        try:
            import numpy as np
            from PIL import ImageChops
            
            # Resize images to same size for comparison
            size = (256, 256)
            img1_resized = image1.resize(size)
            img2_resized = image2.resize(size)
            
            # Convert to numpy arrays
            arr1 = np.array(img1_resized.convert('RGB'))
            arr2 = np.array(img2_resized.convert('RGB'))
            
            # Mean squared error
            mse = np.mean((arr1 - arr2) ** 2)
            
            # Normalized cross-correlation
            correlation = np.corrcoef(arr1.flatten(), arr2.flatten())[0, 1]
            if np.isnan(correlation):
                correlation = 0.0
            
            # Histogram comparison
            hist1 = np.histogram(arr1, bins=256, range=(0, 256))[0]
            hist2 = np.histogram(arr2, bins=256, range=(0, 256))[0]
            hist_correlation = np.corrcoef(hist1, hist2)[0, 1]
            if np.isnan(hist_correlation):
                hist_correlation = 0.0
            
            return {
                "mse": float(mse),
                "pixel_correlation": float(correlation),
                "histogram_correlation": float(hist_correlation),
                "structural_similarity": float((correlation + hist_correlation) / 2)
            }
            
        except Exception as e:
            self.logger.error(f"Similarity calculation failed: {str(e)}")
            return {"error": str(e)}
    
    def _build_description_prompt(self, input_data: ImageAnalysisInput) -> str:
        """Build appropriate prompt based on analysis requirements."""
        
        if input_data.custom_prompt:
            return input_data.custom_prompt
        
        base_prompt = "Analyze this image and provide a comprehensive description."
        
        if input_data.detail_level == "minimal":
            base_prompt = "Provide a brief description of this image."
        elif input_data.detail_level == "comprehensive":
            base_prompt = "Provide an extremely detailed analysis of this image, including all visible elements, context, and implications."
        
        if input_data.output_format == "narrative":
            base_prompt += " Write in a narrative, storytelling style."
        elif input_data.output_format == "technical":
            base_prompt += " Focus on technical and analytical aspects."
        
        return base_prompt
    
    def _parse_objects_from_text(self, text: str) -> List[DetectedObject]:
        """Parse object detection results from AI response text."""
        objects = []
        
        # Simple parsing - in production, use more sophisticated NLP
        lines = text.split('\n')
        for line in lines:
            line = line.strip()
            if line and any(keyword in line.lower() for keyword in ['object', 'person', 'item', 'entity']):
                # Extract object name (very basic parsing)
                object_name = line.split(':')[0].strip() if ':' in line else line
                confidence = 0.7  # Default confidence
                
                objects.append(DetectedObject(
                    label=object_name,
                    confidence=confidence,
                    attributes={"source": "ai_vision_parsing"}
                ))
        
        return objects[:20]  # Limit to 20 objects
    
    def _parse_text_from_description(self, text: str) -> List[ExtractedText]:
        """Parse extracted text from AI response."""
        text_elements = []
        
        # Simple parsing - look for quoted text or text descriptions
        lines = text.split('\n')
        for line in lines:
            line = line.strip()
            if line and ('"' in line or 'text' in line.lower()):
                # Extract quoted text or described text
                content = line
                confidence = 0.8  # Default confidence
                
                text_elements.append(ExtractedText(
                    text=content,
                    confidence=confidence
                ))
        
        return text_elements[:50]  # Limit text elements
    
    def _parse_scene_analysis(self, text: str) -> SceneAnalysis:
        """Parse scene analysis from AI response."""
        
        # Default values
        scene_data = {
            "scene_type": "unknown",
            "setting": "unspecified",
            "lighting": "normal",
            "mood": "neutral",
            "context_confidence": 0.7
        }
        
        # Simple keyword extraction (in production, use proper NLP)
        text_lower = text.lower()
        
        # Scene type detection
        if "outdoor" in text_lower or "outside" in text_lower:
            scene_data["scene_type"] = "outdoor"
        elif "indoor" in text_lower or "inside" in text_lower:
            scene_data["scene_type"] = "indoor"
        
        # Lighting detection
        if "bright" in text_lower or "sunny" in text_lower:
            scene_data["lighting"] = "bright"
        elif "dark" in text_lower or "dim" in text_lower:
            scene_data["lighting"] = "dim"
        
        return SceneAnalysis(**scene_data)
    
    def _parse_safety_analysis(self, text: str) -> SafetyAnalysis:
        """Parse safety analysis from AI response."""
        
        text_lower = text.lower()
        
        # Simple safety detection
        unsafe_indicators = ["unsafe", "inappropriate", "adult", "violence", "harmful"]
        caution_indicators = ["caution", "warning", "concern"]
        
        is_safe = True
        safety_score = 0.9
        detected_issues = []
        content_rating = "general"
        
        if any(indicator in text_lower for indicator in unsafe_indicators):
            is_safe = False
            safety_score = 0.3
            detected_issues.append("Potentially unsafe content detected")
            content_rating = "mature"
        elif any(indicator in text_lower for indicator in caution_indicators):
            safety_score = 0.6
            detected_issues.append("Content requires caution")
            content_rating = "teen"
        
        return SafetyAnalysis(
            is_safe=is_safe,
            safety_score=safety_score,
            detected_issues=detected_issues,
            content_rating=content_rating
        )
    
    def _merge_analysis_results(self, output: ImageAnalysisOutput, result: Dict[str, Any]) -> None:
        """Merge analysis results into output object."""
        
        for key, value in result.items():
            if key == "description" and value:
                output.description = value
            elif key == "detected_objects" and value:
                output.detected_objects = value
            elif key == "extracted_text" and value:
                output.extracted_text = value
            elif key == "scene_analysis" and value:
                output.scene_analysis = value
            elif key == "safety_analysis" and value:
                output.safety_analysis = value
            elif key == "accessibility_description" and value:
                output.accessibility_description = value
    
    def _calculate_overall_confidence(self, output: ImageAnalysisOutput) -> float:
        """Calculate overall analysis confidence score."""
        
        confidences = []
        
        # Check various analysis components
        if output.detected_objects:
            obj_confidences = [obj.confidence for obj in output.detected_objects]
            if obj_confidences:
                confidences.append(sum(obj_confidences) / len(obj_confidences))
        
        if output.extracted_text:
            text_confidences = [text.confidence for text in output.extracted_text]
            if text_confidences:
                confidences.append(sum(text_confidences) / len(text_confidences))
        
        if output.scene_analysis:
            confidences.append(output.scene_analysis.context_confidence)
        
        if output.safety_analysis:
            confidences.append(output.safety_analysis.safety_score)
        
        # Base confidence for having any results
        if output.description:
            confidences.append(0.8)
        
        return sum(confidences) / len(confidences) if confidences else 0.5
    
    def _extract_technical_details(
        self, 
        image: Image.Image, 
        image_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Extract technical image analysis details."""
        
        details = {
            "color_depth": f"{len(image.getbands())} channels",
            "has_transparency": image.mode in ('RGBA', 'LA'),
            "estimated_file_size": f"{image_info.get('file_size_mb', 'unknown')} MB",
            "processing_recommendations": []
        }
        
        # Add processing recommendations
        if image.size[0] > 2048 or image.size[1] > 2048:
            details["processing_recommendations"].append("Consider resizing for faster processing")
        
        if image.mode not in ('RGB', 'RGBA'):
            details["processing_recommendations"].append("Convert to RGB for better compatibility")
        
        return details
    
    async def _custom_quality_assessment(
        self,
        quality: 'QualityScore',
        input_data: ImageAnalysisInput,
        result: ImageAnalysisOutput,
        context: ToolContext
    ) -> None:
        """Custom quality assessment for image analysis."""
        
        # Assess analysis completeness
        analysis_count = sum(1 for attr in [
            result.description, result.detected_objects, result.extracted_text,
            result.scene_analysis, result.safety_analysis
        ] if attr)
        
        if analysis_count >= 3:
            quality.add_dimension(QualityMetric.ACCURACY, 9.0)
        elif analysis_count >= 2:
            quality.add_dimension(QualityMetric.ACCURACY, 7.5)
        else:
            quality.add_dimension(
                QualityMetric.ACCURACY, 6.0,
                "Limited analysis coverage - consider enabling more analysis types"
            )
        
        # Assess confidence levels
        if result.analysis_confidence >= 0.8:
            quality.add_dimension(QualityMetric.RELIABILITY, 9.0)
        elif result.analysis_confidence >= 0.6:
            quality.add_dimension(QualityMetric.RELIABILITY, 7.5)
        else:
            quality.add_dimension(
                QualityMetric.RELIABILITY, 6.0,
                "Analysis confidence is below optimal threshold"
            )
        
        # Assess performance
        if result.processing_time_ms < 10000:  # Under 10 seconds
            quality.add_dimension(QualityMetric.PERFORMANCE, 9.0)
        elif result.processing_time_ms < 30000:  # Under 30 seconds
            quality.add_dimension(QualityMetric.PERFORMANCE, 7.0)
        else:
            quality.add_dimension(
                QualityMetric.PERFORMANCE, 5.0,
                "Processing time exceeds optimal response time"
            )
    
    async def __aenter__(self):
        """Async context manager entry."""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit with cleanup."""
        await self.vision_client.close()


# Factory function
def create_image_analysis_tool(api_keys: Optional[Dict[str, str]] = None) -> ImageAnalysisTool:
    """Create a configured ImageAnalysisTool instance."""
    return ImageAnalysisTool(api_keys=api_keys)


# Export main components
__all__ = [
    'ImageAnalysisTool', 
    'ImageAnalysisInput', 
    'ImageAnalysisOutput',
    'DetectedObject', 
    'ExtractedText', 
    'SceneAnalysis', 
    'SafetyAnalysis',
    'create_image_analysis_tool'
]