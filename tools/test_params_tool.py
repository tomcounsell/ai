"""Test parameter generation tool for AI subjective testing."""

import json
import random
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class TestParamConfig(BaseModel):
    """Configuration for test parameter generation."""
    test_type: str = Field(..., description="Type of test (e.g., 'ui_feedback', 'code_quality', 'response_evaluation')")
    param_categories: List[str] = Field(..., description="Categories of parameters to generate")
    num_variations: int = Field(default=5, description="Number of parameter variations to generate")
    complexity_level: str = Field(default="medium", description="Complexity level: 'simple', 'medium', 'complex'")
    domain_context: Optional[str] = Field(default=None, description="Domain-specific context for parameters")


class TestParams(BaseModel):
    """Generated test parameters."""
    test_id: str
    parameters: Dict[str, Any]
    expected_behavior: str
    evaluation_criteria: List[str]
    complexity_score: float


def generate_test_params(config: TestParamConfig) -> List[TestParams]:
    """
    Generate test parameters for subjective AI testing.
    
    Creates diverse parameter sets to test AI responses across different scenarios,
    useful for evaluating model performance, bias, and consistency.
    
    Examples:
        >>> # Generate UI feedback test parameters
        >>> config = TestParamConfig(
        ...     test_type="ui_feedback",
        ...     param_categories=["ui_feedback"],
        ...     num_variations=3,
        ...     complexity_level="medium"
        ... )
        >>> params = generate_test_params(config)
        >>> len(params)
        3
        
        >>> # Generate code quality tests with domain context
        >>> config = TestParamConfig(
        ...     test_type="code_quality",
        ...     param_categories=["code_quality"],
        ...     num_variations=5,
        ...     complexity_level="complex",
        ...     domain_context="healthcare"
        ... )
        >>> params = generate_test_params(config)
        >>> params[0].parameters["domain_context"]
        'healthcare'
        
        >>> # Generate mixed test parameters
        >>> config = TestParamConfig(
        ...     test_type="comprehensive_test",
        ...     param_categories=["ui_feedback", "code_quality", "response_evaluation"],
        ...     num_variations=10
        ... )
        >>> params = generate_test_params(config)
        >>> len(set(p.test_id for p in params))  # All unique IDs
        10
    """
    
    # Base parameter templates by category
    param_templates = {
        "ui_feedback": {
            "interface_style": ["minimalist", "detailed", "modern", "classic", "mobile-first"],
            "user_expertise": ["beginner", "intermediate", "expert", "domain_expert"],
            "context_urgency": ["low", "medium", "high", "critical"],
            "feedback_tone": ["encouraging", "neutral", "critical", "constructive"]
        },
        "code_quality": {
            "code_style": ["functional", "object_oriented", "procedural", "declarative"],
            "performance_priority": ["readability", "speed", "memory", "maintainability"],
            "error_handling": ["minimal", "defensive", "comprehensive", "fail_fast"],
            "documentation_level": ["none", "basic", "detailed", "comprehensive"]
        },
        "response_evaluation": {
            "response_length": ["brief", "moderate", "detailed", "comprehensive"],
            "technical_depth": ["surface", "intermediate", "deep", "expert"],
            "audience_level": ["general", "technical", "academic", "professional"],
            "format_preference": ["structured", "conversational", "bullet_points", "narrative"]
        },
        "content_creation": {
            "creativity_level": ["conservative", "moderate", "creative", "highly_creative"],
            "formality": ["casual", "professional", "academic", "technical"],
            "perspective": ["first_person", "third_person", "objective", "subjective"],
            "content_length": ["concise", "standard", "extended", "comprehensive"]
        }
    }
    
    # Complexity multipliers
    complexity_multipliers = {
        "simple": 0.3,
        "medium": 0.6,
        "complex": 0.9
    }
    
    generated_params = []
    base_templates = {}
    
    # Select relevant templates based on config
    for category in config.param_categories:
        if category in param_templates:
            base_templates.update(param_templates[category])
    
    # If no matching categories, use all templates
    if not base_templates:
        for templates in param_templates.values():
            base_templates.update(templates)
    
    for i in range(config.num_variations):
        # Generate random parameter combination
        parameters = {}
        for param_name, options in base_templates.items():
            parameters[param_name] = random.choice(options)
        
        # Add domain-specific parameters if context provided
        if config.domain_context:
            parameters["domain_context"] = config.domain_context
            parameters["domain_specific_requirements"] = _generate_domain_requirements(config.domain_context)
        
        # Calculate complexity score
        complexity_score = complexity_multipliers.get(config.complexity_level, 0.6)  # default to medium
        
        # Add random variation factors
        if config.complexity_level == "complex":
            parameters["edge_case_handling"] = random.choice(["ignore", "basic", "comprehensive"])
            parameters["constraint_conflicts"] = random.choice(["none", "minor", "significant"])
            complexity_score += random.uniform(0.1, 0.3)
        
        # Generate expected behavior description
        expected_behavior = _generate_expected_behavior(config.test_type, parameters)
        
        # Generate evaluation criteria
        evaluation_criteria = _generate_evaluation_criteria(config.test_type, parameters)
        
        test_params = TestParams(
            test_id=f"{config.test_type}_{i+1:03d}",
            parameters=parameters,
            expected_behavior=expected_behavior,
            evaluation_criteria=evaluation_criteria,
            complexity_score=min(complexity_score, 1.0)
        )
        
        generated_params.append(test_params)
    
    return generated_params


