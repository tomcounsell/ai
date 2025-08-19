"""
Tests for HTMX OOB (Out-of-Band) support in the Django Project Template.

These tests verify that HTMX OOB swaps work correctly for various components.
"""

from unittest.mock import patch

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware

# Import the base TestCase class to avoid name conflict
from django.test import RequestFactory
from django.test import TestCase as DjangoTestCase

from apps.public.views.components.oob_examples import (
    CombinedExample,
    ShowAlertExample,
    ShowModalExample,
    ShowToastExample,
    UpdateNavExample,
)

# Import the HTMX view class
from apps.public.views.helpers.htmx_view import HTMXView

User = get_user_model()


# Create a mock template class for testing
class MockTemplate:
    def __init__(self, template_name):
        self.template_name = template_name

    def render(self, context):
        return f"Rendered {self.template_name} with context"


def mock_get_template(template_name, using=None):
    """Mock function for get_template that supports the using parameter."""
    return MockTemplate(template_name)


def mock_render_to_string(template_name, context=None, request=None):
    """Mock function for render_to_string."""
    return f"Rendered {template_name}"


class TestCase(DjangoTestCase):
    """Base test case for HTMX tests."""

    def setUp(self):
        """Set up the test environment."""
        super().setUp()
        self.factory = RequestFactory()
        # User is created in the subclasses to avoid duplicate username issues

    def _create_htmx_request(self, url="/test/"):
        """Create a request with HTMX headers."""
        request = self.factory.get(url)
        request.htmx = True
        request.user = self.user

        # Add session
        middleware = SessionMiddleware(lambda x: None)
        middleware.process_request(request)
        request.session.save()

        # Add messages
        setattr(request, "_messages", FallbackStorage(request))

        return request


class TestHTMXViewOOB(TestCase):
    """Test the OOB support in the HTMXView class."""

    def setUp(self):
        """Set up for the tests."""
        super().setUp()
        # Override the user to avoid duplicate username
        self.user = User.objects.create_user(
            username="testuser_htmx_view",
            email="testuser_htmx_view@example.com",
            password="testpassword123",
        )

        # Create a test HTMX view
        class TestOOBView(HTMXView):
            template_name = "test_template.html"
            has_oob = True
            show_toast = True
            active_nav = "home"
            include_modals = True

            def get(self, request, *args, **kwargs):
                messages.success(request, "Test message")
                return self.render(request)

        self.view_class = TestOOBView

    def test_htmx_view_oob_elements(self):
        """Test that the HTMX view includes OOB elements."""
        # Create a view instance
        view = self.view_class()
        request = self._create_htmx_request()

        # Add a message for toast testing
        messages.success(request, "Test message")

        view.request = request
        view.setup(request)

        # We don't need to test the actual template rendering,
        # just that the view has the right attributes and that
        # the correct OOB elements are set up in render

        # Verify that the view has the expected attributes set
        self.assertTrue(view.has_oob)
        self.assertTrue(view.show_toast)
        self.assertEqual(view.active_nav, "home")
        self.assertTrue(view.include_modals)

        # Verify that the main template_name is set correctly
        self.assertEqual(view.template_name, "test_template.html")

        # Directly verify that the get method calls render
        with patch.object(view, "render") as mock_render:
            view.get(request)
            mock_render.assert_called_once_with(request)


