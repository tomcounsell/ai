"""End-to-end tests for MCP OAuth flow.

Tests the complete OAuth 2.0 authorization flow including:
- OAuth Authorization Server Metadata discovery (RFC 8414)
- Dynamic Client Registration (RFC 7591)
- PKCE support with S256 challenge method (RFC 7636)
- Authorization code flow
- Token exchange

These tests ensure Claude Code HTTP transport compatibility.
"""

import base64
import hashlib
import json
import secrets
from urllib.parse import parse_qs, urlparse

import pytest
from django.test import Client


@pytest.fixture
def client():
    """Return Django test client."""
    return Client()


@pytest.fixture
def pkce_params():
    """Generate PKCE code verifier and challenge for testing."""
    # Generate code verifier (RFC 7636 - 43-128 characters)
    code_verifier = (
        base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("utf-8").rstrip("=")
    )

    # Generate S256 code challenge
    challenge_bytes = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    code_challenge = (
        base64.urlsafe_b64encode(challenge_bytes).decode("utf-8").rstrip("=")
    )

    return {
        "code_verifier": code_verifier,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }


# OAuth Authorization Server Metadata Tests (RFC 8414)


@pytest.mark.django_db
def test_oauth_metadata_endpoint_exists(client):
    """Test OAuth metadata endpoint is accessible."""
    response = client.get("/.well-known/oauth-authorization-server")

    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"


@pytest.mark.django_db
def test_oauth_metadata_contains_required_fields(client):
    """Test OAuth metadata contains all required fields."""
    response = client.get("/.well-known/oauth-authorization-server")
    data = response.json()

    # Required fields per RFC 8414
    required_fields = [
        "issuer",
        "authorization_endpoint",
        "token_endpoint",
        "scopes_supported",
        "response_types_supported",
        "grant_types_supported",
    ]

    for field in required_fields:
        assert field in data, f"Missing required field: {field}"


@pytest.mark.django_db
def test_oauth_metadata_advertises_pkce_support(client):
    """Test OAuth metadata advertises PKCE S256 support."""
    response = client.get("/.well-known/oauth-authorization-server")
    data = response.json()

    assert "code_challenge_methods_supported" in data
    assert "S256" in data["code_challenge_methods_supported"]


@pytest.mark.django_db
def test_oauth_metadata_advertises_registration_endpoint(client):
    """Test OAuth metadata includes registration endpoint for dynamic client registration."""
    response = client.get("/.well-known/oauth-authorization-server")
    data = response.json()

    assert "registration_endpoint" in data
    assert "/mcp/oauth/register" in data["registration_endpoint"]


@pytest.mark.django_db
def test_oauth_metadata_advertises_correct_endpoints(client):
    """Test OAuth metadata endpoint URLs are properly formed."""
    response = client.get("/.well-known/oauth-authorization-server")
    data = response.json()

    # Check endpoint paths
    assert data["authorization_endpoint"].endswith("/mcp/oauth/authorize")
    assert data["token_endpoint"].endswith("/mcp/oauth/token")
    assert data["registration_endpoint"].endswith("/mcp/oauth/register")


# Dynamic Client Registration Tests (RFC 7591)


@pytest.mark.django_db
def test_client_registration_succeeds(client):
    """Test dynamic client registration returns client credentials."""
    registration_data = {
        "redirect_uris": ["http://localhost:3000/callback"],
        "grant_types": ["authorization_code"],
    }

    response = client.post(
        "/mcp/oauth/register",
        data=json.dumps(registration_data),
        content_type="application/json",
    )

    assert response.status_code == 200
    data = response.json()

    # Required response fields per RFC 7591
    assert "client_id" in data
    assert len(data["client_id"]) > 0
    assert "client_id_issued_at" in data
    assert isinstance(data["client_id_issued_at"], int)


@pytest.mark.django_db
def test_client_registration_returns_metadata(client):
    """Test client registration response includes client metadata."""
    registration_data = {
        "redirect_uris": ["http://localhost:3000/callback"],
        "grant_types": ["authorization_code"],
    }

    response = client.post(
        "/mcp/oauth/register",
        data=json.dumps(registration_data),
        content_type="application/json",
    )

    data = response.json()

    # Verify metadata fields
    assert data["redirect_uris"] == registration_data["redirect_uris"]
    assert data["grant_types"] == ["authorization_code"]
    assert data["response_types"] == ["code"]
    assert data["token_endpoint_auth_method"] == "none"


