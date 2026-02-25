
from django.urls import URLPattern, path

from apps.ai.views import (
    CreativeJuicesBundleView,
    CreativeJuicesClientView,
    CreativeJuicesLandingView,
    CreativeJuicesManifestView,
    CreativeJuicesMCPServerView,
    CreativeJuicesReadmeView,
    CTOToolsBundleView,
    CTOToolsClientView,
    CTOToolsLandingView,
    CTOToolsManifestView,
    CTOToolsMCPServerView,
    CTOToolsReadmeView,
    MCPOAuthAuthorizeView,
    MCPOAuthRegistrationView,
    MCPOAuthTokenView,
)
from apps.ai.views.test_chat import TestChatView
from apps.ai.views.test_page import TestChatPageView

app_name = "ai"

urlpatterns: list[URLPattern] = [
    # Test endpoints (no database required)
    path("test/", TestChatPageView.as_view(), name="test-page"),
    path("test-chat/", TestChatView.as_view(), name="test-chat"),
    # OAuth endpoints for MCP servers (auto-approve flow)
    path(
        "oauth/authorize", MCPOAuthAuthorizeView.as_view(), name="mcp-oauth-authorize"
    ),
    path("oauth/token", MCPOAuthTokenView.as_view(), name="mcp-oauth-token"),
    path(
        "oauth/register", MCPOAuthRegistrationView.as_view(), name="mcp-oauth-register"
    ),
    # MCP server endpoints
    path(
        "creative-juices/",
        CreativeJuicesLandingView.as_view(),
        name="mcp-creative-juices",
    ),
    path(
        "creative-juices/manifest.json",
        CreativeJuicesManifestView.as_view(),
        name="mcp-creative-juices-manifest",
    ),
    path(
        "creative-juices/README.md",
        CreativeJuicesReadmeView.as_view(),
        name="mcp-creative-juices-readme",
    ),
    path(
        "creative-juices/client.py",
        CreativeJuicesClientView.as_view(),
        name="mcp-creative-juices-client",
    ),
    path(
        "creative-juices/download.mcpb",
        CreativeJuicesBundleView.as_view(),
        name="mcp-creative-juices-bundle",
    ),
    path(
        "creative-juices/serve",
        CreativeJuicesMCPServerView.as_view(),
        name="mcp-creative-juices-serve",
    ),
    # CTO Tools MCP server endpoints
    path(
        "cto-tools/",
        CTOToolsLandingView.as_view(),
        name="mcp-cto-tools",
    ),
    path(
        "cto-tools/manifest.json",
        CTOToolsManifestView.as_view(),
        name="mcp-cto-tools-manifest",
    ),
    path(
        "cto-tools/README.md",
        CTOToolsReadmeView.as_view(),
        name="mcp-cto-tools-readme",
    ),
    path(
        "cto-tools/client.py",
        CTOToolsClientView.as_view(),
        name="mcp-cto-tools-client",
    ),
    path(
        "cto-tools/download.mcpb",
        CTOToolsBundleView.as_view(),
        name="mcp-cto-tools-bundle",
    ),
    path(
        "cto-tools/serve",
        CTOToolsMCPServerView.as_view(),
        name="mcp-cto-tools-serve",
    ),
    # Chat interface (requires migrations)
    # path('chat/', ChatIndexView.as_view(), name='chat-index'),
    # path('chat/send/', ChatSendMessageView.as_view(), name='chat-send'),
    # path('chat/poll/<str:message_id>/', ChatPollMessageView.as_view(), name='chat-poll'),
    # path('chat/new-session/', ChatNewSessionView.as_view(), name='chat-new-session'),
    # path('chat/load/<str:session_id>/', ChatLoadSessionView.as_view(), name='chat-load-session'),
    # path('chat/clear/', ChatClearView.as_view(), name='chat-clear'),
]
