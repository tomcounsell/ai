#!/usr/bin/env python3
"""
Valor Conversation Test Suite

Tests Valor's conversation abilities using real LLM calls and GPT-based evaluation.
Mocks Telegram conversations to test various scenarios.
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import openai

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))

from dotenv import load_dotenv

from integrations.notion.scout import NotionScout
from integrations.telegram.chat_history import ChatHistoryManager
from agents.telegram_chat_agent import handle_telegram_message

load_dotenv()


class MockMessage:
    """Mock Telegram message for testing"""

    def __init__(self, text: str, chat_id: int = 12345):
        self.text = text
        self.chat_id = chat_id
        self.id = int(time.time() * 1000000)  # Unique ID

    async def reply(self, text: str):
        """Mock reply method"""
        print(f"[VALOR REPLY]: {text}")
        return text


class ConversationTester:
    """Test suite for Valor's conversation abilities"""

    def __init__(self):
        self.openai_client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.notion_scout = None
        self.test_results = []
        self.test_chat_id = 99999  # Use unique chat ID for tests
        self.chat_history = ChatHistoryManager()  # Initialize chat history

        # Initialize NotionScout if keys available
        notion_key = os.getenv("NOTION_API_KEY")
        anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        if notion_key and anthropic_key:
            self.notion_scout = NotionScout(notion_key, anthropic_key)
            print("✅ NotionScout initialized for testing")
        else:
            print("⚠️  NotionScout not available for testing")

    async def send_message_to_valor(self, message: str, message_type: str = "general") -> str:
        """Send a message to Valor and get response"""
        try:
            # Add user message to chat history
            self.chat_history.add_message(self.test_chat_id, "user", message)

            # Use the new PydanticAI telegram chat agent
            response = await handle_telegram_message(
                message=message,
                chat_id=self.test_chat_id,
                username="test_user",
                is_group_chat=False,
                chat_history_obj=self.chat_history,
                notion_data=None,
                is_priority_question=(message_type == "priority")
            )

            # Add Valor's response to chat history
            self.chat_history.add_message(self.test_chat_id, "assistant", response)

            return response

        except Exception as e:
            return f"Error: {str(e)}"

    async def evaluate_response_with_gpt(
        self, user_message: str, valor_response: str, expected_criteria: dict[str, str]
    ) -> dict[str, Any]:
        """Use GPT to evaluate if Valor's response meets human-like criteria"""

        evaluation_prompt = f"""
You are evaluating a conversation between a user and "Valor Engels", who should be a human software engineer at Yudame. Valor should NOT sound like an AI assistant.

USER MESSAGE: "{user_message}"
VALOR'S RESPONSE: "{valor_response}"

EVALUATION CRITERIA:
{json.dumps(expected_criteria, indent=2)}

Evaluate Valor's response on each criterion. For each criterion, provide:
1. A score from 1-10 (10 = perfectly human-like)
2. A brief explanation of why
3. Specific suggestions for improvement if score < 8

Respond in JSON format:
{{
    "overall_score": <average score>,
    "criteria_scores": {{
        "criterion_name": {{
            "score": <1-10>,
            "explanation": "brief explanation",
            "suggestions": "improvement suggestions or 'none needed'"
        }}
    }},
    "overall_feedback": "general feedback about human-likeness",
    "passes": <true/false based on overall_score >= 7>
}}
"""

        try:
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",  # Fast and cost-effective
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert at evaluating human-like conversation quality.",
                    },
                    {"role": "user", "content": evaluation_prompt},
                ],
                temperature=0.1,  # Low temperature for consistent evaluation
                max_tokens=800,
            )

            # Parse JSON response
            evaluation = json.loads(response.choices[0].message.content)
            return evaluation

        except Exception as e:
            return {"overall_score": 0, "error": str(e), "passes": False}

    async def run_conversation_test(
        self,
        test_name: str,
        messages: list[str],
        evaluation_criteria: dict[str, str],
        message_type: str = "general",
    ) -> dict[str, Any]:
        """Run a full conversation test scenario"""

        print(f"\n🧪 Running test: {test_name}")
        print("=" * 50)

        conversation_log = []
        test_passed = True
        detailed_results = []

        for i, message in enumerate(messages):
            print(f"\n👤 USER: {message}")

            # Get Valor's response
            response = await self.send_message_to_valor(message, message_type)
            print(f"🤖 VALOR: {response}")

            # Evaluate the response
            evaluation = await self.evaluate_response_with_gpt(
                message, response, evaluation_criteria
            )

            conversation_log.append(
                {"user_message": message, "valor_response": response, "evaluation": evaluation}
            )

            detailed_results.append(evaluation)

            if not evaluation.get("passes", False):
                test_passed = False
                print(
                    f"❌ Message {i+1} failed evaluation (score: {evaluation.get('overall_score', 0)})"
                )
            else:
                print(
                    f"✅ Message {i+1} passed evaluation (score: {evaluation.get('overall_score', 0)})"
                )

        # Calculate overall test results
        avg_score = sum(r.get("overall_score", 0) for r in detailed_results) / len(detailed_results)

        result = {
            "test_name": test_name,
            "passed": test_passed,
            "average_score": avg_score,
            "conversation_log": conversation_log,
            "timestamp": datetime.now().isoformat(),
        }

        self.test_results.append(result)

        print(f"\n📊 Test Result: {'PASS' if test_passed else 'FAIL'} (avg score: {avg_score:.1f})")

        return result


