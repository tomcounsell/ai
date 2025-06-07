#!/usr/bin/env python3
"""
Queue-enabled test runner using Huey for resource-controlled execution.
Now schedules tests as background promises instead of running them immediately.
"""

import sys
import asyncio
from pathlib import Path

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

from tasks.test_runner_tasks import schedule_test_run
from utilities.promise_manager_huey import HueyPromiseManager


def schedule_test_suite(suite_type: str, chat_id: int = 0, message_id: int = 0) -> int:
    """Schedule a test suite to run in the background via Huey queue."""
    manager = HueyPromiseManager()
    
    suite_descriptions = {
        'unit': 'Lightweight unit tests (agent, file reader, validation)',
        'integration': 'API integration tests (converted to mocks)',  
        'intent': 'OLLAMA intent tests (rate-limited)',
        'e2e': 'End-to-end scenario tests'
    }
    
    description = suite_descriptions.get(suite_type, f'{suite_type} tests')
    
    # Create promise for test execution
    promise_id = manager.create_promise(
        chat_id=chat_id,
        message_id=message_id,
        task_description=f"Run {description}",
        task_type='analysis',
        metadata={'suite_type': suite_type}
    )
    
    # Schedule execution
    from tasks.test_runner_tasks import execute_test_suite_by_name
    execute_test_suite_by_name.schedule(args=(promise_id,), delay=1)
    
    return promise_id


def main():
    """Main test runner interface - now uses Huey queue scheduling."""
    print("🧪 Valor Agent Queue-Enabled Test Runner")
    print("=" * 50)
    print("Now using Huey queue for resource-controlled test execution")
    print()
    
    # Check if user wants to schedule tests
    if len(sys.argv) > 1:
        suite_type = sys.argv[1]
        
        if suite_type in ['unit', 'integration', 'intent', 'e2e']:
            print(f"🚀 Scheduling {suite_type} test suite...")
            promise_id = schedule_test_suite(suite_type)
            print(f"✅ Tests scheduled as promise {promise_id}")
            print(f"💡 Monitor progress in Telegram or check promise status")
            return 0
        else:
            print(f"❌ Unknown suite type: {suite_type}")
            print("Available: unit, integration, intent, e2e")
            return 1
    
    # Interactive mode
    print("Available test suites:")
    print("1. unit        - Fast tests (agent, utilities)")
    print("2. integration - API tests (mocked)")  
    print("3. intent      - OLLAMA tests (rate-limited)")
    print("4. e2e         - End-to-end tests")
    print("5. all         - Schedule all suites")
    print()
    
    choice = input("Select test suite (1-5) or press Enter to exit: ").strip()
    
    suite_map = {
        '1': 'unit',
        '2': 'integration', 
        '3': 'intent',
        '4': 'e2e',
        '5': 'all'
    }
    
    if not choice:
        print("Exiting...")
        return 0
    
    if choice == '5':
        # Schedule all suites with delays to prevent resource conflicts
        promises = []
        for i, suite in enumerate(['unit', 'integration', 'intent', 'e2e']):
            print(f"🚀 Scheduling {suite} tests...")
            promise_id = schedule_test_suite(suite)
            promises.append(promise_id)
            
        print(f"✅ All test suites scheduled as promises: {promises}")
        print("💡 Tests will run sequentially in the background")
        return 0
    
    elif choice in suite_map:
        suite_type = suite_map[choice]
        print(f"🚀 Scheduling {suite_type} test suite...")
        promise_id = schedule_test_suite(suite_type)
        print(f"✅ Tests scheduled as promise {promise_id}")
        print(f"💡 Monitor progress in Telegram or check promise status")
        return 0
    
    else:
        print("❌ Invalid choice")
        return 1


if __name__ == "__main__":
    exit(main())


def check_dependencies():
    """Check if required dependencies are installed"""
    try:
        import openai

        print("✅ OpenAI library available")
    except ImportError:
        print("❌ OpenAI library not found. Installing...")
        subprocess.run([sys.executable, "-m", "pip", "install", "openai"], check=True)
        print("✅ OpenAI library installed")


def check_environment():
    """Check if required environment variables are set"""
    import os
    from dotenv import load_dotenv
    
    # Load environment variables from .env file
    load_dotenv()

    required_vars = ["OPENAI_API_KEY", "ANTHROPIC_API_KEY"]
    missing = []

    for var in required_vars:
        if not os.getenv(var):
            missing.append(var)

    if missing:
        print(f"❌ Missing environment variables: {', '.join(missing)}")
        print("Please ensure these are set in your .env file")
        return False

    print("✅ All required environment variables found")
    return True


async def main():
    """Run all available tests"""
    print("🧪 Valor Agent Test Runner")
    print("-" * 40)

    # Check dependencies
    check_dependencies()

    # Check environment
    if not check_environment():
        return 1

    # Run different test suites
    test_suites = [
        ("Conversation Tests", "test_valor_conversations"),
        ("Image Flow Tests", "test_comprehensive_image_flow"),
        ("Image Error Tests", "test_image_error_cases"),
        ("Unified Image Integration", "test_unified_image_integration"),
        ("Telegram Image Integration", "test_telegram_image_integration"),
        ("Image Tools", "test_image_tools")
    ]
    
    total_failures = 0
    
    for suite_name, module_name in test_suites:
        print(f"\n🔄 Running {suite_name}...")
        print("=" * 50)
        
        try:
            module = __import__(module_name)
            if hasattr(module, 'main'):
                result = await module.main()
                if result != 0:
                    print(f"❌ {suite_name} failed")
                    total_failures += 1
                else:
                    print(f"✅ {suite_name} passed")
            else:
                print(f"⏭️ {suite_name} skipped (no main function)")
        except ImportError:
            print(f"⏭️ {suite_name} skipped (module not found)")
        except Exception as e:
            print(f"❌ {suite_name} failed with error: {e}")
            total_failures += 1
    
    print(f"\n🏁 Test Summary")
    print(f"Total test suites: {len(test_suites)}")
    print(f"Failed suites: {total_failures}")
    
    return 1 if total_failures > 0 else 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    print(f"\n🏁 Tests completed with exit code: {exit_code}")
    sys.exit(exit_code)
