"""
Test Params Tool

Generate test parameters for subjective AI testing with diverse variations.
"""

import random
from typing import Literal


class TestParamsError(Exception):
    """Test parameter generation failed."""

    def __init__(self, message: str, category: str = "execution"):
        self.message = message
        self.category = category
        super().__init__(message)


# Parameter generation templates
PARAM_TEMPLATES = {
    "strings": {
        "empty": ["", " ", "  "],
        "short": ["a", "ab", "abc"],
        "long": ["x" * 100, "y" * 500, "z" * 1000],
        "special": ["@#$%", "<script>", "'; DROP TABLE;", "null", "undefined"],
        "unicode": ["你好", "مرحبا", "🎉🎊", "café", "naïve"],
        "numbers_as_strings": ["123", "-456", "3.14", "1e10"],
    },
    "numbers": {
        "zero": [0, 0.0, -0],
        "small": [1, -1, 0.1, -0.1],
        "large": [10**6, 10**9, 10**15],
        "negative": [-1, -100, -(10**6)],
        "float": [0.5, 3.14159, 2.71828],
        "edge": [float("inf"), float("-inf")],
    },
    "arrays": {
        "empty": [[]],
        "single": [[1], ["a"], [None]],
        "mixed": [[1, "a", None, True]],
        "nested": [[[1, 2], [3, 4]], [{"a": 1}]],
        "large": [list(range(100)), list(range(1000))],
    },
    "objects": {
        "empty": [{}],
        "simple": [{"key": "value"}, {"a": 1, "b": 2}],
        "nested": [{"a": {"b": {"c": 1}}}],
        "with_arrays": [{"items": [1, 2, 3]}],
    },
    "booleans": {
        "values": [True, False],
        "truthy": [1, "true", "yes", "1"],
        "falsy": [0, "", "false", "no", "0", None],
    },
    "dates": {
        "valid": ["2024-01-15", "2024-12-31", "2000-01-01"],
        "edge": ["1970-01-01", "2099-12-31", "0001-01-01"],
        "invalid": ["2024-13-01", "2024-02-30", "not-a-date"],
    },
    "emails": {
        "valid": ["test@example.com", "user.name@domain.co.uk"],
        "invalid": ["not-an-email", "@missing.com", "spaces here@test.com"],
    },
    "urls": {
        "valid": ["https://example.com", "http://localhost:8080/path"],
        "invalid": ["not-a-url", "ftp://", "://missing-scheme.com"],
    },
}

# Test type templates
TEST_TYPES = {
    "api": {
        "categories": ["input_validation", "edge_cases", "error_handling"],
        "behaviors": [
            "returns correct response",
            "handles errors gracefully",
            "validates input",
        ],
    },
    "ui": {
        "categories": ["user_input", "display", "interaction"],
        "behaviors": [
            "renders correctly",
            "responds to user action",
            "shows appropriate feedback",
        ],
    },
    "performance": {
        "categories": ["load", "stress", "endurance"],
        "behaviors": [
            "completes within threshold",
            "maintains stability",
            "scales appropriately",
        ],
    },
    "security": {
        "categories": ["injection", "authentication", "authorization"],
        "behaviors": [
            "rejects malicious input",
            "enforces access control",
            "protects sensitive data",
        ],
    },
    "integration": {
        "categories": ["data_flow", "service_communication", "error_propagation"],
        "behaviors": [
            "maintains data integrity",
            "handles service failures",
            "propagates errors correctly",
        ],
    },
}


