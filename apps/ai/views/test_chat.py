"""Simple test view for AI chat without database."""

from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.ai.agent.chat import ChatDependencies
from apps.ai.agent.chat import ChatSession as AgentChatSession
from apps.ai.agent.chat import process_chat_message_sync
from apps.ai.llm.providers import get_openai_model


@method_decorator(csrf_exempt, name="dispatch")
class TestChatView(View):
    """Test chat endpoint that doesn't use database."""

    def post(self, request):
        """Process a chat message without database storage."""
        import json

        # Check if it's form data (HTMX) or JSON
        if request.content_type == "application/json":
            try:
                data = json.loads(request.body)
                message = data.get("message", "")
            except json.JSONDecodeError:
                return JsonResponse({"error": "Invalid JSON"}, status=400)
        else:
            # Handle form data from HTMX
            message = request.POST.get("message", "")

        if not message:
            return JsonResponse({"error": "Message is required"}, status=400)

        # Create in-memory session
        agent_session = AgentChatSession(
            session_id="test-session", user_id=None, messages=[]
        )

        # Get response from AI
        deps = ChatDependencies(session_id="test-session")

        try:
            model = get_openai_model()
            response = process_chat_message_sync(
                message, agent_session, deps=deps, model=model
            )
        except Exception as e:
            # Fallback response if OpenAI not configured
            response = (
                f"AI service not configured. Please set OPENAI_API_KEY. Error: {str(e)}"
            )

        return JsonResponse({"message": message, "response": response})
