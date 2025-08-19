from typing import Dict, Optional

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.template.loader import render_to_string
from django_htmx import http as htmx

from apps.public.views.helpers.main_content_view import MainContentView


class HTMXView(MainContentView):
    """
    Base view class for handling HTMX requests with additional features for
    out-of-band (OOB) responses, URL updates, custom events, and multiple component rendering.

    Attributes:
        template_name: The main template to render
        oob_templates: Dictionary mapping target IDs to template paths
        push_url: URL to push to browser history
        has_oob: Whether the response includes OOB swaps
        active_nav: Active navigation section (e.g., 'home', 'teams', 'todos')
        show_toast: Whether to show toast messages in OOB
        include_modals: Whether to include modal container in OOB
    """

    template_name: str | None = None
    oob_templates: dict[str, str] | None = None  # Maps target IDs to template paths
    push_url: str | None = None  # URL to push to browser history
    has_oob: bool = True  # Whether to use OOB swaps
    active_nav: str | None = None  # Active navigation section
    show_toast: bool = True  # Whether to include toast messages
    include_modals: bool = False  # Whether to include modal container

    def dispatch(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        # Ensure this view only handles HTMX requests
        if not getattr(request, "htmx", False):
            raise NotImplementedError("This view is only accessible via HTMX requests.")

        # Ensure we're using the partial template for HTMX requests
        self.context["base_template"] = self.partial_template

        return super().dispatch(request, *args, **kwargs)

    def render(
        self,
        request: HttpRequest = None,
        template_name: str | None = None,
        context: dict | None = None,
        oob_templates: dict[str, str] | None = None,
        push_url: str | None = None,
    ) -> HttpResponse:
        """
        Render a main template with optional OOB templates in a combined response.

        :param request: Django HttpRequest object
        :param template_name: The main template to render (defaults to class attribute)
        :param context: Context data for the template(s)
        :param oob_templates: A dictionary of OOB templates with target IDs as keys (defaults to class attribute)
        :param push_url: URL to push to the history state (defaults to class attribute)
        :return: HttpResponse with combined template output and HTMX-specific modifications
        """
        request = request or self.request
        combined_context = {**self.context, **(context or {})}

        # Use class attributes as defaults if not explicitly provided
        template_name = template_name or self.template_name
        oob_templates = oob_templates or self.oob_templates or {}
        push_url = push_url or self.push_url

        # Render the main template if provided
        main_html = (
            render_to_string(template_name, combined_context, request=request)
            if template_name
            else ""
        )

        # Add standard OOB components based on view settings
        if self.has_oob:
            # Add toast messages if enabled and there are messages
            if (
                self.show_toast
                and messages.get_messages(request)
                and "toast-container" not in oob_templates
            ):
                oob_templates["toast-container"] = "layout/messages/toast.html"

            # Add navigation active state if explicitly specified in the view
            # This will override the context processor's value
            if self.active_nav:
                combined_context["active_section"] = self.active_nav

            # Add modal container if enabled
            if self.include_modals:
                oob_templates["modal-container"] = "layout/modals/modal_container.html"

        # Set context flag for OOB templates
        if len(oob_templates):
            combined_context["is_oob"] = True

        # Render each OOB template and wrap it in the OOB structure
        oob_html = ""
        for oob_target_id, oob_template in oob_templates.items():
            # Render the template with the same context
            template_html = render_to_string(
                oob_template, combined_context, request=request
            )

            # Wrap with OOB attribute for HTMX
            # Format: <div id="target-id" hx-swap-oob="true">content</div>
            if (
                "<div" in template_html
                and 'id="' + oob_target_id + '"' in template_html
            ):
                # If the template already has the target ID, just add hx-swap-oob
                oob_html += template_html.replace(
                    'id="' + oob_target_id + '"',
                    'id="' + oob_target_id + '" hx-swap-oob="true"',
                )
            else:
                # Otherwise wrap the entire template in a div with OOB attributes
                oob_html += f'<div id="{oob_target_id}" hx-swap-oob="true">{template_html}</div>'

        # Combine main content and OOB content
        combined_html = main_html + oob_html

        # Create the response with the combined HTML output
        response = HttpResponse(combined_html)

        # Handle URL push for history
        if push_url:
            response = htmx.push_url(response, url=push_url)

        return response


# EXAMPLE USAGE:
"""
class TeamDashboardComponent(TeamSessionMixin, HTMXView):
    template_name = "teams/dashboard.html"
    oob_templates = {
        "team-stats": "components/team_stats.html",
        "toast-container": "layout/messages/toast.html"
    }
    push_url = "/teams/dashboard"

    def get(self, request, *args, **kwargs):
        self.context["stats"] = get_team_stats(self.team)
        return self.render(request)
"""
