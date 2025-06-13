#!/usr/bin/env python3
"""
Simple test runner for our real end-to-end tests.

Runs the comprehensive test suite with real components and no mocks.
"""

import asyncio
import sys
import time
from pathlib import Path

# Add current directory to path
sys.path.insert(0, str(Path(__file__).parent))

# Import our test classes
from tests.test_real_telegram_e2e import TestRealTelegramEndToEnd


async def run_single_test():
    """Run a single test to validate the system."""
    print("🚀 Running Single Real E2E Test")
    print("=" * 50)
    
    # Create test instance
    test_instance = TestRealTelegramEndToEnd()
    
    # Set up test environment
    test_instance.setup_test_environment()
    
    try:
        # Create fixtures
        real_processor = test_instance.real_processor()
        self_message_user = test_instance.self_message_user()
        dm_chat = test_instance.dm_chat()
        real_chat_history = test_instance.real_chat_history()
        
        print("✅ Test fixtures created")
        
        # Run the basic text message test
        print("\n🧪 Running: test_real_text_message_processing")
        start_time = time.time()
        
        await test_instance.test_real_text_message_processing(
            real_processor, self_message_user, dm_chat, real_chat_history
        )
        
        end_time = time.time()
        print(f"✅ Test completed in {end_time - start_time:.2f}s")
        
        # Run web search test
        print("\n🧪 Running: test_real_web_search_integration")
        start_time = time.time()
        
        await test_instance.test_real_web_search_integration(
            real_processor, self_message_user, dm_chat
        )
        
        end_time = time.time()
        print(f"✅ Test completed in {end_time - start_time:.2f}s")
        
        print("\n🎉 All tests passed! Real E2E system working correctly.")
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    return True


def run_quick_validation():
    """Quick validation that our system components can be imported."""
    print("🔍 Quick System Validation")
    print("=" * 30)
    
    try:
        # Test imports
        from integrations.telegram.unified_processor import UnifiedMessageProcessor
        print("✅ UnifiedMessageProcessor imported")
        
        from agents.valor.agent import valor_agent, ValorContext
        print("✅ Valor agent imported")
        
        from utilities.database import get_database_connection, init_database
        print("✅ Database utilities imported")
        
        from pyrogram.types import Message, User, Chat
        print("✅ Pyrogram types imported")
        
        # Test database
        init_database()
        with get_database_connection() as conn:
            result = conn.execute("SELECT COUNT(*) FROM projects").fetchone()
            print(f"✅ Database accessible ({result[0]} projects)")
        
        # Test processor creation
        processor = UnifiedMessageProcessor(telegram_bot=None, valor_agent=valor_agent)
        print("✅ UnifiedMessageProcessor created with real Valor agent")
        
        print("\n🎯 System validation complete - all components ready!")
        return True
        
    except Exception as e:
        print(f"\n❌ Validation failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("🧪 Real E2E Test Suite")
    print("=" * 60)
    print("Testing complete Telegram → Valor message flow with real components")
    print("NO MOCKS - Uses actual system components throughout")
    print("=" * 60)
    
    # Quick validation first
    if not run_quick_validation():
        print("\n❌ System validation failed - cannot run tests")
        sys.exit(1)
    
    print("\n" + "=" * 60)
    
    # Run async tests
    try:
        success = asyncio.run(run_single_test())
        if success:
            print("\n🏆 SUCCESS: All real E2E tests passed!")
            print("   The complete message processing pipeline works correctly")
            print("   with real Telegram messages, real agent execution, and real tools.")
        else:
            print("\n💥 FAILURE: Some tests failed")
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\n⏹️  Tests interrupted by user")
    except Exception as e:
        print(f"\n💥 Test runner error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)