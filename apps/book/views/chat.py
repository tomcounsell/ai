import logging
import time

from asgiref.sync import async_to_sync
from django.http import JsonResponse
from django.utils.html import format_html
from django.views import View
from django.views.generic import TemplateView

from apps.book.chat import get_valor_response

logger = logging.getLogger(__name__)

# Rate limiting constants
BOOK_CHAT_RATE_LIMIT = 20  # max messages per hour
BOOK_CHAT_RATE_WINDOW = 3600  # 1 hour in seconds


class ValorChatView(TemplateView):
    """Page hosting the Valor chat widget."""

    template_name = "book/chat.html"


class ValorSendMessageView(View):
    """HTMX endpoint: accept a user message, return Valor's reply as HTML fragment."""

    http_method_names = ["post"]

    def post(self, request):
        user_message = request.POST.get("message", "").strip()
        if not user_message:
            return JsonResponse({"error": "Empty message"}, status=400)

        # Session-based rate limiting
        now = time.time()
        timestamps = request.session.get("book_chat_rate_timestamps", [])
        # Remove timestamps older than the rate window
        cutoff = now - BOOK_CHAT_RATE_WINDOW
        timestamps = [ts for ts in timestamps if ts > cutoff]
        if len(timestamps) >= BOOK_CHAT_RATE_LIMIT:
            return JsonResponse(
                {"error": "Rate limit exceeded. Please try again later."},
                status=429,
            )

        # Rebuild conversation history from the session
        history = request.session.get("book_chat_history", [])

        chat_failed = False
        try:
            response_text = async_to_sync(get_valor_response)(
                user_message, conversation_history=history
            )
        except Exception:
            logger.exception("Valor chat error")
            chat_failed = True
            response_text = (
                "I'm sorry, I'm having trouble responding right now. "
                "Please try again in a moment."
            )

        if not chat_failed:
            # Only count successful messages toward rate limit
            timestamps.append(now)
            request.session["book_chat_rate_timestamps"] = timestamps

            # Only persist successful exchanges to conversation history
            history.append({"role": "user", "content": user_message})
            history.append({"role": "assistant", "content": response_text})
            # Keep last 20 messages to avoid session bloat
            request.session["book_chat_history"] = history[-20:]

        # Return an HTMX-friendly HTML fragment
        user_html = format_html(
            '<div class="flex justify-end mb-3">'
            '<div class="rounded-lg px-4 py-2 max-w-xs text-sm" '
            'style="background-color: var(--color-accent); color: var(--color-paper);">'
            "{}</div></div>",
            user_message,
        )
        assistant_html = format_html(
            '<div class="flex justify-start mb-3">'
            '<div class="rounded-lg px-4 py-2 max-w-xs text-sm" '
            'style="background-color: var(--color-border); color: var(--color-ink);">'
            "{}</div></div>",
            response_text,
        )
        return JsonResponse(
            {"html": user_html + assistant_html, "message": response_text},
            status=200,
        )
