"""Views for serving MCP server landing pages and assets."""

import io
import json
import os
import zipfile

from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt


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


class CTOToolsLandingView(View):
    """Serve the CTO Tools MCP landing page."""

    def get(self, request):
        """Return the HTML landing page."""
        html_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "mcp",
            "cto_tools_web.html",
        )

        with open(html_path) as f:
            content = f.read()

        return HttpResponse(content, content_type="text/html; charset=utf-8")


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


class CreativeJuicesBundleView(View):
    """Serve a dynamically generated .mcpb bundle for Creative Juices MCP server.

    Currently generates the same bundle for all users, but can be customized
    in the future to include user-specific API keys or configuration.
    """

    def get(self, request):
        """Generate and return the .mcpb bundle as a ZIP file."""
        # Future: Check if user is authenticated and customize bundle
        # if request.user.is_authenticated:
        #     api_key = get_or_create_api_key(request.user)
        #     # Include API key in manifest or env vars

        # Create manifest for the bundle
        manifest = {
            "manifest_version": "0.3",
            "name": "creative-juices",
            "version": "1.0.0",
            "display_name": "Creative Juices",
            "description": "MCP server that generates random creative prompts and strategic questions to help LLMs think outside the box",
            "long_description": "Creative Juices provides three tools for divergent and convergent thinking:\n\n"
                              "1. **get_inspiration** - Gentle creative nudges with everyday metaphors\n"
                              "2. **think_outside_the_box** - Intense creative shocks to break linear thinking\n"
                              "3. **reality_check** - Strategic validation using proven frameworks\n\n"
                              "Features 600+ curated words spanning human history, runs completely locally with no external dependencies.",
            "author": {
                "name": "Tom Counsell",
                "url": "https://github.com/tomcounsell"
            },
            "homepage": "https://ai.yuda.me/mcp/creative-juices",
            "repository": {
                "type": "git",
                "url": "https://github.com/yudame/cuttlefish"
            },
            "documentation": "https://ai.yuda.me/mcp/creative-juices/README.md",
            "license": "MIT",
            "keywords": [
                "creativity",
                "brainstorming",
                "divergent-thinking",
                "strategic-frameworks",
                "metaphors",
                "first-principles"
            ],
            "server": {
                "type": "http",
                "url": "https://ai.yuda.me/mcp/creative-juices/serve",
                "transport": "streamable-http",
                # Future: Add authentication headers if needed
                # "headers": {
                #     "Authorization": "Bearer ${user_config.api_key}"
                # }
            },
            "compatibility": {
                "claude_desktop": ">=1.0.0",
                "platforms": ["darwin", "win32", "linux"],
                "runtimes": {
                    "python": ">=3.11"
                }
            },
            "tools": [
                {
                    "name": "get_inspiration",
                    "description": "Generate 3 gentle verb-noun combinations for early-stage creative framing"
                },
                {
                    "name": "think_outside_the_box",
                    "description": "Generate 3 intense verb-noun combinations for breaking linear thinking"
                },
                {
                    "name": "reality_check",
                    "description": "Get strategic questions from proven thinking frameworks for validation"
                }
            ],
            "user_config": {
                # Future: Add configuration options here
                # "api_key": {
                #     "type": "string",
                #     "description": "Your Creative Juices API key",
                #     "required": False
                # }
            }
        }

        # Create ZIP file in memory
        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            # Add manifest.json to the bundle
            zip_file.writestr('manifest.json', json.dumps(manifest, indent=2))

            # Future: Add icon if available
            # icon_path = os.path.join(
            #     os.path.dirname(os.path.dirname(__file__)),
            #     "static",
            #     "icons",
            #     "creative_juices.png"
            # )
            # if os.path.exists(icon_path):
            #     with open(icon_path, 'rb') as icon_file:
            #         zip_file.writestr('icon.png', icon_file.read())

        # Prepare response
        zip_buffer.seek(0)
        response = HttpResponse(
            zip_buffer.getvalue(),
            content_type='application/zip'
        )
        response['Content-Disposition'] = 'attachment; filename="creative-juices.mcpb"'

        return response


# ASGI endpoint for Creative Juices MCP Server over HTTP
from django.views.decorators.csrf import csrf_exempt


@csrf_exempt
async def creative_juices_mcp_http(scope, receive, send):
    """ASGI endpoint that serves the Creative Juices MCP server via streamable HTTP.

    This allows remote clients to connect to the MCP server over HTTP instead of stdio.
    Mounted at: /mcp/creative-juices/serve

    Usage in client config:
    {
      "mcpServers": {
        "creative-juices": {
          "url": "https://ai.yuda.me/mcp/creative-juices/serve"
        }
      }
    }
    """
    # Import here to avoid loading FastMCP unless needed
    from apps.ai.mcp.creative_juices_server import mcp

    # Get the ASGI app from FastMCP
    asgi_app = mcp.streamable_http_app()

    # Forward the request to the FastMCP ASGI app
    await asgi_app(scope, receive, send)
