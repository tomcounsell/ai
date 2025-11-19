"""
Demonstration of the Valor Agent Architecture

This script shows how to use the comprehensive agent system with
context management, tool registry, and PydanticAI integration.
"""

import asyncio
import logging
from pathlib import Path

from agents import (
    ValorAgent,
    ToolRegistry,
    tool_registry_decorator,
    CompressionStrategy
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Example tools using the decorator
@tool_registry_decorator(
    name="calculator",
    description="Perform basic mathematical calculations",
    category="math",
    version="1.0.0",
    tags=["math", "calculator"]
)
def calculator_tool(operation: str, a: float, b: float) -> dict:
    """
    Perform basic mathematical operations.
    
    Args:
        operation: The operation to perform (add, subtract, multiply, divide)
        a: First number
        b: Second number
        
    Returns:
        dict: Result of the calculation
    """
    operations = {
        "add": lambda x, y: x + y,
        "subtract": lambda x, y: x - y,
        "multiply": lambda x, y: x * y,
        "divide": lambda x, y: x / y if y != 0 else float('inf')
    }
    
    if operation not in operations:
        raise ValueError(f"Unsupported operation: {operation}")
    
    result = operations[operation](a, b)
    return {
        "operation": operation,
        "operands": [a, b],
        "result": result
    }


@tool_registry_decorator(
    name="text_processor",
    description="Process and analyze text",
    category="text",
    version="1.0.0",
    tags=["text", "nlp"]
)
def text_processor_tool(text: str, action: str) -> dict:
    """
    Process text with various actions.
    
    Args:
        text: Input text to process
        action: Action to perform (uppercase, lowercase, word_count, reverse)
        
    Returns:
        dict: Processed text result
    """
    actions = {
        "uppercase": lambda x: x.upper(),
        "lowercase": lambda x: x.lower(),
        "word_count": lambda x: len(x.split()),
        "reverse": lambda x: x[::-1],
        "length": lambda x: len(x)
    }
    
    if action not in actions:
        raise ValueError(f"Unsupported action: {action}")
    
    if action == "word_count" or action == "length":
        result = actions[action](text)
    else:
        result = actions[action](text)
    
    return {
        "original_text": text,
        "action": action,
        "result": result
    }


@tool_registry_decorator(
    name="system_info",
    description="Get system information",
    category="system",
    version="1.0.0"
)
async def system_info_tool(info_type: str) -> dict:
    """
    Get various types of system information.
    
    Args:
        info_type: Type of info to get (time, platform, memory)
        
    Returns:
        dict: System information
    """
    import platform
    import psutil
    from datetime import datetime
    
    info_getters = {
        "time": lambda: datetime.now().isoformat(),
        "platform": lambda: platform.system(),
        "memory": lambda: f"{psutil.virtual_memory().percent}%"
    }
    
    if info_type not in info_getters:
        available = ", ".join(info_getters.keys())
        raise ValueError(f"Unknown info type: {info_type}. Available: {available}")
    
    # Simulate async operation
    await asyncio.sleep(0.1)
    
    result = info_getters[info_type]()
    return {
        "info_type": info_type,
        "result": result,
        "timestamp": datetime.now().isoformat()
    }


async def demo_basic_agent():
    """Demonstrate basic agent functionality."""
    print("\n=== Basic Agent Demo ===")
    
    # Create agent with custom settings
    agent = ValorAgent(
        model="openai:gpt-4",  # This would use actual model in production
        max_context_tokens=50000,
        debug=True
    )
    
    # Register our example tools
    agent.register_tool(calculator_tool)
    agent.register_tool(text_processor_tool) 
    agent.register_tool(system_info_tool)
    
    print(f"Registered tools: {agent.tool_registry.list_tools()}")
    
    # Simulate a conversation
    chat_id = "demo_chat_1"
    user_name = "demo_user"
    
    try:
        # First message
        response1 = await agent.process_message(
            message="Hello! Can you help me calculate 15 + 27?",
            chat_id=chat_id,
            user_name=user_name,
            workspace="demo_workspace"
        )
        print(f"Response 1: {response1.content}")
        print(f"Tools used: {response1.tools_used}")
        
        # Follow-up message
        response2 = await agent.process_message(
            message="Now can you convert 'Hello World' to uppercase?",
            chat_id=chat_id
        )
        print(f"Response 2: {response2.content}")
        
        # Check context stats
        stats = agent.get_context_stats(chat_id)
        print(f"Context stats: {stats}")
        
    except Exception as e:
        print(f"Demo error (expected in example): {e}")


async def demo_tool_registry():
    """Demonstrate tool registry capabilities."""
    print("\n=== Tool Registry Demo ===")
    
    # Create standalone tool registry
    registry = ToolRegistry()
    
    # Register tools
    registry.register_tool(calculator_tool)
    registry.register_tool(text_processor_tool)
    registry.register_tool(system_info_tool)
    
    # List and search tools
    print(f"All tools: {registry.list_tools()}")
    print(f"Math tools: {registry.list_tools(category='math')}")
    print(f"Search 'text': {registry.search_tools('text')}")
    
    # Execute tools directly
    try:
        calc_result = await registry.execute_tool(
            "calculator",
            {"operation": "multiply", "a": 7, "b": 8}
        )
        print(f"Calculator result: {calc_result.result}")
        print(f"Execution time: {calc_result.execution_time:.3f}s")
        
        text_result = await registry.execute_tool(
            "text_processor",
            {"text": "Hello World", "action": "word_count"}
        )
        print(f"Text processing result: {text_result.result}")
        
        # Get usage stats
        stats = registry.get_tool_usage_stats("calculator")
        print(f"Calculator usage stats: {stats}")
        
        # Overall registry stats
        registry_stats = registry.get_registry_stats()
        print(f"Registry stats: {registry_stats}")
        
    except Exception as e:
        print(f"Tool execution error: {e}")


async def demo_context_management():
    """Demonstrate context management features."""
    print("\n=== Context Management Demo ===")
    
    # Create agent with custom compression strategy
    compression_strategy = CompressionStrategy(
        preserve_recent=10,
        preserve_important_threshold=7.5,
        max_summary_length=200
    )
    
    agent = ValorAgent(
        max_context_tokens=2000,  # Small limit to trigger compression
        debug=True
    )
    agent.context_manager.set_compression_strategy(compression_strategy)
    
    chat_id = "context_demo"
    user_name = "context_user"
    
    try:
        # Add many messages to trigger compression
        for i in range(30):
            importance = 9.0 if i % 10 == 0 else 5.0  # Every 10th message important
            
            await agent.process_message(
                message=f"This is message number {i} with some content to use tokens",
                chat_id=chat_id,
                user_name=user_name
            )
            
            if i % 10 == 0:
                # Mark important messages
                context = agent.get_context(chat_id)
                if context and context.message_history:
                    context.mark_message_important(context.message_history[-1].id)
        
        # Check final context state
        context = agent.get_context(chat_id)
        print(f"Final message count: {len(context.message_history)}")
        print(f"Compression count: {context.context_metrics.context_compressions}")
        
        # Get conversation summary
        summary = await agent.get_conversation_summary(chat_id)
        print(f"Conversation summary: {summary}")
        
        # Export context for analysis
        exported = await agent.export_context(chat_id)
        if exported:
            print(f"Exported context has {len(exported['message_history'])} messages")
        
    except Exception as e:
        print(f"Context demo error: {e}")


async def demo_advanced_features():
    """Demonstrate advanced agent features."""
    print("\n=== Advanced Features Demo ===")
    
    agent = ValorAgent(debug=True)
    
    # Multiple concurrent conversations
    conversations = [
        ("chat_1", "user_1", "Hello from chat 1"),
        ("chat_2", "user_2", "Hello from chat 2"),
        ("chat_3", "user_3", "Hello from chat 3")
    ]
    
    try:
        # Process messages concurrently
        tasks = []
        for chat_id, user_name, message in conversations:
            task = agent.process_message(
                message=message,
                chat_id=chat_id,
                user_name=user_name
            )
            tasks.append(task)
        
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        
        for i, response in enumerate(responses):
            if isinstance(response, Exception):
                print(f"Chat {i+1} error: {response}")
            else:
                print(f"Chat {i+1} response: {response.content[:50]}...")
        
        # List all active contexts
        active_contexts = agent.list_contexts()
        print(f"Active contexts: {active_contexts}")
        
        # Clear one context
        cleared = await agent.clear_context("chat_2")
        print(f"Cleared chat_2: {cleared}")
        print(f"Remaining contexts: {agent.list_contexts()}")
        
    except Exception as e:
        print(f"Advanced demo error: {e}")


async def main():
    """Run all demonstrations."""
    print("Valor Agent Architecture Demonstration")
    print("=====================================")
    
    try:
        await demo_basic_agent()
        await demo_tool_registry()
        await demo_context_management()
        await demo_advanced_features()
        
        print("\n=== Demo Complete ===")
        print("Note: Some features require actual model integration for full functionality.")
        
    except KeyboardInterrupt:
        print("\nDemo interrupted by user")
    except Exception as e:
        print(f"Demo failed with error: {e}")
        logger.exception("Demo error details")


if __name__ == "__main__":
    # Create examples directory if it doesn't exist
    examples_dir = Path(__file__).parent
    examples_dir.mkdir(exist_ok=True)
    
    # Run the demo
    asyncio.run(main())