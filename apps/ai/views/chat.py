"""HTMX views for AI chat interface."""

import json
import uuid
from typing import Optional

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.ai.agent.chat import ChatDependencies
from apps.ai.agent.chat import ChatSession as AgentChatSession
from apps.ai.agent.chat import process_chat_message_sync
from apps.ai.llm.providers import get_default_model
from apps.ai.models import ChatMessage, ChatSession
from apps.public.views.helpers import HTMXView, MainContentView


class ChatIndexView(MainContentView):
    """Main chat interface page."""

    template_name = "ai/chat/index.html"
    title = "AI Chat Assistant"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Get or create a chat session
        session_id = self.request.session.get("chat_session_id")

        if session_id:
            try:
                chat_session = ChatSession.objects.get(id=session_id)
            except ChatSession.DoesNotExist:
                chat_session = self.create_new_session()
        else:
            chat_session = self.create_new_session()

        context["chat_session"] = chat_session
        context["messages"] = chat_session.messages.all().order_by("created_at")

        # Get user's recent sessions if logged in
        if self.request.user.is_authenticated:
            context["recent_sessions"] = ChatSession.objects.filter(
                user=self.request.user
            ).order_by("-modified_at")[:5]

        return context

    def create_new_session(self) -> ChatSession:
        """Create a new chat session."""
        user = self.request.user if self.request.user.is_authenticated else None
        chat_session = ChatSession.objects.create(user=user)
        self.request.session["chat_session_id"] = str(chat_session.id)
        return chat_session


class ChatSendMessageView(HTMXView):
    """HTMX endpoint for sending chat messages."""

    template_name = "ai/chat/partials/message.html"

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        """Handle sending a new chat message."""
        message_content = request.POST.get("message", "").strip()

        if not message_content:
            return HttpResponse("Message cannot be empty", status=400)

        # Get the current chat session
        session_id = request.session.get("chat_session_id")
        if not session_id:
            return HttpResponse("No active chat session", status=400)

        try:
            chat_session = ChatSession.objects.get(id=session_id)
        except ChatSession.DoesNotExist:
            return HttpResponse("Chat session not found", status=404)

        # Save user message
        user_message = ChatMessage.objects.create(
            session=chat_session, role="user", content=message_content
        )

        # Create assistant message placeholder (will be updated via polling)
        assistant_message = ChatMessage.objects.create(
            session=chat_session,
            role="assistant",
            content="",
            is_processed=False,
            metadata={"status": "pending"},
        )

        # Process the message asynchronously (in a real app, this would be a background task)
        self.process_message_async(chat_session, user_message, assistant_message)

        # Return both messages
        context = {
            "messages": [user_message, assistant_message],
            "session": chat_session,
        }

        return render(request, self.template_name, context)

    def process_message_async(
        self,
        chat_session: ChatSession,
        user_message: ChatMessage,
        assistant_message: ChatMessage,
    ):
        """Process the message and generate AI response."""
        try:
            # Create agent session
            agent_session = AgentChatSession(
                session_id=str(chat_session.id),
                user_id=chat_session.user.id if chat_session.user else None,
                messages=[],
            )

            # Load message history
            for msg in (
                chat_session.messages.filter(is_processed=True)
                .exclude(id=user_message.id)
                .order_by("created_at")
            ):
                agent_session.add_message(msg.role, msg.content)

            # Get AI response
            deps = ChatDependencies(
                user_id=chat_session.user.id if chat_session.user else None,
                session_id=str(chat_session.id),
            )

            try:
                model = get_default_model()
            except:
                # Fallback if OpenAI key not configured
                model = None

            response = process_chat_message_sync(
                user_message.content, agent_session, deps=deps, model=model
            )

            # Update assistant message
            assistant_message.content = response
            assistant_message.is_processed = True
            assistant_message.metadata = {"status": "completed", "model": "gpt-4.1"}
            assistant_message.save()

            # Auto-generate title if this is the first message
            if chat_session.message_count == 2 and not chat_session.title:
                chat_session.title = chat_session.generate_title()
                chat_session.save()

        except Exception as e:
            # Handle errors
            assistant_message.content = (
                f"I apologize, but I encountered an error: {str(e)}"
            )
            assistant_message.is_processed = True
            assistant_message.metadata = {"status": "error", "error": str(e)}
            assistant_message.save()


class ChatPollMessageView(HTMXView):
    """HTMX endpoint for polling message updates."""

    template_name = "ai/chat/partials/message_content.html"

    def get(
        self, request: HttpRequest, message_id: str, *args, **kwargs
    ) -> HttpResponse:
        """Poll for message updates."""
        try:
            message = ChatMessage.objects.get(id=message_id)
        except ChatMessage.DoesNotExist:
            return HttpResponse("Message not found", status=404)

        context = {
            "message": message,
        }

        # If message is still processing, include polling header
        if not message.is_processed:
            response = render(request, self.template_name, context)
            response["HX-Trigger"] = "poll-message"
            return response

        return render(request, self.template_name, context)


class ChatNewSessionView(HTMXView):
    """Create a new chat session."""

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        """Create a new chat session and redirect."""
        user = request.user if request.user.is_authenticated else None
        chat_session = ChatSession.objects.create(user=user)
        request.session["chat_session_id"] = str(chat_session.id)

        # Return HTMX redirect
        response = HttpResponse()
        response["HX-Redirect"] = "/ai/chat/"
        return response


class ChatLoadSessionView(HTMXView):
    """Load an existing chat session."""

    def get(
        self, request: HttpRequest, session_id: str, *args, **kwargs
    ) -> HttpResponse:
        """Load a chat session."""
        chat_session = get_object_or_404(ChatSession, id=session_id)

        # Check permissions
        if chat_session.user and chat_session.user != request.user:
            return HttpResponse("Unauthorized", status=403)

        # Set as current session
        request.session["chat_session_id"] = str(chat_session.id)

        # Return HTMX redirect
        response = HttpResponse()
        response["HX-Redirect"] = "/ai/chat/"
        return response


class ChatClearView(HTMXView):
    """Clear the current chat session."""

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        """Clear messages from the current session."""
        session_id = request.session.get("chat_session_id")
        if not session_id:
            return HttpResponse("No active chat session", status=400)

        try:
            chat_session = ChatSession.objects.get(id=session_id)
            # Only clear if user owns the session or it's anonymous
            if not chat_session.user or chat_session.user == request.user:
                chat_session.messages.all().delete()
                chat_session.title = ""
                chat_session.save()
        except ChatSession.DoesNotExist:
            pass

        # Return HTMX redirect to refresh
        response = HttpResponse()
        response["HX-Redirect"] = "/ai/chat/"
        return response
