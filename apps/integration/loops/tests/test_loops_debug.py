"""
Test script to verify Loops client behavior
"""

import logging
import os
import sys

import django

# Set up Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
django.setup()

# Set up logging
logging.basicConfig(level=logging.INFO)

# Test with DEBUG=True (should log instead of sending)
from django.conf import settings

# Import the LoopsClient
from apps.integration.loops.client import LoopsClient

print(f"DEBUG mode is: {settings.DEBUG}")


def test_basic():
    """Test basic client functionality"""
    client = LoopsClient()
    print("Testing transactional_email in current mode...")
    result = client.transactional_email(
        to_email="test@example.com",
        transactional_id="test_id",
        data_variables={"key": "value"},
        bcc=["bcc@example.com"],
    )
    print(f"Result: {result}")

    print("\nTesting event in current mode...")
    result = client.event(
        to_email="test@example.com",
        event_name="test_event",
        event_properties={"prop": "value"},
    )
    print(f"Result: {result}")


def test_custom_template_email():
    """Test a custom template email"""
    client = LoopsClient()

    print("\nTesting custom template email...")
    result = client.transactional_email(
        to_email="test@example.com",
        transactional_id="__test_custom_template_id__",
        data_variables={
            "user_name": "Test User",
            "confirmation_link": "https://example.com/confirm",
            # Additional example variables
            "company_name": "Example Company",
            "product_name": "Example Product",
        },
    )
    print(f"Result: {result}")


def test_notification_email():
    """Test notification email"""
    client = LoopsClient()

    print("\nTesting notification email...")
    result = client.transactional_email(
        to_email="test@example.com",
        transactional_id="__test_notification_id__",
        data_variables={
            "user_name": "Test User",
            "notification_message": "This is a test notification",
            "action_link": "https://example.com/action",
            # Additional metadata
            "_notification_type": "update",
            "_triggered_at": "March 20, 2025 14:32:00",
        },
    )
    print(f"Result: {result}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "custom":
            test_custom_template_email()
        elif sys.argv[1] == "notification":
            test_notification_email()
        else:
            print(f"Unknown test: {sys.argv[1]}")
            print("Available tests: custom, notification")
    else:
        test_basic()

    print("\nDone testing Loops client.")
