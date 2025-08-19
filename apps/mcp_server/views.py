"""
Views for MCP server and QuickBooks OAuth.
"""

import json
import logging
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .models import APIKey, Organization, QuickBooksConnection

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name="dispatch")
class MCPServerView(View):
    """WebSocket endpoint for MCP protocol."""
    
    def post(self, request: HttpRequest) -> JsonResponse:
        """Handle MCP protocol messages."""
        
        # Authenticate via API key
        api_key = request.headers.get("X-API-Key")
        if not api_key:
            return JsonResponse({"error": "Missing API key"}, status=401)
            
        try:
            key_obj = APIKey.objects.select_related("organization").get(
                key=api_key,
                is_active=True,
            )
        except APIKey.DoesNotExist:
            return JsonResponse({"error": "Invalid API key"}, status=401)
            
        # Process MCP message
        try:
            message = json.loads(request.body)
            # In production, this would handle full MCP protocol
            # For now, return a simple response
            return JsonResponse({
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "result": {
                    "status": "ok",
                    "organization": key_obj.organization.name,
                },
            })
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)


class QuickBooksConnectView(View):
    """Initiate QuickBooks OAuth flow."""
    
    def get(self, request: HttpRequest) -> HttpResponse:
        """Redirect to QuickBooks OAuth authorization."""
        
        # Get organization from session or parameter
        org_id = request.GET.get("org_id")
        if not org_id:
            return JsonResponse({"error": "Missing organization ID"}, status=400)
            
        # Build OAuth URL
        client_id = settings.QUICKBOOKS_CLIENT_ID
        redirect_uri = request.build_absolute_uri(reverse("mcp_server:quickbooks_callback"))
        scope = "com.intuit.quickbooks.accounting"
        
        oauth_url = (
            f"https://appcenter.intuit.com/connect/oauth2?"
            f"client_id={client_id}"
            f"&scope={scope}"
            f"&redirect_uri={redirect_uri}"
            f"&response_type=code"
            f"&state={org_id}"
        )
        
        return redirect(oauth_url)


class QuickBooksCallbackView(View):
    """Handle QuickBooks OAuth callback."""
    
    def get(self, request: HttpRequest) -> HttpResponse:
        """Process OAuth callback and save tokens."""
        
        code = request.GET.get("code")
        state = request.GET.get("state")  # organization ID
        realm_id = request.GET.get("realmId")  # QuickBooks company ID
        
        if not code or not state or not realm_id:
            return JsonResponse({"error": "Missing OAuth parameters"}, status=400)
            
        try:
            organization = Organization.objects.get(id=state)
        except Organization.DoesNotExist:
            return JsonResponse({"error": "Invalid organization"}, status=400)
            
        # Exchange code for tokens
        # In production, make actual API call to QuickBooks
        # For now, create dummy connection
        connection, created = QuickBooksConnection.objects.update_or_create(
            organization=organization,
            company_id=realm_id,
            defaults={
                "company_name": f"Company {realm_id}",
                "access_token": "dummy_access_token",
                "refresh_token": "dummy_refresh_token",
                "token_expires_at": "2025-12-31T23:59:59Z",
                "is_active": True,
            },
        )
        
        return JsonResponse({
            "status": "success",
            "message": "QuickBooks connected successfully",
            "company": connection.company_name,
        })


@method_decorator(csrf_exempt, name="dispatch")
class WebhookView(View):
    """Handle QuickBooks webhooks."""
    
    def post(self, request: HttpRequest) -> JsonResponse:
        """Process webhook notifications."""
        
        # Verify webhook signature
        signature = request.headers.get("intuit-signature")
        if not self._verify_signature(request.body, signature):
            return JsonResponse({"error": "Invalid signature"}, status=401)
            
        try:
            payload = json.loads(request.body)
            
            # Process each event
            for event in payload.get("eventNotifications", []):
                self._process_event(event)
                
            return JsonResponse({"status": "ok"})
            
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
            
    def _verify_signature(self, body: bytes, signature: str) -> bool:
        """Verify webhook signature."""
        # In production, implement actual signature verification
        return True
        
    def _process_event(self, event: Dict[str, Any]):
        """Process a single webhook event."""
        
        entity_name = event.get("dataChangeEvent", {}).get("entities", [{}])[0].get("name")
        entity_id = event.get("dataChangeEvent", {}).get("entities", [{}])[0].get("id")
        operation = event.get("dataChangeEvent", {}).get("entities", [{}])[0].get("operation")
        
        logger.info(f"Webhook event: {entity_name} {entity_id} {operation}")
        
        # In production, update local cache or trigger sync
        pass