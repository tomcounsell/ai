"""
Image Generation Tool - DALL-E Integration

Advanced image generation tool with DALL-E API integration, offering comprehensive
creative capabilities, style control, and quality optimization.

Features:
- Multi-model image generation (DALL-E 3, DALL-E 2)
- Advanced prompt engineering and enhancement
- Style transfer and artistic controls
- Batch generation with variations
- Quality assessment and refinement
- Content safety and policy compliance
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
from typing import Dict, List, Optional, Any, Union, Tuple
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageEnhance, ImageFilter
from pydantic import BaseModel, Field, validator

from .base import (
    ToolImplementation, BaseInputModel, BaseOutputModel, ToolContext,
    ToolError, ErrorCategory, QualityMetric, performance_monitor
)


class ImageGenerationInput(BaseInputModel):
    """Input model for image generation requests."""
    
    prompt: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="Text prompt for image generation"
    )
    
    model: str = Field(
        default="dall-e-3",
        pattern="^(dall-e-2|dall-e-3)$",
        description="Model to use for generation"
    )
    
    size: str = Field(
        default="1024x1024",
        pattern="^(256x256|512x512|1024x1024|1024x1792|1792x1024)$",
        description="Generated image size"
    )
    
    quality: str = Field(
        default="standard",
        pattern="^(standard|hd)$",
        description="Image quality level (DALL-E 3 only)"
    )
    
    style: str = Field(
        default="vivid",
        pattern="^(natural|vivid)$",
        description="Image style preference (DALL-E 3 only)"
    )
    
    n_images: int = Field(
        default=1,
        ge=1,
        le=10,
        description="Number of images to generate"
    )
    
    enhance_prompt: bool = Field(
        default=True,
        description="Enable automatic prompt enhancement"
    )
    
    negative_prompt: Optional[str] = Field(
        default=None,
        max_length=1000,
        description="Elements to avoid in generation"
    )
    
    artistic_style: Optional[str] = Field(
        default=None,
        description="Specific artistic style to emulate"
    )
    
    color_palette: Optional[List[str]] = Field(
        default=None,
        description="Preferred color palette (hex colors)"
    )
    
    composition: Optional[str] = Field(
        default=None,
        pattern="^(portrait|landscape|square|close-up|wide-shot|rule-of-thirds)$",
        description="Composition preference"
    )
    
    lighting: Optional[str] = Field(
        default=None,
        pattern="^(natural|dramatic|soft|studio|golden-hour|blue-hour)$",
        description="Lighting preference"
    )
    
    mood: Optional[str] = Field(
        default=None,
        description="Desired mood or atmosphere"
    )
    
    reference_image: Optional[str] = Field(
        default=None,
        description="Reference image URL or path for style guidance"
    )
    
    safety_filter: bool = Field(
        default=True,
        description="Enable content safety filtering"
    )
    
    save_to_disk: bool = Field(
        default=False,
        description="Save generated images to disk"
    )
    
    output_directory: Optional[str] = Field(
        default=None,
        description="Directory to save images (if save_to_disk is True)"
    )
    
    @validator('prompt')
    def validate_prompt(cls, v):
        """Validate and clean prompt."""
        if not v.strip():
            raise ValueError("Prompt cannot be empty")
        
        # Check for potentially harmful content
        harmful_patterns = [
            'nude', 'naked', 'nsfw', 'sexual', 'explicit',
            'violence', 'gore', 'blood', 'weapon', 'drug',
            'hate', 'discrimination', 'offensive'
        ]
        
        prompt_lower = v.lower()
        flagged_terms = [term for term in harmful_patterns if term in prompt_lower]
        
        if flagged_terms:
            raise ValueError(f"Prompt contains potentially harmful content: {flagged_terms}")
        
        return v.strip()
    
    @validator('size')
    def validate_size_compatibility(cls, v, values):
        """Validate size compatibility with selected model."""
        if 'model' in values:
            model = values['model']
            if model == 'dall-e-2' and v not in ['256x256', '512x512', '1024x1024']:
                raise ValueError(f"Size {v} not supported for DALL-E 2")
        return v
    
    @validator('color_palette')
    def validate_color_palette(cls, v):
        """Validate hex color format."""
        if v is None:
            return v
        
        validated_colors = []
        for color in v:
            if not color.startswith('#') or len(color) != 7:
                raise ValueError(f"Invalid hex color format: {color}")
            
            try:
                int(color[1:], 16)  # Validate hex digits
                validated_colors.append(color.upper())
            except ValueError:
                raise ValueError(f"Invalid hex color: {color}")
        
        return validated_colors


class GeneratedImage(BaseModel):
    """Individual generated image with metadata."""
    
    image_url: Optional[str] = Field(None, description="URL of generated image")
    image_data: Optional[str] = Field(None, description="Base64 encoded image data")
    revised_prompt: Optional[str] = Field(None, description="AI-revised prompt used")
    
    # Generation metadata
    model_used: str = Field(..., description="Model used for generation")
    generation_time_ms: float = Field(..., description="Generation time in milliseconds")
    size: str = Field(..., description="Image dimensions")
    quality: str = Field(..., description="Quality setting used")
    style: str = Field(..., description="Style setting used")
    
    # Quality metrics
    estimated_quality_score: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="Estimated quality score"
    )
    content_safety_score: float = Field(
        default=1.0, ge=0.0, le=1.0,
        description="Content safety score"
    )
    prompt_adherence_score: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="How well image matches prompt"
    )
    
    # Technical details
    file_size_bytes: Optional[int] = Field(None, description="Image file size")
    color_depth: Optional[str] = Field(None, description="Color depth information")
    dominant_colors: List[str] = Field(
        default_factory=list, description="Dominant colors in the image"
    )
    
    # Storage information
    local_path: Optional[str] = Field(None, description="Local file path if saved")
    saved_at: Optional[datetime] = Field(None, description="Timestamp when saved")


class ImageGenerationOutput(BaseOutputModel):
    """Complete image generation response."""
    
    original_prompt: str = Field(..., description="Original input prompt")
    enhanced_prompt: Optional[str] = Field(None, description="AI-enhanced prompt")
    generated_images: List[GeneratedImage] = Field(..., description="Generated images")
    
    # Generation summary
    total_generated: int = Field(..., description="Total number of images generated")
    successful_generations: int = Field(..., description="Number of successful generations")
    failed_generations: int = Field(default=0, description="Number of failed generations")
    
    # Performance metrics
    total_generation_time_ms: float = Field(..., description="Total processing time")
    average_generation_time_ms: float = Field(..., description="Average time per image")
    
    # Quality assessment
    overall_quality_score: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="Overall quality assessment"
    )
    style_consistency_score: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="Style consistency across images"
    )
    prompt_satisfaction_score: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="How well results satisfy the prompt"
    )
    
    # Analysis and insights
    generation_insights: Dict[str, Any] = Field(
        default_factory=dict,
        description="Insights about the generation process"
    )
    improvement_suggestions: List[str] = Field(
        default_factory=list,
        description="Suggestions for better results"
    )
    
    # Content analysis
    detected_themes: List[str] = Field(
        default_factory=list,
        description="Detected themes in generated images"
    )
    artistic_elements: Dict[str, Any] = Field(
        default_factory=dict,
        description="Artistic elements analysis"
    )
    
    # Safety and compliance
    content_safety_assessment: Dict[str, Any] = Field(
        default_factory=dict,
        description="Content safety analysis"
    )
    policy_compliance: bool = Field(
        default=True,
        description="Whether content complies with usage policies"
    )


class PromptEnhancer:
    """Advanced prompt enhancement for better image generation."""
    
    def __init__(self):
        # Style keywords and modifiers
        self.style_keywords = {
            "photorealistic": ["photorealistic", "hyperrealistic", "detailed photography"],
            "artistic": ["artistic", "painterly", "expressive", "creative"],
            "cinematic": ["cinematic", "film-like", "dramatic lighting", "movie scene"],
            "fantasy": ["fantasy", "magical", "ethereal", "dreamlike"],
            "abstract": ["abstract", "geometric", "non-representational"],
            "vintage": ["vintage", "retro", "classic", "nostalgic"]
        }
        
        # Quality enhancers
        self.quality_modifiers = [
            "high quality", "detailed", "sharp focus", "professional",
            "award-winning", "masterpiece", "stunning", "beautiful"
        ]
        
        # Technical modifiers
        self.technical_terms = [
            "8K resolution", "highly detailed", "professional photography",
            "studio lighting", "perfect composition", "ultra-realistic"
        ]
    
    def enhance_prompt(
        self, 
        original_prompt: str, 
        artistic_style: Optional[str] = None,
        composition: Optional[str] = None,
        lighting: Optional[str] = None,
        mood: Optional[str] = None,
        color_palette: Optional[List[str]] = None
    ) -> str:
        """Enhance prompt with style and quality modifiers."""
        
        enhanced_parts = [original_prompt]
        
        # Add artistic style
        if artistic_style:
            if artistic_style.lower() in self.style_keywords:
                style_terms = self.style_keywords[artistic_style.lower()]
                enhanced_parts.extend(style_terms[:2])  # Add up to 2 style terms
            else:
                enhanced_parts.append(f"in {artistic_style} style")
        
        # Add composition guidance
        if composition:
            if composition == "portrait":
                enhanced_parts.append("portrait orientation, vertical composition")
            elif composition == "landscape":
                enhanced_parts.append("landscape orientation, wide composition")
            elif composition == "rule-of-thirds":
                enhanced_parts.append("rule of thirds composition, balanced framing")
            elif composition == "close-up":
                enhanced_parts.append("close-up shot, detailed focus")
            elif composition == "wide-shot":
                enhanced_parts.append("wide shot, expansive view")
        
        # Add lighting
        if lighting:
            lighting_map = {
                "natural": "natural lighting, soft shadows",
                "dramatic": "dramatic lighting, strong contrast",
                "soft": "soft diffused lighting, gentle shadows",
                "studio": "professional studio lighting, even illumination",
                "golden-hour": "golden hour lighting, warm tones",
                "blue-hour": "blue hour lighting, cool tones"
            }
            if lighting in lighting_map:
                enhanced_parts.append(lighting_map[lighting])
        
        # Add mood
        if mood:
            enhanced_parts.append(f"{mood} mood")
        
        # Add color palette guidance
        if color_palette and len(color_palette) > 0:
            color_names = self._hex_to_color_names(color_palette[:3])  # Use up to 3 colors
            enhanced_parts.append(f"color palette: {', '.join(color_names)}")
        
        # Add quality modifiers
        enhanced_parts.extend(self.quality_modifiers[:2])
        
        # Join and clean up
        enhanced_prompt = ", ".join(enhanced_parts)
        
        # Clean up duplicate commas and spaces
        enhanced_prompt = ", ".join(part.strip() for part in enhanced_prompt.split(",") if part.strip())
        
        # Limit total length
        if len(enhanced_prompt) > 3800:  # Leave room for API limits
            enhanced_prompt = enhanced_prompt[:3800].rsplit(",", 1)[0]
        
        return enhanced_prompt
    
    def _hex_to_color_names(self, hex_colors: List[str]) -> List[str]:
        """Convert hex colors to approximate color names."""
        color_map = {
            "#FF0000": "red", "#00FF00": "green", "#0000FF": "blue",
            "#FFFF00": "yellow", "#FF00FF": "magenta", "#00FFFF": "cyan",
            "#FFA500": "orange", "#800080": "purple", "#FFC0CB": "pink",
            "#A52A2A": "brown", "#808080": "gray", "#000000": "black",
            "#FFFFFF": "white", "#FFD700": "gold", "#C0C0C0": "silver"
        }
        
        color_names = []
        for hex_color in hex_colors:
            # Find closest match (simplified)
            closest_color = "colored"
            min_distance = float('inf')
            
            try:
                r1 = int(hex_color[1:3], 16)
                g1 = int(hex_color[3:5], 16)
                b1 = int(hex_color[5:7], 16)
                
                for known_hex, name in color_map.items():
                    r2 = int(known_hex[1:3], 16)
                    g2 = int(known_hex[3:5], 16)
                    b2 = int(known_hex[5:7], 16)
                    
                    distance = ((r1-r2)**2 + (g1-g2)**2 + (b1-b2)**2)**0.5
                    if distance < min_distance:
                        min_distance = distance
                        closest_color = name
                
                color_names.append(closest_color)
            except:
                color_names.append("colored")
        
        return color_names


class ImageGenerationTool(ToolImplementation[ImageGenerationInput, ImageGenerationOutput]):
    """
    Advanced Image Generation Tool with DALL-E Integration
    
    Provides comprehensive image generation capabilities with prompt enhancement,
    quality optimization, and batch processing support.
    """
    
    def __init__(self, api_key: Optional[str] = None, **kwargs):
        super().__init__(
            name="image_generation",
            version="1.2.0",
            description="Advanced AI-powered image generation with DALL-E integration",
            **kwargs
        )
        
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key is required for image generation")
        
        self.client = httpx.AsyncClient(
            timeout=120.0,  # Longer timeout for image generation
            headers={"Authorization": f"Bearer {self.api_key}"}
        )
        
        # Initialize prompt enhancer
        self.prompt_enhancer = PromptEnhancer()
        
        # Configuration
        self.max_retries = 3
        self.rate_limit_delay = 1.0
        self.default_output_dir = Path("./generated_images")
        
        # Tracking
        self._generation_count = 0
        self._total_cost = 0.0
    
    @property
    def input_model(self) -> type:
        return ImageGenerationInput
    
    @property
    def output_model(self) -> type:
        return ImageGenerationOutput
    
    async def _execute_core(
        self, 
        input_data: ImageGenerationInput, 
        context: ToolContext
    ) -> ImageGenerationOutput:
        """Core image generation execution."""
        
        start_time = time.time()
        
        try:
            # Step 1: Enhance prompt if requested
            enhanced_prompt = input_data.prompt
            if input_data.enhance_prompt:
                enhanced_prompt = self.prompt_enhancer.enhance_prompt(
                    input_data.prompt,
                    input_data.artistic_style,
                    input_data.composition,
                    input_data.lighting,
                    input_data.mood,
                    input_data.color_palette
                )
                context.add_trace_data("enhanced_prompt", enhanced_prompt)
            
            # Step 2: Validate generation parameters
            self._validate_generation_parameters(input_data)
            
            # Step 3: Generate images
            generated_images = []
            successful_count = 0
            failed_count = 0
            
            for i in range(input_data.n_images):
                try:
                    self.logger.info(f"Generating image {i+1}/{input_data.n_images}")
                    
                    image_result = await self._generate_single_image(
                        enhanced_prompt, input_data, context
                    )
                    
                    if image_result:
                        # Post-process image
                        processed_image = await self._post_process_image(
                            image_result, input_data, i
                        )
                        generated_images.append(processed_image)
                        successful_count += 1
                    else:
                        failed_count += 1
                    
                    # Rate limiting between generations
                    if i < input_data.n_images - 1:
                        await asyncio.sleep(self.rate_limit_delay)
                
                except Exception as e:
                    self.logger.error(f"Failed to generate image {i+1}: {str(e)}")
                    failed_count += 1
                    continue
            
            # Step 4: Analyze and assess results
            total_time = (time.time() - start_time) * 1000
            avg_time = total_time / max(successful_count, 1)
            
            # Step 5: Generate insights and analysis
            insights = await self._analyze_generation_results(
                generated_images, input_data, enhanced_prompt
            )
            
            # Step 6: Build output
            output = ImageGenerationOutput(
                original_prompt=input_data.prompt,
                enhanced_prompt=enhanced_prompt if enhanced_prompt != input_data.prompt else None,
                generated_images=generated_images,
                total_generated=len(generated_images),
                successful_generations=successful_count,
                failed_generations=failed_count,
                total_generation_time_ms=total_time,
                average_generation_time_ms=avg_time,
                overall_quality_score=insights.get("overall_quality", 0.7),
                style_consistency_score=insights.get("style_consistency", 0.8),
                prompt_satisfaction_score=insights.get("prompt_satisfaction", 0.8),
                generation_insights=insights.get("insights", {}),
                improvement_suggestions=insights.get("suggestions", []),
                detected_themes=insights.get("themes", []),
                artistic_elements=insights.get("artistic_elements", {}),
                content_safety_assessment=insights.get("safety_assessment", {}),
                policy_compliance=insights.get("policy_compliant", True)
            )
            
            return output
            
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(
                f"Image generation failed: {str(e)}",
                ErrorCategory.INTERNAL_ERROR,
                details={"error": str(e)}
            )
    
    async def _generate_single_image(
        self,
        prompt: str,
        input_data: ImageGenerationInput,
        context: ToolContext
    ) -> Optional[Dict[str, Any]]:
        """Generate a single image using DALL-E API."""
        
        generation_start = time.time()
        
        try:
            # Build request payload
            payload = {
                "model": input_data.model,
                "prompt": prompt,
                "size": input_data.size,
                "n": 1,  # Generate one at a time for better error handling
                "response_format": "url"  # Get URL initially
            }
            
            # Add model-specific parameters
            if input_data.model == "dall-e-3":
                payload.update({
                    "quality": input_data.quality,
                    "style": input_data.style
                })
            
            # Make API request
            response = await self.client.post(
                "https://api.openai.com/v1/images/generations",
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            
            response.raise_for_status()
            data = response.json()
            
            generation_time = (time.time() - generation_start) * 1000
            
            # Extract result
            if data.get("data") and len(data["data"]) > 0:
                image_data = data["data"][0]
                
                result = {
                    "image_url": image_data.get("url"),
                    "revised_prompt": image_data.get("revised_prompt"),
                    "generation_time_ms": generation_time,
                    "model_used": input_data.model,
                    "size": input_data.size,
                    "quality": input_data.quality,
                    "style": input_data.style
                }
                
                # Download image data if needed
                if input_data.save_to_disk or not image_data.get("url"):
                    image_binary = await self._download_image(image_data.get("url"))
                    if image_binary:
                        result["image_data"] = base64.b64encode(image_binary).decode()
                
                self._generation_count += 1
                return result
            
            return None
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get("error", {}).get("message", "Bad request")
                
                if "content_policy_violation" in error_message.lower():
                    raise ToolError(
                        f"Content policy violation: {error_message}",
                        ErrorCategory.INPUT_VALIDATION,
                        details={"policy_violation": True}
                    )
                else:
                    raise ToolError(
                        f"Invalid generation request: {error_message}",
                        ErrorCategory.INPUT_VALIDATION,
                        details={"api_error": error_message}
                    )
            
            elif e.response.status_code == 401:
                raise ToolError(
                    "Invalid API key",
                    ErrorCategory.AUTHENTICATION,
                    recoverable=False
                )
            
            elif e.response.status_code == 429:
                raise ToolError(
                    "Rate limit exceeded",
                    ErrorCategory.RATE_LIMITING,
                    retry_after=60.0
                )
            
            else:
                raise ToolError(
                    f"API error: {e.response.status_code}",
                    ErrorCategory.EXTERNAL_API,
                    details={"status_code": e.response.status_code}
                )
        
        except httpx.TimeoutException:
            raise ToolError(
                "Generation request timed out",
                ErrorCategory.TIMEOUT,
                recoverable=True,
                retry_after=30.0
            )
    
    async def _download_image(self, image_url: str) -> Optional[bytes]:
        """Download image from URL."""
        if not image_url:
            return None
        
        try:
            response = await self.client.get(image_url)
            response.raise_for_status()
            return response.content
        except Exception as e:
            self.logger.error(f"Failed to download image: {str(e)}")
            return None
    
    async def _post_process_image(
        self,
        image_result: Dict[str, Any],
        input_data: ImageGenerationInput,
        image_index: int
    ) -> GeneratedImage:
        """Post-process generated image with quality assessment."""
        
        try:
            # Download image if not already downloaded
            image_data = image_result.get("image_data")
            if not image_data and image_result.get("image_url"):
                image_binary = await self._download_image(image_result["image_url"])
                if image_binary:
                    image_data = base64.b64encode(image_binary).decode()
            
            # Create PIL image for analysis
            pil_image = None
            if image_data:
                try:
                    image_binary = base64.b64decode(image_data)
                    pil_image = Image.open(io.BytesIO(image_binary))
                except Exception as e:
                    self.logger.warning(f"Failed to create PIL image: {str(e)}")
            
            # Basic quality assessment
            quality_score = await self._assess_image_quality(pil_image, input_data.prompt)
            
            # Extract dominant colors
            dominant_colors = []
            if pil_image:
                try:
                    dominant_colors = self._extract_dominant_colors(pil_image)
                except Exception as e:
                    self.logger.warning(f"Failed to extract colors: {str(e)}")
            
            # Save to disk if requested
            local_path = None
            saved_at = None
            if input_data.save_to_disk and image_data:
                local_path = await self._save_image_to_disk(
                    image_data, input_data, image_index
                )
                saved_at = datetime.utcnow()
            
            # Build GeneratedImage object
            generated_image = GeneratedImage(
                image_url=image_result.get("image_url"),
                image_data=image_data,
                revised_prompt=image_result.get("revised_prompt"),
                model_used=image_result["model_used"],
                generation_time_ms=image_result["generation_time_ms"],
                size=image_result["size"],
                quality=image_result["quality"],
                style=image_result["style"],
                estimated_quality_score=quality_score,
                content_safety_score=1.0,  # Assume safe if generation succeeded
                prompt_adherence_score=quality_score * 0.9,  # Approximate
                file_size_bytes=len(base64.b64decode(image_data)) if image_data else None,
                color_depth="RGB",
                dominant_colors=dominant_colors,
                local_path=local_path,
                saved_at=saved_at
            )
            
            return generated_image
            
        except Exception as e:
            self.logger.error(f"Post-processing failed: {str(e)}")
            
            # Return minimal GeneratedImage object
            return GeneratedImage(
                image_url=image_result.get("image_url"),
                model_used=image_result.get("model_used", "unknown"),
                generation_time_ms=image_result.get("generation_time_ms", 0),
                size=image_result.get("size", "unknown"),
                quality=image_result.get("quality", "standard"),
                style=image_result.get("style", "vivid")
            )
    
    async def _assess_image_quality(
        self, 
        image: Optional[Image.Image], 
        original_prompt: str
    ) -> float:
        """Assess generated image quality."""
        
        if not image:
            return 0.5  # Default score for unavailable images
        
        quality_score = 0.7  # Base score
        
        try:
            # Check image dimensions and aspect ratio
            width, height = image.size
            
            # Prefer standard resolutions
            if min(width, height) >= 512:
                quality_score += 0.1
            
            if width == height:  # Square images often work well
                quality_score += 0.05
            
            # Check for common quality issues
            # Convert to RGB for analysis
            if image.mode != 'RGB':
                image = image.convert('RGB')
            
            # Simple blur detection (very basic)
            gray_image = image.convert('L')
            import numpy as np
            img_array = np.array(gray_image)
            
            # Calculate image variance (higher = less blurry)
            variance = np.var(img_array)
            if variance > 1000:  # Arbitrary threshold
                quality_score += 0.1
            
            # Color distribution check
            colors = image.getcolors(maxcolors=256*256*256)
            if colors:
                unique_colors = len(colors)
                if unique_colors > 1000:  # Rich color palette
                    quality_score += 0.05
            
        except Exception as e:
            self.logger.warning(f"Quality assessment failed: {str(e)}")
        
        return min(quality_score, 1.0)
    
    def _extract_dominant_colors(self, image: Image.Image, num_colors: int = 5) -> List[str]:
        """Extract dominant colors from image."""
        try:
            # Convert to RGB if necessary
            if image.mode != 'RGB':
                image = image.convert('RGB')
            
            # Resize for faster processing
            small_image = image.resize((150, 150))
            
            # Use quantization to find dominant colors
            quantized = small_image.quantize(colors=num_colors, method=Image.Quantize.MEDIANCUT)
            palette = quantized.getpalette()
            
            # Extract colors
            colors = []
            for i in range(num_colors):
                r, g, b = palette[i*3:(i+1)*3]
                hex_color = f"#{r:02x}{g:02x}{b:02x}"
                colors.append(hex_color)
            
            return colors[:3]  # Return top 3 colors
            
        except Exception as e:
            self.logger.warning(f"Color extraction failed: {str(e)}")
            return []
    
    async def _save_image_to_disk(
        self,
        image_data: str,
        input_data: ImageGenerationInput,
        image_index: int
    ) -> Optional[str]:
        """Save generated image to disk."""
        
        try:
            # Determine output directory
            output_dir = Path(input_data.output_directory) if input_data.output_directory else self.default_output_dir
            output_dir.mkdir(parents=True, exist_ok=True)
            
            # Generate filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            prompt_hash = hashlib.md5(input_data.prompt.encode()).hexdigest()[:8]
            filename = f"generated_{timestamp}_{prompt_hash}_{image_index:02d}.png"
            
            file_path = output_dir / filename
            
            # Save image
            image_binary = base64.b64decode(image_data)
            with open(file_path, 'wb') as f:
                f.write(image_binary)
            
            self.logger.info(f"Image saved to: {file_path}")
            return str(file_path)
            
        except Exception as e:
            self.logger.error(f"Failed to save image: {str(e)}")
            return None
    
    async def _analyze_generation_results(
        self,
        generated_images: List[GeneratedImage],
        input_data: ImageGenerationInput,
        enhanced_prompt: str
    ) -> Dict[str, Any]:
        """Analyze generation results for insights and improvements."""
        
        analysis = {
            "overall_quality": 0.7,
            "style_consistency": 0.8,
            "prompt_satisfaction": 0.8,
            "insights": {},
            "suggestions": [],
            "themes": [],
            "artistic_elements": {},
            "safety_assessment": {"compliant": True},
            "policy_compliant": True
        }
        
        if not generated_images:
            analysis["suggestions"] = [
                "All generations failed - check prompt content and API status",
                "Consider simplifying the prompt",
                "Verify API key and account status"
            ]
            return analysis
        
        # Calculate average quality
        quality_scores = [img.estimated_quality_score for img in generated_images]
        analysis["overall_quality"] = sum(quality_scores) / len(quality_scores)
        
        # Analyze generation times
        gen_times = [img.generation_time_ms for img in generated_images]
        avg_time = sum(gen_times) / len(gen_times)
        
        analysis["insights"]["performance"] = {
            "average_generation_time_ms": avg_time,
            "fastest_generation_ms": min(gen_times),
            "slowest_generation_ms": max(gen_times),
            "total_images_generated": len(generated_images)
        }
        
        # Model usage analysis
        models_used = [img.model_used for img in generated_images]
        analysis["insights"]["models"] = {
            "primary_model": max(set(models_used), key=models_used.count),
            "model_distribution": {model: models_used.count(model) for model in set(models_used)}
        }
        
        # Generate improvement suggestions
        if analysis["overall_quality"] < 0.6:
            analysis["suggestions"].append("Consider using DALL-E 3 for higher quality")
            analysis["suggestions"].append("Try more specific and detailed prompts")
        
        if input_data.model == "dall-e-2":
            analysis["suggestions"].append("Consider upgrading to DALL-E 3 for better quality and prompt adherence")
        
        if avg_time > 30000:  # 30 seconds
            analysis["suggestions"].append("Generation times are high - consider using standard quality")
        
        # Extract themes from revised prompts
        revised_prompts = [img.revised_prompt for img in generated_images if img.revised_prompt]
        if revised_prompts:
            common_words = self._extract_common_themes(revised_prompts)
            analysis["themes"] = common_words[:5]
        
        # Color analysis
        all_colors = []
        for img in generated_images:
            all_colors.extend(img.dominant_colors)
        
        if all_colors:
            from collections import Counter
            color_counts = Counter(all_colors)
            analysis["artistic_elements"]["color_palette"] = dict(color_counts.most_common(5))
        
        return analysis
    
    def _extract_common_themes(self, texts: List[str]) -> List[str]:
        """Extract common themes from text list."""
        # Simple word frequency analysis
        from collections import Counter
        import re
        
        all_words = []
        for text in texts:
            # Simple word extraction
            words = re.findall(r'\b[a-zA-Z]{4,}\b', text.lower())
            # Filter out common words
            stopwords = {
                'with', 'this', 'that', 'have', 'from', 'they', 'been',
                'were', 'said', 'each', 'which', 'their', 'time', 'will'
            }
            meaningful_words = [w for w in words if w not in stopwords]
            all_words.extend(meaningful_words)
        
        word_counts = Counter(all_words)
        return [word for word, count in word_counts.most_common(10)]
    
    def _validate_generation_parameters(self, input_data: ImageGenerationInput) -> None:
        """Validate generation parameters for compatibility."""
        
        # Check model-specific constraints
        if input_data.model == "dall-e-2":
            if input_data.size not in ["256x256", "512x512", "1024x1024"]:
                raise ToolError(
                    f"Size {input_data.size} not supported for DALL-E 2",
                    ErrorCategory.INPUT_VALIDATION
                )
            
            if input_data.n_images > 10:
                raise ToolError(
                    "DALL-E 2 supports maximum 10 images per request",
                    ErrorCategory.INPUT_VALIDATION
                )
        
        elif input_data.model == "dall-e-3":
            if input_data.n_images > 1:
                # Note: DALL-E 3 typically generates 1 image at a time
                self.logger.warning("DALL-E 3 generates images sequentially")
    
    async def _custom_quality_assessment(
        self,
        quality: 'QualityScore',
        input_data: ImageGenerationInput,
        result: ImageGenerationOutput,
        context: ToolContext
    ) -> None:
        """Custom quality assessment for image generation."""
        
        # Assess generation success rate
        success_rate = result.successful_generations / (result.successful_generations + result.failed_generations)
        
        if success_rate >= 0.9:
            quality.add_dimension(QualityMetric.RELIABILITY, 9.5)
        elif success_rate >= 0.7:
            quality.add_dimension(QualityMetric.RELIABILITY, 8.0)
        else:
            quality.add_dimension(
                QualityMetric.RELIABILITY, 6.0,
                f"Low success rate: {success_rate:.1%}"
            )
        
        # Assess overall quality
        if result.overall_quality_score >= 0.8:
            quality.add_dimension(QualityMetric.ACCURACY, 9.0)
        elif result.overall_quality_score >= 0.6:
            quality.add_dimension(QualityMetric.ACCURACY, 7.5)
        else:
            quality.add_dimension(
                QualityMetric.ACCURACY, 6.0,
                "Generated image quality below expectations"
            )
        
        # Assess performance
        if result.average_generation_time_ms < 15000:  # Under 15 seconds
            quality.add_dimension(QualityMetric.PERFORMANCE, 9.0)
        elif result.average_generation_time_ms < 45000:  # Under 45 seconds
            quality.add_dimension(QualityMetric.PERFORMANCE, 7.5)
        else:
            quality.add_dimension(
                QualityMetric.PERFORMANCE, 6.0,
                "Generation times are longer than optimal"
            )
        
        # Assess prompt satisfaction
        if result.prompt_satisfaction_score >= 0.8:
            quality.add_dimension(QualityMetric.USABILITY, 8.5)
        else:
            quality.add_dimension(
                QualityMetric.USABILITY, 7.0,
                "Generated images may not fully match the intended prompt"
            )
    
    async def __aenter__(self):
        """Async context manager entry."""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit with cleanup."""
        await self.client.aclose()


# Factory function
def create_image_generation_tool(api_key: Optional[str] = None) -> ImageGenerationTool:
    """Create a configured ImageGenerationTool instance."""
    return ImageGenerationTool(api_key=api_key)


# Export main components
__all__ = [
    'ImageGenerationTool',
    'ImageGenerationInput',
    'ImageGenerationOutput',
    'GeneratedImage',
    'create_image_generation_tool'
]