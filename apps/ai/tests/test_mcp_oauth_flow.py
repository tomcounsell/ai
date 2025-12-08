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
from django.urls import reverse


@pytest.fixture
def client():
    """Return Django test client."""
    return Client()


@pytest.fixture
def pkce_params():
    """Generate PKCE code verifier and challenge for testing."""
    # Generate code verifier (RFC 7636 - 43-128 characters)
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('utf-8').rstrip('=')

    # Generate S256 code challenge
    challenge_bytes = hashlib.sha256(code_verifier.encode('utf-8')).digest()
    code_challenge = base64.urlsafe_b64encode(challenge_bytes).decode('utf-8').rstrip('=')

    return {
        'code_verifier': code_verifier,
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256'
    }


# OAuth Authorization Server Metadata Tests (RFC 8414)


@pytest.mark.django_db
def test_oauth_metadata_endpoint_exists(client):
    """Test OAuth metadata endpoint is accessible."""
    response = client.get('/.well-known/oauth-authorization-server')

    assert response.status_code == 200
    assert response['Content-Type'] == 'application/json'


@pytest.mark.django_db
def test_oauth_metadata_contains_required_fields(client):
    """Test OAuth metadata contains all required fields."""
    response = client.get('/.well-known/oauth-authorization-server')
    data = response.json()

    # Required fields per RFC 8414
    required_fields = [
        'issuer',
        'authorization_endpoint',
        'token_endpoint',
        'scopes_supported',
        'response_types_supported',
        'grant_types_supported',
    ]

    for field in required_fields:
        assert field in data, f"Missing required field: {field}"


@pytest.mark.django_db
def test_oauth_metadata_advertises_pkce_support(client):
    """Test OAuth metadata advertises PKCE S256 support."""
    response = client.get('/.well-known/oauth-authorization-server')
    data = response.json()

    assert 'code_challenge_methods_supported' in data
    assert 'S256' in data['code_challenge_methods_supported']


@pytest.mark.django_db
def test_oauth_metadata_advertises_registration_endpoint(client):
    """Test OAuth metadata includes registration endpoint for dynamic client registration."""
    response = client.get('/.well-known/oauth-authorization-server')
    data = response.json()

    assert 'registration_endpoint' in data
    assert '/mcp/oauth/register' in data['registration_endpoint']


@pytest.mark.django_db
def test_oauth_metadata_advertises_correct_endpoints(client):
    """Test OAuth metadata endpoint URLs are properly formed."""
    response = client.get('/.well-known/oauth-authorization-server')
    data = response.json()

    # Check endpoint paths
    assert data['authorization_endpoint'].endswith('/mcp/oauth/authorize')
    assert data['token_endpoint'].endswith('/mcp/oauth/token')
    assert data['registration_endpoint'].endswith('/mcp/oauth/register')


# Dynamic Client Registration Tests (RFC 7591)


@pytest.mark.django_db
def test_client_registration_succeeds(client):
    """Test dynamic client registration returns client credentials."""
    registration_data = {
        'redirect_uris': ['http://localhost:3000/callback'],
        'grant_types': ['authorization_code'],
    }

    response = client.post(
        '/mcp/oauth/register',
        data=json.dumps(registration_data),
        content_type='application/json'
    )

    assert response.status_code == 200
    data = response.json()

    # Required response fields per RFC 7591
    assert 'client_id' in data
    assert len(data['client_id']) > 0
    assert 'client_id_issued_at' in data
    assert isinstance(data['client_id_issued_at'], int)


@pytest.mark.django_db
def test_client_registration_returns_metadata(client):
    """Test client registration response includes client metadata."""
    registration_data = {
        'redirect_uris': ['http://localhost:3000/callback'],
        'grant_types': ['authorization_code'],
    }

    response = client.post(
        '/mcp/oauth/register',
        data=json.dumps(registration_data),
        content_type='application/json'
    )

    data = response.json()

    # Verify metadata fields
    assert data['redirect_uris'] == registration_data['redirect_uris']
    assert data['grant_types'] == ['authorization_code']
    assert data['response_types'] == ['code']
    assert data['token_endpoint_auth_method'] == 'none'


@pytest.mark.django_db
def test_client_registration_rejects_invalid_json(client):
    """Test client registration rejects malformed JSON."""
    response = client.post(
        '/mcp/oauth/register',
        data='invalid json',
        content_type='application/json'
    )

    assert response.status_code == 400
    data = response.json()
    assert 'error' in data
    assert data['error'] == 'invalid_request'


# Authorization Endpoint Tests


@pytest.mark.django_db
def test_authorization_endpoint_redirects_with_code(client):
    """Test authorization endpoint returns authorization code via redirect."""
    response = client.get('/mcp/oauth/authorize', {
        'client_id': 'test_client',
        'redirect_uri': 'http://localhost:3000/callback',
        'response_type': 'code',
        'state': 'test_state_xyz',
    })

    assert response.status_code == 302

    # Parse redirect URL
    location = response['Location']
    parsed = urlparse(location)
    params = parse_qs(parsed.query)

    assert 'code' in params
    assert len(params['code'][0]) > 0
    assert params['state'][0] == 'test_state_xyz'


