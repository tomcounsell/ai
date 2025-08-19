"""
Unit tests for the LoopsClient
"""

from unittest.mock import MagicMock, patch

import pytest
import requests
from django.test import TestCase

from apps.integration.loops.client import LoopsAPIError, LoopsClient


class LoopsClientDebugModeTestCase(TestCase):
    """Test LoopsClient in DEBUG mode"""

    def setUp(self):
        super().setUp()
        self.client = LoopsClient(api_key="test_key", debug_mode=True)

    def test_transactional_email_debug_mode(self):
        """Test transactional_email in DEBUG mode"""
        result = self.client.transactional_email(
            to_email="test@example.com",
            transactional_id="test_id",
            data_variables={"key": "value"},
        )
        assert result == {"success": True}

    def test_event_debug_mode(self):
        """Test event in DEBUG mode"""
        result = self.client.event(
            to_email="test@example.com",
            event_name="test_event",
            event_properties={"prop": "value"},
        )
        assert result == {"success": True}


class LoopsClientLiveTestCase(TestCase):
    """Test LoopsClient in live mode with mocked requests"""

    def setUp(self):
        super().setUp()
        self.client = LoopsClient(api_key="test_key", debug_mode=False)

    @patch("apps.integration.loops.client.requests.request")
    def test_transactional_email_success(self, mock_request):
        """Test successful transactional_email request"""
        # Setup the mock
        mock_response = MagicMock()
        mock_response.json.return_value = {"success": True}
        mock_request.return_value = mock_response

        # Call the method
        result = self.client.transactional_email(
            to_email="test@example.com",
            transactional_id="test_id",
            data_variables={"key": "value"},
        )

        # Verify the result and request
        assert result == {"success": True}
        mock_request.assert_called_once()
        call_args = mock_request.call_args
        assert call_args[0][0] == "POST"
        assert call_args[0][1].endswith("/transactional")
        assert call_args[1]["json"]["email"] == "test@example.com"
        assert call_args[1]["json"]["transactionalId"] == "test_id"

    @patch("apps.integration.loops.client.requests.request")
    def test_transactional_email_failure(self, mock_request):
        """Test failed transactional_email request"""
        # Setup the mock
        mock_response = MagicMock()
        mock_response.json.return_value = {"success": False, "message": "API error"}
        mock_request.return_value = mock_response

        # Call the method and check for exception
        with pytest.raises(LoopsAPIError, match="API error"):
            self.client.transactional_email(
                to_email="test@example.com",
                transactional_id="test_id",
                data_variables={"key": "value"},
            )

    @patch("apps.integration.loops.client.requests.request")
    def test_event_success(self, mock_request):
        """Test successful event request"""
        # Setup the mock
        mock_response = MagicMock()
        mock_response.json.return_value = {"success": True}
        mock_request.return_value = mock_response

        # Call the method
        result = self.client.event(
            to_email="test@example.com",
            event_name="test_event",
            event_properties={"prop": "value"},
        )

        # Verify the result and request
        assert result == {"success": True}
        mock_request.assert_called_once()
        call_args = mock_request.call_args
        assert call_args[0][0] == "POST"
        assert call_args[0][1].endswith("/events/send")
        assert call_args[1]["json"]["email"] == "test@example.com"
        assert call_args[1]["json"]["eventName"] == "test_event"

    @patch("apps.integration.loops.client.requests.request")
    def test_network_error(self, mock_request):
        """Test network error handling"""
        # Setup the mock to raise an exception
        mock_request.side_effect = requests.RequestException("Network error")

        # Call the method and check for exception
        with pytest.raises(LoopsAPIError, match="Network error"):
            self.client.event(to_email="test@example.com", event_name="test_event")

    @patch("apps.integration.loops.client.requests.request")
    def test_api_key_validation(self, mock_request):
        """Test API key validation endpoint"""
        # Setup the mock
        mock_response = MagicMock()
        mock_response.json.return_value = {"success": True}
        mock_request.return_value = mock_response

        # Call the method
        result = self.client.test_api_key()

        # Verify the request
        assert result == {"success": True}
        mock_request.assert_called_once()
        call_args = mock_request.call_args
        assert call_args[0][0] == "GET"
        assert call_args[0][1].endswith("/api-key")
