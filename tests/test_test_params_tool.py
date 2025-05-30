"""Tests for test_params_tool.py - Test parameter generation functionality."""

import pytest
import json
from tools.test_params_tool import (
    TestParamConfig,
    TestParams,
    generate_test_params,
    generate_ui_test_params,
    generate_code_quality_test_params,
    generate_custom_test_params
)


class TestTestParamConfig:
    """Test TestParamConfig model validation."""
    
    def test_valid_config_creation(self):
        """Test creating valid configuration."""
        config = TestParamConfig(
            test_type="ui_feedback",
            param_categories=["ui_feedback", "response_evaluation"],
            num_variations=3,
            complexity_level="medium"
        )
        
        assert config.test_type == "ui_feedback"
        assert config.param_categories == ["ui_feedback", "response_evaluation"]
        assert config.num_variations == 3
        assert config.complexity_level == "medium"
        assert config.domain_context is None
    
    def test_config_with_domain_context(self):
        """Test configuration with domain context."""
        config = TestParamConfig(
            test_type="code_quality",
            param_categories=["code_quality"],
            domain_context="healthcare"
        )
        
        assert config.domain_context == "healthcare"
        assert config.num_variations == 5  # default
    
    def test_config_defaults(self):
        """Test default values in configuration."""
        config = TestParamConfig(
            test_type="test",
            param_categories=["category"]
        )
        
        assert config.num_variations == 5
        assert config.complexity_level == "medium"
        assert config.domain_context is None


class TestTestParams:
    """Test TestParams model validation."""
    
    def test_valid_test_params(self):
        """Test creating valid test parameters."""
        params = TestParams(
            test_id="test_001",
            parameters={"param1": "value1", "param2": "value2"},
            expected_behavior="Should behave correctly",
            evaluation_criteria=["criterion1", "criterion2"],
            complexity_score=0.7
        )
        
        assert params.test_id == "test_001"
        assert params.parameters == {"param1": "value1", "param2": "value2"}
        assert params.expected_behavior == "Should behave correctly"
        assert params.evaluation_criteria == ["criterion1", "criterion2"]
        assert params.complexity_score == 0.7
    
    def test_complexity_score_validation(self):
        """Test complexity score bounds validation."""
        # Valid scores
        params1 = TestParams(
            test_id="test_001",
            parameters={},
            expected_behavior="test",
            evaluation_criteria=[],
            complexity_score=0.0
        )
        assert params1.complexity_score == 0.0
        
        params2 = TestParams(
            test_id="test_002", 
            parameters={},
            expected_behavior="test",
            evaluation_criteria=[],
            complexity_score=1.0
        )
        assert params2.complexity_score == 1.0


