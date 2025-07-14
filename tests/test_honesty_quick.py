"""Quick test that honesty protocol is properly configured."""

import pytest
from agents.valor.agent import valor_agent
from agents.valor.agent import load_persona


class TestHonestyConfiguration:
    """Test that honesty protocol is properly configured."""
    
    def test_honesty_protocol_in_system_prompt(self):
        """Verify honesty protocol is at the top of system prompt."""
        # Get the actual system prompt string by reading the agent file
        with open("../agents/valor/agent.py", "r") as f:
            agent_content = f.read()
        
        # Check that honesty protocol is in the system prompt
        assert "HONESTY PROTOCOL - THIS OVERRIDES ALL OTHER INSTRUCTIONS:" in agent_content
        
        # Should contain key honesty elements in the agent file
        honesty_elements = [
            "verify tools actually executed successfully",
            "admit limitations honestly", 
            "Never fabricate implementation details",
            "Better to say \"I cannot do that\" than to lie"
        ]
        
        for element in honesty_elements:
            assert element in agent_content, f"Missing honesty element: {element}"
    
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
        """Verify persona contains honesty requirements."""
        persona_content = load_persona()
        
        # Should contain honesty requirements
        honesty_requirements = [
            "Validate Capabilities Before Claiming Results",
            "Be truthful about tool availability and limitations",
            "Never fabricate results for work that wasn't performed"
        ]
        
        for requirement in honesty_requirements:
            assert requirement in persona_content, f"Missing honesty requirement: {requirement}"
            
    def test_youtube_transcription_delegation_documented(self):
        """Verify YouTube transcription is documented as delegation."""
        persona_content = load_persona()
        
        # Should mention delegation for YouTube transcription
        delegation_elements = [
            "delegate_coding_task to access Claude Code's MCP social-tools server",
            "DO NOT claim to have transcription capabilities directly",
            "Always delegate transcription work rather than fabricating results"
        ]
        
        for element in delegation_elements:
            assert element in persona_content, f"Missing delegation element: {element}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])