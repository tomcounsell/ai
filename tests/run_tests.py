#!/usr/bin/env python3
"""
Simple test runner for Valor conversation tests
"""

import asyncio
import subprocess
import sys
from pathlib import Path

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
    """Run the conversation tests"""
    print("🧪 Valor Conversation Test Runner")
    print("-" * 40)
    
    # Check dependencies
    check_dependencies()
    
    # Check environment
    if not check_environment():
        return 1
    
    # Import and run tests
    try:
        from test_valor_conversations import main as run_tests
        return await run_tests()
    except Exception as e:
        print(f"❌ Error running tests: {e}")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    print(f"\n🏁 Tests completed with exit code: {exit_code}")
    sys.exit(exit_code)