class TestGenerateTestParams:
    """Test core test parameter generation functionality."""
    
    def test_basic_parameter_generation(self):
        """Test basic parameter generation."""
        config = TestParamConfig(
            test_type="ui_feedback",
            param_categories=["ui_feedback"],
            num_variations=3
        )
        
        params = generate_test_params(config)
        
        assert len(params) == 3
        assert all(isinstance(p, TestParams) for p in params)
        assert all(p.test_id.startswith("ui_feedback_") for p in params)
        assert all(len(p.evaluation_criteria) > 0 for p in params)
    
    def test_parameter_uniqueness(self):
        """Test that generated parameters are unique."""
        config = TestParamConfig(
            test_type="code_quality",
            param_categories=["code_quality"],
            num_variations=5
        )
        
        params = generate_test_params(config)
        
        # Check that test IDs are unique
        test_ids = [p.test_id for p in params]
        assert len(test_ids) == len(set(test_ids))
        
        # Parameters should have some variation
        param_sets = [frozenset(p.parameters.items()) for p in params]
        assert len(set(param_sets)) > 1  # Should have different parameter combinations
    
    def test_complexity_levels(self):
        """Test different complexity levels generate appropriate parameters."""
        base_config = {
            "test_type": "response_evaluation",
            "param_categories": ["response_evaluation"],
            "num_variations": 3
        }
        
        # Test simple complexity
        simple_config = TestParamConfig(complexity_level="simple", **base_config)
        simple_params = generate_test_params(simple_config)
        
        # Test complex complexity
        complex_config = TestParamConfig(complexity_level="complex", **base_config)
        complex_params = generate_test_params(complex_config)
        
        # Complex should have higher complexity scores on average
        simple_avg = sum(p.complexity_score for p in simple_params) / len(simple_params)
        complex_avg = sum(p.complexity_score for p in complex_params) / len(complex_params)
        
        assert complex_avg > simple_avg
        
        # Complex parameters might have additional fields
        complex_param_keys = set()
        for p in complex_params:
            complex_param_keys.update(p.parameters.keys())
        
        assert len(complex_param_keys) >= 4  # Should have multiple parameter types
    
    def test_domain_context_integration(self):
        """Test domain context affects parameter generation."""
        config = TestParamConfig(
            test_type="code_quality",
            param_categories=["code_quality"],
            num_variations=3,
            domain_context="healthcare"
        )
        
        params = generate_test_params(config)
        
        # Should include domain context in parameters
        assert all("domain_context" in p.parameters for p in params)
        assert all(p.parameters["domain_context"] == "healthcare" for p in params)
        
        # Should include domain-specific requirements
        assert all("domain_specific_requirements" in p.parameters for p in params)
        
        # Healthcare should include specific requirements
        for p in params:
            requirements = p.parameters["domain_specific_requirements"]
            assert any("HIPAA" in req or "medical" in req or "patient" in req for req in requirements)
    
    def test_multiple_categories(self):
        """Test parameter generation with multiple categories."""
        config = TestParamConfig(
            test_type="mixed_test",
            param_categories=["ui_feedback", "code_quality"],
            num_variations=4
        )
        
        params = generate_test_params(config)
        
        assert len(params) == 4
        
        # Should include parameters from both categories
        all_param_keys = set()
        for p in params:
            all_param_keys.update(p.parameters.keys())
        
        # Should have UI feedback parameters
        ui_keys = {"interface_style", "user_expertise", "context_urgency", "feedback_tone"}
        code_keys = {"code_style", "performance_priority", "error_handling", "documentation_level"}
        
        assert len(ui_keys.intersection(all_param_keys)) > 0
        assert len(code_keys.intersection(all_param_keys)) > 0
    
    def test_empty_categories_fallback(self):
        """Test fallback behavior with empty or invalid categories."""
        config = TestParamConfig(
            test_type="test",
            param_categories=["nonexistent_category"],
            num_variations=2
        )
        
        params = generate_test_params(config)
        
        # Should still generate parameters using fallback
        assert len(params) == 2
        assert all(len(p.parameters) > 0 for p in params)


class TestConvenienceFunctions:
    """Test convenience functions for specific test types."""
    
    def test_generate_ui_test_params(self):
        """Test UI test parameter generation."""
        result = generate_ui_test_params(num_variations=3, complexity="simple")
        
        # Should return JSON string
        assert isinstance(result, str)
        
        # Should parse as valid JSON
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 3
        
        # Each item should have required fields
        for item in parsed:
            assert "test_id" in item
            assert "parameters" in item
            assert "expected_behavior" in item
            assert "evaluation_criteria" in item
            assert item["test_id"].startswith("ui_feedback_")
    
    def test_generate_code_quality_test_params(self):
        """Test code quality test parameter generation."""
        result = generate_code_quality_test_params(num_variations=2, complexity="complex")
        
        parsed = json.loads(result)
        assert len(parsed) == 2
        
        for item in parsed:
            assert item["test_id"].startswith("code_quality_")
            assert "code_style" in item["parameters"] or "performance_priority" in item["parameters"]
    
    def test_generate_custom_test_params(self):
        """Test custom test parameter generation."""
        result = generate_custom_test_params(
            test_type="custom_test",
            categories=["ui_feedback", "response_evaluation"],
            num_variations=3,
            complexity="medium",
            domain="finance"
        )
        
        parsed = json.loads(result)
        assert len(parsed) == 3
        
        for item in parsed:
            assert item["test_id"].startswith("custom_test_")
            assert item["parameters"]["domain_context"] == "finance"
            
            # Should include domain-specific requirements for finance
            requirements = item["parameters"]["domain_specific_requirements"]
            assert any("regulatory" in req or "security" in req for req in requirements)


