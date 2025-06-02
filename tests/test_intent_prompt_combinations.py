#!/usr/bin/env python3
"""
Comprehensive test suite for intent classification and prompt combinations.

This test suite validates:
1. Intent classification accuracy across different message types
2. Prompt generation for each intent type
3. Agent response quality with different intent-prompt combinations
4. Fallback behavior when classification fails
5. Performance metrics for the complete pipeline

Usage:
    python tests/test_intent_prompt_combinations.py
    pytest tests/test_intent_prompt_combinations.py -v
"""

import asyncio
import pytest
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Any
from dataclasses import dataclass
from enum import Enum

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))

from integrations.ollama_intent import MessageIntent, IntentResult, classify_message_intent
from integrations.intent_prompts import get_intent_system_prompt
from agents.valor.handlers import handle_telegram_message_with_intent


class TestCategory(Enum):
    """Categories of test scenarios."""
    BASIC_INTENT = "basic_intent"
    EDGE_CASES = "edge_cases"
    FALLBACK_BEHAVIOR = "fallback_behavior"
    PERFORMANCE = "performance"
    INTEGRATION = "integration"


@dataclass
class TestCase:
    """Individual test case for intent-prompt combinations."""
    name: str
    message: str
    expected_intent: MessageIntent
    min_confidence: float
    category: TestCategory
    context: Dict[str, Any] = None
    expected_keywords: List[str] = None
    should_fail: bool = False
    description: str = ""


