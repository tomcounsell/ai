"""Browser-based E2E tests for AI chat interface."""

import os
import time
from unittest.mock import patch

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


class AIChaBrowserE2ETestCase(StaticLiveServerTestCase):
    """Browser-based E2E tests for AI chat interface."""

    @classmethod
    def setUpClass(cls):
        """Set up the test class."""
        super().setUpClass()
        # Use Chrome in headless mode for CI
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        cls.selenium = webdriver.Chrome(options=options)
        cls.selenium.implicitly_wait(10)

    @classmethod
    def tearDownClass(cls):
        """Tear down the test class."""
        cls.selenium.quit()
        super().tearDownClass()

    def test_chat_interface_loads(self):
        """Test that the chat interface loads correctly."""
        self.selenium.get(f"{self.live_server_url}/ai/test/")

        # Check page title
        self.assertIn("AI Chat Test", self.selenium.title)

        # Check main elements are present
        heading = self.selenium.find_element(By.TAG_NAME, "h1")
        self.assertEqual(heading.text, "AI Chat Test (No Database)")

        # Check chat messages container
        messages_div = self.selenium.find_element(By.ID, "chat-messages")
        self.assertIsNotNone(messages_div)

        # Check input form
        message_input = self.selenium.find_element(By.NAME, "message")
        self.assertIsNotNone(message_input)

        send_button = self.selenium.find_element(By.XPATH, '//button[@type="submit"]')
        self.assertEqual(send_button.text, "Send")

    @patch("apps.ai.views.test_chat.process_chat_message_sync")
    def test_send_message_and_receive_response(self, mock_process):
        """Test sending a message and receiving a response."""
        mock_process.return_value = "Hello! I am your AI assistant."

        self.selenium.get(f"{self.live_server_url}/ai/test/")

        # Find and fill the message input
        message_input = self.selenium.find_element(By.NAME, "message")
        message_input.send_keys("Hello AI!")

        # Submit the form
        send_button = self.selenium.find_element(By.XPATH, '//button[@type="submit"]')
        send_button.click()

        # Wait for the response to appear
        wait = WebDriverWait(self.selenium, 10)

        # Check that user message appears
        user_message = wait.until(
            EC.presence_of_element_located(
                (By.XPATH, '//div[contains(@class, "bg-blue-600")]')
            )
        )
        self.assertIn("Hello AI!", user_message.text)

        # Check that AI response appears
        ai_response = wait.until(
            EC.presence_of_element_located(
                (By.XPATH, '//div[contains(@class, "bg-gray-200")]')
            )
        )
        self.assertIn("Hello! I am your AI assistant.", ai_response.text)

    def test_message_input_clears_after_send(self):
        """Test that the message input clears after sending."""
        self.selenium.get(f"{self.live_server_url}/ai/test/")

        # Find and fill the message input
        message_input = self.selenium.find_element(By.NAME, "message")
        message_input.send_keys("Test message")

        # Submit the form
        send_button = self.selenium.find_element(By.XPATH, '//button[@type="submit"]')
        send_button.click()

        # Wait a moment for the form to reset
        time.sleep(1)

        # Check that input is cleared
        message_input = self.selenium.find_element(By.NAME, "message")
        self.assertEqual(message_input.get_attribute("value"), "")

    @patch.dict(os.environ, {}, clear=True)
    def test_api_key_warning_shown_when_not_configured(self):
        """Test that API key warning is shown when not configured."""
        self.selenium.get(f"{self.live_server_url}/ai/test/")

        # Check for warning message
        warning = self.selenium.find_element(
            By.XPATH, '//div[contains(@class, "bg-yellow-50")]//p'
        )
        self.assertIn("Set OPENAI_API_KEY", warning.text)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_api_key_warning_hidden_when_configured(self):
        """Test that API key warning is hidden when configured."""
        self.selenium.get(f"{self.live_server_url}/ai/test/")

        # Check that warning is not present
        warnings = self.selenium.find_elements(
            By.XPATH, '//div[contains(@class, "bg-yellow-50")]'
        )
        self.assertEqual(len(warnings), 0)

    @patch("apps.ai.views.test_chat.process_chat_message_sync")
    def test_multiple_messages_in_conversation(self, mock_process):
        """Test sending multiple messages in a conversation."""
        # Mock different responses
        mock_process.side_effect = [
            "Hello! How can I help you?",
            "The capital of France is Paris.",
            "You're welcome!",
        ]

        self.selenium.get(f"{self.live_server_url}/ai/test/")

        # Send first message
        message_input = self.selenium.find_element(By.NAME, "message")
        message_input.send_keys("Hello")
        message_input.send_keys(Keys.RETURN)

        time.sleep(1)

        # Send second message
        message_input = self.selenium.find_element(By.NAME, "message")
        message_input.send_keys("What is the capital of France?")
        message_input.send_keys(Keys.RETURN)

        time.sleep(1)

        # Send third message
        message_input = self.selenium.find_element(By.NAME, "message")
        message_input.send_keys("Thank you!")
        message_input.send_keys(Keys.RETURN)

        time.sleep(1)

        # Check all messages are present
        messages = self.selenium.find_elements(
            By.XPATH, '//div[@id="chat-messages"]//div[contains(@class, "rounded-lg")]'
        )

        # Should have 6 messages (3 user + 3 AI)
        self.assertEqual(len(messages), 6)

        # Verify message content
        message_texts = [msg.text for msg in messages]
        self.assertIn("Hello", message_texts)
        self.assertIn("Hello! How can I help you?", message_texts)
        self.assertIn("What is the capital of France?", message_texts)
        self.assertIn("The capital of France is Paris.", message_texts)
        self.assertIn("Thank you!", message_texts)
        self.assertIn("You're welcome!", message_texts)

    def test_chat_scrolls_to_bottom_on_new_message(self):
        """Test that chat scrolls to bottom when new messages are added."""
        self.selenium.get(f"{self.live_server_url}/ai/test/")

        # Send multiple messages to fill the chat
        for i in range(10):
            message_input = self.selenium.find_element(By.NAME, "message")
            message_input.send_keys(f"Message {i}")
            message_input.send_keys(Keys.RETURN)
            time.sleep(0.5)

        # Check that the chat container is scrolled to bottom
        messages_container = self.selenium.find_element(By.ID, "chat-messages")
        scroll_height = self.selenium.execute_script(
            "return arguments[0].scrollHeight", messages_container
        )
        scroll_top = self.selenium.execute_script(
            "return arguments[0].scrollTop", messages_container
        )
        client_height = self.selenium.execute_script(
            "return arguments[0].clientHeight", messages_container
        )

        # Allow for small difference due to rounding
        self.assertAlmostEqual(scroll_top + client_height, scroll_height, delta=50)