class TestEdgeCases:
    """Test edge cases and error conditions."""
    
    def test_zero_variations(self):
        """Test handling of zero variations."""
        config = TestParamConfig(
            test_type="test",
            param_categories=["ui_feedback"],
            num_variations=0
        )
        
        params = generate_test_params(config)
        assert len(params) == 0
    
    def test_large_variation_count(self):
        """Test handling of large variation counts."""
        config = TestParamConfig(
            test_type="test",
            param_categories=["ui_feedback"],
            num_variations=50
        )
        
        params = generate_test_params(config)
        assert len(params) == 50
        
        # Should still have unique test IDs
        test_ids = [p.test_id for p in params]
        assert len(set(test_ids)) == 50
    
    def test_invalid_complexity_level(self):
        """Test handling of invalid complexity levels."""
        config = TestParamConfig(
            test_type="test",
            param_categories=["ui_feedback"],
            complexity_level="invalid"
        )
        
        # Should use fallback complexity handling
        params = generate_test_params(config)
        assert len(params) > 0
        
        # Should have some complexity score (even if fallback)
        assert all(hasattr(p, 'complexity_score') for p in params)
    
    def test_unknown_domain_context(self):
        """Test handling of unknown domain contexts."""
        config = TestParamConfig(
            test_type="test",
            param_categories=["code_quality"],
            domain_context="unknown_domain"
        )
        
        params = generate_test_params(config)
        
        # Should still generate parameters with domain context
        assert all("domain_context" in p.parameters for p in params)
        assert all(p.parameters["domain_context"] == "unknown_domain" for p in params)
        
        # Should have generic domain requirements as fallback
        for p in params:
            requirements = p.parameters["domain_specific_requirements"]
            assert "domain expertise" in requirements


class TestIntegration:
    """Integration tests for the complete parameter generation workflow."""
    
    def test_end_to_end_ui_testing_workflow(self):
        """Test complete UI testing parameter generation workflow."""
        # Generate parameters for UI testing
        config = TestParamConfig(
            test_type="ui_feedback",
            param_categories=["ui_feedback"],
            num_variations=5,
            complexity_level="medium",
            domain_context="education"
        )
        
        params = generate_test_params(config)
        
        # Validate complete workflow
        assert len(params) == 5
        
        for param in params:
            # Should have proper test structure
            assert param.test_id.startswith("ui_feedback_")
            assert len(param.parameters) >= 3
            assert len(param.evaluation_criteria) >= 3
            assert 0.0 <= param.complexity_score <= 1.0
            
            # Should include domain context
            assert param.parameters["domain_context"] == "education"
            
            # Should have UI-specific parameters
            ui_params = {"interface_style", "user_expertise", "context_urgency", "feedback_tone"}
            assert len(ui_params.intersection(param.parameters.keys())) >= 2
            
            # Expected behavior should be relevant
            assert "feedback" in param.expected_behavior.lower()
            
            # Evaluation criteria should be appropriate
            criteria_text = " ".join(param.evaluation_criteria).lower()
            assert any(word in criteria_text for word in ["usability", "feedback", "appropriate", "tone"])
    
    def test_batch_parameter_generation_consistency(self):
        """Test consistency across multiple parameter generation calls."""
        config = TestParamConfig(
            test_type="code_quality",
            param_categories=["code_quality"],
            num_variations=3,
            complexity_level="simple"
        )
        
        # Generate multiple batches
        batch1 = generate_test_params(config)
        batch2 = generate_test_params(config)
        
        # Should generate consistent structure
        assert len(batch1) == len(batch2) == 3
        
        # Should have same parameter categories available
        batch1_keys = set()
        batch2_keys = set()
        
        for params in batch1:
            batch1_keys.update(params.parameters.keys())
        for params in batch2:
            batch2_keys.update(params.parameters.keys())
        
        # Should overlap significantly but allow for randomness
        overlap = len(batch1_keys.intersection(batch2_keys))
        assert overlap >= len(batch1_keys) * 0.7  # At least 70% overlap