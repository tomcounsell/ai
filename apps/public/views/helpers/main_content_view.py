import logging
from typing import Optional

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render as django_render
from django.views import View


class MainContentView(View):
    """
    Base view class for general views in the project.
    Implements a standardized block structure for consistent layouts.
    """

    url: str = ""
    template_name: str | None = None

    # Default base templates - don't override these in most cases
    base_template: str = "base.html"
    partial_template: str = "partial.html"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Initialize base context
        self.context = {
            "is_oob": False,  # templates need to check this
        }

    def dispatch(self, request, *args, **kwargs):
        # Update context with default URL
        if not self.context.get("url", None):
            self.context["url"] = self.url

        # Set appropriate base template based on request type
        if getattr(request, "htmx", False):
            self.context["base_template"] = self.partial_template
        else:
            self.context["base_template"] = self.base_template

        self.context["just_logged_in"] = request.session.get("just_logged_in", False)
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        logging.warning("GET method not implemented.")
        if self.template_name:
            logging.warning("Will attempt to render template as-is.")
            return self.render(request)
        raise NotImplementedError("GET method must be implemented in subclass.")

    def post(self, request, *args, **kwargs):
        raise NotImplementedError("POST method must be implemented in subclass.")

    def render(
        self,
        request: HttpRequest = None,
        template_name: str | None = None,
        context: dict | None = None,
    ) -> HttpResponse:
        """
        Render the template with the provided or default context.
        """
        request = request or self.request
        template_name = template_name or self.template_name
        combined_context = {**self.context, **(context or {})}

        return django_render(request, template_name, combined_context)