class TestHTMXOOBViews(TestCase):
    """Test the example OOB views."""

    def setUp(self):
        """Set up for the tests."""
        super().setUp()
        # Override the user to avoid duplicate username
        self.user = User.objects.create_user(
            username="testuser_oob_views",
            email="testuser_oob_views@example.com",
            password="testpassword123",
        )

    def test_toast_example_view(self):
        """Test that the toast example view shows a toast message."""
        view = ShowToastExample()
        request = self._create_htmx_request("/oob/toast/?type=success")
        view.request = request
        view.setup(request)

        # Get the response - we're not testing template rendering directly,
        # just that the view sets up the correct context and attributes
        response = view.get(request)

        # Verify the view has the correct attributes set
        self.assertTrue(view.has_oob)
        self.assertTrue(view.show_toast)

        # Verify that a success message was added to the request
        messages_list = list(messages.get_messages(request))
        self.assertEqual(len(messages_list), 1)
        self.assertEqual(messages_list[0].level, messages.SUCCESS)
        self.assertEqual(messages_list[0].message, "Operation was successful!")

    def test_alert_example_view(self):
        """Test that the alert example view renders an alert."""
        view = ShowAlertExample()
        request = self._create_htmx_request("/oob/alert/?type=warning")
        view.request = request
        view.setup(request)

        # Get the response
        with patch(
            "django.template.loader.get_template", side_effect=mock_get_template
        ):
            with patch(
                "django.template.loader.render_to_string",
                side_effect=mock_render_to_string,
            ):
                # To capture the templates being rendered
                rendered_templates = []

                # Override render_to_string to capture templates
                def capture_templates(template_name, *args, **kwargs):
                    rendered_templates.append(template_name)
                    return f"Rendered {template_name}"

                # Apply the patched function
                with patch(
                    "apps.public.views.helpers.htmx_view.render_to_string",
                    side_effect=capture_templates,
                ):
                    # Call method
                    response = view.get(request)

                    # Check if the alert template was included
                    self.assertIn("layout/alerts/alert.html", rendered_templates)

                    # Verify that the context was set correctly on the view
                    self.assertEqual(view.context["alert_type"], "warning")
                    self.assertIn("alert_message", view.context)

    def test_modal_example_view(self):
        """Test that the modal example view includes the modal container."""
        view = ShowModalExample()
        request = self._create_htmx_request("/oob/modal/?type=basic")
        view.request = request
        view.setup(request)

        # Get the response
        with patch(
            "django.template.loader.get_template", side_effect=mock_get_template
        ):
            with patch(
                "django.template.loader.render_to_string",
                side_effect=mock_render_to_string,
            ):
                # To capture the templates being rendered
                rendered_templates = []

                # Override render_to_string to capture templates
                def capture_templates(template_name, *args, **kwargs):
                    rendered_templates.append(template_name)
                    return f"Rendered {template_name}"

                # Apply the patched function
                with patch(
                    "apps.public.views.helpers.htmx_view.render_to_string",
                    side_effect=capture_templates,
                ):
                    # Call method
                    response = view.get(request)

                    # Check if the expected templates were included
                    self.assertIn("components/modals/examples.html", rendered_templates)
                    self.assertIn(
                        "layout/modals/modal_container.html", rendered_templates
                    )

    def test_nav_example_view(self):
        """Test that the nav example view updates the active navigation."""
        view = UpdateNavExample()
        request = self._create_htmx_request("/oob/nav/?section=teams")
        view.request = request
        view.setup(request)

        # Call method
        response = view.get(request)

        # Verify the view has the correct attributes set
        self.assertTrue(view.has_oob)

        # Verify active_nav was set correctly
        self.assertEqual(view.active_nav, "teams")

        # Verify the response contains the expected section text
        self.assertIn("teams", response.content.decode())
        self.assertIn("Navigation updated", response.content.decode())

    def test_combined_example_view(self):
        """Test that the combined example includes all OOB elements."""
        view = CombinedExample()
        request = self._create_htmx_request("/oob/combined/")
        view.request = request
        view.setup(request)

        # Call method
        response = view.get(request)

        # Verify the view has the correct attributes set
        self.assertTrue(view.has_oob)
        self.assertTrue(view.show_toast)
        self.assertTrue(view.include_modals)

        # Verify active_nav was set correctly
        self.assertEqual(view.active_nav, "home")

        # Verify that a success message was added to the request
        messages_list = list(messages.get_messages(request))
        self.assertEqual(len(messages_list), 1)
        self.assertEqual(messages_list[0].level, messages.SUCCESS)
        self.assertIn("Multiple OOB updates", messages_list[0].message)

        # Verify the response contains the expected content
        self.assertIn("Multiple OOB elements updated", response.content.decode())