# Test scenarios
TEST_SCENARIOS = [
    {
        "name": "Casual Greeting Test",
        "messages": [
            "Hey, how are you?",
            "What's today's date?",
            "Nice! What are you up to today?",
        ],
        "criteria": {
            "brevity": "Response should be brief and natural, not verbose",
            "human_like": "Should sound like a real person, not an AI assistant",
            "no_over_helping": "Should not immediately offer technical assistance",
            "casual_tone": "Should match the casual tone of the question",
            "context_appropriate": "Should give appropriate context (what they're doing, etc.)",
        },
        "type": "general",
    },
    {
        "name": "Technical Discussion Test",
        "messages": [
            "I'm having issues with JWT authentication",
            "The tokens keep expiring too quickly",
            "Should I implement refresh tokens?",
        ],
        "criteria": {
            "technical_accuracy": "Should provide accurate technical information",
            "clarifying_questions": "Should ask appropriate clarifying questions",
            "implementation_focus": "Should focus on implementation details",
            "human_expertise": "Should sound like an experienced engineer",
        },
        "type": "general",
    },
    {
        "name": "Work Priority Test",
        "messages": [
            "What should I work on next?",
            "I'm feeling overwhelmed with all these projects",
        ],
        "criteria": {
            "context_checking": "Should check conversation history for context",
            "helpful_guidance": "Should provide actionable guidance",
            "empathy": "Should show understanding for feeling overwhelmed",
            "human_response": "Should respond like a colleague, not a task manager",
        },
        "type": "priority",
    },
    {
        "name": "Mixed Conversation Test",
        "messages": [
            "Good morning!",
            "I've been working on a React component",
            "It's not rendering properly",
            "Thanks for the help!",
        ],
        "criteria": {
            "tone_adaptation": "Should adapt tone from casual to technical appropriately",
            "conversation_flow": "Should maintain natural conversation flow",
            "context_retention": "Should remember earlier parts of conversation",
            "natural_transitions": "Should transition naturally between topics",
        },
        "type": "general",
    },
]


async def main():
    """Run all conversation tests"""
    print("🚀 Starting Valor Conversation Test Suite")
    print(f"⏰ Test started at: {datetime.now()}")

    tester = ConversationTester()

    if not tester.notion_scout:
        print("⚠️  Warning: Some tests may fail without NotionScout integration")

    # Run all test scenarios
    for scenario in TEST_SCENARIOS:
        await tester.run_conversation_test(
            scenario["name"],
            scenario["messages"],
            scenario["criteria"],
            scenario.get("type", "general"),
        )

        # Brief pause between tests
        await asyncio.sleep(1)

    # Generate final report
    print("\n" + "=" * 60)
    print("📋 FINAL TEST REPORT")
    print("=" * 60)

    total_tests = len(tester.test_results)
    passed_tests = sum(1 for r in tester.test_results if r["passed"])
    avg_score = sum(r["average_score"] for r in tester.test_results) / total_tests

    print(f"Tests Run: {total_tests}")
    print(f"Tests Passed: {passed_tests}")
    print(f"Pass Rate: {passed_tests/total_tests*100:.1f}%")
    print(f"Average Score: {avg_score:.1f}/10")

    # Save detailed results
    results_file = Path(__file__).parent / f"test_results_{int(time.time())}.json"
    with open(results_file, "w") as f:
        json.dump(tester.test_results, f, indent=2)

    print(f"\n💾 Detailed results saved to: {results_file}")

    if passed_tests == total_tests:
        print("\n🎉 ALL TESTS PASSED!")
        return 0
    else:
        print(f"\n❌ {total_tests - passed_tests} TEST(S) FAILED")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
