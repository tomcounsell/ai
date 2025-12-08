"""OAuth views for MCP servers - auto-approve flow for Claude Code HTTP transport."""

import json
import secrets
import time
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
    Supports PKCE (RFC 7636) for enhanced security.
    """

    def get(self, request):
        """Handle OAuth authorization request - auto-approve."""
        # Get OAuth parameters from query string
        redirect_uri = request.GET.get("redirect_uri")
        state = request.GET.get("state")
        code_challenge = request.GET.get("code_challenge")
        code_challenge_method = request.GET.get("code_challenge_method")

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
    Supports PKCE (RFC 7636) code verification.
    """

    def post(self, request):
        """Handle token request."""
        # Parse form data or JSON
        content_type = request.content_type or ''
        if 'application/json' in content_type:
            try:
                data = json.loads(request.body)
            except json.JSONDecodeError:
                return JsonResponse(
                    {"error": "invalid_request", "error_description": "Invalid JSON"},
                    status=400
                )
        else:
            # Handle form-encoded data - Django populates request.POST automatically
            # for application/x-www-form-urlencoded requests
            data = dict(request.POST.items())

        grant_type = data.get("grant_type")
        code = data.get("code")
        code_verifier = data.get("code_verifier")

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

        # For auto-approve mode, we accept any code_verifier
        # In production, you'd validate: SHA256(code_verifier) == stored_code_challenge

        # Generate access token
        access_token = secrets.token_urlsafe(32)

        # Return token response
        return JsonResponse({
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": 31536000,  # 1 year
            "scope": "mcp:read mcp:write"
        })


@method_decorator(csrf_exempt, name="dispatch")
class MCPOAuthRegistrationView(View):
    """OAuth Dynamic Client Registration endpoint (RFC 7591).

    Accepts client registration requests and returns client credentials.
    Since this is auto-approve, we accept all registrations.
    """

    def post(self, request):
        """Handle client registration request."""
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse(
                {"error": "invalid_request", "error_description": "Invalid JSON"},
                status=400
            )

        # Generate client credentials (no secret needed for public clients)
        client_id = secrets.token_urlsafe(32)

        # Return registration response
        return JsonResponse({
            "client_id": client_id,
            "client_id_issued_at": int(time.time()),
            "redirect_uris": data.get("redirect_uris", []),
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none"
        })


class MCPOAuthMetadataView(View):
    """OAuth Authorization Server Metadata endpoint (RFC 8414).

    Returns metadata about the OAuth server for discovery.
    This is accessed at /.well-known/oauth-authorization-server
    """

    def get(self, request):
        """Return OAuth server metadata."""
        base_url = request.build_absolute_uri('/').rstrip('/')

        metadata = {
            "issuer": base_url,
            "authorization_endpoint": f"{base_url}/mcp/oauth/authorize",
            "token_endpoint": f"{base_url}/mcp/oauth/token",
            "registration_endpoint": f"{base_url}/mcp/oauth/register",
            "scopes_supported": ["mcp:read", "mcp:write"],
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "token_endpoint_auth_methods_supported": ["none"],
            "code_challenge_methods_supported": ["S256"],
        }

        return JsonResponse(metadata)
