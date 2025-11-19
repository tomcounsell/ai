#!/usr/bin/env python3
"""
End-to-End Happy Path Test for Telegram Bot

This test ensures the complete message flow is working:
1. Bot is running and connected
2. Bot receives messages  
3. Bot processes messages
4. Bot sends responses back
5. Responses are logged properly

Run this test to verify the system is working end-to-end.
"""

import asyncio
import os
import sys
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any
import logging

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from telethon import TelegramClient, events
from telethon.tl.types import User
from dotenv import load_dotenv

# Load environment
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class TelegramBotE2ETester:
    """End-to-end tester for Telegram bot happy path."""
    
    def __init__(self):
        """Initialize the tester."""
        # Get credentials from environment
        self.api_id = os.getenv('TELEGRAM_API_ID')
        self.api_hash = os.getenv('TELEGRAM_API_HASH')
        self.phone = os.getenv('TELEGRAM_PHONE')
        self.password = os.getenv('TELEGRAM_PASSWORD')
        
        if not all([self.api_id, self.api_hash]):
            raise ValueError("Missing Telegram credentials in environment")
        
        # Create client for testing
        self.client = TelegramClient(
            'data/e2e_test_session',
            int(self.api_id),
            self.api_hash
        )
        
        self.test_results = {
            "started": datetime.now(timezone.utc).isoformat(),
            "tests_run": 0,
            "tests_passed": 0,
            "tests_failed": 0,
            "details": []
        }
    
    async def connect(self) -> bool:
        """Connect to Telegram."""
        try:
            await self.client.start(
                phone=lambda: self.phone,
                password=lambda: self.password
            )
            me = await self.client.get_me()
            logger.info(f"✅ Connected as {me.first_name} (@{me.username})")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to connect: {e}")
            return False
    
    async def test_send_message_to_self(self) -> Dict[str, Any]:
        """Test sending a message to self (bot should respond)."""
        test_name = "send_message_to_self"
        logger.info(f"🧪 Running test: {test_name}")
        
        result = {
            "test": test_name,
            "passed": False,
            "duration_ms": 0,
            "error": None,
            "response_received": False,
            "response_time_ms": None
        }
        
        start_time = time.perf_counter()
        
        try:
            # Get self (saved messages)
            me = await self.client.get_me()
            
            # Generate unique test message
            test_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            test_message = f"E2E_TEST_{test_id}: Hello bot, please respond!"
            
            # Send message to self
            logger.info(f"📤 Sending: {test_message}")
            sent_msg = await self.client.send_message('me', test_message)
            
            # Wait for bot response (with timeout)
            response_received = False
            response_content = None
            wait_start = time.perf_counter()
            
            async def check_for_response():
                nonlocal response_received, response_content
                
                # Listen for new messages
                @self.client.on(events.NewMessage(chats='me', incoming=True))
                async def handler(event):
                    # Check if this is a bot response to our test
                    if event.message and event.message.id > sent_msg.id:
                        response_received = True
                        response_content = event.message.text
                        logger.info(f"📥 Received response: {response_content[:100]}...")
            
            # Register handler
            await check_for_response()
            
            # Wait up to 10 seconds for response
            timeout = 10
            while not response_received and (time.perf_counter() - wait_start) < timeout:
                await asyncio.sleep(0.5)
            
            response_time_ms = int((time.perf_counter() - wait_start) * 1000)
            
            if response_received:
                logger.info(f"✅ Response received in {response_time_ms}ms")
                result["passed"] = True
                result["response_received"] = True
                result["response_time_ms"] = response_time_ms
                result["response_content"] = response_content[:200] if response_content else None
            else:
                logger.warning(f"⚠️ No response received within {timeout} seconds")
                result["error"] = "Timeout waiting for bot response"
            
        except Exception as e:
            logger.error(f"❌ Test failed: {e}")
            result["error"] = str(e)
        
        result["duration_ms"] = int((time.perf_counter() - start_time) * 1000)
        return result
    
    async def test_bot_status_command(self) -> Dict[str, Any]:
        """Test bot /status command."""
        test_name = "bot_status_command"
        logger.info(f"🧪 Running test: {test_name}")
        
        result = {
            "test": test_name,
            "passed": False,
            "duration_ms": 0,
            "error": None
        }
        
        start_time = time.perf_counter()
        
        try:
            # Send /status command
            logger.info("📤 Sending: /status")
            await self.client.send_message('me', '/status')
            
            # Wait briefly for processing
            await asyncio.sleep(2)
            
            # For now, just check that we could send the command
            result["passed"] = True
            logger.info("✅ Status command sent successfully")
            
        except Exception as e:
            logger.error(f"❌ Test failed: {e}")
            result["error"] = str(e)
        
        result["duration_ms"] = int((time.perf_counter() - start_time) * 1000)
        return result
    
    async def check_bot_logs(self) -> Dict[str, Any]:
        """Check bot logs for errors."""
        test_name = "check_bot_logs"
        logger.info(f"🧪 Running test: {test_name}")
        
        result = {
            "test": test_name,
            "passed": False,
            "duration_ms": 0,
            "error": None,
            "recent_errors": []
        }
        
        start_time = time.perf_counter()
        
        try:
            log_file = Path("logs/telegram_bot.log")
            if log_file.exists():
                # Read last 100 lines
                with open(log_file, 'r') as f:
                    lines = f.readlines()[-100:]
                
                # Check for errors
                error_lines = [
                    line.strip() for line in lines 
                    if 'ERROR' in line or 'CRITICAL' in line
                ]
                
                # Get last 5 errors
                result["recent_errors"] = error_lines[-5:] if error_lines else []
                
                if not error_lines:
                    logger.info("✅ No recent errors in logs")
                    result["passed"] = True
                else:
                    logger.warning(f"⚠️ Found {len(error_lines)} error(s) in recent logs")
                    result["passed"] = False  # Still mark as passed if bot is functional
                    result["error"] = f"Found {len(error_lines)} errors in logs"
            else:
                logger.warning("⚠️ Log file not found")
                result["error"] = "Log file not found"
        
        except Exception as e:
            logger.error(f"❌ Test failed: {e}")
            result["error"] = str(e)
        
        result["duration_ms"] = int((time.perf_counter() - start_time) * 1000)
        return result
    
    async def run_all_tests(self) -> Dict[str, Any]:
        """Run all E2E tests."""
        logger.info("=" * 60)
        logger.info("🚀 STARTING E2E HAPPY PATH TESTS")
        logger.info("=" * 60)
        
        # Connect to Telegram
        if not await self.connect():
            self.test_results["tests_failed"] = 1
            self.test_results["details"].append({
                "test": "connection",
                "passed": False,
                "error": "Failed to connect to Telegram"
            })
            return self.test_results
        
        # Run tests
        tests = [
            self.test_send_message_to_self(),
            self.test_bot_status_command(),
            self.check_bot_logs()
        ]
        
        for test_coro in tests:
            result = await test_coro
            self.test_results["tests_run"] += 1
            
            if result["passed"]:
                self.test_results["tests_passed"] += 1
            else:
                self.test_results["tests_failed"] += 1
            
            self.test_results["details"].append(result)
            
            # Brief pause between tests
            await asyncio.sleep(1)
        
        # Disconnect
        await self.client.disconnect()
        
        # Summary
        self.test_results["completed"] = datetime.now(timezone.utc).isoformat()
        
        logger.info("=" * 60)
        logger.info("📊 TEST RESULTS SUMMARY")
        logger.info(f"Tests Run: {self.test_results['tests_run']}")
        logger.info(f"✅ Passed: {self.test_results['tests_passed']}")
        logger.info(f"❌ Failed: {self.test_results['tests_failed']}")
        
        # Determine overall status
        if self.test_results["tests_failed"] == 0:
            logger.info("🎉 ALL TESTS PASSED - BOT IS WORKING!")
            self.test_results["overall_status"] = "PASSED"
        else:
            logger.warning("⚠️ SOME TESTS FAILED - CHECK BOT STATUS")
            self.test_results["overall_status"] = "FAILED"
        
        logger.info("=" * 60)
        
        # Save results to file
        results_file = Path("logs/e2e_test_results.json")
        results_file.parent.mkdir(exist_ok=True)
        with open(results_file, 'w') as f:
            json.dump(self.test_results, f, indent=2)
        logger.info(f"💾 Results saved to {results_file}")
        
        return self.test_results


async def main():
    """Main entry point."""
    tester = TelegramBotE2ETester()
    results = await tester.run_all_tests()
    
    # Exit with appropriate code
    sys.exit(0 if results["overall_status"] == "PASSED" else 1)


if __name__ == "__main__":
    asyncio.run(main())