@pytest.mark.django_db
def test_authorization_endpoint_accepts_pkce_parameters(client, pkce_params):
    """Test authorization endpoint accepts PKCE code challenge."""
    response = client.get('/mcp/oauth/authorize', {
        'client_id': 'test_client',
        'redirect_uri': 'http://localhost:3000/callback',
        'response_type': 'code',
        'state': 'test_state',
        'code_challenge': pkce_params['code_challenge'],
        'code_challenge_method': pkce_params['code_challenge_method'],
    })

    assert response.status_code == 302

    # Verify code is still returned
    location = response['Location']
    parsed = urlparse(location)
    params = parse_qs(parsed.query)
    assert 'code' in params


@pytest.mark.django_db
def test_authorization_endpoint_requires_redirect_uri(client):
    """Test authorization endpoint validates redirect_uri presence."""
    response = client.get('/mcp/oauth/authorize', {
        'client_id': 'test_client',
        'response_type': 'code',
    })

    assert response.status_code == 400
    data = response.json()
    assert data['error'] == 'invalid_request'
    assert 'redirect_uri' in data['error_description']


@pytest.mark.django_db
def test_authorization_endpoint_preserves_state(client):
    """Test authorization endpoint preserves state parameter in redirect."""
    state_value = 'random_state_' + secrets.token_urlsafe(16)

    response = client.get('/mcp/oauth/authorize', {
        'client_id': 'test_client',
        'redirect_uri': 'http://localhost:3000/callback',
        'response_type': 'code',
        'state': state_value,
    })

    location = response['Location']
    parsed = urlparse(location)
    params = parse_qs(parsed.query)

    assert params['state'][0] == state_value


# Token Endpoint Tests


@pytest.mark.django_db
def test_token_endpoint_issues_access_token(client):
    """Test token endpoint exchanges authorization code for access token."""
    token_request = {
        'grant_type': 'authorization_code',
        'code': 'test_auth_code',
        'redirect_uri': 'http://localhost:3000/callback',
    }

    response = client.post(
        '/mcp/oauth/token',
        data=json.dumps(token_request),
        content_type='application/json'
    )

    assert response.status_code == 200
    data = response.json()

    # Required token response fields
    assert 'access_token' in data
    assert len(data['access_token']) > 0
    assert data['token_type'] == 'Bearer'
    assert 'expires_in' in data
    assert isinstance(data['expires_in'], int)


@pytest.mark.django_db
def test_token_endpoint_accepts_pkce_verifier(client, pkce_params):
    """Test token endpoint accepts PKCE code verifier."""
    token_request = {
        'grant_type': 'authorization_code',
        'code': 'test_auth_code',
        'redirect_uri': 'http://localhost:3000/callback',
        'code_verifier': pkce_params['code_verifier'],
    }

    response = client.post(
        '/mcp/oauth/token',
        data=json.dumps(token_request),
        content_type='application/json'
    )

    assert response.status_code == 200
    data = response.json()
    assert 'access_token' in data


@pytest.mark.django_db
def test_token_endpoint_validates_grant_type(client):
    """Test token endpoint rejects unsupported grant types."""
    token_request = {
        'grant_type': 'client_credentials',  # Not supported
        'code': 'test_auth_code',
    }

    response = client.post(
        '/mcp/oauth/token',
        data=json.dumps(token_request),
        content_type='application/json'
    )

    assert response.status_code == 400
    data = response.json()
    assert data['error'] == 'unsupported_grant_type'


@pytest.mark.django_db
def test_token_endpoint_requires_code(client):
    """Test token endpoint validates code presence."""
    token_request = {
        'grant_type': 'authorization_code',
        'redirect_uri': 'http://localhost:3000/callback',
    }

    response = client.post(
        '/mcp/oauth/token',
        data=json.dumps(token_request),
        content_type='application/json'
    )

    assert response.status_code == 400
    data = response.json()
    assert data['error'] == 'invalid_request'
    assert 'code' in data['error_description']


@pytest.mark.django_db
def test_token_endpoint_rejects_invalid_json(client):
    """Test token endpoint rejects malformed JSON."""
    response = client.post(
        '/mcp/oauth/token',
        data='invalid json',
        content_type='application/json'
    )

    assert response.status_code == 400
    data = response.json()
    assert data['error'] == 'invalid_request'


# End-to-End Flow Tests


