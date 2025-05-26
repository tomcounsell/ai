#!/usr/bin/env python3
"""
End-to-End Test Runner for Valor Engels Telegram Bot

Executes comprehensive test scenarios using LLM evaluation.
Run with: python tests/run_e2e_tests.py
"""

import asyncio
import os
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
        notion_scout=None,  # Could add NotionScout for integration tests
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
