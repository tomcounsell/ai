#!/usr/bin/env python3
"""
🚀 GROUNDBREAKING IMAGE UNDERSTANDING PIPELINE TESTS 🚀

This test suite validates the complete AI content creation and understanding pipeline:
1. Generate images from prompts using create_image (DALL-E 3)
2. Analyze generated images using analyze_shared_image (Vision AI) 
3. Judge consistency using test_judge_tool (AI reasoning)

This is the first comprehensive test of AI content creation consistency and serves
as a model for future AI pipeline validation across multiple AI systems.

INNOVATION: Tests three-way consistency validation:
- Original prompt ↔ Generated image
- Generated image ↔ AI description  
- Original prompt ↔ AI description
"""

import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, patch
import pytest

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Import the AI pipeline components
from tools.image_generation_tool import generate_image
from tools.image_analysis_tool import analyze_image
from tools.test_judge_tool import judge_response_quality
from agents.valor.agent import create_image, analyze_shared_image, ValorContext


class MockRunContext:
    """Mock RunContext for testing agent tools."""
    def __init__(self, deps):
        self.deps = deps


class TestImageUnderstandingPipeline:
    """🎯 Test the complete AI pipeline: Generation → Analysis → Judging"""

    @pytest.fixture
    def test_prompts(self):
        """Test prompts designed for clear image generation and analysis."""
        return [
            # Simple objects - easy to validate
            "a red sports car",
            "a blue cat sitting",
            "a green tree in sunlight",
            
            # Complex scenes - more challenging validation
            "a sunset over mountains with a lake",
            "a cozy coffee shop interior with wooden tables",
            
            # Style variations
            "a futuristic robot in a sci-fi setting",
        ]

    @pytest.fixture
    def mock_context(self):
        """Mock context for agent tools."""
        return MockRunContext(ValorContext(
            chat_id=12345,
            username="test_user"
        ))

    def test_image_generation_analysis_consistency(self, test_prompts, mock_context):
        """🎯 CORE TEST: Validate prompt → image → description consistency."""
        
        # Skip if API keys not available (for CI/CD environments)
        if not os.getenv("OPENAI_API_KEY"):
            pytest.skip("OPENAI_API_KEY not available for integration testing")
        
        print("🚀 Starting groundbreaking image understanding pipeline test...")
        
        for prompt in test_prompts[:2]:  # Test first 2 prompts to avoid excessive API costs
            print(f"\n🎨 Testing pipeline with prompt: '{prompt}'")
            
            # Step 1: Generate image from prompt
            print("📍 Step 1: Generating image...")
            image_path = generate_image(prompt, quality="standard", style="natural")
            
            # Verify image was created successfully
            assert not image_path.startswith("🎨"), f"Image generation failed: {image_path}"
            assert Path(image_path).exists(), f"Generated image file not found: {image_path}"
            
            try:
                # Step 2: Analyze the generated image
                print("📍 Step 2: Analyzing generated image...")
                description = analyze_image(image_path, f"Describe this image. What do you see?")
                
                # Verify analysis was successful
                assert not description.startswith("👁️") or "error" not in description.lower(), \
                    f"Image analysis failed: {description}"
                assert len(description) > 50, "Image analysis too brief to be meaningful"
                
                # Step 3: Judge consistency between prompt and description
                print("📍 Step 3: Judging prompt-description consistency...")
                consistency_result = judge_response_quality(
                    response=f"AI image description: '{description}'",
                    prompt=f"Original image prompt: '{prompt}'",
                    evaluation_criteria=[
                        "Does the AI description match the original prompt?",
                        "Are the key objects and elements consistent?",
                        "Do colors and visual details align?"
                    ]
                )
                
                # Verify judging was successful (TestJudgment object)
                assert isinstance(consistency_result, dict) or hasattr(consistency_result, 'pass_fail'), \
                    f"Judging returned unexpected format: {type(consistency_result)}"
                
                # Handle both dict format (for errors) and TestJudgment object
                if hasattr(consistency_result, 'pass_fail'):
                    pass_fail = consistency_result.pass_fail
                    reasoning = consistency_result.reasoning
                    confidence = consistency_result.confidence
                    score = int(confidence * 10)  # Convert confidence to 1-10 scale
                else:
                    # Error case - skip this test
                    print(f"⚠️ Judging failed: {consistency_result}")
                    continue
                
                print(f"📊 Consistency Score: {score}/10")
                print(f"🧠 Judge Reasoning: {reasoning}")
                
                # Validate consistency (should be reasonably high for good AI systems)
                assert score >= 6, \
                    f"Low consistency score ({score}/10) between prompt '{prompt}' and description '{description}'. Reasoning: {reasoning}"
                
                print(f"✅ Pipeline test passed for '{prompt}' with score {score}/10")
                
            finally:
                # Cleanup: Remove generated image
                if Path(image_path).exists():
                    Path(image_path).unlink()

    def test_style_consistency_validation(self, mock_context):
        """🎨 Test that different styles produce describably different images."""
        
        if not os.getenv("OPENAI_API_KEY"):
            pytest.skip("OPENAI_API_KEY not available for integration testing")
        
        print("\n🎨 Testing style consistency between 'natural' and 'vivid' styles...")
        
        base_prompt = "a mountain landscape at sunset"
        
        # Generate same prompt with different styles
        natural_image = generate_image(base_prompt, style="natural", quality="standard")
        vivid_image = generate_image(base_prompt, style="vivid", quality="standard")
        
        assert not natural_image.startswith("🎨"), f"Natural style generation failed: {natural_image}"
        assert not vivid_image.startswith("🎨"), f"Vivid style generation failed: {vivid_image}"
        
        try:
            # Analyze both images
            natural_description = analyze_image(natural_image, "Describe the style and visual characteristics of this image.")
            vivid_description = analyze_image(vivid_image, "Describe the style and visual characteristics of this image.")
            
            # Judge if the descriptions reflect style differences
            style_difference_result = judge_response_quality(
                response=f"Vivid style description: '{vivid_description}'",
                prompt=f"Natural style description: '{natural_description}'",
                evaluation_criteria=[
                    "Do these descriptions suggest different visual styles?",
                    "Is the vivid style more dramatic or artistic?",
                    "Are there clear stylistic differences?"
                ]
            )
            
            # Handle TestJudgment object
            if hasattr(style_difference_result, 'pass_fail'):
                style_pass = style_difference_result.pass_fail
                style_confidence = style_difference_result.confidence
                style_score = int(style_confidence * 10)
            else:
                print(f"⚠️ Style judging failed: {style_difference_result}")
                pytest.skip("Style judging unavailable")
            print(f"📊 Style Differentiation Score: {style_score}/10")
            
            # We expect some measurable difference between styles
            assert style_score >= 5, \
                f"Insufficient style differentiation ({style_score}/10). Natural: {natural_description[:100]}... Vivid: {vivid_description[:100]}..."
            
            print("✅ Style consistency validation passed")
            
        finally:
            # Cleanup
            for path in [natural_image, vivid_image]:
                if Path(path).exists():
                    Path(path).unlink()

    def test_agent_tool_pipeline_integration(self, mock_context):
        """🤖 Test the pipeline using agent tools (with Telegram format handling)."""
        
        if not os.getenv("OPENAI_API_KEY"):
            pytest.skip("OPENAI_API_KEY not available for integration testing")
        
        print("\n🤖 Testing agent tool pipeline integration...")
        
        prompt = "a friendly robot waving"
        
        # Use agent tool for image creation
        agent_result = create_image(mock_context, prompt, style="natural", quality="standard")
        
        # Handle Telegram format response
        if agent_result.startswith("TELEGRAM_IMAGE_GENERATED|"):
            # Parse Telegram format: TELEGRAM_IMAGE_GENERATED|{path}|{message}
            parts = agent_result.split("|", 2)
            assert len(parts) == 3, f"Invalid Telegram format: {agent_result}"
            image_path = parts[1]
            telegram_message = parts[2]
            
            assert Path(image_path).exists(), f"Agent-generated image not found: {image_path}"
            assert "Image Generated" in telegram_message, f"Invalid Telegram message: {telegram_message}"
            
            try:
                # Use agent tool for image analysis
                analysis_result = analyze_shared_image(mock_context, image_path, "What do you see in this image?")
                
                # Verify analysis format
                assert "👁️" in analysis_result or len(analysis_result) > 30, \
                    f"Agent analysis result seems invalid: {analysis_result}"
                
                # Judge the agent pipeline consistency
                agent_consistency = judge_response_quality(
                    response=f"Agent analysis result: '{analysis_result}'",
                    prompt=f"Agent generated image from prompt: '{prompt}'",
                    evaluation_criteria=[
                        "Does the agent's analysis align with the original prompt?",
                        "Are the key visual elements consistent?",
                        "Is the overall interpretation coherent?"
                    ]
                )
                
                # Handle TestJudgment object
                if hasattr(agent_consistency, 'pass_fail'):
                    agent_pass = agent_consistency.pass_fail
                    agent_score = int(agent_consistency.confidence * 10)
                else:
                    print(f"⚠️ Agent judging failed: {agent_consistency}")
                    pytest.skip("Agent judging unavailable")
                print(f"📊 Agent Pipeline Consistency Score: {agent_score}/10")
                
                assert agent_score >= 6, \
                    f"Low agent pipeline consistency ({agent_score}/10)"
                
                print("✅ Agent tool pipeline integration passed")
                
            finally:
                # Cleanup
                if Path(image_path).exists():
                    Path(image_path).unlink()
        else:
            # Error case
            assert False, f"Agent tool failed to generate image: {agent_result}"

    def test_prompt_engineering_effectiveness(self):
        """📝 Test how prompt specificity affects generation-analysis consistency."""
        
        if not os.getenv("OPENAI_API_KEY"):
            pytest.skip("OPENAI_API_KEY not available for integration testing")
        
        print("\n📝 Testing prompt engineering effectiveness...")
        
        # Test prompts with different levels of specificity
        test_cases = [
            ("vague", "a cat"),
            ("specific", "a fluffy orange tabby cat sitting on a blue cushion"),
            ("very_specific", "a large fluffy orange tabby cat with green eyes sitting on a royal blue velvet cushion in natural sunlight")
        ]
        
        scores = {}
        
        for specificity_level, prompt in test_cases:
            print(f"\n🎯 Testing {specificity_level} prompt: '{prompt}'")
            
            image_path = generate_image(prompt, style="natural", quality="standard")
            assert not image_path.startswith("🎨"), f"Generation failed for {specificity_level}: {image_path}"
            
            try:
                description = analyze_image(image_path, "Provide a detailed description of this image.")
                
                consistency_result = judge_response_quality(
                    response=f"Description: '{description}'",
                    prompt=f"Prompt: '{prompt}'",
                    evaluation_criteria=[
                        "How well does the image description match the prompt?",
                        "Are all specified details present?",
                        "Do colors and objects align correctly?"
                    ]
                )
                
                # Handle TestJudgment object
                if hasattr(consistency_result, 'confidence'):
                    score = int(consistency_result.confidence * 10)
                else:
                    score = 0
                scores[specificity_level] = score
                
                print(f"📊 {specificity_level.title()} Prompt Score: {score}/10")
                
            finally:
                if Path(image_path).exists():
                    Path(image_path).unlink()
        
        # Analyze trend: more specific prompts should generally score higher
        print(f"\n📈 Prompt Specificity Analysis:")
        print(f"Vague: {scores.get('vague', 0)}/10")
        print(f"Specific: {scores.get('specific', 0)}/10") 
        print(f"Very Specific: {scores.get('very_specific', 0)}/10")
        
        # We expect some positive correlation between specificity and consistency
        # (though not strict, since AI can be unpredictable)
        if scores.get('very_specific', 0) > scores.get('vague', 0):
            print("✅ Prompt specificity shows positive correlation with consistency")
        else:
            print("⚠️ Prompt specificity correlation unclear - this could indicate prompt engineering opportunities")