def generate_params(
    test_type: str,
    param_categories: list[str],
    num_variations: int = 5,
    complexity_level: Literal["simple", "medium", "complex"] = "medium",
    domain_context: str | None = None,
) -> dict:
    """
    Generate test parameters for a specific test type.

    Args:
        test_type: Type of test (api, ui, performance, security, integration)
        param_categories: Categories of parameters to generate
        num_variations: Number of variations per category (default: 5)
        complexity_level: Complexity of generated parameters
        domain_context: Domain-specific context for generation

    Returns:
        dict with:
            - test_params: List of generated parameter sets
            - evaluation_criteria: Criteria for each test
            - expected_behaviors: Expected outcomes
    """
    if not test_type:
        return {"error": "Test type is required"}

    if not param_categories:
        return {"error": "Parameter categories are required"}

    # Get test type template
    test_template = TEST_TYPES.get(test_type.lower(), TEST_TYPES["api"])

    # Generate parameter sets
    test_params = []

    for category in param_categories:
        category_lower = category.lower()

        # Map category to parameter templates
        if category_lower in ("edge_cases", "boundary"):
            param_types = ["numbers", "strings", "arrays"]
        elif category_lower in ("input_validation", "user_input"):
            param_types = ["strings", "emails", "urls", "dates"]
        elif category_lower in ("error_handling",):
            param_types = ["strings", "numbers", "objects"]
        elif category_lower in ("injection", "security"):
            param_types = ["strings"]
        else:
            param_types = list(PARAM_TEMPLATES.keys())

        for param_type in param_types:
            template = PARAM_TEMPLATES.get(param_type, {})

            # Select subsets based on complexity
            if complexity_level == "simple":
                subsets = list(template.keys())[:2]
            elif complexity_level == "complex":
                subsets = list(template.keys())
            else:
                subsets = list(template.keys())[:4]

            for subset in subsets:
                values = template.get(subset, [])

                # Sample values up to num_variations
                sampled = (
                    values[:num_variations]
                    if len(values) <= num_variations
                    else random.sample(values, num_variations)
                )

                for value in sampled:
                    test_params.append(
                        {
                            "category": category,
                            "param_type": param_type,
                            "subset": subset,
                            "value": value,
                            "complexity": complexity_level,
                        }
                    )

    # Generate evaluation criteria
    evaluation_criteria = []
    for category in param_categories:
        criteria = {
            "category": category,
            "criteria": [
                f"System handles {category} inputs correctly",
                f"No unexpected errors for {category} cases",
                f"Response is appropriate for {category}",
            ],
        }
        evaluation_criteria.append(criteria)

    # Get expected behaviors from template
    expected_behaviors = test_template.get("behaviors", [])

    return {
        "test_type": test_type,
        "param_categories": param_categories,
        "complexity_level": complexity_level,
        "test_params": test_params[: num_variations * len(param_categories) * 5],
        "evaluation_criteria": evaluation_criteria,
        "expected_behaviors": expected_behaviors,
        "total_params": len(test_params),
    }


def generate_edge_cases(
    param_type: str,
    num_cases: int = 10,
) -> dict:
    """
    Generate edge case parameters for a specific type.

    Args:
        param_type: Type of parameter (strings, numbers, etc.)
        num_cases: Number of cases to generate

    Returns:
        dict with edge cases
    """
    template = PARAM_TEMPLATES.get(param_type.lower())
    if not template:
        return {
            "error": f"Unknown parameter type: {param_type}",
            "available_types": list(PARAM_TEMPLATES.keys()),
        }

    edge_cases = []
    for subset, values in template.items():
        for value in values:
            edge_cases.append(
                {
                    "subset": subset,
                    "value": value,
                    "description": f"{param_type} {subset} case",
                }
            )

    return {
        "param_type": param_type,
        "edge_cases": edge_cases[:num_cases],
        "total_available": len(edge_cases),
    }


def get_param_types() -> dict:
    """Get available parameter types and their subsets."""
    return {
        "param_types": {
            name: list(template.keys()) for name, template in PARAM_TEMPLATES.items()
        },
        "test_types": list(TEST_TYPES.keys()),
    }


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 3:
        print("Usage: python -m tools.test_params 'test_type' 'category1,category2'")
        sys.exit(1)

    test_type = sys.argv[1]
    categories = sys.argv[2].split(",")

    print(f"Generating params for {test_type} test...")

    result = generate_params(test_type, categories)

    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)
    else:
        print(json.dumps(result, indent=2, default=str))
