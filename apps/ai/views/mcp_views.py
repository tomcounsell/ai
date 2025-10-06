"""Views for serving MCP server landing pages and assets."""

import json
import os

from django.http import HttpResponse, JsonResponse
from django.views import View


class CreativeJuicesLandingView(View):
    """Serve the Creative Juices MCP landing page."""

    def get(self, request):
        """Return the HTML landing page."""
        html_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "mcp",
            "creative_juices_web.html",
        )

        with open(html_path) as f:
            content = f.read()

        return HttpResponse(content, content_type="text/html; charset=utf-8")


class CreativeJuicesManifestView(View):
    """Serve the Creative Juices MCP manifest.json."""

    def get(self, request):
        """Return the manifest.json with CORS headers."""
        manifest_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "mcp",
            "creative_juices_manifest.json",
        )

        with open(manifest_path) as f:
            manifest_data = json.load(f)

        response = JsonResponse(manifest_data)
        response["Access-Control-Allow-Origin"] = "*"
        response["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response["Access-Control-Allow-Headers"] = "Content-Type"
        return response


class CreativeJuicesReadmeView(View):
    """Serve the Creative Juices MCP README."""

    def get(self, request):
        """Return the README.md as markdown."""
        readme_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "mcp",
            "CREATIVE_JUICES_README.md",
        )

        with open(readme_path) as f:
            content = f.read()

        return HttpResponse(content, content_type="text/markdown; charset=utf-8")