@pytest.mark.django_db
def test_client_registration_rejects_invalid_json(client):
    """Test client registration rejects malformed JSON."""
    response = client.post(
        "/mcp/oauth/register", data="invalid json", content_type="application/json"
    )

    assert response.status_code == 400
    data = response.json()
    assert "error" in data
    assert data["error"] == "invalid_request"


# Authorization Endpoint Tests


@pytest.mark.django_db
def test_authorization_endpoint_redirects_with_code(client):
    """Test authorization endpoint returns authorization code via redirect."""
    response = client.get(
        "/mcp/oauth/authorize",
        {
            "client_id": "test_client",
            "redirect_uri": "http://localhost:3000/callback",
            "response_type": "code",
            "state": "test_state_xyz",
        },
    )

    assert response.status_code == 302

    # Parse redirect URL
    location = response["Location"]
    parsed = urlparse(location)
    params = parse_qs(parsed.query)

    assert "code" in params
    assert len(params["code"][0]) > 0
    assert params["state"][0] == "test_state_xyz"


@pytest.mark.django_db
def test_authorization_endpoint_accepts_pkce_parameters(client, pkce_params):
    """Test authorization endpoint accepts PKCE code challenge."""
    response = client.get(
        "/mcp/oauth/authorize",
        {
            "client_id": "test_client",
            "redirect_uri": "http://localhost:3000/callback",
            "response_type": "code",
            "state": "test_state",
            "code_challenge": pkce_params["code_challenge"],
            "code_challenge_method": pkce_params["code_challenge_method"],
        },
    )

    assert response.status_code == 302

    # Verify code is still returned
    location = response["Location"]
    parsed = urlparse(location)
    params = parse_qs(parsed.query)
    assert "code" in params


@pytest.mark.django_db
def test_authorization_endpoint_requires_redirect_uri(client):
    """Test authorization endpoint validates redirect_uri presence."""
    response = client.get(
        "/mcp/oauth/authorize",
        {
            "client_id": "test_client",
            "response_type": "code",
        },
    )

    assert response.status_code == 400
    data = response.json()
    assert data["error"] == "invalid_request"
    assert "redirect_uri" in data["error_description"]


@pytest.mark.django_db
def test_authorization_endpoint_preserves_state(client):
    """Test authorization endpoint preserves state parameter in redirect."""
    state_value = "random_state_" + secrets.token_urlsafe(16)

    response = client.get(
        "/mcp/oauth/authorize",
        {
            "client_id": "test_client",
            "redirect_uri": "http://localhost:3000/callback",
            "response_type": "code",
            "state": state_value,
        },
    )

    location = response["Location"]
    parsed = urlparse(location)
    params = parse_qs(parsed.query)

    assert params["state"][0] == state_value


# Token Endpoint Tests


@pytest.mark.django_db
def test_token_endpoint_issues_access_token(client):
    """Test token endpoint exchanges authorization code for access token."""
    token_request = {
        "grant_type": "authorization_code",
        "code": "test_auth_code",
        "redirect_uri": "http://localhost:3000/callback",
    }

    response = client.post(
        "/mcp/oauth/token",
        data=json.dumps(token_request),
        content_type="application/json",
    )

    assert response.status_code == 200
    data = response.json()

    # Required token response fields
    assert "access_token" in data
    assert len(data["access_token"]) > 0
    assert data["token_type"] == "Bearer"
    assert "expires_in" in data
    assert isinstance(data["expires_in"], int)


@pytest.mark.django_db
def test_token_endpoint_accepts_pkce_verifier(client, pkce_params):
    """Test token endpoint accepts PKCE code verifier."""
    token_request = {
        "grant_type": "authorization_code",
        "code": "test_auth_code",
        "redirect_uri": "http://localhost:3000/callback",
        "code_verifier": pkce_params["code_verifier"],
    }

    response = client.post(
        "/mcp/oauth/token",
        data=json.dumps(token_request),
        content_type="application/json",
    )

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data


