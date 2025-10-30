"""Views for serving MCP server landing pages and assets."""

import json
import os

from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView


class CreativeJuicesLandingView(TemplateView):
    """Serve the Creative Juices MCP landing page."""

    template_name = "mcp/creative_juices.html"


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


class CreativeJuicesClientView(View):
    """Serve the Creative Juices MCP client proxy script."""

    def get(self, request):
        """Return the client.py file."""
        client_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "mcp",
            "creative_juices_client.py",
        )

        with open(client_path) as f:
            content = f.read()

        return HttpResponse(content, content_type="text/x-python; charset=utf-8")


class CTOToolsLandingView(TemplateView):
    """Serve the CTO Tools MCP landing page."""

    template_name = "mcp/cto_tools.html"


class CTOToolsManifestView(View):
    """Serve the CTO Tools MCP manifest.json."""

    def get(self, request):
        """Return the manifest.json with CORS headers."""
        manifest_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "mcp",
            "cto_tools_manifest.json",
        )

        with open(manifest_path) as f:
            manifest_data = json.load(f)

        response = JsonResponse(manifest_data)
        response["Access-Control-Allow-Origin"] = "*"
        response["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response["Access-Control-Allow-Headers"] = "Content-Type"
        return response


class CTOToolsReadmeView(View):
    """Serve the CTO Tools MCP README."""

    def get(self, request):
        """Return the README.md as markdown."""
        readme_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "mcp",
            "CTO_TOOLS_README.md",
        )

        with open(readme_path) as f:
            content = f.read()

        return HttpResponse(content, content_type="text/markdown; charset=utf-8")


class CTOToolsClientView(View):
    """Serve the CTO Tools MCP client proxy script."""

    def get(self, request):
        """Return the client.py file."""
        client_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "mcp",
            "cto_tools_client.py",
        )

        with open(client_path) as f:
            content = f.read()

        return HttpResponse(content, content_type="text/x-python; charset=utf-8")


class CreativeJuicesBundleView(View):
    """Serve the pre-built .mcpb bundle for Creative Juices MCP server.

    The bundle is statically compiled and stored in the repository to ensure
    it conforms to MCPB specifications and works with Claude Desktop.
    """

    def get(self, request):
        """Return the pre-built .mcpb bundle file."""
        bundle_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "mcp",
            "creative-juices.mcpb",
        )

        if not os.path.exists(bundle_path):
            return HttpResponse(
                "Bundle file not found. Please run the build process.",
                status=404
            )

        with open(bundle_path, 'rb') as f:
            response = HttpResponse(
                f.read(),
                content_type='application/zip'
            )
            response['Content-Disposition'] = 'attachment; filename="creative-juices.mcpb"'
            return response


class CTOToolsBundleView(View):
    """Serve the pre-built .mcpb bundle for CTO Tools MCP server.

    The bundle is statically compiled and stored in the repository to ensure
    it conforms to MCPB specifications and works with Claude Desktop.
    """

    def get(self, request):
        """Return the pre-built .mcpb bundle file."""
        bundle_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "mcp",
            "cto-tools.mcpb",
        )

        if not os.path.exists(bundle_path):
            return HttpResponse(
                "Bundle file not found. Please run the build process.",
                status=404
            )

        with open(bundle_path, 'rb') as f:
            response = HttpResponse(
                f.read(),
                content_type='application/zip'
            )
            response['Content-Disposition'] = 'attachment; filename="cto-tools.mcpb"'
            return response
