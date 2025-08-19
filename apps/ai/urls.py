from typing import List

from django.urls import URLPattern, path

from apps.ai.views import (
    ChatClearView,
    ChatIndexView,
    ChatLoadSessionView,
    ChatNewSessionView,
    ChatPollMessageView,
    ChatSendMessageView,
)
from apps.ai.views.test_chat import TestChatView
from apps.ai.views.test_page import TestChatPageView

app_name = "ai"

urlpatterns: list[URLPattern] = [
    # Test endpoints (no database required)
    path("test/", TestChatPageView.as_view(), name="test-page"),
    path("test-chat/", TestChatView.as_view(), name="test-chat"),
    # Chat interface (requires migrations)
    # path('chat/', ChatIndexView.as_view(), name='chat-index'),
    # path('chat/send/', ChatSendMessageView.as_view(), name='chat-send'),
    # path('chat/poll/<str:message_id>/', ChatPollMessageView.as_view(), name='chat-poll'),
    # path('chat/new-session/', ChatNewSessionView.as_view(), name='chat-new-session'),
    # path('chat/load/<str:session_id>/', ChatLoadSessionView.as_view(), name='chat-load-session'),
    # path('chat/clear/', ChatClearView.as_view(), name='chat-clear'),
]
