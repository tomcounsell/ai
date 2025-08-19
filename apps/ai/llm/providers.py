"""LLM provider configuration for PydanticAI."""

import os
from typing import Optional

from pydantic_ai.models.openai import OpenAIModel


def get_openai_model(
    model_name: str = "gpt-4.1", api_key: str | None = None
) -> OpenAIModel:
    """
    Get an OpenAI model instance.

    Args:
        model_name: The OpenAI model to use (e.g., 'gpt-4.1', 'gpt-4o', 'gpt-4o-mini')
        api_key: Optional API key. If not provided, uses OPENAI_API_KEY env var

    Returns:
        Configured OpenAI model instance
    """
    # Set API key in environment if provided
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key
    elif not os.getenv("OPENAI_API_KEY"):
        raise ValueError(
            "OpenAI API key not found. Please set OPENAI_API_KEY environment variable "
            "or pass api_key parameter."
        )

    return OpenAIModel(model_name)


def get_default_model() -> OpenAIModel:
    """Get the default LLM model for the application."""
    return get_openai_model("gpt-4.1")


def get_gpt_4_1_model(api_key: str | None = None) -> OpenAIModel:
    """Get GPT-4.1 model - best for tool use and agentic workflows."""
    return get_openai_model("gpt-4.1", api_key)


def get_gpt_4o_model(api_key: str | None = None) -> OpenAIModel:
    """Get GPT-4o model - balanced performance and cost."""
    return get_openai_model("gpt-4o", api_key)


def get_gpt_4o_mini_model(api_key: str | None = None) -> OpenAIModel:
    """Get GPT-4o-mini model - cost-efficient for simple tasks."""
    return get_openai_model("gpt-4o-mini", api_key)


# Create a default model instance for use throughout the app
default_model = None


def initialize_default_model():
    """Initialize the default model. Call this after Django settings are loaded."""
    global default_model
    try:
        default_model = get_default_model()
    except ValueError as e:
        print(f"Warning: Could not initialize default model: {e}")
