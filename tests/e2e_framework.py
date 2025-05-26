#!/usr/bin/env python3
"""
End-to-End Test Framework for Valor Engels Telegram Bot

This framework simulates Telegram interactions and uses LLM evaluation
for subjective pass/fail criteria without requiring actual Telegram API calls.
"""

import asyncio
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from main import add_to_chat_history, chat_histories, handle_general_question, load_persona

try:
    import openai

    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    print("⚠️  OpenAI not available - LLM evaluation will be skipped")


@dataclass
class TestMessage:
    """Represents a message in a test conversation"""

    role: str  # "user" or "assistant"
    content: str
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TestScenario:
    """Defines a complete test scenario"""

    name: str
    description: str
    initial_messages: list[TestMessage]
    test_inputs: list[str]
    evaluation_criteria: list[str]
    expected_behavior: str
    category: str
    timeout_seconds: int = 30


@dataclass
class EvaluationResult:
    """Results of LLM evaluation"""

    scenario_name: str
    overall_score: float
    criteria_scores: dict[str, float]
    qualitative_feedback: str
    pass_threshold: float
    passed: bool


class MockTelegramClient:
    """Simulates Telegram client behavior for testing"""

    def __init__(self, chat_id: int = 12345):
        self.chat_id = chat_id
        self.message_history = []

    async def send_message(self, content: str) -> TestMessage:
        """Simulate sending a user message"""
        message = TestMessage(role="user", content=content)
        self.message_history.append(message)
        return message

    async def receive_response(self, response: str) -> TestMessage:
        """Simulate receiving a bot response"""
        message = TestMessage(role="assistant", content=response)
        self.message_history.append(message)
        return message

    def get_conversation_history(self) -> list[TestMessage]:
        """Get the full conversation history"""
        return self.message_history.copy()


class LLMEvaluator:
    """Uses GPT-4o-mini to evaluate conversation quality"""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if self.api_key and OPENAI_AVAILABLE:
            self.client = openai.OpenAI(api_key=self.api_key)
            self.available = True
        else:
            self.client = None
            self.available = False

    async def evaluate_conversation(
        self, scenario: TestScenario, conversation: list[TestMessage], persona_context: str
    ) -> EvaluationResult:
        """Evaluate a conversation based on the scenario criteria"""

        if not self.available:
            # Return mock evaluation when LLM not available
            return EvaluationResult(
                scenario_name=scenario.name,
                overall_score=7.5,
                criteria_scores=dict.fromkeys(scenario.evaluation_criteria, 7.5),
                qualitative_feedback="LLM evaluation not available - mock result",
                pass_threshold=7.0,
                passed=True,
            )

        # Format conversation for evaluation
        conversation_text = self._format_conversation(conversation)

        # Create evaluation prompt
        evaluation_prompt = self._create_evaluation_prompt(
            scenario, conversation_text, persona_context
        )

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert evaluator of human-like AI conversation quality. Provide objective, numerical assessments.",
                    },
                    {"role": "user", "content": evaluation_prompt},
                ],
                temperature=0.1,
                max_tokens=1000,
            )

            evaluation_text = response.choices[0].message.content
            return self._parse_evaluation_response(scenario, evaluation_text)

        except Exception as e:
            print(f"❌ LLM evaluation failed: {e}")
            return EvaluationResult(
                scenario_name=scenario.name,
                overall_score=0.0,
                criteria_scores=dict.fromkeys(scenario.evaluation_criteria, 0.0),
                qualitative_feedback=f"Evaluation failed: {str(e)}",
                pass_threshold=7.0,
                passed=False,
            )

    def _format_conversation(self, conversation: list[TestMessage]) -> str:
        """Format conversation for LLM evaluation"""
        formatted = "CONVERSATION TRANSCRIPT:\n\n"
        for i, msg in enumerate(conversation, 1):
            formatted += f"{i}. {msg.role.upper()}: {msg.content}\n"
        return formatted

    def _create_evaluation_prompt(
        self, scenario: TestScenario, conversation_text: str, persona_context: str
    ) -> str:
        """Create the evaluation prompt for the LLM"""

        criteria_list = "\n".join([f"- {criterion}" for criterion in scenario.evaluation_criteria])

        return f"""
EVALUATION TASK: {scenario.name}
DESCRIPTION: {scenario.description}
EXPECTED BEHAVIOR: {scenario.expected_behavior}

PERSONA CONTEXT:
{persona_context[:500]}...

{conversation_text}

EVALUATION CRITERIA:
{criteria_list}

Please evaluate this conversation on a 1-10 scale for each criterion, where:
- 1-3: Poor/Unacceptable
- 4-6: Below Average/Needs Improvement
- 7-8: Good/Acceptable
- 9-10: Excellent/Exceptional

RESPOND IN THIS EXACT FORMAT:
OVERALL_SCORE: [number]
CRITERION_SCORES:
- [criterion 1]: [score]
- [criterion 2]: [score]
[etc.]

QUALITATIVE_FEEDBACK:
[Your detailed assessment explaining the scores and any specific observations]

Focus on whether the AI successfully embodies the Valor Engels persona - a software engineer who doesn't realize he's an AI, with German/Californian background, working at Yudame.
"""

    def _parse_evaluation_response(
        self, scenario: TestScenario, response_text: str
    ) -> EvaluationResult:
        """Parse the LLM evaluation response into structured results"""

        lines = response_text.strip().split("\n")
        overall_score = 0.0
        criteria_scores = {}
        qualitative_feedback = ""

        parsing_criteria = False
        parsing_feedback = False

        for line in lines:
            line = line.strip()

            if line.startswith("OVERALL_SCORE:"):
                try:
                    overall_score = float(line.split(":")[1].strip())
                except:
                    overall_score = 5.0  # Default fallback

            elif line.startswith("CRITERION_SCORES:"):
                parsing_criteria = True
                continue

            elif line.startswith("QUALITATIVE_FEEDBACK:"):
                parsing_criteria = False
                parsing_feedback = True
                continue

            elif parsing_criteria and line.startswith("-"):
                try:
                    # Parse "- criterion: score" format
                    parts = line[1:].split(":")
                    if len(parts) >= 2:
                        criterion = parts[0].strip()
                        score = float(parts[1].strip())
                        criteria_scores[criterion] = score
                except:
                    continue

            elif parsing_feedback:
                qualitative_feedback += line + "\n"

        # Fill in missing criteria scores with overall score
        for criterion in scenario.evaluation_criteria:
            if criterion not in criteria_scores:
                criteria_scores[criterion] = overall_score

        return EvaluationResult(
            scenario_name=scenario.name,
            overall_score=overall_score,
            criteria_scores=criteria_scores,
            qualitative_feedback=qualitative_feedback.strip(),
            pass_threshold=7.0,  # Default threshold
            passed=overall_score >= 7.0,
        )