class IntentPromptTestSuite:
    """Comprehensive test suite for intent classification and prompt generation."""
    
    def __init__(self):
        """Initialize the test suite with predefined test cases."""
        self.test_cases = self._generate_test_cases()
        self.results = []
        
    def _generate_test_cases(self) -> List[TestCase]:
        """Generate comprehensive test cases for all intent types."""
        return [
            # === CASUAL CHAT TESTS ===
            TestCase(
                name="casual_greeting",
                message="Hey there! How are you doing?",
                expected_intent=MessageIntent.CASUAL_CHAT,
                min_confidence=0.8,
                category=TestCategory.BASIC_INTENT,
                expected_keywords=["friendly", "conversation", "greeting"],
                description="Basic friendly greeting"
            ),
            TestCase(
                name="casual_emoji",
                message="😊 Just saying hi!",
                expected_intent=MessageIntent.CASUAL_CHAT,
                min_confidence=0.7,
                category=TestCategory.BASIC_INTENT,
                description="Casual message with emoji"
            ),
            TestCase(
                name="casual_short",
                message="lol",
                expected_intent=MessageIntent.CASUAL_CHAT,
                min_confidence=0.6,
                category=TestCategory.EDGE_CASES,
                description="Very short casual response"
            ),
            
            # === QUESTION ANSWER TESTS ===
            TestCase(
                name="direct_question",
                message="What is the weather like today?",
                expected_intent=MessageIntent.QUESTION_ANSWER,
                min_confidence=0.8,
                category=TestCategory.BASIC_INTENT,
                expected_keywords=["factual", "question", "answer"],
                description="Direct factual question"
            ),
            TestCase(
                name="how_question",
                message="How does machine learning work?",
                expected_intent=MessageIntent.QUESTION_ANSWER,
                min_confidence=0.8,
                category=TestCategory.BASIC_INTENT,
                description="Technical how-to question"
            ),
            TestCase(
                name="definition_question",
                message="What does API mean?",
                expected_intent=MessageIntent.QUESTION_ANSWER,
                min_confidence=0.8,
                category=TestCategory.BASIC_INTENT,
                description="Definition request"
            ),
            
            # === PROJECT QUERY TESTS ===
            TestCase(
                name="project_status",
                message="What's the status of the PsyOPTIMAL project?",
                expected_intent=MessageIntent.PROJECT_QUERY,
                min_confidence=0.8,
                category=TestCategory.BASIC_INTENT,
                expected_keywords=["project", "work", "status"],
                description="Project status inquiry"
            ),
            TestCase(
                name="task_priorities",
                message="What are my highest priority tasks?",
                expected_intent=MessageIntent.PROJECT_QUERY,
                min_confidence=0.8,
                category=TestCategory.BASIC_INTENT,
                description="Priority task query"
            ),
            TestCase(
                name="deadline_question",
                message="When is the FlexTrip deadline?",
                expected_intent=MessageIntent.PROJECT_QUERY,
                min_confidence=0.7,
                category=TestCategory.BASIC_INTENT,
                description="Deadline inquiry"
            ),
            
            # === DEVELOPMENT TASK TESTS ===
            TestCase(
                name="code_request",
                message="Can you help me write a Python function to parse JSON?",
                expected_intent=MessageIntent.DEVELOPMENT_TASK,
                min_confidence=0.8,
                category=TestCategory.BASIC_INTENT,
                expected_keywords=["code", "programming", "implementation"],
                description="Code writing request"
            ),
            TestCase(
                name="bug_fix",
                message="There's a bug in the authentication system, can you fix it?",
                expected_intent=MessageIntent.DEVELOPMENT_TASK,
                min_confidence=0.8,
                category=TestCategory.BASIC_INTENT,
                description="Bug fix request"
            ),
            TestCase(
                name="debug_help",
                message="My tests are failing, can you help debug?",
                expected_intent=MessageIntent.DEVELOPMENT_TASK,
                min_confidence=0.7,
                category=TestCategory.BASIC_INTENT,
                description="Debugging assistance"
            ),
            
            # === IMAGE GENERATION TESTS ===
            TestCase(
                name="image_creation",
                message="Generate an image of a sunset over mountains",
                expected_intent=MessageIntent.IMAGE_GENERATION,
                min_confidence=0.8,
                category=TestCategory.BASIC_INTENT,
                expected_keywords=["image", "create", "generate"],
                description="Image creation request"
            ),
            TestCase(
                name="artwork_request",
                message="Create artwork showing a futuristic city",
                expected_intent=MessageIntent.IMAGE_GENERATION,
                min_confidence=0.8,
                category=TestCategory.BASIC_INTENT,
                description="Artwork generation"
            ),
            
            # === IMAGE ANALYSIS TESTS ===
            TestCase(
                name="image_analysis",
                message="[IMAGE] What do you see in this photo?",
                expected_intent=MessageIntent.IMAGE_ANALYSIS,
                min_confidence=0.9,
                category=TestCategory.BASIC_INTENT,
                context={"has_image": True},
                expected_keywords=["analyze", "image", "vision"],
                description="Image analysis with marker"
            ),
            TestCase(
                name="photo_description",
                message="[PHOTO] Can you describe this image?",
                expected_intent=MessageIntent.IMAGE_ANALYSIS,
                min_confidence=0.9,
                category=TestCategory.BASIC_INTENT,
                context={"has_image": True},
                description="Photo description request"
            ),
            
            # === WEB SEARCH TESTS ===
            TestCase(
                name="current_events",
                message="What's the latest news about AI?",
                expected_intent=MessageIntent.WEB_SEARCH,
                min_confidence=0.7,
                category=TestCategory.BASIC_INTENT,
                expected_keywords=["current", "search", "web"],
                description="Current events inquiry"
            ),
            TestCase(
                name="recent_info",
                message="What happened today in tech?",
                expected_intent=MessageIntent.WEB_SEARCH,
                min_confidence=0.7,
                category=TestCategory.BASIC_INTENT,
                description="Recent information request"
            ),
            
            # === LINK ANALYSIS TESTS ===
            TestCase(
                name="url_analysis",
                message="https://example.com/article - what's this about?",
                expected_intent=MessageIntent.LINK_ANALYSIS,
                min_confidence=0.9,
                category=TestCategory.BASIC_INTENT,
                context={"has_links": True},
                expected_keywords=["link", "url", "analyze"],
                description="URL analysis request"
            ),
            TestCase(
                name="website_summary",
                message="Can you summarize this website: https://github.com/project",
                expected_intent=MessageIntent.LINK_ANALYSIS,
                min_confidence=0.8,
                category=TestCategory.BASIC_INTENT,
                context={"has_links": True},
                description="Website summary request"
            ),
            
            # === SYSTEM HEALTH TESTS ===
            TestCase(
                name="ping_command",
                message="ping",
                expected_intent=MessageIntent.SYSTEM_HEALTH,
                min_confidence=1.0,
                category=TestCategory.BASIC_INTENT,
                expected_keywords=["system", "health", "status"],
                description="Ping command"
            ),
            TestCase(
                name="status_check",
                message="What's your status?",
                expected_intent=MessageIntent.SYSTEM_HEALTH,
                min_confidence=0.7,
                category=TestCategory.BASIC_INTENT,
                description="Status inquiry"
            ),
            
            # === EDGE CASES ===
            TestCase(
                name="empty_message",
                message="",
                expected_intent=MessageIntent.UNCLEAR,
                min_confidence=1.0,
                category=TestCategory.EDGE_CASES,
                description="Empty message"
            ),
            TestCase(
                name="only_punctuation",
                message="!@#$%^&*()",
                expected_intent=MessageIntent.UNCLEAR,
                min_confidence=0.5,
                category=TestCategory.EDGE_CASES,
                description="Only punctuation"
            ),
            TestCase(
                name="mixed_intent",
                message="Hi! Can you check my project status and also generate an image?",
                expected_intent=MessageIntent.PROJECT_QUERY,  # Should prioritize work-related
                min_confidence=0.6,
                category=TestCategory.EDGE_CASES,
                description="Mixed intent message"
            ),
            TestCase(
                name="very_long_message",
                message="This is a very long message " * 50 + "that asks about project status",
                expected_intent=MessageIntent.PROJECT_QUERY,
                min_confidence=0.6,
                category=TestCategory.EDGE_CASES,
                description="Very long message"
            ),
            
            # === GROUP CHAT CONTEXT ===
            TestCase(
                name="group_mention",
                message="@valorengels what's the project status?",
                expected_intent=MessageIntent.PROJECT_QUERY,
                min_confidence=0.7,
                category=TestCategory.INTEGRATION,
                context={"is_group_chat": True},
                description="Group chat mention with question"
            ),
            
            # === MULTILINGUAL TESTS ===
            TestCase(
                name="spanish_greeting",
                message="¡Hola! ¿Cómo estás?",
                expected_intent=MessageIntent.CASUAL_CHAT,
                min_confidence=0.6,
                category=TestCategory.EDGE_CASES,
                description="Spanish greeting"
            ),
        ]
    
    async def run_intent_classification_tests(self) -> Dict[str, Any]:
        """Run all intent classification tests."""
        print("🧠 Running Intent Classification Tests...")
        print("=" * 60)
        
        results = {
            "total_tests": len(self.test_cases),
            "passed": 0,
            "failed": 0,
            "failures": [],
            "performance_metrics": {},
            "category_results": {}
        }
        
        for test_case in self.test_cases:
            print(f"\n🔍 Testing: {test_case.name}")
            print(f"   Message: '{test_case.message[:50]}{'...' if len(test_case.message) > 50 else ''}'")
            print(f"   Expected: {test_case.expected_intent.value}")
            
            try:
                # Run intent classification
                import time
                start_time = time.time()
                
                intent_result = await classify_message_intent(
                    test_case.message, 
                    test_case.context or {}
                )
                
                end_time = time.time()
                duration = end_time - start_time
                
                # Check results
                intent_match = intent_result.intent == test_case.expected_intent
                confidence_ok = intent_result.confidence >= test_case.min_confidence
                
                if intent_match and confidence_ok:
                    print(f"   ✅ PASS - {intent_result.intent.value} ({intent_result.confidence:.2f})")
                    results["passed"] += 1
                else:
                    print(f"   ❌ FAIL - Got {intent_result.intent.value} ({intent_result.confidence:.2f})")
                    results["failed"] += 1
                    results["failures"].append({
                        "test": test_case.name,
                        "expected": test_case.expected_intent.value,
                        "actual": intent_result.intent.value,
                        "confidence": intent_result.confidence,
                        "reason": "Intent mismatch" if not intent_match else "Low confidence"
                    })
                
                # Track performance
                results["performance_metrics"][test_case.name] = duration
                
                # Track by category
                category = test_case.category.value
                if category not in results["category_results"]:
                    results["category_results"][category] = {"passed": 0, "total": 0}
                results["category_results"][category]["total"] += 1
                if intent_match and confidence_ok:
                    results["category_results"][category]["passed"] += 1
                    
            except Exception as e:
                print(f"   💥 ERROR - {e}")
                results["failed"] += 1
                results["failures"].append({
                    "test": test_case.name,
                    "error": str(e),
                    "reason": "Exception during classification"
                })
        
        return results
    
    async def run_prompt_generation_tests(self) -> Dict[str, Any]:
        """Test prompt generation for different intents."""
        print("\n🎯 Running Prompt Generation Tests...")
        print("=" * 60)
        
        results = {
            "total_intents": len(MessageIntent),
            "prompt_tests": {},
            "prompt_quality": {}
        }
        
        for intent in MessageIntent:
            print(f"\n🎨 Testing prompt generation for: {intent.value}")
            
            try:
                # Create mock intent result
                mock_intent_result = IntentResult(
                    intent=intent,
                    confidence=0.9,
                    reasoning=f"Test case for {intent.value}",
                    suggested_emoji="🤖"
                )
                
                # Test different contexts
                contexts = [
                    {"chat_id": 12345, "username": "testuser", "is_group_chat": False},
                    {"chat_id": 67890, "username": "groupuser", "is_group_chat": True},
                    {"chat_id": 11111, "username": None, "is_group_chat": False, "has_image": True},
                    {"chat_id": 22222, "username": "linkuser", "is_group_chat": False, "has_links": True},
                ]
                
                for i, context in enumerate(contexts):
                    try:
                        prompt = get_intent_system_prompt(mock_intent_result, context)
                        
                        # Basic validation
                        if prompt and len(prompt) > 50:
                            print(f"   ✅ Context {i+1}: Generated {len(prompt)} char prompt")
                        else:
                            print(f"   ⚠️  Context {i+1}: Short prompt ({len(prompt) if prompt else 0} chars)")
                        
                        # Store for analysis
                        if intent.value not in results["prompt_tests"]:
                            results["prompt_tests"][intent.value] = []
                        results["prompt_tests"][intent.value].append({
                            "context": context,
                            "prompt_length": len(prompt) if prompt else 0,
                            "has_intent_keywords": intent.value.lower() in prompt.lower() if prompt else False
                        })
                        
                    except Exception as e:
                        print(f"   ❌ Context {i+1}: Error - {e}")
                        
            except Exception as e:
                print(f"   💥 Failed to test {intent.value}: {e}")
        
        return results
    
    async def run_integration_tests(self) -> Dict[str, Any]:
        """Test complete intent-to-response pipeline."""
        print("\n🚀 Running Integration Tests...")
        print("=" * 60)
        
        results = {
            "integration_tests": [],
            "response_quality": {},
            "pipeline_performance": {}
        }
        
        # Select representative test cases for integration testing
        integration_cases = [
            tc for tc in self.test_cases 
            if tc.category in [TestCategory.BASIC_INTENT, TestCategory.INTEGRATION]
        ][:10]  # Limit to 10 for performance
        
        for test_case in integration_cases:
            print(f"\n🔄 Integration test: {test_case.name}")
            
            try:
                import time
                start_time = time.time()
                
                # Run complete pipeline
                response = await handle_telegram_message_with_intent(
                    message=test_case.message,
                    chat_id=12345,
                    username="testuser",
                    is_group_chat=test_case.context and test_case.context.get("is_group_chat", False),
                    chat_history_obj=None,
                    notion_data=None,
                    is_priority_question=False,
                    intent_result=None  # Let it classify naturally
                )
                
                end_time = time.time()
                duration = end_time - start_time
                
                # Validate response
                if response and len(response) > 10:
                    print(f"   ✅ Generated response ({len(response)} chars in {duration:.2f}s)")
                    print(f"   Preview: {response[:100]}...")
                else:
                    print(f"   ⚠️  Short/empty response ({len(response) if response else 0} chars)")
                
                results["integration_tests"].append({
                    "test": test_case.name,
                    "message": test_case.message,
                    "response_length": len(response) if response else 0,
                    "duration": duration,
                    "success": bool(response and len(response) > 10)
                })
                
            except Exception as e:
                print(f"   💥 Integration test failed: {e}")
                results["integration_tests"].append({
                    "test": test_case.name,
                    "message": test_case.message,
                    "error": str(e),
                    "success": False
                })
        
        return results
    
    async def run_performance_benchmarks(self) -> Dict[str, Any]:
        """Run performance benchmarks for the intent system."""
        print("\n⚡ Running Performance Benchmarks...")
        print("=" * 60)
        
        results = {
            "classification_speed": {},
            "prompt_generation_speed": {},
            "memory_usage": {},
            "concurrent_performance": {}
        }
        
        # Test classification speed
        test_messages = [
            "Hello there!",
            "What's the project status?",
            "Can you help me debug this code?",
            "Generate an image of a cat",
            "https://example.com what's this about?"
        ]
        
        print("🏃 Testing classification speed...")
        import time
        
        for msg in test_messages:
            times = []
            for _ in range(5):  # Average of 5 runs
                start = time.time()
                await classify_message_intent(msg, {})
                end = time.time()
                times.append(end - start)
            
            avg_time = sum(times) / len(times)
            results["classification_speed"][msg[:20]] = avg_time
            print(f"   {msg[:30]:30} - {avg_time:.3f}s avg")
        
        # Test concurrent performance
        print("\n🔀 Testing concurrent classification...")
        start = time.time()
        
        tasks = [classify_message_intent(msg, {}) for msg in test_messages * 3]
        await asyncio.gather(*tasks)
        
        end = time.time()
        concurrent_time = end - start
        results["concurrent_performance"]["15_messages"] = concurrent_time
        print(f"   15 concurrent classifications: {concurrent_time:.3f}s")
        
        return results
    
    def generate_test_report(self, all_results: Dict[str, Any]) -> str:
        """Generate comprehensive test report."""
        report = [
            "# Intent Classification and Prompt Generation Test Report",
            "=" * 60,
            f"Generated at: {__import__('datetime').datetime.now()}",
            "",
        ]
        
        # Intent Classification Results
        intent_results = all_results.get("intent_classification", {})
        if intent_results:
            report.extend([
                "## Intent Classification Results",
                f"Total Tests: {intent_results['total_tests']}",
                f"Passed: {intent_results['passed']} ({intent_results['passed']/intent_results['total_tests']*100:.1f}%)",
                f"Failed: {intent_results['failed']} ({intent_results['failed']/intent_results['total_tests']*100:.1f}%)",
                "",
                "### Results by Category:",
            ])
            
            for category, stats in intent_results.get("category_results", {}).items():
                success_rate = stats["passed"] / stats["total"] * 100
                report.append(f"- {category}: {stats['passed']}/{stats['total']} ({success_rate:.1f}%)")
            
            if intent_results.get("failures"):
                report.extend(["", "### Failures:"])
                for failure in intent_results["failures"][:5]:  # Show first 5
                    report.append(f"- {failure['test']}: {failure.get('reason', 'Error')}")
        
        # Performance Results
        perf_results = all_results.get("performance", {})
        if perf_results:
            report.extend([
                "",
                "## Performance Benchmarks",
                "### Classification Speed (avg):",
            ])
            
            for msg, time in perf_results.get("classification_speed", {}).items():
                report.append(f"- {msg}: {time:.3f}s")
            
            concurrent = perf_results.get("concurrent_performance", {})
            if concurrent:
                report.extend([
                    "",
                    "### Concurrent Performance:",
                    f"- 15 concurrent classifications: {concurrent.get('15_messages', 0):.3f}s"
                ])
        
        # Integration Results
        integration_results = all_results.get("integration", {})
        if integration_results:
            tests = integration_results.get("integration_tests", [])
            successful = len([t for t in tests if t.get("success")])
            report.extend([
                "",
                "## Integration Test Results",
                f"Pipeline Tests: {successful}/{len(tests)} successful",
            ])
        
        return "\n".join(report)
    
    async def run_all_tests(self) -> Dict[str, Any]:
        """Run complete test suite."""
        print("🚀 Starting Comprehensive Intent-Prompt Test Suite")
        print("=" * 80)
        
        all_results = {}
        
        try:
            # Run intent classification tests
            all_results["intent_classification"] = await self.run_intent_classification_tests()
            
            # Run prompt generation tests
            all_results["prompt_generation"] = await self.run_prompt_generation_tests()
            
            # Run integration tests
            all_results["integration"] = await self.run_integration_tests()
            
            # Run performance benchmarks
            all_results["performance"] = await self.run_performance_benchmarks()
            
            # Generate report
            report = self.generate_test_report(all_results)
            
            print("\n" + "=" * 80)
            print("📊 TEST SUITE COMPLETE")
            print("=" * 80)
            print(report)
            
            return all_results
            
        except Exception as e:
            print(f"💥 Test suite failed: {e}")
            import traceback
            traceback.print_exc()
            return {"error": str(e)}


