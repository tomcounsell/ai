"""
Homepage view for Cuttlefish AI Integration Platform.

This module contains the main homepage view that showcases available
MCP servers and provides setup instructions.

HOMEPAGE STRUCTURE
==================
The homepage (templates/home.html) consists of four main sections:

1. Hero Section
   - Platform title and value proposition
   - Uses technical-spec-box styling from brand.css

2. Available MCP Servers
   - Grid of server cards showing each MCP server
   - Each card displays: name, description, specs, and documentation link
   - Status indicators: green dot = live, orange dot = in development

3. Protocol Overview
   - Explains what MCP (Model Context Protocol) is
   - Three-column benefit grid

4. Quick Start Guide
   - Step-by-step installation instructions
   - Code snippets for Claude Desktop configuration
   - Restart instructions

MAINTENANCE
===========
- To add a new MCP server, duplicate a server-card div in the template
- Update server count when adding new servers
- Server documentation pages live at /mcp/{server-name}/
"""

from django.views.generic import TemplateView


class HomeView(TemplateView):
    """
    Main homepage for the Cuttlefish AI Integration Platform.

    Displays:
    - Hero section with platform description
    - Available MCP servers (Creative Juices, CTO Tools, etc.)
    - Protocol overview explaining MCP
    - Quick start installation guide

    Template: templates/home.html
    URL: / (root)
    """

    template_name = "home.html"
