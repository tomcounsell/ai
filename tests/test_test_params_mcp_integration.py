"""Tests for test_params_tool MCP integration."""

import json
from unittest.mock import patch

from mcp_servers.development_tools import (
    generate_test_parameters,
    generate_ui_testing_params,
    generate_code_testing_params
)


class TestMCPIntegration:
    """Test MCP wrapper functions for test parameter generation."""
    
    def test_generate_test_parameters_success(self):
        """Test successful test parameter generation through MCP."""
        result = generate_test_parameters(
            test_type="ui_feedback",
            param_categories=["ui_feedback"],
            num_variations=2,
            complexity_level="simple"
        )
        
        # Should return JSON string
        assert isinstance(result, str)
        
        # Should parse as valid JSON
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 2
        
        # Each item should have required structure
        for item in parsed:
            assert "test_id" in item
            assert "parameters" in item
            assert "expected_behavior" in item
            assert "evaluation_criteria" in item
            assert item["test_id"].startswith("ui_feedback_")
    
    def test_generate_test_parameters_with_domain(self):
        """Test parameter generation with domain context through MCP."""
        result = generate_test_parameters(
            test_type="code_quality",
            param_categories=["code_quality"],
            num_variations=1,
            domain_context="healthcare"
        )
        
        parsed = json.loads(result)
        assert len(parsed) == 1
        
        item = parsed[0]
        assert item["parameters"]["domain_context"] == "healthcare"
        assert "domain_specific_requirements" in item["parameters"]
        
        # Healthcare should have specific requirements
        requirements = item["parameters"]["domain_specific_requirements"]
        assert any("HIPAA" in req or "medical" in req or "patient" in req for req in requirements)
    
    def test_generate_test_parameters_error_handling(self):
        """Test MCP error handling for invalid parameters."""
        # Test with invalid parameter type (should still work due to fallback)
        result = generate_test_parameters(
            test_type="invalid_test",
            param_categories=["nonexistent"],
            num_variations=1
        )
        
        # Should handle gracefully and return parameters (uses fallback)
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert len(parsed) == 1
    
    def test_generate_ui_testing_params_mcp(self):
        """Test UI testing parameter generation through MCP."""
        result = generate_ui_testing_params(num_variations=2, complexity="medium")
        
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert len(parsed) == 2
        
        for item in parsed:
            assert item["test_id"].startswith("ui_feedback_")
            # Should have UI-specific parameters
            ui_params = {"interface_style", "user_expertise", "context_urgency", "feedback_tone"}
            assert len(ui_params.intersection(item["parameters"].keys())) > 0
    
    def test_generate_code_testing_params_mcp(self):
        """Test code testing parameter generation through MCP."""
        result = generate_code_testing_params(num_variations=3, complexity="complex")
        
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert len(parsed) == 3
        
        for item in parsed:
            assert item["test_id"].startswith("code_quality_")
            # Should have code quality parameters
            code_params = {"code_style", "performance_priority", "error_handling", "documentation_level"}
            assert len(code_params.intersection(item["parameters"].keys())) > 0
    
    @patch('mcp_servers.development_tools.generate_test_params')
    def test_mcp_error_handling_with_exception(self, mock_generate):
        """Test MCP error handling when underlying function raises exception."""
        # Mock an exception
        mock_generate.side_effect = ValueError("Test error")
        
        result = generate_test_parameters(
            test_type="test",
            param_categories=["test"],
            num_variations=1
        )
        
        # Should return error message
        assert "❌ Error generating test parameters" in result
        assert "Test error" in result
    
    def test_default_parameter_handling(self):
        """Test MCP functions handle default parameters correctly."""
        # Test with minimal parameters
        result = generate_test_parameters(
            test_type="response_evaluation",
            param_categories=["response_evaluation"]
            # Using defaults: num_variations=5, complexity_level="medium", domain_context=None
        )
        
        parsed = json.loads(result)
        assert len(parsed) == 5  # Default num_variations
        
        # Should have medium complexity (default)
        complexity_scores = [item["complexity_score"] for item in parsed]
        avg_complexity = sum(complexity_scores) / len(complexity_scores)
        assert 0.4 < avg_complexity < 0.8  # Should be around medium range
    
    def test_parameter_validation_through_mcp(self):
        """Test that parameter validation works through MCP layer."""
        # Test zero variations
        result = generate_test_parameters(
            test_type="test",
            param_categories=["ui_feedback"],
            num_variations=0
        )
        
        parsed = json.loads(result)
        assert len(parsed) == 0
        
        # Test large variations
        result = generate_test_parameters(
            test_type="test",
            param_categories=["ui_feedback"],
            num_variations=20
        )
        
        parsed = json.loads(result)
        assert len(parsed) == 20
        
        # All should have unique test IDs
        test_ids = [item["test_id"] for item in parsed]
        assert len(set(test_ids)) == 20


class TestMCPJSONConsistency:
    """Test JSON serialization consistency in MCP layer."""
    
    def test_json_serialization_format(self):
        """Test that MCP functions return properly formatted JSON."""
        result = generate_test_parameters(
            test_type="content_creation",
            param_categories=["content_creation"],
            num_variations=1
        )
        
        # Should be valid JSON
        parsed = json.loads(result)
        
        # Re-serialize to check consistency
        re_serialized = json.dumps(parsed, indent=2)
        re_parsed = json.loads(re_serialized)
        
        assert parsed == re_parsed
    
    def test_all_required_fields_present(self):
        """Test that all required fields are present in MCP output."""
        result = generate_test_parameters(
            test_type="ui_feedback",
            param_categories=["ui_feedback"],
            num_variations=1
        )
        
        parsed = json.loads(result)
        item = parsed[0]
        
        # Check all required TestParams fields
        required_fields = ["test_id", "parameters", "expected_behavior", "evaluation_criteria", "complexity_score"]
        for field in required_fields:
            assert field in item, f"Missing required field: {field}"
            assert item[field] is not None, f"Field {field} is None"
        
        # Check field types
        assert isinstance(item["test_id"], str)
        assert isinstance(item["parameters"], dict)
        assert isinstance(item["expected_behavior"], str)
        assert isinstance(item["evaluation_criteria"], list)
        assert isinstance(item["complexity_score"], (int, float))