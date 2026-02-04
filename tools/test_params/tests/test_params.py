"""
Integration tests for test-params tool.

Run with: pytest tools/test-params/tests/ -v
"""

from tools.test_params import generate_edge_cases, generate_params, get_param_types


class TestParamsInstallation:
    """Verify tool is properly configured."""

    def test_import(self):
        """Tool can be imported."""
        from tools.test_params import generate_params

        assert callable(generate_params)


class TestParamsValidation:
    """Test input validation."""

    def test_empty_test_type(self):
        """Empty test type returns error."""
        result = generate_params("", ["edge_cases"])
        assert "error" in result

    def test_empty_categories(self):
        """Empty categories returns error."""
        result = generate_params("api", [])
        assert "error" in result


class TestGenerateParams:
    """Test parameter generation."""

    def test_basic_generation(self):
        """Basic generation returns results."""
        result = generate_params("api", ["edge_cases"])

        assert "error" not in result
        assert "test_params" in result
        assert len(result["test_params"]) > 0

    def test_multiple_categories(self):
        """Multiple categories generate more params."""
        result = generate_params("api", ["edge_cases", "input_validation"])

        assert "error" not in result
        assert len(result["test_params"]) > 0

    def test_complexity_levels(self):
        """Different complexity levels work."""
        for level in ["simple", "medium", "complex"]:
            result = generate_params("api", ["edge_cases"], complexity_level=level)
            assert "error" not in result

    def test_num_variations(self):
        """Number of variations is respected."""
        result = generate_params("api", ["edge_cases"], num_variations=3)

        assert "error" not in result

    def test_evaluation_criteria(self):
        """Evaluation criteria are generated."""
        result = generate_params("api", ["edge_cases"])

        assert "evaluation_criteria" in result
        assert len(result["evaluation_criteria"]) > 0

    def test_expected_behaviors(self):
        """Expected behaviors are included."""
        result = generate_params("api", ["edge_cases"])

        assert "expected_behaviors" in result
        assert len(result["expected_behaviors"]) > 0


class TestGenerateEdgeCases:
    """Test edge case generation."""

    def test_string_edge_cases(self):
        """String edge cases are generated."""
        result = generate_edge_cases("strings")

        assert "error" not in result
        assert "edge_cases" in result
        assert len(result["edge_cases"]) > 0

    def test_number_edge_cases(self):
        """Number edge cases are generated."""
        result = generate_edge_cases("numbers")

        assert "error" not in result
        assert "edge_cases" in result

    def test_unknown_type(self):
        """Unknown type returns error with suggestions."""
        result = generate_edge_cases("unknown_type")

        assert "error" in result
        assert "available_types" in result


class TestGetParamTypes:
    """Test param type listing."""

    def test_get_types(self):
        """Returns available types."""
        result = get_param_types()

        assert "param_types" in result
        assert "test_types" in result
        assert "strings" in result["param_types"]
        assert "api" in result["test_types"]