def _generate_domain_requirements(domain: str) -> List[str]:
    """Generate domain-specific requirements."""
    domain_requirements = {
        "healthcare": ["HIPAA compliance", "medical accuracy", "patient safety"],
        "finance": ["regulatory compliance", "data security", "risk assessment"],
        "education": ["age appropriateness", "learning objectives", "accessibility"],
        "legal": ["accuracy of legal concepts", "jurisdiction awareness", "ethics"],
        "technology": ["technical accuracy", "best practices", "security considerations"]
    }
    
    return domain_requirements.get(domain.lower(), ["domain expertise", "accuracy", "relevance"])


def _generate_expected_behavior(test_type: str, parameters: Dict[str, Any]) -> str:
    """Generate expected behavior description based on test type and parameters."""
    behavior_templates = {
        "ui_feedback": f"Should provide {parameters.get('feedback_tone', 'constructive')} feedback appropriate for {parameters.get('user_expertise', 'intermediate')} users",
        "code_quality": f"Should prioritize {parameters.get('performance_priority', 'maintainability')} while following {parameters.get('code_style', 'object_oriented')} principles",
        "response_evaluation": f"Should deliver {parameters.get('response_length', 'moderate')} response with {parameters.get('technical_depth', 'intermediate')} technical depth",
        "content_creation": f"Should create {parameters.get('creativity_level', 'moderate')} content in {parameters.get('formality', 'professional')} tone"
    }
    
    return behavior_templates.get(test_type, f"Should respond appropriately to the given {test_type} scenario")


def _generate_evaluation_criteria(test_type: str, parameters: Dict[str, Any]) -> List[str]:
    """Generate evaluation criteria based on test type and parameters."""
    base_criteria = ["accuracy", "relevance", "clarity"]
    
    type_specific_criteria = {
        "ui_feedback": ["usability insight", "actionable suggestions", "tone appropriateness"],
        "code_quality": ["code correctness", "best practices adherence", "maintainability"],
        "response_evaluation": ["completeness", "technical accuracy", "audience appropriateness"],
        "content_creation": ["creativity", "coherence", "style consistency"]
    }
    
    criteria = base_criteria + type_specific_criteria.get(test_type, ["domain knowledge"])
    
    # Add parameter-specific criteria
    if "user_expertise" in parameters:
        criteria.append(f"appropriate for {parameters['user_expertise']} level")
    
    if "complexity_score" in parameters and parameters.get("complexity_score", 0) > 0.7:
        criteria.append("handles complexity appropriately")
    
    return criteria


# Example usage functions for PydanticAI integration
def generate_ui_test_params(num_variations: int = 5, complexity: str = "medium") -> str:
    """Generate test parameters for UI feedback evaluation.
    
    Example:
        >>> params_json = generate_ui_test_params(num_variations=3, complexity="simple")
        >>> import json
        >>> params = json.loads(params_json)
        >>> len(params)
        3
        >>> params[0]["test_id"].startswith("ui_feedback_")
        True
    """
    config = TestParamConfig(
        test_type="ui_feedback",
        param_categories=["ui_feedback"],
        num_variations=num_variations,
        complexity_level=complexity
    )
    
    params = generate_test_params(config)
    return json.dumps([p.model_dump() for p in params], indent=2)


def generate_code_quality_test_params(num_variations: int = 5, complexity: str = "medium") -> str:
    """Generate test parameters for code quality evaluation."""
    config = TestParamConfig(
        test_type="code_quality",
        param_categories=["code_quality"],
        num_variations=num_variations,
        complexity_level=complexity
    )
    
    params = generate_test_params(config)
    return json.dumps([p.model_dump() for p in params], indent=2)


def generate_custom_test_params(
    test_type: str,
    categories: List[str],
    num_variations: int = 5,
    complexity: str = "medium",
    domain: Optional[str] = None
) -> str:
    """Generate custom test parameters based on specified configuration."""
    config = TestParamConfig(
        test_type=test_type,
        param_categories=categories,
        num_variations=num_variations,
        complexity_level=complexity,
        domain_context=domain
    )
    
    params = generate_test_params(config)
    return json.dumps([p.model_dump() for p in params], indent=2)