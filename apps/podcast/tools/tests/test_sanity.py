"""Sanity tests to verify the test environment is working."""


def test_basic_assertion():
    """Verify pytest is working with a basic assertion."""
    assert 1 + 1 == 2


def test_imports():
    """Verify all required dependencies can be imported."""
    import anthropic
    import openai
    import PIL
    import pytest
    import whisper

    assert anthropic is not None
    assert openai is not None
    assert whisper is not None
    assert PIL is not None
    assert pytest is not None
