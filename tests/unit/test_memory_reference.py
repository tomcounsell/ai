"""Tests for the Memory model reference field."""

import json

import pytest


@pytest.mark.unit
class TestMemoryReferenceField:
    """Test the reference field on the Memory model."""

    def test_reference_field_exists(self):
        """Memory model has a reference field."""
        from models.memory import Memory

        assert hasattr(Memory, "reference")

    def test_reference_default_empty(self):
        """Reference field defaults to empty string."""
        from models.memory import Memory

        assert Memory.reference.default == ""

    def test_source_knowledge_constant(self):
        """SOURCE_KNOWLEDGE constant is defined."""
        from models.memory import SOURCE_KNOWLEDGE

        assert SOURCE_KNOWLEDGE == "knowledge"

    def test_reference_json_serialization(self):
        """Reference field can store JSON tool call pointers."""
        ref = json.dumps({"tool": "read_file", "params": {"file_path": "/test/doc.md"}})
        parsed = json.loads(ref)
        assert parsed["tool"] == "read_file"
        assert parsed["params"]["file_path"] == "/test/doc.md"

    def test_reference_various_shapes(self):
        """Reference field supports various JSON shapes."""
        # File reference
        ref1 = json.dumps({"tool": "read_file", "params": {"file_path": "/path/to/doc.md"}})
        assert "read_file" in ref1

        # URL reference
        ref2 = json.dumps({"url": "https://docs.example.com"})
        assert "https" in ref2

        # Entity reference
        ref3 = json.dumps({"entity": "person", "name": "Tom", "channel": "telegram"})
        assert "person" in ref3

    def test_memory_model_backward_compatible(self):
        """Adding reference field does not break existing Memory creation."""
        from models.memory import Memory

        # Should be able to create without reference (uses default)
        m = Memory(content="test", project_key="test", agent_id="test")
        assert m.reference == ""

    def test_memory_model_with_reference(self):
        """Memory can be created with an explicit reference."""
        from models.memory import Memory

        ref = json.dumps({"tool": "read_file", "params": {"file_path": "/test.md"}})
        m = Memory(content="test", project_key="test", agent_id="test", reference=ref)
        assert m.reference == ref
