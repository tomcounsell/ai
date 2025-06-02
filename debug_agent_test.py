#!/usr/bin/env python3
"""
Standalone test to debug agent hanging issue.
"""

import asyncio
import time
import signal
import sys
from agents.valor.agent import valor_agent, ValorContext

async def test_agent_with_timeout():
    """Test agent with different messages to isolate the hanging issue."""
    
    test_cases = [
        "Hello",
        "What's the weather?",
        "Help me with coding",
        "What is test coverage?",
        "What is your test coverage %?",  # The problematic message
    ]
    
    for i, message in enumerate(test_cases, 1):
        print(f"\n🧪 Test {i}/5: '{message}'")
        
        context = ValorContext(
            chat_id=12345,
            username="testuser",
            is_group_chat=False
        )
        
        start_time = time.time()
        try:
            # Set a timeout for each test
            result = await asyncio.wait_for(
                valor_agent.run(message, deps=context),
                timeout=10.0  # 10 second timeout
            )
            
            end_time = time.time()
            duration = end_time - start_time
            
            print(f"   ✅ Success in {duration:.2f}s")
            print(f"   Response: {result.output[:100]}...")
            
        except asyncio.TimeoutError:
            print(f"   ⏰ TIMEOUT after 10 seconds")
            print("   This suggests the agent is hanging on this message")
            
        except Exception as e:
            print(f"   ❌ Error: {e}")

def signal_handler(signum, frame):
    """Handle Ctrl+C gracefully."""
    print("\n🛑 Test interrupted by user")
    sys.exit(0)

if __name__ == "__main__":
    print("🔍 Agent Debugging Test")
    print("=" * 50)
    print("Testing different messages to identify hanging patterns...")
    
    # Set up signal handler for graceful exit
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        asyncio.run(test_agent_with_timeout())
        print("\n✅ All tests completed")
    except KeyboardInterrupt:
        print("\n🛑 Test suite interrupted")
    except Exception as e:
        print(f"\n💥 Test suite failed: {e}")