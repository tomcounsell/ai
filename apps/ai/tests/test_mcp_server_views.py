"""Tests for MCP server views - validates MCP protocol implementation.

These tests ensure the Django views properly implement the MCP JSON-RPC 2.0
protocol and handle authentication correctly.
"""

import json

import pytest
from django.test import Client


@pytest.fixture
def client():
    """Return Django test client."""
    return Client()


@pytest.fixture
def valid_bearer_token():
    """Return a valid Bearer token for testing."""
    return "test_access_token_12345"


# CTO Tools Server Tests


@pytest.mark.django_db
def test_cto_tools_get_returns_server_info(client):
    """Test GET endpoint returns server metadata."""
    response = client.get("/mcp/cto-tools/serve")

    assert response.status_code == 200
    data = response.json()

    assert data["name"] == "cto-tools"
    assert data["version"] == "1.1.0"
    assert data["protocol"] == "MCP"
    assert "endpoint" in data


@pytest.mark.django_db
def test_cto_tools_indicates_authentication_required(client):
    """Test server advertises that authentication is required."""
    response = client.get("/mcp/cto-tools/serve")
    data = response.json()

    assert (
        data["authentication"] is True
    ), "Server must indicate authentication is required to match OAuth manifest"


@pytest.mark.django_db
def test_cto_tools_post_requires_bearer_token(client):
    """Test POST requests require Bearer token authentication."""
    mcp_request = {
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"},
        },
        "id": 1,
    }

    # Request without Authorization header
    response = client.post(
        "/mcp/cto-tools/serve",
        data=json.dumps(mcp_request),
        content_type="application/json",
    )

    assert response.status_code == 401
    data = response.json()
    assert "error" in data
    assert "Authentication required" in data["error"]["message"]


@pytest.mark.django_db
def test_cto_tools_post_rejects_invalid_auth_header(client):
    """Test POST requests reject invalid Authorization headers."""
    mcp_request = {"jsonrpc": "2.0", "method": "initialize", "params": {}, "id": 1}

    # Request with invalid Authorization header (not Bearer)
    response = client.post(
        "/mcp/cto-tools/serve",
        data=json.dumps(mcp_request),
        content_type="application/json",
        HTTP_AUTHORIZATION="Basic invalid",
    )

    assert response.status_code == 401


@pytest.mark.django_db
def test_cto_tools_initialize_with_auth(client, valid_bearer_token):
    """Test initialize method works with valid Bearer token."""
    mcp_request = {
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"},
        },
        "id": 1,
    }

    response = client.post(
        "/mcp/cto-tools/serve",
        data=json.dumps(mcp_request),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {valid_bearer_token}",
    )

    assert response.status_code == 200
    data = response.json()

    assert data["jsonrpc"] == "2.0"
    assert data["id"] == 1
    assert "result" in data
    assert data["result"]["protocolVersion"] == "2024-11-05"
    assert data["result"]["serverInfo"]["name"] == "cto-tools"


@pytest.mark.django_db
def test_cto_tools_tools_list_with_auth(client, valid_bearer_token):
    """Test tools/list method returns available tools."""
    mcp_request = {"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 2}

    response = client.post(
        "/mcp/cto-tools/serve",
        data=json.dumps(mcp_request),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {valid_bearer_token}",
    )

    assert response.status_code == 200
    data = response.json()

    assert "result" in data
    assert "tools" in data["result"]
    assert len(data["result"]["tools"]) > 0

    # Verify tools have required fields
    for tool in data["result"]["tools"]:
        assert "name" in tool
        assert "description" in tool
        assert "inputSchema" in tool


@pytest.mark.django_db
def test_cto_tools_handles_notifications(client, valid_bearer_token):
    """Test server handles MCP notifications correctly."""
    mcp_request = {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    }

    response = client.post(
        "/mcp/cto-tools/serve",
        data=json.dumps(mcp_request),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {valid_bearer_token}",
    )

    # Notifications should return 200 with minimal response
    assert response.status_code == 200
    data = response.json()
    assert data["jsonrpc"] == "2.0"


@pytest.mark.django_db
def test_cto_tools_invalid_method(client, valid_bearer_token):
    """Test server returns error for unknown methods."""
    mcp_request = {"jsonrpc": "2.0", "method": "invalid/method", "params": {}, "id": 3}

    response = client.post(
        "/mcp/cto-tools/serve",
        data=json.dumps(mcp_request),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {valid_bearer_token}",
    )

    assert response.status_code == 400
    data = response.json()

    assert "error" in data
    assert data["error"]["code"] == -32601
    assert "Method not found" in data["error"]["message"]


@pytest.mark.django_db
def test_cto_tools_invalid_json(client, valid_bearer_token):
    """Test server handles malformed JSON gracefully."""
    response = client.post(
        "/mcp/cto-tools/serve",
        data="invalid json{",
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {valid_bearer_token}",
    )

    assert response.status_code == 400
    data = response.json()

    assert "error" in data
    assert data["error"]["code"] == -32700
    assert "Parse error" in data["error"]["message"]


# Creative Juices Server Tests (no authentication)


@pytest.mark.django_db
def test_creative_juices_get_returns_server_info(client):
    """Test Creative Juices GET endpoint returns server metadata."""
    response = client.get("/mcp/creative-juices/serve")

    assert response.status_code == 200
    data = response.json()

    assert data["name"] == "creative-juices"
    assert data["version"] == "1.0.0"
    assert data["protocol"] == "MCP"


@pytest.mark.django_db
def test_creative_juices_no_authentication_required(client):
    """Test Creative Juices server doesn't require authentication."""
    response = client.get("/mcp/creative-juices/serve")
    data = response.json()

    assert data["authentication"] is False


@pytest.mark.django_db
def test_creative_juices_initialize_without_auth(client):
    """Test Creative Juices initialize works without authentication."""
    mcp_request = {
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"},
        },
        "id": 1,
    }

    response = client.post(
        "/mcp/creative-juices/serve",
        data=json.dumps(mcp_request),
        content_type="application/json",
    )

    assert response.status_code == 200
    data = response.json()

    assert data["jsonrpc"] == "2.0"
    assert "result" in data
    assert data["result"]["serverInfo"]["name"] == "creative-juices"