@pytest.mark.django_db
def test_token_endpoint_validates_grant_type(client):
    """Test token endpoint rejects unsupported grant types."""
    token_request = {
        "grant_type": "client_credentials",  # Not supported
        "code": "test_auth_code",
    }

    response = client.post(
        "/mcp/oauth/token",
        data=json.dumps(token_request),
        content_type="application/json",
    )

    assert response.status_code == 400
    data = response.json()
    assert data["error"] == "unsupported_grant_type"


@pytest.mark.django_db
def test_token_endpoint_requires_code(client):
    """Test token endpoint validates code presence."""
    token_request = {
        "grant_type": "authorization_code",
        "redirect_uri": "http://localhost:3000/callback",
    }

    response = client.post(
        "/mcp/oauth/token",
        data=json.dumps(token_request),
        content_type="application/json",
    )

    assert response.status_code == 400
    data = response.json()
    assert data["error"] == "invalid_request"
    assert "code" in data["error_description"]


@pytest.mark.django_db
def test_token_endpoint_rejects_invalid_json(client):
    """Test token endpoint rejects malformed JSON."""
    response = client.post(
        "/mcp/oauth/token", data="invalid json", content_type="application/json"
    )

    assert response.status_code == 400
    data = response.json()
    assert data["error"] == "invalid_request"


# End-to-End Flow Tests


@pytest.mark.django_db
def test_complete_oauth_flow_without_pkce(client):
    """Test complete OAuth flow: metadata -> register -> authorize -> token."""
    # Step 1: Discover OAuth server metadata
    metadata_response = client.get("/.well-known/oauth-authorization-server")
    assert metadata_response.status_code == 200
    metadata = metadata_response.json()

    # Step 2: Register client
    registration_response = client.post(
        "/mcp/oauth/register",
        data=json.dumps({"redirect_uris": ["http://localhost:3000/callback"]}),
        content_type="application/json",
    )
    assert registration_response.status_code == 200
    client_data = registration_response.json()
    client_id = client_data["client_id"]

    # Step 3: Get authorization code
    auth_response = client.get(
        "/mcp/oauth/authorize",
        {
            "client_id": client_id,
            "redirect_uri": "http://localhost:3000/callback",
            "response_type": "code",
            "state": "test_state",
        },
    )
    assert auth_response.status_code == 302

    # Extract authorization code from redirect
    location = auth_response["Location"]
    parsed = urlparse(location)
    params = parse_qs(parsed.query)
    auth_code = params["code"][0]

    # Step 4: Exchange code for token
    token_response = client.post(
        "/mcp/oauth/token",
        data=json.dumps(
            {
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": "http://localhost:3000/callback",
            }
        ),
        content_type="application/json",
    )
    assert token_response.status_code == 200
    token_data = token_response.json()
    assert "access_token" in token_data
    assert token_data["token_type"] == "Bearer"


@pytest.mark.django_db
def test_complete_oauth_flow_with_pkce(client, pkce_params):
    """Test complete OAuth flow with PKCE: metadata -> register -> authorize -> token."""
    # Step 1: Discover OAuth server metadata
    metadata_response = client.get("/.well-known/oauth-authorization-server")
    metadata = metadata_response.json()
    assert "S256" in metadata["code_challenge_methods_supported"]

    # Step 2: Register client
    registration_response = client.post(
        "/mcp/oauth/register",
        data=json.dumps({"redirect_uris": ["http://localhost:3000/callback"]}),
        content_type="application/json",
    )
    client_data = registration_response.json()
    client_id = client_data["client_id"]

    # Step 3: Get authorization code with PKCE challenge
    auth_response = client.get(
        "/mcp/oauth/authorize",
        {
            "client_id": client_id,
            "redirect_uri": "http://localhost:3000/callback",
            "response_type": "code",
            "state": "test_state",
            "code_challenge": pkce_params["code_challenge"],
            "code_challenge_method": pkce_params["code_challenge_method"],
        },
    )
    assert auth_response.status_code == 302

    # Extract authorization code
    location = auth_response["Location"]
    parsed = urlparse(location)
    params = parse_qs(parsed.query)
    auth_code = params["code"][0]

    # Step 4: Exchange code for token with PKCE verifier
    token_response = client.post(
        "/mcp/oauth/token",
        data=json.dumps(
            {
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": "http://localhost:3000/callback",
                "code_verifier": pkce_params["code_verifier"],
            }
        ),
        content_type="application/json",
    )
    assert token_response.status_code == 200
    token_data = token_response.json()
    assert "access_token" in token_data
    assert token_data["token_type"] == "Bearer"
    assert "mcp:read" in token_data["scope"]
    assert "mcp:write" in token_data["scope"]


