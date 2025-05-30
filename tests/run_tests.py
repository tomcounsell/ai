#!/usr/bin/env python3
"""
Simple test runner for Valor conversation tests
"""

import asyncio
import subprocess
import sys


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