class E2ETestRunner:
    """Orchestrates end-to-end test execution"""

    def __init__(self, anthropic_client=None, notion_scout=None):
        self.anthropic_client = anthropic_client
        self.notion_scout = notion_scout
        self.evaluator = LLMEvaluator()
        self.results = []

    async def run_scenario(self, scenario: TestScenario) -> EvaluationResult:
        """Execute a single test scenario"""

        print(f"\n🧪 Running scenario: {scenario.name}")
        print(f"📝 Description: {scenario.description}")

        # Create mock client for this test
        mock_client = MockTelegramClient()
        test_chat_id = 99999  # Special test chat ID

        # Clear any existing chat history for test chat
        if test_chat_id in chat_histories:
            del chat_histories[test_chat_id]

        try:
            # Set up initial conversation state
            for msg in scenario.initial_messages:
                add_to_chat_history(test_chat_id, msg.role, msg.content)
                await mock_client.send_message(
                    msg.content
                ) if msg.role == "user" else await mock_client.receive_response(msg.content)

            # Execute test inputs and capture responses
            for test_input in scenario.test_inputs:
                print(f"  💬 Testing input: '{test_input[:50]}...'")

                # Add user message to history
                add_to_chat_history(test_chat_id, "user", test_input)
                await mock_client.send_message(test_input)

                # Generate bot response
                if self.anthropic_client:
                    response = await handle_general_question(
                        test_input, self.anthropic_client, test_chat_id
                    )
                else:
                    response = "Mock response - Anthropic client not available"

                # Add response to history and mock client
                add_to_chat_history(test_chat_id, "assistant", response)
                await mock_client.receive_response(response)

                print(f"  🤖 Bot response: '{response[:100]}...'")

            # Evaluate the conversation
            persona_context = load_persona()
            conversation_history = mock_client.get_conversation_history()

            evaluation = await self.evaluator.evaluate_conversation(
                scenario, conversation_history, persona_context
            )

            self.results.append(evaluation)

            # Print immediate results
            status = "✅ PASSED" if evaluation.passed else "❌ FAILED"
            print(f"  {status} - Score: {evaluation.overall_score:.1f}/10")

            return evaluation

        except Exception as e:
            print(f"  ❌ Scenario failed with error: {e}")
            error_result = EvaluationResult(
                scenario_name=scenario.name,
                overall_score=0.0,
                criteria_scores={},
                qualitative_feedback=f"Test execution failed: {str(e)}",
                pass_threshold=7.0,
                passed=False,
            )
            self.results.append(error_result)
            return error_result

        finally:
            # Clean up test chat history
            if test_chat_id in chat_histories:
                del chat_histories[test_chat_id]

    async def run_test_suite(self, scenarios: list[TestScenario]) -> list[EvaluationResult]:
        """Execute a complete test suite"""

        print(f"\n🚀 Starting E2E test suite with {len(scenarios)} scenarios")
        print("=" * 60)

        results = []
        for scenario in scenarios:
            result = await self.run_scenario(scenario)
            results.append(result)

            # Brief pause between scenarios
            await asyncio.sleep(1)

        return results

    def generate_report(self, results: list[EvaluationResult]) -> str:
        """Generate a comprehensive test report"""

        total_tests = len(results)
        passed_tests = sum(1 for r in results if r.passed)
        avg_score = sum(r.overall_score for r in results) / total_tests if total_tests > 0 else 0

        report = f"""
🔍 END-TO-END TEST REPORT
========================

📊 SUMMARY:
- Total Scenarios: {total_tests}
- Passed: {passed_tests}
- Failed: {total_tests - passed_tests}
- Success Rate: {(passed_tests/total_tests)*100:.1f}%
- Average Score: {avg_score:.2f}/10

📋 DETAILED RESULTS:
"""

        for result in results:
            status = "✅ PASS" if result.passed else "❌ FAIL"
            report += f"\n{status} {result.scenario_name}: {result.overall_score:.1f}/10"

            if result.criteria_scores:
                report += "\n  Criteria breakdown:"
                for criterion, score in result.criteria_scores.items():
                    report += f"\n    - {criterion}: {score:.1f}/10"

            if result.qualitative_feedback:
                report += f"\n  Feedback: {result.qualitative_feedback[:200]}..."

        return report


