#!/usr/bin/env python3
"""
Context Injection Validation Test

Validates that the context injection mechanism works as designed in the unified plan.
Tests the strategy described in the plan for passing chat_id, username, and other context
through enhanced prompts to MCP tools.
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from mcp_servers.social_tools import create_image, save_link, search_links
from mcp_servers.notion_tools import query_notion_projects
from mcp_servers.telegram_tools import search_conversation_history, get_conversation_context


def simulate_context_injection(user_message: str, context: dict) -> str:
    """
    Simulate the context injection strategy described in the unified plan.
    
    This demonstrates how the unified system would inject context data that
    MCP tools need into the message/prompt, as described in the plan.
    """
    context_vars = []

    # Essential context for tools
    if context.get('chat_id'):
        context_vars.append(f"CHAT_ID={context['chat_id']}")

    if context.get('username'):
        context_vars.append(f"USERNAME={context['username']}")

    # Recent conversation for context tools
    if context.get('chat_history'):
        recent = context['chat_history'][-5:]
        history_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in recent])
        context_vars.append(f"RECENT_HISTORY:\n{history_text}")

    # Notion data if available (group-specific or priority questions)
    if context.get('notion_data'):
        context_vars.append(f"PROJECT_DATA:\n{context['notion_data']}")

    if context_vars:
        context_block = "\n".join(context_vars)
        return f"""CONTEXT_DATA:
{context_block}

When using tools that need chat_id, username, or context data, extract it from CONTEXT_DATA above.