# Pytest integration
class TestIntentPromptCombinations:
    """Pytest-compatible test class."""
    
    @pytest.fixture(scope="class")
    def test_suite(self):
        return IntentPromptTestSuite()
    
    @pytest.mark.asyncio
    async def test_intent_classification(self, test_suite):
        """Test intent classification accuracy."""
        results = await test_suite.run_intent_classification_tests()
        
        # Assert minimum success rate
        success_rate = results["passed"] / results["total_tests"]
        assert success_rate >= 0.7, f"Intent classification success rate {success_rate:.2f} below threshold"
        
        # Assert no critical failures
        critical_failures = [f for f in results["failures"] if "ERROR" in f.get("reason", "")]
        assert len(critical_failures) == 0, f"Critical failures: {critical_failures}"
    
    @pytest.mark.asyncio
    async def test_prompt_generation(self, test_suite):
        """Test prompt generation for all intents."""
        results = await test_suite.run_prompt_generation_tests()
        
        # Assert all intents have prompts
        assert len(results["prompt_tests"]) >= len(MessageIntent) - 1  # Allow for some missing
        
        # Assert prompt quality
        for intent, tests in results["prompt_tests"].items():
            assert len(tests) > 0, f"No prompts generated for {intent}"
            assert any(t["prompt_length"] > 50 for t in tests), f"All prompts too short for {intent}"
    
    @pytest.mark.asyncio
    async def test_integration_pipeline(self, test_suite):
        """Test complete intent-to-response pipeline."""
        results = await test_suite.run_integration_tests()
        
        tests = results["integration_tests"]
        successful = [t for t in tests if t.get("success")]
        
        # Assert minimum success rate for integration
        success_rate = len(successful) / len(tests) if tests else 0
        assert success_rate >= 0.6, f"Integration success rate {success_rate:.2f} below threshold"
    
    @pytest.mark.asyncio
    async def test_performance_benchmarks(self, test_suite):
        """Test performance meets requirements."""
        results = await test_suite.run_performance_benchmarks()
        
        # Assert classification speed
        speeds = results["classification_speed"]
        if speeds:
            max_time = max(speeds.values())
            assert max_time < 2.0, f"Classification too slow: {max_time:.3f}s"
        
        # Assert concurrent performance
        concurrent = results["concurrent_performance"]
        if "15_messages" in concurrent:
            assert concurrent["15_messages"] < 10.0, f"Concurrent processing too slow"


# CLI interface
async def main():
    """Main CLI interface for running tests."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Intent-Prompt Test Suite")
    parser.add_argument("--category", choices=[c.value for c in TestCategory], 
                       help="Run tests for specific category only")
    parser.add_argument("--performance", action="store_true", 
                       help="Run performance benchmarks only")
    parser.add_argument("--integration", action="store_true", 
                       help="Run integration tests only")
    parser.add_argument("--report", type=str, 
                       help="Save report to file")
    
    args = parser.parse_args()
    
    suite = IntentPromptTestSuite()
    
    if args.performance:
        results = await suite.run_performance_benchmarks()
    elif args.integration:
        results = await suite.run_integration_tests()
    else:
        results = await suite.run_all_tests()
    
    if args.report:
        report = suite.generate_test_report(results)
        with open(args.report, 'w') as f:
            f.write(report)
        print(f"📄 Report saved to {args.report}")


if __name__ == "__main__":
    asyncio.run(main())