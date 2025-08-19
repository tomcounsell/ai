"""
Example views demonstrating HTMX Out-of-Band (OOB) swaps.

These views showcase different ways to use OOB swaps for:
- Toasts
- Alerts
- Modals
- Navigation active state
"""

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.urls import path

from apps.public.views.helpers.htmx_view import HTMXView
from apps.public.views.helpers.main_content_view import MainContentView


class OOBExamplesPage(MainContentView):
    """Main page showing OOB examples."""

    template_name = "components/oob/examples.html"
    page_title = "OOB Examples"

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        self.context.update(
            {
                "page_description": "Examples of HTMX Out-of-Band (OOB) swaps",
            }
        )
        return self.render(request)


class ShowToastExample(HTMXView):
    """Example view that shows a toast message."""

    has_oob = True
    show_toast = True

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        # Add a message based on the type parameter
        message_type = request.GET.get("type", "success")

        message_text = {
            "success": "Operation was successful!",
            "info": "Here's some information for you.",
            "warning": "Warning: This is a cautionary message.",
            "error": "Error: Something went wrong.",
        }.get(message_type, "This is a notification.")

        if message_type == "success":
            messages.success(request, message_text)
        elif message_type == "info":
            messages.info(request, message_text)
        elif message_type == "warning":
            messages.warning(request, message_text)
        elif message_type == "error":
            messages.error(request, message_text)

        # Return empty response since we're just showing a toast
        return HttpResponse("")


class ShowAlertExample(HTMXView):
    """Example view that shows an alert."""

    template_name = "layout/alerts/alert.html"

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        # Get alert type from the request
        alert_type = request.GET.get("type", "info")

        # Prepare alert message
        alert_message = {
            "success": "Your changes have been saved successfully.",
            "info": "Here's some information about this feature.",
            "warning": "Please note this will affect your account settings.",
            "error": "We couldn't complete the requested action.",
        }.get(alert_type, "This is an important notice.")

        # Set context for the alert template
        self.context.update(
            {
                "alert_type": alert_type,
                "alert_message": alert_message,
                "dismissible": True,
            }
        )

        # Render response
        return self.render(request)


class ShowModalExample(HTMXView):
    """Example view that shows a modal."""

    template_name = "components/modals/examples.html"
    include_modals = True

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        # Get modal type from the request
        modal_type = request.GET.get("type", "basic")

        # Set context based on modal type
        self.context.update(
            {
                "modal_type": modal_type,
                "modal_title": {
                    "basic": "Basic Modal Example",
                    "form": "Form Modal Example",
                    "confirm": "Confirmation Modal Example",
                    "large": "Large Content Modal Example",
                }.get(modal_type, "Modal Example"),
            }
        )

        # Add any additional context specific to modal type
        if modal_type == "form":
            self.context["form_fields"] = [
                {"name": "name", "label": "Your Name", "type": "text"},
                {"name": "email", "label": "Email Address", "type": "email"},
                {"name": "message", "label": "Message", "type": "textarea"},
            ]

        # Render response
        return self.render(request)


class UpdateNavExample(HTMXView):
    """Example view that updates the navigation active state."""

    has_oob = True

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        # Get section from the request
        section = request.GET.get("section", "home")

        # Set active navigation section
        self.active_nav = section

        # Return a message indicating what happened
        return HttpResponse(
            f"<div>Navigation updated to: <strong>{section}</strong></div>"
        )


class CombinedExample(HTMXView):
    """Example view that combines multiple OOB updates."""

    has_oob = True
    show_toast = True
    include_modals = True

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        # Add a toast message
        messages.success(request, "Multiple OOB updates successfully demonstrated!")

        # Set active navigation
        self.active_nav = "home"

        # Return combined response
        return HttpResponse(
            "<div class='p-4 bg-green-50 rounded-md'>Multiple OOB elements updated</div>"
        )


# URL patterns for OOB examples
urlpatterns = [
    path("oob/examples/", OOBExamplesPage.as_view(), name="oob-examples"),
    path("oob/toast/", ShowToastExample.as_view(), name="oob-toast"),
    path("oob/alert/", ShowAlertExample.as_view(), name="oob-alert"),
    path("oob/modal/", ShowModalExample.as_view(), name="oob-modal"),
    path("oob/nav/", UpdateNavExample.as_view(), name="oob-nav"),
    path("oob/combined/", CombinedExample.as_view(), name="oob-combined"),
]
