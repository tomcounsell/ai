"""Views for serving MCP server landing pages and assets."""

import io
import json
import os
import zipfile

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
                "type": "python",
                "command": "uvx",
                "args": [
                    "run",
                    "--with", "mcp",
                    "--with", "httpx",
                    "https://ai.yuda.me/mcp/creative-juices/client.py"
                ]
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


class CTOToolsBundleView(View):
    """Serve a dynamically generated .mcpb bundle for CTO Tools MCP server.

    Currently generates the same bundle for all users, but can be customized
    in the future to include user-specific API keys or configuration.
    """

    def get(self, request):
        """Generate and return the .mcpb bundle as a ZIP file."""
        # Create manifest for the bundle
        manifest = {
            "manifest_version": "0.3",
            "name": "cto-tools",
            "version": "1.0.0",
            "display_name": "CTO Tools",
            "description": "MCP server providing engineering leadership frameworks for weekly reviews, team analysis, and strategic decision-making",
            "long_description": "CTO Tools provides structured frameworks for engineering leadership:\n\n"
                              "1. **Weekly review templates** - Consistent format for tracking progress\n"
                              "2. **Team analysis frameworks** - Structured approach to people challenges\n"
                              "3. **Strategic decision guides** - First principles thinking for technical choices\n\n"
                              "Designed for CTOs and engineering leaders who need consistent, proven approaches to leadership tasks.",
            "author": {
                "name": "Tom Counsell",
                "url": "https://github.com/tomcounsell"
            },
            "homepage": "https://ai.yuda.me/mcp/cto-tools",
            "repository": {
                "type": "git",
                "url": "https://github.com/yudame/cuttlefish"
            },
            "documentation": "https://ai.yuda.me/mcp/cto-tools/README.md",
            "license": "MIT",
            "keywords": [
                "leadership",
                "cto",
                "engineering-management",
                "frameworks",
                "weekly-reviews",
                "strategic-thinking"
            ],
            "server": {
                "type": "python",
                "command": "uvx",
                "args": [
                    "run",
                    "--with", "mcp",
                    "--with", "httpx",
                    "https://ai.yuda.me/mcp/cto-tools/client.py"
                ]
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
                    "name": "get_weekly_review_template",
                    "description": "Get structured weekly review template for engineering leaders"
                }
            ],
            "user_config": {}
        }

        # Create ZIP file in memory
        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            # Add manifest.json to the bundle
            zip_file.writestr('manifest.json', json.dumps(manifest, indent=2))

        # Prepare response
        zip_buffer.seek(0)
        response = HttpResponse(
            zip_buffer.getvalue(),
            content_type='application/zip'
        )
        response['Content-Disposition'] = 'attachment; filename="cto-tools.mcpb"'

        return response
