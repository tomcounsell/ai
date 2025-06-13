#!/usr/bin/env python3
"""
Voice and Image E2E Test Runner

This script runs the new comprehensive voice and image message E2E tests
that use Valor's own DM for testing media message processing.

Features tested:
- Voice message transcription via Whisper API
- Image analysis via GPT-4 Vision API  
- TRUE E2E tests with real Telegram API
- Valor's DM context (user ID: 66968934582)
- Media routing and tool integration
"""

import asyncio
import subprocess
import sys
from pathlib import Path

def print_banner():
    """Print test runner banner."""
    print("🎙️🖼️ " + "="*60)
    print("    VOICE & IMAGE E2E TESTS FOR VALOR'S DM")
    print("="*64)
    print()
    print("📋 Test Suite Overview:")
    print("  🎙️ Voice Message Tests:")
    print("     • Comprehensive voice processing in Valor's DM")
    print("     • Voice transcription tool integration")
    print("     • TRUE E2E with real Telegram API")
    print("     • Actual voice file testing (if available)")
    print()
    print("  🖼️ Image Message Tests:")
    print("     • Comprehensive image analysis in Valor's DM")
    print("     • GPT-4 Vision API integration")
    print("     • TRUE E2E with real Telegram API")
    print("     • Actual image file testing (if available)")
    print()
    print("  🤖 Valor's DM Context:")
    print(f"     • User ID: 66968934582")
    print(f"     • Username: @valorengels")
    print(f"     • Chat Type: private (DM)")
    print()
    print("="*64)

def run_specific_tests():
    """Run specific voice and image tests."""
    
    print("\n🏃‍♂️ Running Voice and Image E2E Tests...")
    print("-" * 50)
    
    # Test commands to run
    test_commands = [
        # TRUE E2E Tests (Real Telegram API)
        {
            "name": "🎙️ TRUE E2E Voice Message Test",
            "cmd": [
                "python", "-m", "pytest", 
                "tests/test_real_telegram_e2e.py::TestRealTelegramEndToEnd::test_true_telegram_e2e_voice_message",
                "-v", "-s"
            ]
        },
        {
            "name": "🖼️ TRUE E2E Image Message Test", 
            "cmd": [
                "python", "-m", "pytest",
                "tests/test_real_telegram_e2e.py::TestRealTelegramEndToEnd::test_true_telegram_e2e_image_message", 
                "-v", "-s"
            ]
        },
        {
            "name": "🎧 TRUE E2E Actual Voice File Test",
            "cmd": [
                "python", "-m", "pytest",
                "tests/test_real_telegram_e2e.py::TestRealTelegramEndToEnd::test_true_telegram_e2e_actual_voice_file",
                "-v", "-s"
            ]
        },
        {
            "name": "🎨 TRUE E2E Actual Image File Test",
            "cmd": [
                "python", "-m", "pytest", 
                "tests/test_real_telegram_e2e.py::TestRealTelegramEndToEnd::test_true_telegram_e2e_actual_image_file",
                "-v", "-s"
            ]
        },
        
        # Comprehensive Processing Tests
        {
            "name": "🎙️ Valor DM Voice Processing Test",
            "cmd": [
                "python", "-m", "pytest",
                "tests/test_end_to_end_message_handling.py::TestEndToEndMessageHandling::test_valor_dm_voice_message_comprehensive",
                "-v", "-s"
            ]
        },
        {
            "name": "🖼️ Valor DM Image Processing Test", 
            "cmd": [
                "python", "-m", "pytest",
                "tests/test_end_to_end_message_handling.py::TestEndToEndMessageHandling::test_valor_dm_image_message_comprehensive",
                "-v", "-s" 
            ]
        }
    ]
    
    results = []
    
    for test in test_commands:
        print(f"\n▶️  Running: {test['name']}")
        print("   Command:", " ".join(test['cmd']))
        
        try:
            result = subprocess.run(
                test['cmd'],
                capture_output=True,
                text=True,
                timeout=120  # 2 minute timeout per test
            )
            
            if result.returncode == 0:
                print(f"   ✅ PASSED")
                results.append(("✅", test['name'], "PASSED"))
            else:
                print(f"   ❌ FAILED")
                print(f"   Error: {result.stderr[:200]}...")
                results.append(("❌", test['name'], "FAILED"))
                
        except subprocess.TimeoutExpired:
            print(f"   ⏰ TIMEOUT (120s)")
            results.append(("⏰", test['name'], "TIMEOUT"))
            
        except Exception as e:
            print(f"   💥 ERROR: {e}")
            results.append(("💥", test['name'], f"ERROR: {e}"))
    
    return results

def print_summary(results):
    """Print test results summary."""
    print("\n" + "="*64)
    print("📊 TEST RESULTS SUMMARY")
    print("="*64)
    
    passed = sum(1 for status, _, _ in results if status == "✅")
    failed = sum(1 for status, _, _ in results if status == "❌") 
    timeouts = sum(1 for status, _, _ in results if status == "⏰")
    errors = sum(1 for status, _, _ in results if status == "💥")
    
    print(f"📈 Overall Results:")
    print(f"   ✅ Passed: {passed}")
    print(f"   ❌ Failed: {failed}")
    print(f"   ⏰ Timeouts: {timeouts}")
    print(f"   💥 Errors: {errors}")
    print()
    
    print("📋 Detailed Results:")
    for status, name, result in results:
        print(f"   {status} {name}: {result}")
    
    print("\n" + "="*64)
    
    if passed == len(results):
        print("🎉 ALL VOICE & IMAGE TESTS PASSED!")
        print("🚀 Media message processing is working perfectly!")
    elif passed > 0:
        print(f"✨ {passed}/{len(results)} tests passed - partial success!")
        print("🔧 Some features may need attention")
    else:
        print("🚨 ALL TESTS FAILED")
        print("🛠️ Media message processing needs debugging")
    
    print("="*64)

def setup_test_assets():
    """Set up test assets directory and check for test files."""
    assets_dir = Path("tests/assets")
    assets_dir.mkdir(exist_ok=True)
    
    print(f"📁 Test assets directory: {assets_dir.absolute()}")
    
    # Check for existing test files
    voice_files = list(assets_dir.glob("*.ogg")) + list(assets_dir.glob("*.mp3"))
    image_files = list(assets_dir.glob("*.jpg")) + list(assets_dir.glob("*.png"))
    
    print(f"🎧 Voice files found: {len(voice_files)}")
    for f in voice_files:
        print(f"   📄 {f.name}")
        
    print(f"🖼️ Image files found: {len(image_files)}")
    for f in image_files:
        print(f"   📄 {f.name}")
    
    if not voice_files:
        print("💡 To test with real voice files: add test_voice.ogg to tests/assets/")
    
    if not image_files:
        print("💡 To test with real images: add test_image.jpg to tests/assets/")
    
    print()

def main():
    """Main test runner."""
    print_banner()
    
    # Setup test environment
    setup_test_assets()
    
    # Verify system status
    print("🔍 Checking system status...")
    try:
        import requests
        response = requests.get("http://localhost:9000/health", timeout=5)
        if response.status_code == 200:
            health = response.json()
            print(f"✅ System health: {health}")
        else:
            print(f"⚠️ System not responding properly: {response.status_code}")
    except Exception as e:
        print(f"❌ System check failed: {e}")
        print("💡 Make sure to run: scripts/start.sh")
        return
    
    # Run tests
    results = run_specific_tests()
    
    # Print summary
    print_summary(results)

if __name__ == "__main__":
    main()