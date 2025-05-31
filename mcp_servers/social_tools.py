#!/usr/bin/env python3
"""
Social Tools MCP Server

Provides web search, image generation, and link analysis tools for Claude Code integration.
Converts existing tools from tools/ directory to MCP server format.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from openai import OpenAI

# Load environment variables
load_dotenv()

# Initialize MCP server
mcp = FastMCP("Social Tools")

@mcp.tool()
def search_current_info(query: str, max_results: int = 3) -> str:
    """Search the web and return AI-synthesized answers using Perplexity.
    
    Use this tool when you need current web information or recent news about any topic.
    Provides AI-synthesized answers based on current web content.

    Args:
        query: The search query to execute
        max_results: Maximum number of results (not used with Perplexity, kept for compatibility)

    Returns:
        AI-synthesized answer based on current web information
    """
    api_key = os.getenv("PERPLEXITY_API_KEY")

    if not api_key:
        return "🔍 Search unavailable: Missing PERPLEXITY_API_KEY configuration."

    # Validate inputs
    if not query or not query.strip():
        return "🔍 Search error: Query cannot be empty."
    
    if len(query) > 500:
        return "🔍 Search error: Query too long (max 500 characters)."

    try:
        client = OpenAI(api_key=api_key, base_url="https://api.perplexity.ai", timeout=180)

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a helpful search assistant. Provide a concise, "
                    "informative answer based on current web information. "
                    "Keep responses under 300 words for messaging platforms. "
                    "Format your response clearly and include key facts."
                ),
            },
            {
                "role": "user",
                "content": query,
            },
        ]

        response = client.chat.completions.create(
            model="sonar-pro", messages=messages, temperature=0.2, max_tokens=400
        )

        answer = response.choices[0].message.content
        return f"🔍 **{query}**\n\n{answer}"

    except requests.exceptions.RequestException as e:
        return f"🔍 Search network error: Failed to connect to search service - {str(e)}"
    except Exception as e:
        error_type = type(e).__name__
        if "API" in str(e) or "Perplexity" in str(e) or "401" in str(e):
            return f"🔍 Search API error: {str(e)} - Check PERPLEXITY_API_KEY"
        if "timeout" in str(e).lower():
            return f"🔍 Search timeout: Query took too long to process"
        return f"🔍 Search error ({error_type}): {str(e)}"


@mcp.tool()
def create_image(
    prompt: str,
    size: str = "1024x1024",
    quality: str = "standard",
    style: str = "natural",
    chat_id: str = ""
) -> str:
    """Generate an image using DALL-E 3 and save it locally.
    
    Use this tool when you need to create custom images from text descriptions.
    Generated images are saved locally and can be shared in conversations.

    Args:
        prompt: Text description of the image to generate
        size: Image size - "1024x1024", "1792x1024", or "1024x1792"
        quality: Image quality - "standard" or "hd"
        style: Image style - "natural" (realistic) or "vivid" (dramatic/artistic)
        chat_id: Chat ID for context (extracted from CONTEXT_DATA if available)

    Returns:
        Path to the generated image file or error message
    """
    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        return "🎨 Image generation unavailable: Missing OPENAI_API_KEY configuration."

    # Validate inputs
    if not prompt or not prompt.strip():
        return "🎨 Image generation error: Prompt cannot be empty."
    
    valid_sizes = ["1024x1024", "1792x1024", "1024x1792"]
    if size not in valid_sizes:
        return f"🎨 Image generation error: Size must be one of {valid_sizes}. Got '{size}'."
    
    valid_qualities = ["standard", "hd"]
    if quality not in valid_qualities:
        return f"🎨 Image generation error: Quality must be one of {valid_qualities}. Got '{quality}'."
    
    valid_styles = ["natural", "vivid"]
    if style not in valid_styles:
        return f"🎨 Image generation error: Style must be one of {valid_styles}. Got '{style}'."

    try:
        client = OpenAI(api_key=api_key, timeout=180)

        # Generate image using DALL-E 3
        response = client.images.generate(
            prompt=prompt, model="dall-e-3", size=size, quality=quality, style=style, n=1
        )

        # Get the image URL
        image_url = response.data[0].url

        # Download the image
        image_response = requests.get(image_url, timeout=180)
        image_response.raise_for_status()

        # Determine save path
        save_path = Path("/tmp")
        save_path.mkdir(parents=True, exist_ok=True)

        # Create filename from prompt (cleaned up)
        safe_filename = "".join(
            c for c in prompt[:50] if c.isalnum() or c in (" ", "-", "_")
        ).rstrip()
        safe_filename = safe_filename.replace(" ", "_")
        image_path = save_path / f"generated_{safe_filename}.png"

        # Save the image
        with open(image_path, "wb") as f:
            f.write(image_response.content)

        # Format response for Telegram if chat_id provided
        if chat_id:
            return f"TELEGRAM_IMAGE_GENERATED|{image_path}|{chat_id}"
        else:
            return str(image_path)

    except requests.exceptions.RequestException as e:
        return f"🎨 Image download error: Failed to download generated image - {str(e)}"
    except OSError as e:
        return f"🎨 Image save error: Failed to save image file - {str(e)}"
    except Exception as e:
        error_type = type(e).__name__
        if "API" in str(e) or "OpenAI" in str(e):
            return f"🎨 OpenAI API error: {str(e)}"
        return f"🎨 Image generation error ({error_type}): {str(e)}"


def _extract_urls(text: str) -> list[str]:
    """Extract URLs from text using regex."""
    url_pattern = re.compile(
        r"http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+"
    )
    return url_pattern.findall(text)


def _validate_url(url: str) -> bool:
    """Validate if a URL is properly formatted."""
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except Exception:
        return False


def _analyze_url_content(url: str) -> dict[str, str]:
    """Analyze a URL and extract structured data using Perplexity."""
    if not _validate_url(url):
        return {"error": f"Invalid URL format: {url}"}

    api_key = os.getenv("PERPLEXITY_API_KEY")
    if not api_key:
        return {"error": "Missing PERPLEXITY_API_KEY configuration"}

    try:
        client = OpenAI(api_key=api_key, base_url="https://api.perplexity.ai")

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a content analyzer. For the given URL, extract and return "
                    "ONLY the following information in this exact format:\n\n"
                    "TITLE: [The actual title of the page/article]\n"
                    "MAIN_TOPIC: [The primary subject matter in 1-2 sentences]\n"
                    "REASONS_TO_CARE: [2-3 bullet points explaining why this might be valuable or interesting]\n\n"
                    "Be concise and factual. If you cannot access the content, say 'Unable to access content'."
                ),
            },
            {
                "role": "user",
                "content": f"Analyze this URL: {url}",
            },
        ]

        response = client.chat.completions.create(
            model="sonar-pro", messages=messages, temperature=0.1, max_tokens=400
        )

        content = response.choices[0].message.content

        # Parse the structured response
        analysis = {"title": None, "main_topic": None, "reasons_to_care": None}

        lines = content.split("\n")
        current_field = None

        for line in lines:
            line = line.strip()
            if line.startswith("TITLE:"):
                analysis["title"] = line[6:].strip()
            elif line.startswith("MAIN_TOPIC:"):
                analysis["main_topic"] = line[12:].strip()
            elif line.startswith("REASONS_TO_CARE:"):
                analysis["reasons_to_care"] = line[17:].strip()
            elif line.startswith("•") or line.startswith("-") and current_field == "reasons":
                if analysis["reasons_to_care"]:
                    analysis["reasons_to_care"] += "\n" + line
                else:
                    analysis["reasons_to_care"] = line
            elif line and not line.startswith("TITLE:") and not line.startswith("MAIN_TOPIC:"):
                if "REASONS_TO_CARE" in content and content.index(line) > content.index("REASONS_TO_CARE"):
                    current_field = "reasons"
                    if analysis["reasons_to_care"]:
                        analysis["reasons_to_care"] += "\n" + line
                    else:
                        analysis["reasons_to_care"] = line

        return analysis

    except Exception as e:
        return {"error": str(e)}


def _commit_links_file():
    """Automatically commit the links.json file after updates."""
    import subprocess

    try:
        project_root = Path(__file__).parent.parent

        if not (project_root / ".git").exists():
            return

        result = subprocess.run(
            ["git", "add", "docs/links.json"], cwd=project_root, capture_output=True, text=True
        )

        if result.returncode != 0:
            return

        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet", "docs/links.json"],
            cwd=project_root,
            capture_output=True,
        )

        if result.returncode == 1:
            subprocess.run(
                ["git", "commit", "-m", "Auto-save link analysis data"],
                cwd=project_root,
                capture_output=True,
                text=True,
            )

    except Exception:
        pass


@mcp.tool()
def save_link(url: str, chat_id: str = "", username: str = "") -> str:
    """Save a link with AI-generated analysis to the knowledge base.
    
    Use this tool when a user shares a URL that should be saved for future reference.
    Automatically analyzes the content and stores structured metadata.

    Args:
        url: The URL to analyze and save
        chat_id: Chat ID for context (extracted from CONTEXT_DATA if available)
        username: Username for context (extracted from CONTEXT_DATA if available)

    Returns:
        Success message with analysis summary or error message
    """
    if not _validate_url(url):
        return f"❌ Invalid URL format: {url}"

    # Use docs directory for storage
    storage_file = Path("docs/links.json")

    # Load existing links dictionary
    links = {}
    if storage_file.exists():
        try:
            with open(storage_file, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    # Handle migration from old list format
                    links = {}
                    for item in data:
                        if "url" in item:
                            clean_item = {
                                "url": item["url"],
                                "domain": item.get("domain", urlparse(item["url"]).netloc),
                                "timestamp": item.get("timestamp", datetime.now().isoformat()),
                                "analysis": item.get("analysis", {}),
                            }
                            links[item["url"]] = clean_item
                else:
                    links = data
        except (OSError, json.JSONDecodeError):
            links = {}

    # Get AI analysis of the URL
    analysis = _analyze_url_content(url)

    # Create link entry
    parsed = urlparse(url)
    link_entry = {
        "url": url,
        "domain": parsed.netloc,
        "timestamp": datetime.now().isoformat(),
        "analysis": analysis,
    }

    # Store with URL as key (automatically overwrites duplicates)
    links[url] = link_entry

    try:
        with open(storage_file, "w", encoding="utf-8") as f:
            json.dump(links, f, indent=2, ensure_ascii=False)

        # Auto-commit the links file after saving
        _commit_links_file()

        # Format response with analysis summary
        if "error" in analysis:
            return f"🔗 **Link Saved**: {parsed.netloc}\n\n⚠️ Analysis error: {analysis['error']}"
        else:
            title = analysis.get("title", "Unknown")
            topic = analysis.get("main_topic", "No topic available")
            return f"🔗 **Link Saved**: {title}\n\n📝 **Topic**: {topic}\n🌐 **Domain**: {parsed.netloc}"

    except Exception as e:
        return f"❌ Error saving link: {str(e)}"


@mcp.tool()
def search_links(query: str, chat_id: str = "", limit: int = 10) -> str:
    """Search through previously saved links by domain, content, or timestamp.
    
    Use this tool to find links that were previously saved to the knowledge base.
    Searches through domains, URLs, and timestamps.

    Args:
        query: Search query (domain name, URL content, or date pattern)
        chat_id: Chat ID for context (extracted from CONTEXT_DATA if available)
        limit: Maximum number of results to return

    Returns:
        Formatted list of matching links or message indicating no matches
    """
    storage_file = Path("docs/links.json")
    if not storage_file.exists():
        return "📂 No links stored yet."

    try:
        with open(storage_file, encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                links = data
            else:
                links = list(data.values())
    except (OSError, json.JSONDecodeError):
        return "📂 Error reading stored links."

    # Search in domain and URL
    query_lower = query.lower()
    matching_links = []
    for link in links:
        if (
            query_lower in link.get("domain", "").lower()
            or query_lower in link["url"].lower()
            or query_lower in link.get("timestamp", "")
        ):
            matching_links.append(link)

    # Sort by timestamp (newest first) and limit
    matching_links.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    matching_links = matching_links[:limit]

    if not matching_links:
        return f"📂 No links found matching '{query}'"

    # Format results
    result = f"📂 **Found {len(matching_links)} link(s) matching '{query}':**\n\n"
    for link in matching_links:
        timestamp = link.get("timestamp", "Unknown")[:10]  # Just date part
        domain = link.get("domain", "Unknown")
        
        # Include title from analysis if available
        title = "Unknown"
        if "analysis" in link and isinstance(link["analysis"], dict):
            title = link["analysis"].get("title", domain)
        
        result += f"• **{title}** ({timestamp})\n  {link['url']}\n\n"

    return result.strip()


if __name__ == "__main__":
    mcp.run()