@pytest.mark.django_db
def test_complete_real_world_mcp_session(client, pkce_params):
    """Comprehensive end-to-end test simulating a real Claude Code session.

    This test simulates the COMPLETE real-world flow that Claude Code performs,
    designed to catch issues before they reach production. It validates:
    - OAuth discovery and dynamic registration
    - PKCE-secured authorization and token exchange
    - MCP server discovery and initialization
    - Complete protocol handshake including notifications
    - Actual tool operations
    - Error handling and edge cases

    This test should catch issues like:
    - Missing protocol steps (notifications/initialized)
    - Authentication inconsistencies (GET vs POST requirements)
    - Invalid JSON-RPC responses
    - Missing required fields in responses
    - Incorrect HTTP status codes
    """
    # PHASE 1: OAuth Discovery
    metadata = client.get("/.well-known/oauth-authorization-server").json()
    assert "registration_endpoint" in metadata
    assert "S256" in metadata.get("code_challenge_methods_supported", [])
    assert "authorization_endpoint" in metadata
    assert "token_endpoint" in metadata

    # PHASE 2: Dynamic Client Registration
    registration = client.post(
        metadata["registration_endpoint"],
        data=json.dumps({"redirect_uris": ["http://localhost:3000/callback"]}),
        content_type="application/json",
    ).json()
    client_id = registration["client_id"]
    assert client_id, "Must receive client_id"
    assert "client_id_issued_at" in registration

    # PHASE 3: Authorization with PKCE
    auth_response = client.get(
        metadata["authorization_endpoint"],
        {
            "client_id": client_id,
            "redirect_uri": "http://localhost:3000/callback",
            "response_type": "code",
            "state": "test_state",
            "code_challenge": pkce_params["code_challenge"],
            "code_challenge_method": "S256",
        },
    )
    assert auth_response.status_code == 302, "Must redirect with auth code"

    auth_code = parse_qs(urlparse(auth_response["Location"]).query)["code"][0]
    assert len(auth_code) > 0, "Auth code must not be empty"

    # PHASE 4: Token Exchange
    token_response = client.post(
        metadata["token_endpoint"],
        data=json.dumps(
            {
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": "http://localhost:3000/callback",
                "code_verifier": pkce_params["code_verifier"],
            }
        ),
        content_type="application/json",
    )
    assert token_response.status_code == 200, "Token exchange must succeed"
    token = token_response.json()
    access_token = token["access_token"]
    assert token["token_type"] == "Bearer"
    assert "expires_in" in token

    # PHASE 5: MCP Server Discovery
    server_info = client.get("/mcp/cto-tools/serve").json()
    assert server_info["name"] == "cto-tools"
    assert server_info["protocol"] == "MCP"
    assert (
        server_info["authentication"] is True
    ), "Server must advertise authentication requirement"

    # PHASE 6: MCP Initialize (with auth)
    init_response = client.post(
        "/mcp/cto-tools/serve",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "claude-code", "version": "1.0"},
                },
                "id": 1,
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    )
    assert init_response.status_code == 200
    init_data = init_response.json()
    assert init_data["jsonrpc"] == "2.0"
    assert "result" in init_data
    assert "protocolVersion" in init_data["result"]
    assert "capabilities" in init_data["result"]
    assert "serverInfo" in init_data["result"]

    # PHASE 7: Send initialized notification (CRITICAL - this is where bugs occurred)
    notification_response = client.post(
        "/mcp/cto-tools/serve",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    )
    assert (
        notification_response.status_code == 200
    ), "Notification must succeed - previous bug caused 400 here"
    assert notification_response.json()["jsonrpc"] == "2.0"

    # PHASE 8: List available tools
    tools_response = client.post(
        "/mcp/cto-tools/serve",
        data=json.dumps(
            {"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 2}
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    )
    assert tools_response.status_code == 200
    tools_data = tools_response.json()
    assert "result" in tools_data
    assert "tools" in tools_data["result"]
    tools = tools_data["result"]["tools"]
    assert len(tools) > 0, "Must have at least one tool"

    # Validate tool schema
    for tool in tools:
        assert "name" in tool, "Tool must have name"
        assert "description" in tool, "Tool must have description"
        assert "inputSchema" in tool, "Tool must have inputSchema"

    # PHASE 9: Test error handling - invalid method
    invalid_response = client.post(
        "/mcp/cto-tools/serve",
        data=json.dumps(
            {"jsonrpc": "2.0", "method": "invalid/method", "params": {}, "id": 3}
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    )
    assert invalid_response.status_code == 400
    error_data = invalid_response.json()
    assert "error" in error_data
    assert error_data["error"]["code"] == -32601  # Method not found

    # PHASE 10: Test authentication is enforced
    unauth_response = client.post(
        "/mcp/cto-tools/serve",
        data=json.dumps(
            {"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 4}
        ),
        content_type="application/json",
        # No Authorization header
    )
    assert (
        unauth_response.status_code == 401
    ), "Must reject requests without Bearer token"


@pytest.mark.django_db
def test_oauth_flow_matches_claude_code_requirements(client, pkce_params):
    """Test OAuth flow meets all Claude Code HTTP transport requirements.

    This test validates that the OAuth implementation satisfies all requirements
    for Claude Code to successfully connect via HTTP transport:
    1. OAuth metadata discovery with registration_endpoint
    2. PKCE S256 support
    3. Dynamic client registration
    4. Authorization code flow
    5. MCP server initialization with Bearer token
    6. MCP notifications/initialized (critical for connection success)
    7. MCP server operations (tools/list)
    """
    # Requirement 1: OAuth metadata with registration endpoint and PKCE support
    metadata = client.get("/.well-known/oauth-authorization-server").json()
    assert (
        "registration_endpoint" in metadata
    ), "Claude Code requires registration_endpoint"
    assert "S256" in metadata.get(
        "code_challenge_methods_supported", []
    ), "Claude Code requires PKCE S256 support"

    # Requirement 2: Dynamic client registration
    registration = client.post(
        "/mcp/oauth/register",
        data=json.dumps({"redirect_uris": ["http://localhost:3000/callback"]}),
        content_type="application/json",
    ).json()
    assert (
        "client_id" in registration
    ), "Claude Code requires client_id from registration"

    # Requirement 3: PKCE-enabled authorization
    auth_response = client.get(
        "/mcp/oauth/authorize",
        {
            "client_id": registration["client_id"],
            "redirect_uri": "http://localhost:3000/callback",
            "response_type": "code",
            "code_challenge": pkce_params["code_challenge"],
            "code_challenge_method": "S256",
        },
    )
    assert auth_response.status_code == 302, "Authorization must redirect with code"

    # Requirement 4: Token exchange with PKCE verifier
    auth_code = parse_qs(urlparse(auth_response["Location"]).query)["code"][0]
    token = client.post(
        "/mcp/oauth/token",
        data=json.dumps(
            {
                "grant_type": "authorization_code",
                "code": auth_code,
                "code_verifier": pkce_params["code_verifier"],
            }
        ),
        content_type="application/json",
    ).json()
    assert "access_token" in token, "Claude Code requires access_token"
    assert token["token_type"] == "Bearer", "Claude Code requires Bearer token type"

    # Requirement 5: Initialize MCP server with Bearer token
    access_token = token["access_token"]
    init_response = client.post(
        "/mcp/cto-tools/serve",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "claude-code", "version": "1.0"},
                },
                "id": 1,
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    )
    assert init_response.status_code == 200, "Initialize must succeed with Bearer token"
    assert "result" in init_response.json(), "Initialize must return result"

    # Requirement 6: Send notifications/initialized (this is where previous bugs occurred!)
    notification_response = client.post(
        "/mcp/cto-tools/serve",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    )
    assert (
        notification_response.status_code == 200
    ), "Notifications must succeed or Claude Code connection fails"

    # Requirement 7: Verify server operations work (tools/list)
    tools_response = client.post(
        "/mcp/cto-tools/serve",
        data=json.dumps(
            {"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 2}
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    )
    assert tools_response.status_code == 200, "Tools list must succeed"
    assert "tools" in tools_response.json()["result"], "Must return available tools"
