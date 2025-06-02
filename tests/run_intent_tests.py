#!/usr/bin/env python3
"""
Quick test runner for intent classification and prompt combinations.

This provides a simple interface to test the intent system without
the full pytest overhead.

Usage:
    python tests/run_intent_tests.py
    python tests/run_intent_tests.py --quick
    python tests/run_intent_tests.py --intent casual_chat
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

from integrations.ollama_intent import MessageIntent, classify_message_intent
from integrations.intent_prompts import get_intent_system_prompt
from agents.valor.handlers import handle_telegram_message_with_intent


class QuickIntentTester:
    """Quick and simple intent testing."""
    
    def __init__(self):
        self.test_messages = {
            MessageIntent.CASUAL_CHAT: [
                "Hey there! How are you?",
                "lol that's funny",
                "Good morning!",
                "😊 just saying hi"
            ],
            MessageIntent.QUESTION_ANSWER: [
                "What is the weather like?",
                "How does Python work?",
                "What does API mean?",
                "Can you explain machine learning?"
            ],
            MessageIntent.PROJECT_QUERY: [
                "What's my highest priority task?",
                "Show me the PsyOPTIMAL project status",
                "What are my deadlines this week?",
                "Any updates on FlexTrip?"
            ],
            MessageIntent.DEVELOPMENT_TASK: [
                "Help me write a Python function",
                "There's a bug in my code",
                "Can you review this implementation?",
                "Debug this error for me"
            ],
            MessageIntent.IMAGE_GENERATION: [
                "Generate an image of a sunset",
                "Create artwork showing a robot",
                "Make a picture of a cat",
                "Draw me a futuristic city"
            ],
            MessageIntent.IMAGE_ANALYSIS: [
                "[IMAGE] What do you see here?",
                "[PHOTO] Describe this image",
                "Image file path: /tmp/test.jpg - analyze this"
            ],
            MessageIntent.WEB_SEARCH: [
                "What's the latest AI news?",
                "What happened today in tech?",
                "Current weather in San Francisco",
                "Recent developments in blockchain"
            ],
            MessageIntent.LINK_ANALYSIS: [
                "https://example.com - what's this about?",
                "Summarize this article: https://news.com/article",
                "www.github.com/project - analyze this repo"
            ],
            MessageIntent.SYSTEM_HEALTH: [
                "ping",
                "What's your status?",
                "Are you running okay?",
                "System health check"
            ]
        }
    
    async def test_single_intent(self, intent: MessageIntent) -> dict:
        """Test classification for a single intent."""
        print(f"\n🎯 Testing {intent.value}")
        print("-" * 50)
        
        messages = self.test_messages.get(intent, [])
        results = {"total": len(messages), "correct": 0, "tests": []}
        
        for msg in messages:
            try:
                result = await classify_message_intent(msg, {})
                is_correct = result.intent == intent
                
                status = "✅" if is_correct else "❌"
                print(f"{status} '{msg[:40]:40}' → {result.intent.value:15} ({result.confidence:.2f})")
                
                if is_correct:
                    results["correct"] += 1
                
                results["tests"].append({
                    "message": msg,
                    "expected": intent,
                    "actual": result.intent,
                    "confidence": result.confidence,
                    "correct": is_correct
                })
                
            except Exception as e:
                print(f"💥 '{msg[:40]:40}' → ERROR: {e}")
                results["tests"].append({
                    "message": msg,
                    "expected": intent,
                    "error": str(e),
                    "correct": False
                })
        
        accuracy = results["correct"] / results["total"] * 100 if results["total"] > 0 else 0
        print(f"Accuracy: {results['correct']}/{results['total']} ({accuracy:.1f}%)")
        
        return results
    
    async def test_all_intents(self) -> dict:
        """Test all intent classifications."""
        print("🧠 Testing All Intent Classifications")
        print("=" * 60)
        
        all_results = {}
        total_correct = 0
        total_tests = 0
        
        for intent in MessageIntent:
            if intent in self.test_messages:
                results = await self.test_single_intent(intent)
                all_results[intent.value] = results
                total_correct += results["correct"]
                total_tests += results["total"]
        
        overall_accuracy = total_correct / total_tests * 100 if total_tests > 0 else 0
        
        print(f"\n📊 OVERALL RESULTS")
        print("=" * 60)
        print(f"Total Tests: {total_tests}")
        print(f"Correct: {total_correct}")
        print(f"Overall Accuracy: {overall_accuracy:.1f}%")
        
        return {
            "results": all_results,
            "summary": {
                "total_tests": total_tests,
                "total_correct": total_correct,
                "overall_accuracy": overall_accuracy
            }
        }
    
    async def test_prompt_generation(self) -> dict:
        """Test prompt generation for each intent."""
        print("\n🎨 Testing Prompt Generation")
        print("=" * 60)
        
        results = {}
        
        for intent in MessageIntent:
            print(f"\n📝 {intent.value}")
            
            try:
                from integrations.ollama_intent import IntentResult
                
                # Create mock intent result
                mock_result = IntentResult(
                    intent=intent,
                    confidence=0.9,
                    reasoning=f"Mock test for {intent.value}",
                    suggested_emoji="🤖"
                )
                
                # Test prompt generation
                context = {
                    "chat_id": 12345,
                    "username": "testuser",
                    "is_group_chat": False,
                    "has_image": False,
                    "has_links": False
                }
                
                prompt = get_intent_system_prompt(mock_result, context)
                
                if prompt:
                    print(f"   ✅ Generated prompt ({len(prompt)} chars)")
                    print(f"   Preview: {prompt[:100]}...")
                    results[intent.value] = {
                        "success": True,
                        "length": len(prompt),
                        "preview": prompt[:100]
                    }
                else:
                    print(f"   ❌ No prompt generated")
                    results[intent.value] = {"success": False, "error": "No prompt"}
                    
            except Exception as e:
                print(f"   💥 Error: {e}")
                results[intent.value] = {"success": False, "error": str(e)}
        
        return results
    
    async def test_integration_sample(self) -> dict:
        """Test a few end-to-end integrations."""
        print("\n🚀 Testing Integration Samples")
        print("=" * 60)
        
        test_cases = [
            ("Hey there!", MessageIntent.CASUAL_CHAT),
            ("What's my project status?", MessageIntent.PROJECT_QUERY),
            ("Help me debug this code", MessageIntent.DEVELOPMENT_TASK),
            ("ping", MessageIntent.SYSTEM_HEALTH)
        ]
        
        results = []
        
        for msg, expected_intent in test_cases:
            print(f"\n🔄 Testing: '{msg}'")
            
            try:
                # Test complete pipeline
                response = await handle_telegram_message_with_intent(
                    message=msg,
                    chat_id=12345,
                    username="testuser",
                    is_group_chat=False,
                    chat_history_obj=None,
                    notion_data=None,
                    is_priority_question=False
                )
                
                if response:
                    print(f"   ✅ Got response ({len(response)} chars)")
                    print(f"   Preview: {response[:100]}...")
                    results.append({
                        "message": msg,
                        "expected_intent": expected_intent.value,
                        "response_length": len(response),
                        "success": True
                    })
                else:
                    print(f"   ❌ No response")
                    results.append({
                        "message": msg,
                        "expected_intent": expected_intent.value,
                        "success": False,
                        "error": "No response"
                    })
                    
            except Exception as e:
                print(f"   💥 Error: {e}")
                results.append({
                    "message": msg,
                    "expected_intent": expected_intent.value,
                    "success": False,
                    "error": str(e)
                })
        
        successful = len([r for r in results if r["success"]])
        print(f"\n📊 Integration Results: {successful}/{len(results)} successful")
        
        return {"tests": results, "success_rate": successful / len(results)}
    
    async def run_quick_test(self):
        """Run a quick subset of tests."""
        print("⚡ Quick Intent Test")
        print("=" * 40)
        
        # Test a few key intents
        key_intents = [
            MessageIntent.CASUAL_CHAT,
            MessageIntent.PROJECT_QUERY,
            MessageIntent.DEVELOPMENT_TASK
        ]
        
        for intent in key_intents:
            if intent in self.test_messages:
                await self.test_single_intent(intent)
        
        # Quick integration test
        print("\n🚀 Quick Integration Test")
        print("-" * 40)
        
        try:
            response = await handle_telegram_message_with_intent(
                message="Hey, what's my project status?",
                chat_id=12345,
                username="testuser"
            )
            
            if response:
                print(f"✅ Integration working - got {len(response)} char response")
            else:
                print("❌ Integration failed - no response")
                
        except Exception as e:
            print(f"💥 Integration error: {e}")


async def main():
    """Main CLI interface."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Quick Intent Tester")
    parser.add_argument("--quick", action="store_true", help="Run quick test only")
    parser.add_argument("--intent", type=str, help="Test specific intent only")
    parser.add_argument("--prompts", action="store_true", help="Test prompt generation only")
    parser.add_argument("--integration", action="store_true", help="Test integration only")
    
    args = parser.parse_args()
    
    tester = QuickIntentTester()
    
    if args.quick:
        await tester.run_quick_test()
    elif args.intent:
        try:
            intent = MessageIntent(args.intent)
            await tester.test_single_intent(intent)
        except ValueError:
            print(f"❌ Invalid intent: {args.intent}")
            print(f"Available intents: {[i.value for i in MessageIntent]}")
    elif args.prompts:
        await tester.test_prompt_generation()
    elif args.integration:
        await tester.test_integration_sample()
    else:
        # Run all tests
        await tester.test_all_intents()
        await tester.test_prompt_generation()
        await tester.test_integration_sample()


if __name__ == "__main__":
    asyncio.run(main())