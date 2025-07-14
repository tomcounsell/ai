"""Quick test that honesty protocol is properly configured."""

import pytest
from agents.valor.agent import valor_agent
from agents.valor.agent import load_persona


class TestHonestyConfiguration:
    """Test that honesty protocol is properly configured."""
    
    def test_honesty_protocol_in_system_prompt(self):
        """Verify honesty protocol is in the condensed system prompt."""
        # Get the actual system prompt string by reading the agent file
        with open("../agents/valor/agent.py", "r") as f:
            agent_content = f.read()
        
        # Check that condensed honesty protocol is in the system prompt
        assert "HONESTY FIRST: Never fabricate completion claims" in agent_content
        assert "If you can't do something, say so" in agent_content
    
    def test_persona_removed_dangerous_directives(self):
        """Verify dangerous directives were removed from persona."""
        persona_content = load_persona()
        
        # Should NOT contain dangerous directives
        dangerous_phrases = [
            "Do Work First, Respond After",
            "Execute the task immediately", 
            "Don't make promises (\"I'll fix...\") - do the work then report completion"
        ]
        
        for phrase in dangerous_phrases:
            assert phrase not in persona_content, f"Found dangerous directive: {phrase}"
    
    def test_persona_has_honesty_requirements(self):
        """Verify persona contains condensed honesty requirements."""
        persona_content = load_persona()
        
        # Should contain condensed honesty requirements
        assert "Be honest about capabilities" in persona_content
        assert "Only claim completion when tools actually succeed" in persona_content
            
    def test_youtube_transcription_delegation_documented(self):
        """Verify YouTube transcription is documented as delegation."""
        persona_content = load_persona()
        
        # Should mention delegation for YouTube transcription in condensed format
        assert "Delegate transcription to Claude Code MCP" in persona_content
        assert "delegate_coding_task" in persona_content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])