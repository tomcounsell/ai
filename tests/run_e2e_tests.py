#!/usr/bin/env python3
"""
Queue-enabled E2E test runner using Huey for resource-controlled execution.
Now schedules E2E tests as background promises with API usage monitoring.
"""

import sys
import os
import asyncio
from pathlib import Path

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from utilities.promise_manager_huey import HueyPromiseManager


def schedule_e2e_tests(scenario_type: str = 'basic', chat_id: int = 0, message_id: int = 0) -> int:
    """Schedule E2E tests to run in the background via Huey queue."""
    manager = HueyPromiseManager()
    
    scenario_descriptions = {
        'basic': 'Basic conversation scenarios (low resource)',
        'full': 'Complete E2E test suite (high resource)',
        'api': 'API integration scenarios (medium resource)',
        'ollama': 'OLLAMA-focused scenarios (local resource)'
    }
    
    description = scenario_descriptions.get(scenario_type, f'{scenario_type} E2E tests')
    
    # Create promise for E2E execution
    promise_id = manager.create_promise(
        chat_id=chat_id,
        message_id=message_id,
        task_description=f"Run E2E tests: {description}",
        task_type='analysis',
        metadata={
            'suite_type': 'e2e',
            'scenario_type': scenario_type,
            'resource_level': _get_resource_level(scenario_type)
        }
    )
    
    # Schedule execution
    from tasks.test_runner_tasks import execute_test_suite_by_name
    execute_test_suite_by_name.schedule(args=(promise_id,), delay=2)  # Small delay for E2E prep
    
    return promise_id


def _get_resource_level(scenario_type: str) -> str:
    """Get resource usage level for scenario type."""
    levels = {
        'basic': 'low',
        'ollama': 'medium',
        'api': 'medium', 
        'full': 'high'
    }
    return levels.get(scenario_type, 'medium')


def main():
    """Main E2E test runner interface - now uses Huey queue scheduling."""
    print("🤖 Valor Engels E2E Test Suite (Queue-Enabled)")
    print("=" * 60)
    print("Now using Huey queue for resource-controlled E2E test execution")
    print()
    
    # Setup environment check
    load_dotenv()
    
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")
    
    print("🔧 Environment Status:")
    print(f"  Anthropic API: {'✅ Available' if anthropic_key else '❌ Missing'}")
    print(f"  OpenAI API: {'✅ Available' if openai_key else '❌ Missing'}")
    
    if not anthropic_key:
        print("  ⚠️  Some E2E tests will use mocked responses")
    
    if not openai_key:
        print("  ⚠️  LLM evaluation will be skipped")
    
    print()
    
    # Check command line arguments
    if len(sys.argv) > 1:
        scenario_type = sys.argv[1]
        
        if scenario_type in ['basic', 'full', 'api', 'ollama']:
            print(f"🚀 Scheduling {scenario_type} E2E tests...")
            promise_id = schedule_e2e_tests(scenario_type)
            print(f"✅ E2E tests scheduled as promise {promise_id}")
            print(f"💡 Monitor progress in Telegram or check promise status")
            return 0
        elif scenario_type == '--quick':
            # Legacy support for --quick flag
            print("🚀 Scheduling basic E2E tests (quick mode)...")
            promise_id = schedule_e2e_tests('basic')
            print(f"✅ Quick E2E tests scheduled as promise {promise_id}")
            return 0
        else:
            print(f"❌ Unknown scenario type: {scenario_type}")
            print("Available: basic, api, ollama, full")
            return 1
    
    # Interactive mode
    print("Available E2E test scenarios:")
    print("1. basic  - Basic conversation tests (5-10 min)")
    print("2. api    - API integration tests (10-15 min)")
    print("3. ollama - OLLAMA-focused tests (15-20 min)")
    print("4. full   - Complete test suite (30+ min)")
    print()
    
    choice = input("Select scenario (1-4) or press Enter to exit: ").strip()
    
    scenario_map = {
        '1': 'basic',
        '2': 'api',
        '3': 'ollama', 
        '4': 'full'
    }
    
    if not choice:
        print("Exiting...")
        return 0
    
    if choice in scenario_map:
        scenario_type = scenario_map[choice]
        resource_level = _get_resource_level(scenario_type)
        
        print(f"🚀 Scheduling {scenario_type} E2E tests...")
        print(f"⚡ Resource level: {resource_level}")
        
        promise_id = schedule_e2e_tests(scenario_type)
        print(f"✅ E2E tests scheduled as promise {promise_id}")
        print(f"💡 Monitor progress in Telegram or check promise status")
        
        if scenario_type == 'full':
            print("⚠️  Full suite may take 30+ minutes - monitor carefully")
        
        return 0
    
    else:
        print("❌ Invalid choice")
        return 1


if __name__ == "__main__":
    exit(main())
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

from tests.e2e_framework import E2ETestRunner, get_default_scenarios


def setup_environment():
    """Load environment variables and check dependencies"""
    load_dotenv()

    # Check for required API keys
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")

    if not anthropic_key:
        print("⚠️  ANTHROPIC_API_KEY not found - bot responses will be mocked")

    if not openai_key:
        print("⚠️  OPENAI_API_KEY not found - LLM evaluation will be mocked")

    return anthropic_key, openai_key


async def main():
    """Main test execution function"""

    print("🤖 Valor Engels E2E Test Suite")
    print("=" * 40)

    # Setup
    anthropic_key, openai_key = setup_environment()

    # Initialize clients
    anthropic_client = None
    if anthropic_key:
        try:
            import anthropic

            anthropic_client = anthropic.Anthropic(api_key=anthropic_key)
            print("✅ Anthropic client initialized")
        except ImportError:
            print("❌ Anthropic library not available")

    # Initialize test runner
    runner = E2ETestRunner(
        anthropic_client=anthropic_client,
        # Notion functionality now handled through MCP pm_tools server
    )

    # Get test scenarios
    scenarios = get_default_scenarios()
    print(f"📋 Loaded {len(scenarios)} test scenarios")

    # Run tests
    results = await runner.run_test_suite(scenarios)

    # Generate and display report
    report = runner.generate_report(results)
    print(report)

    # Summary
    passed = sum(1 for r in results if r.passed)
    total = len(results)

    if passed == total:
        print(f"\n🎉 All tests passed! ({passed}/{total})")
        return 0
    else:
        print(f"\n⚠️  Some tests failed ({passed}/{total} passed)")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
