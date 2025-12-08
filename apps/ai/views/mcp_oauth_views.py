"""OAuth views for MCP servers - auto-approve flow for Claude Code HTTP transport."""

import json
import secrets
from urllib.parse import urlencode

from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt


@method_decorator(csrf_exempt, name="dispatch")
class MCPOAuthAuthorizeView(View):
    """OAuth authorization endpoint - auto-approves all requests.

    This enables Claude Code HTTP transport while keeping the server effectively open.
    Returns an authorization code that can be exchanged for an access token.
    """

    def get(self, request):
        """Handle OAuth authorization request - auto-approve."""
        # Get OAuth parameters from query string
        redirect_uri = request.GET.get("redirect_uri")
        state = request.GET.get("state")

        if not redirect_uri:
            return JsonResponse(
                {"error": "invalid_request", "error_description": "Missing redirect_uri"},
                status=400
            )

        # Generate a simple authorization code
        auth_code = secrets.token_urlsafe(32)

        # Build redirect URL with code and state
        params = {"code": auth_code}
        if state:
            params["state"] = state

        redirect_url = f"{redirect_uri}?{urlencode(params)}"
        return redirect(redirect_url)


@method_decorator(csrf_exempt, name="dispatch")
class MCPOAuthTokenView(View):
    """OAuth token endpoint - issues access tokens.

    Accepts authorization codes and returns access tokens.
    Since this is auto-approve, any valid-looking code gets a token.
    """

    def post(self, request):
        """Handle token request."""
        # Parse form data or JSON
        if request.content_type == "application/json":
            try:
                data = json.loads(request.body)
            except json.JSONDecodeError:
                return JsonResponse(
                    {"error": "invalid_request", "error_description": "Invalid JSON"},
                    status=400
                )
        else:
            data = request.POST

        grant_type = data.get("grant_type")
        code = data.get("code")

        # Validate grant_type
        if grant_type != "authorization_code":
            return JsonResponse(
                {"error": "unsupported_grant_type"},
                status=400
            )

        # Validate code exists (we accept any code for auto-approve)
        if not code:
            return JsonResponse(
                {"error": "invalid_request", "error_description": "Missing code"},
                status=400
            )

        # Generate access token
        access_token = secrets.token_urlsafe(32)

        # Return token response
        return JsonResponse({
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": 31536000,  # 1 year
            "scope": "mcp:read mcp:write"
        })