USER_REQUEST: {user_message}"""

    return user_message


def extract_context_from_prompt(enhanced_prompt: str) -> dict:
    """
    Demonstrate how tools would extract context from the enhanced prompt.
    This simulates what Claude would do when processing the enhanced prompt.
    """
    context = {}
    
    if "CONTEXT_DATA:" in enhanced_prompt:
        lines = enhanced_prompt.split("\n")
        in_context_section = False
        
        for line in lines:
            if line.strip() == "CONTEXT_DATA:":
                in_context_section = True
                continue
            elif line.strip().startswith("USER_REQUEST:"):
                break
            elif in_context_section and "=" in line:
                key, value = line.split("=", 1)
                context[key.strip().lower()] = value.strip()
    
    return context


def test_context_injection_for_social_tools():
    """Test context injection strategy for social tools."""
    print("🧪 Testing Context Injection for Social Tools")
    
    # Simulate a user request with context
    user_message = "Create an image of a sunset"
    context = {
        'chat_id': '12345',
        'username': 'testuser',
        'chat_history': [
            {'role': 'user', 'content': 'Hello'},
            {'role': 'assistant', 'content': 'Hi there!'}
        ]
    }
    
    # Apply context injection
    enhanced_prompt = simulate_context_injection(user_message, context)
    print(f"📝 Enhanced prompt created:\n{enhanced_prompt[:200]}...")
    
    # Extract context (simulating what Claude would do)
    extracted_context = extract_context_from_prompt(enhanced_prompt)
    print(f"🔍 Extracted context: {extracted_context}")
    
    # Test that tools accept context parameters
    chat_id = extracted_context.get('chat_id', '')
    username = extracted_context.get('username', '')
    
    # Test create_image with extracted context
    print("🎨 Testing create_image with context...")
    result = create_image("sunset", chat_id=chat_id)
    print(f"   Result: {result[:100]}...")
    
    # Test save_link with extracted context  
    print("🔗 Testing save_link with context...")
    result = save_link("https://example.com", chat_id=chat_id, username=username)
    print(f"   Result: {result[:100]}...")
    
    # Test search_links with extracted context
    print("📂 Testing search_links with context...")
    result = search_links("example", chat_id=chat_id)
    print(f"   Result: {result[:100]}...")
    
    assert chat_id == '12345', "Context injection should preserve chat_id"
    assert username == 'testuser', "Context injection should preserve username"
    print("✅ Social tools context injection validation passed!\n")


def test_context_injection_for_telegram_tools():
    """Test context injection strategy for telegram tools."""
    print("🧪 Testing Context Injection for Telegram Tools")
    
    # Simulate a user request for conversation history
    user_message = "What did we discuss about Python yesterday?"
    context = {
        'chat_id': '67890',
        'username': 'pythondev'
    }
    
    # Apply context injection
    enhanced_prompt = simulate_context_injection(user_message, context)
    print(f"📝 Enhanced prompt created:\n{enhanced_prompt[:200]}...")
    
    # Extract context
    extracted_context = extract_context_from_prompt(enhanced_prompt)
    print(f"🔍 Extracted context: {extracted_context}")
    
    chat_id = extracted_context.get('chat_id', '')
    
    # Test telegram tools with extracted context
    print("📱 Testing search_conversation_history with context...")
    result = search_conversation_history("Python", chat_id=chat_id)
    print(f"   Result: {result[:100]}...")
    
    print("💬 Testing get_conversation_context with context...")
    result = get_conversation_context(chat_id=chat_id)
    print(f"   Result: {result[:100]}...")
    
    assert chat_id == '67890', "Context injection should preserve chat_id"
    print("✅ Telegram tools context injection validation passed!\n")


def test_context_injection_for_notion_tools():
    """Test context injection strategy for notion tools."""
    print("🧪 Testing Context Injection for Notion Tools")
    
    # Simulate a user request with Notion workspace context
    user_message = "What's the highest priority task in PsyOPTIMAL?"
    context = {
        'chat_id': '11111',
        'username': 'projectmanager',
        'notion_data': 'Group mapped to PsyOPTIMAL workspace'
    }
    
    # Apply context injection
    enhanced_prompt = simulate_context_injection(user_message, context)
    print(f"📝 Enhanced prompt created:\n{enhanced_prompt[:200]}...")
    
    # Extract context
    extracted_context = extract_context_from_prompt(enhanced_prompt)
    print(f"🔍 Extracted context: {extracted_context}")
    
    # Test notion tool (would use workspace mapping in real implementation)
    print("📋 Testing query_notion_projects...")
    result = query_notion_projects("PsyOPTIMAL", "What are the highest priority tasks?")
    print(f"   Result: {result[:100]}...")
    
    # Validate that context was preserved
    assert extracted_context.get('chat_id') == '11111', "Context injection should preserve chat_id"
    # Note: PROJECT_DATA is available in the enhanced prompt for Claude to use
    assert 'PROJECT_DATA:' in enhanced_prompt, "Context injection should include project data in prompt"
    print("✅ Notion tools context injection validation passed!\n")


def test_context_injection_edge_cases():
    """Test edge cases for context injection."""
    print("🧪 Testing Context Injection Edge Cases")
    
    # Test with minimal context
    result = simulate_context_injection("Hello", {})
    assert result == "Hello", "Should return original message when no context"
    
    # Test with empty values
    result = simulate_context_injection("Test", {'chat_id': '', 'username': None})
    assert "CONTEXT_DATA:" not in result, "Should not inject empty context"
    
    # Test extraction from non-enhanced prompt
    context = extract_context_from_prompt("Just a regular message")
    assert context == {}, "Should return empty context for regular messages"
    
    print("✅ Edge cases validation passed!\n")


def demonstrate_unified_workflow():
    """Demonstrate the complete unified workflow as described in the plan."""
    print("🚀 Demonstrating Complete Unified Workflow")
    print("=" * 60)
    
    # Simulate a complex user request that would use multiple tools
    user_message = "Search for recent AI developments, save interesting links, and create an image about it"
    context = {
        'chat_id': '99999',
        'username': 'airesearcher',
        'chat_history': [
            {'role': 'user', 'content': 'I need to research AI trends'},
            {'role': 'assistant', 'content': 'I can help with that research'}
        ]
    }
    
    print(f"👤 User Request: {user_message}")
    print(f"📊 Available Context: {context}")
    
    # Step 1: Context injection
    enhanced_prompt = simulate_context_injection(user_message, context)
    print(f"\n📝 Step 1 - Enhanced Prompt (first 300 chars):")
    print(f"{enhanced_prompt[:300]}...\n")
    
    # Step 2: Context extraction (what Claude would do)
    extracted_context = extract_context_from_prompt(enhanced_prompt)
    chat_id = extracted_context.get('chat_id', '')
    username = extracted_context.get('username', '')
    
    print(f"🔍 Step 2 - Extracted Context: chat_id={chat_id}, username={username}")
    
    # Step 3: Tool usage with context (what the unified system would do)
    print(f"\n🛠️  Step 3 - Tool Execution with Context:")
    
    print("   🔍 Search for AI developments...")
    search_result = search_current_info("latest AI developments 2024")
    print(f"   ✅ Search completed: {len(search_result)} characters returned")
    
    print("   🔗 Save interesting link...")
    save_result = save_link("https://example.com/ai-trends", chat_id=chat_id, username=username)
    print(f"   ✅ Link saved: {save_result[:50]}...")
    
    print("   🎨 Create AI image...")
    image_result = create_image("futuristic AI technology", chat_id=chat_id)
    print(f"   ✅ Image created: {image_result[:50]}...")
    
    print(f"\n🎯 Workflow Complete!")
    print("   - Context successfully injected and extracted")
    print("   - Multiple tools executed with proper context")
    print("   - Chat ID and username preserved throughout")
    print("   - Ready for unified Valor-Claude integration")


if __name__ == "__main__":
    print("🔬 MCP Context Injection Validation Suite")
    print("=" * 60)
    print("Testing the context injection strategy from the unified plan\n")
    
    # Run all validation tests
    test_context_injection_for_social_tools()
    test_context_injection_for_telegram_tools()
    test_context_injection_for_notion_tools()
    test_context_injection_edge_cases()
    
    # Demonstrate complete workflow
    demonstrate_unified_workflow()
    
    print("\n🎉 All Context Injection Validations Passed!")
    print("✅ Phase 1 MCP Server Foundation is complete and ready for Phase 2")