@pytest.mark.django_db
def test_complete_oauth_flow_without_pkce(client):
    """Test complete OAuth flow: metadata -> register -> authorize -> token."""
    # Step 1: Discover OAuth server metadata
    metadata_response = client.get('/.well-known/oauth-authorization-server')
    assert metadata_response.status_code == 200
    metadata = metadata_response.json()

    # Step 2: Register client
    registration_response = client.post(
        '/mcp/oauth/register',
        data=json.dumps({'redirect_uris': ['http://localhost:3000/callback']}),
        content_type='application/json'
    )
    assert registration_response.status_code == 200
    client_data = registration_response.json()
    client_id = client_data['client_id']

    # Step 3: Get authorization code
    auth_response = client.get('/mcp/oauth/authorize', {
        'client_id': client_id,
        'redirect_uri': 'http://localhost:3000/callback',
        'response_type': 'code',
        'state': 'test_state',
    })
    assert auth_response.status_code == 302

    # Extract authorization code from redirect
    location = auth_response['Location']
    parsed = urlparse(location)
    params = parse_qs(parsed.query)
    auth_code = params['code'][0]

    # Step 4: Exchange code for token
    token_response = client.post(
        '/mcp/oauth/token',
        data=json.dumps({
            'grant_type': 'authorization_code',
            'code': auth_code,
            'redirect_uri': 'http://localhost:3000/callback',
        }),
        content_type='application/json'
    )
    assert token_response.status_code == 200
    token_data = token_response.json()
    assert 'access_token' in token_data
    assert token_data['token_type'] == 'Bearer'


@pytest.mark.django_db
def test_complete_oauth_flow_with_pkce(client, pkce_params):
    """Test complete OAuth flow with PKCE: metadata -> register -> authorize -> token."""
    # Step 1: Discover OAuth server metadata
    metadata_response = client.get('/.well-known/oauth-authorization-server')
    metadata = metadata_response.json()
    assert 'S256' in metadata['code_challenge_methods_supported']

    # Step 2: Register client
    registration_response = client.post(
        '/mcp/oauth/register',
        data=json.dumps({'redirect_uris': ['http://localhost:3000/callback']}),
        content_type='application/json'
    )
    client_data = registration_response.json()
    client_id = client_data['client_id']

    # Step 3: Get authorization code with PKCE challenge
    auth_response = client.get('/mcp/oauth/authorize', {
        'client_id': client_id,
        'redirect_uri': 'http://localhost:3000/callback',
        'response_type': 'code',
        'state': 'test_state',
        'code_challenge': pkce_params['code_challenge'],
        'code_challenge_method': pkce_params['code_challenge_method'],
    })
    assert auth_response.status_code == 302

    # Extract authorization code
    location = auth_response['Location']
    parsed = urlparse(location)
    params = parse_qs(parsed.query)
    auth_code = params['code'][0]

    # Step 4: Exchange code for token with PKCE verifier
    token_response = client.post(
        '/mcp/oauth/token',
        data=json.dumps({
            'grant_type': 'authorization_code',
            'code': auth_code,
            'redirect_uri': 'http://localhost:3000/callback',
            'code_verifier': pkce_params['code_verifier'],
        }),
        content_type='application/json'
    )
    assert token_response.status_code == 200
    token_data = token_response.json()
    assert 'access_token' in token_data
    assert token_data['token_type'] == 'Bearer'
    assert 'mcp:read' in token_data['scope']
    assert 'mcp:write' in token_data['scope']


@pytest.mark.django_db
def test_oauth_flow_matches_claude_code_requirements(client, pkce_params):
    """Test OAuth flow meets all Claude Code HTTP transport requirements.

    This test validates that the OAuth implementation satisfies all requirements
    for Claude Code to successfully connect via HTTP transport:
    1. OAuth metadata discovery with registration_endpoint
    2. PKCE S256 support
    3. Dynamic client registration
    4. Authorization code flow
    """
    # Requirement 1: OAuth metadata with registration endpoint and PKCE support
    metadata = client.get('/.well-known/oauth-authorization-server').json()
    assert 'registration_endpoint' in metadata, "Claude Code requires registration_endpoint"
    assert 'S256' in metadata.get('code_challenge_methods_supported', []), \
        "Claude Code requires PKCE S256 support"

    # Requirement 2: Dynamic client registration
    registration = client.post(
        '/mcp/oauth/register',
        data=json.dumps({'redirect_uris': ['http://localhost:3000/callback']}),
        content_type='application/json'
    ).json()
    assert 'client_id' in registration, "Claude Code requires client_id from registration"

    # Requirement 3: PKCE-enabled authorization
    auth_response = client.get('/mcp/oauth/authorize', {
        'client_id': registration['client_id'],
        'redirect_uri': 'http://localhost:3000/callback',
        'response_type': 'code',
        'code_challenge': pkce_params['code_challenge'],
        'code_challenge_method': 'S256',
    })
    assert auth_response.status_code == 302, "Authorization must redirect with code"

    # Requirement 4: Token exchange with PKCE verifier
    auth_code = parse_qs(urlparse(auth_response['Location']).query)['code'][0]
    token = client.post(
        '/mcp/oauth/token',
        data=json.dumps({
            'grant_type': 'authorization_code',
            'code': auth_code,
            'code_verifier': pkce_params['code_verifier'],
        }),
        content_type='application/json'
    ).json()
    assert 'access_token' in token, "Claude Code requires access_token"
    assert token['token_type'] == 'Bearer', "Claude Code requires Bearer token type"