@pytest.mark.django_db
def test_creative_juices_tools_list(client):
    """Test Creative Juices tools/list returns available tools."""
    mcp_request = {"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 2}

    response = client.post(
        "/mcp/creative-juices/serve",
        data=json.dumps(mcp_request),
        content_type="application/json",
    )

    assert response.status_code == 200
    data = response.json()

    assert "result" in data
    assert "tools" in data["result"]

    # Verify creative tools are present
    tool_names = [tool["name"] for tool in data["result"]["tools"]]
    assert "get_inspiration" in tool_names
    assert "think_outside_the_box" in tool_names
    assert "reality_check" in tool_names


# End-to-End Integration Tests


@pytest.mark.django_db
def test_complete_cto_tools_session_with_oauth(client, valid_bearer_token):
    """Test complete MCP session flow with OAuth authentication.

    This simulates the EXACT flow that Claude Code follows:
    1. GET server info to discover capabilities
    2. Initialize with Bearer token
    3. Send notifications/initialized (critical step that was missing!)
    4. List available tools
    """
    # Step 1: Discover server info
    info_response = client.get("/mcp/cto-tools/serve")
    assert info_response.status_code == 200
    info = info_response.json()
    assert info["authentication"] is True

    # Step 2: Initialize session with authentication
    init_request = {
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "claude-code", "version": "1.0"},
        },
        "id": 1,
    }

    init_response = client.post(
        "/mcp/cto-tools/serve",
        data=json.dumps(init_request),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {valid_bearer_token}",
    )

    assert init_response.status_code == 200
    init_data = init_response.json()
    assert "result" in init_data
    assert "capabilities" in init_data["result"]

    # Step 3: Send initialized notification (what Claude Code does after initialize)
    notification_request = {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    }

    notification_response = client.post(
        "/mcp/cto-tools/serve",
        data=json.dumps(notification_request),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {valid_bearer_token}",
    )

    # Notifications must succeed or the connection fails
    assert notification_response.status_code == 200
    assert notification_response.json()["jsonrpc"] == "2.0"

    # Step 4: List tools (now the server is fully initialized)
    tools_request = {"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 2}

    tools_response = client.post(
        "/mcp/cto-tools/serve",
        data=json.dumps(tools_request),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {valid_bearer_token}",
    )

    assert tools_response.status_code == 200
    tools_data = tools_response.json()
    assert "result" in tools_data
    assert "tools" in tools_data["result"]
    assert len(tools_data["result"]["tools"]) > 0


@pytest.mark.django_db
def test_authentication_mismatch_detected(client):
    """Test that authentication requirements are consistent across endpoints.

    This test ensures the server info matches the actual authentication behavior,
    preventing the bug where GET said authentication=false but POST required auth.
    """
    # Get server info
    info_response = client.get("/mcp/cto-tools/serve")
    info = info_response.json()

    # Try to make authenticated request
    mcp_request = {"jsonrpc": "2.0", "method": "initialize", "params": {}, "id": 1}

    post_response = client.post(
        "/mcp/cto-tools/serve",
        data=json.dumps(mcp_request),
        content_type="application/json",
    )

    # If GET says authentication is required, POST should require it
    if info["authentication"]:
        assert (
            post_response.status_code == 401
        ), "Server advertises authentication required but POST doesn't enforce it"
    else:
        assert (
            post_response.status_code == 200
        ), "Server advertises no authentication but POST requires it"


@pytest.mark.django_db
def test_server_info_matches_manifest(client):
    """Test server info is consistent with manifest declarations.

    The CTO Tools manifest declares OAuth authentication, so the server
    must advertise authentication=true to be consistent.
    """
    response = client.get("/mcp/cto-tools/serve")
    data = response.json()

    # CTO Tools has OAuth in manifest, must require authentication
    assert (
        data["authentication"] is True
    ), "CTO Tools manifest declares OAuth but server says authentication=false"