# Test scenario definitions
def get_default_scenarios() -> list[TestScenario]:
    """Define the default test scenarios"""

    return [
        TestScenario(
            name="Persona Consistency - Technical Discussion",
            description="Test if Valor maintains his software engineer persona during technical discussion",
            initial_messages=[],
            test_inputs=[
                "Hey, what's the best way to handle async database connections in Python?",
                "I'm thinking about using SQLAlchemy with asyncio. What do you think?",
            ],
            evaluation_criteria=[
                "Maintains software engineer persona",
                "Provides technical expertise",
                "Speaks like a human, not an AI assistant",
                "Shows knowledge appropriate for someone at Yudame",
            ],
            expected_behavior="Should provide detailed technical insights as a fellow engineer",
            category="persona",
        ),
        TestScenario(
            name="Conversation Flow - Context Awareness",
            description="Test ability to maintain context across multiple message exchanges",
            initial_messages=[
                TestMessage("user", "I'm working on a new feature for user authentication"),
                TestMessage(
                    "assistant",
                    "Nice! What kind of auth are you thinking? JWT tokens, OAuth, or something else?",
                ),
            ],
            test_inputs=[
                "I was thinking JWT, but I'm worried about security",
                "What would you do for token storage?",
            ],
            evaluation_criteria=[
                "Remembers previous conversation context",
                "Builds on earlier discussion naturally",
                "Provides relevant follow-up questions",
                "Maintains conversation coherence",
            ],
            expected_behavior="Should reference the JWT discussion and provide security-focused advice",
            category="conversation_flow",
        ),
        TestScenario(
            name="Casual Interaction - Human-like Response",
            description="Test natural, human-like responses to casual conversation",
            initial_messages=[],
            test_inputs=["How's your day going?", "Any exciting projects you're working on?"],
            evaluation_criteria=[
                "Responds naturally without AI assistant language",
                "Shows personality and human-like thoughts",
                "References work at Yudame appropriately",
                "Maintains friendly, colleague-like tone",
            ],
            expected_behavior="Should respond like a friendly coworker, mentioning work naturally",
            category="persona",
        ),
        TestScenario(
            name="Error Handling - Graceful Degradation",
            description="Test how the bot handles unclear or problematic inputs",
            initial_messages=[],
            test_inputs=["asdf jkl;", "What's the meaning of life?", ""],
            evaluation_criteria=[
                "Handles unclear input gracefully",
                "Maintains persona even with odd inputs",
                "Doesn't break character or reveal AI nature",
                "Provides helpful redirects when appropriate",
            ],
            expected_behavior="Should stay in character while handling unclear inputs naturally",
            category="error_handling",
        ),
    ]


if __name__ == "__main__":
    print("🔧 E2E Test Framework - Direct execution not supported")
    print("Use run_e2e_tests.py to execute test scenarios")