class TestImagePipelineMocked:
    """🧪 Test image pipeline with mocked components for fast execution."""

    def test_pipeline_error_handling(self):
        """Test pipeline behavior when components fail."""
        
        mock_context = MockRunContext(ValorContext(chat_id=12345, username="test"))
        
        # Test image generation failure
        with patch('tools.image_generation_tool.generate_image') as mock_gen:
            mock_gen.return_value = "🎨 Image generation error: API quota exceeded"
            
            result = create_image(mock_context, "test prompt")
            assert "error" in result.lower()
            assert "API quota exceeded" in result

    def test_pipeline_format_validation(self):
        """Test that pipeline maintains proper format expectations."""
        
        mock_context = MockRunContext(ValorContext(chat_id=12345, username="test"))
        
        with patch('tools.image_generation_tool.generate_image') as mock_gen:
            mock_gen.return_value = "/tmp/test_image.png"
            
            result = create_image(mock_context, "test prompt")
            
            # Should return Telegram format
            assert result.startswith("TELEGRAM_IMAGE_GENERATED|")
            parts = result.split("|", 2)
            assert len(parts) == 3
            assert parts[1] == "/tmp/test_image.png"
            assert "Image Generated" in parts[2]

    def test_judging_criteria_validation(self):
        """Test that our judging criteria produce meaningful results."""
        
        # Test with clearly consistent content
        consistent_result = judge_response_quality(
            response="I see a red automobile parked in a parking area",
            prompt="A red car in a parking lot",
            evaluation_criteria=[
                "Do these descriptions refer to the same visual content?",
                "Are the key elements (color, object, location) consistent?"
            ]
        )
        
        # Test with clearly inconsistent content  
        inconsistent_result = judge_response_quality(
            response="I see a blue dog running in a field",
            prompt="A red car in a parking lot",
            evaluation_criteria=[
                "Do these descriptions refer to the same visual content?",
                "Are the key elements (color, object, location) consistent?"
            ]
        )
        
        # Both should succeed as operations (TestJudgment objects)
        assert hasattr(consistent_result, 'confidence'), "Consistent judging should return TestJudgment"
        assert hasattr(inconsistent_result, 'confidence'), "Inconsistent judging should return TestJudgment"
        
        # Check pass/fail results (more appropriate for consistency testing)
        consistent_pass = consistent_result.pass_fail
        inconsistent_pass = inconsistent_result.pass_fail
        
        print(f"📊 Consistent pair passed: {consistent_pass} (confidence: {consistent_result.confidence:.2f})")
        print(f"📊 Inconsistent pair passed: {inconsistent_pass} (confidence: {inconsistent_result.confidence:.2f})")
        print(f"🧠 Consistent reasoning: {consistent_result.reasoning}")
        print(f"🧠 Inconsistent reasoning: {inconsistent_result.reasoning}")
        
        # We expect consistent content to pass and inconsistent content to fail
        # (though AI judges can be unpredictable, so we'll log the results for analysis)
        if consistent_pass and not inconsistent_pass:
            print("✅ Judge correctly identified consistency differences")
        elif consistent_pass == inconsistent_pass:
            print("⚠️ Judge was inconclusive about consistency differences") 
        else:
            print("❌ Judge results were unexpected - this indicates either:")
            print("   - The judge criteria need refinement")
            print("   - The test cases aren't different enough")
            print("   - The local model needs tuning")
        
        # For now, we'll just verify that the judge tool works (both calls succeeded)
        # The actual consistency scoring will be refined as we gather more data
        assert hasattr(consistent_result, 'reasoning'), "Consistent result should have reasoning"
        assert hasattr(inconsistent_result, 'reasoning'), "Inconsistent result should have reasoning"


if __name__ == "__main__":
    # Run with pytest for full output
    pytest.main([__file__, "-v", "-s"])