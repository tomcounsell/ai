"""LLM provider configuration for PydanticAI."""

import os

from pydantic_ai.models.openai import OpenAIModel


def get_openai_model(
    model_name: str = "gpt-5.2", api_key: str | None = None
) -> OpenAIModel:
    """
    Get an OpenAI model instance.

    Args:
        model_name: The OpenAI model to use (e.g., 'gpt-5.2')
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
    return get_openai_model("gpt-5.2")


# Create a default model instance for use throughout the app
default_model = None


def initialize_default_model():
    """Initialize the default model. Call this after Django settings are loaded."""
    global default_model
    try:
        default_model = get_default_model()
    except ValueError as e:
        print(f"Warning: Could not initialize default model: {e}")
