from django.urls import path

from .views import (
    MCPServerView,
    QuickBooksCallbackView,
    QuickBooksConnectView,
    WebhookView,
)

app_name = "mcp_server"

urlpatterns = [
    # MCP Server endpoint
    path("mcp/", MCPServerView.as_view(), name="mcp_server"),
    
    # QuickBooks OAuth
    path("quickbooks/connect/", QuickBooksConnectView.as_view(), name="quickbooks_connect"),
    path("quickbooks/callback/", QuickBooksCallbackView.as_view(), name="quickbooks_callback"),
    
    # Webhooks
    path("webhooks/quickbooks/", WebhookView.as_view(), name="quickbooks_webhook"),
]