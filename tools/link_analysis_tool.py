# tools/link_analysis_tool.py
"""
PydanticAI function tool for link analysis and summarization using Perplexity AI.
This replaces integrations/telegram/link_tracker.py with a proper tool implementation.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from openai import OpenAI

# Ensure environment variables are loaded
load_dotenv()


def extract_urls(text: str) -> list[str]:
    """Extract URLs from text using regex.
    
    Finds all HTTP and HTTPS URLs in the provided text using a
    comprehensive regex pattern that matches standard URL formats.
    
    Args:
        text: Text content to search for URLs.
        
    Returns:
        list[str]: List of URLs found in the text.
        
    Example:
        >>> extract_urls("Visit https://example.com for more info")
        ['https://example.com']
    """
    url_pattern = re.compile(
        r"http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+"
    )
    return url_pattern.findall(text)


def is_url_only_message(text: str) -> bool:
    """Check if message contains only a URL (and optional whitespace).
    
    Determines whether a message consists solely of a single URL,
    which is useful for triggering automatic link analysis in chat contexts.
    
    Args:
        text: Message text to check.
        
    Returns:
        bool: True if the message contains only a URL, False otherwise.
        
    Example:
        >>> is_url_only_message("https://example.com")
        True
        >>> is_url_only_message("Check out https://example.com")
        False
    """
    if not text or not text.strip():
        return False

    clean_text = text.strip()
    urls = extract_urls(clean_text)

    if len(urls) == 1:
        url = urls[0]
        return clean_text == url or clean_text.replace("https://", "http://") == url.replace(
            "https://", "http://"
        )

    return False


def validate_url(url: str) -> bool:
    """Validate if a URL is properly formatted.
    
    Checks whether a URL string has a valid format with both
    scheme (http/https) and network location (domain).
    
    Args:
        url: URL string to validate.
        
    Returns:
        bool: True if URL is valid, False otherwise.
        
    Example:
        >>> validate_url("https://example.com")
        True
        >>> validate_url("not-a-url")
        False
    """
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except Exception:
        return False


def analyze_url_content(url: str) -> dict[str, str]:
    """Analyze a URL and extract structured data using Perplexity.
    
    This function uses the Perplexity API to analyze web content at a given URL
    and extract structured information including title, main topic, and reasons
    why the content might be valuable or interesting.

    Args:
        url: The URL to analyze.

    Returns:
        dict[str, str]: Dict with 'title', 'main_topic', and 'reasons_to_care' keys,
                       or 'error' key if analysis fails.
                       
    Example:
        >>> result = analyze_url_content("https://example.com/article")
        >>> 'title' in result or 'error' in result
        True
        
    Note:
        Requires PERPLEXITY_API_KEY environment variable to be set.
    """
    if not validate_url(url):
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
                # Continue adding to reasons_to_care if it's a bullet point
                if analysis["reasons_to_care"]:
                    analysis["reasons_to_care"] += "\n" + line
                else:
                    analysis["reasons_to_care"] = line
            elif line and not line.startswith("TITLE:") and not line.startswith("MAIN_TOPIC:"):
                # Check which field we're currently in
                if "REASONS_TO_CARE" in content and content.index(line) > content.index(
                    "REASONS_TO_CARE"
                ):
                    current_field = "reasons"
                    if analysis["reasons_to_care"]:
                        analysis["reasons_to_care"] += "\n" + line
                    else:
                        analysis["reasons_to_care"] = line

        return analysis

    except Exception as e:
        return {"error": str(e)}


def store_link_with_analysis(
    url: str, chat_id: int = None, message_id: int | None = None, username: str | None = None
) -> bool:
    """Store a link with timestamp and AI-generated analysis.
    
    Saves a URL along with its AI-generated analysis to the links.json file
    in the docs directory. Automatically analyzes the content and stores
    structured metadata for later retrieval.

    Args:
        url: The URL to store.
        chat_id: Unused parameter, kept for backward compatibility.
        message_id: Unused parameter, kept for backward compatibility.
        username: Unused parameter, kept for backward compatibility.

    Returns:
        bool: True if storage was successful, False if it failed.
        
    Example:
        >>> store_link_with_analysis("https://example.com")
        True
        
    Note:
        Automatically commits the links file to git if in a git repository.
        Overwrites existing entries for the same URL.
    """
    if not validate_url(url):
        return False

    # Use docs directory for storage
    storage_file = Path("docs/links.json")

    # Load existing links dictionary
    links = {}
    if storage_file.exists():
        try:
            with open(storage_file, encoding="utf-8") as f:
                data = json.load(f)
                # Handle migration from old list format to new dict format
                if isinstance(data, list):
                    links = {}
                    for item in data:
                        if "url" in item:
                            # Clean up old entries during migration
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
    analysis = analyze_url_content(url)

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

        return True
    except Exception:
        return False


def _commit_links_file():
    """Automatically commit the links.json file after updates.
    
    Internal function that automatically commits changes to the links.json
    file using git. This helps maintain a history of link additions and
    ensures the data is versioned.
    
    Note:
        Silently ignores errors to avoid breaking the main functionality.
        Only commits if there are actual changes to the file.
    """
    import subprocess

    try:
        # Get the project root directory
        project_root = Path(__file__).parent.parent

        # Check if we're in a git repository
        if not (project_root / ".git").exists():
            return

        # Add the links file
        result = subprocess.run(
            ["git", "add", "docs/links.json"], cwd=project_root, capture_output=True, text=True
        )

        if result.returncode != 0:
            return

        # Check if there are changes to commit
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet", "docs/links.json"],
            cwd=project_root,
            capture_output=True,
        )

        # If returncode is 1, there are changes to commit
        if result.returncode == 1:
            subprocess.run(
                ["git", "commit", "-m", "Auto-save link analysis data"],
                cwd=project_root,
                capture_output=True,
                text=True,
            )

    except Exception:
        # Silently ignore any errors to not break the main functionality
        pass


def search_stored_links(query: str, chat_id: int | None = None, limit: int = 10) -> str:
    """Search stored links by domain or timestamp.
    
    Searches through previously stored links to find matches based on
    domain name, URL content, or timestamp. Returns formatted results
    suitable for display in conversations.

    Args:
        query: Search query (domain name or date pattern).
        chat_id: Optional chat ID filter (unused, kept for compatibility).
        limit: Maximum number of results to return.

    Returns:
        str: Formatted list of matching links with metadata,
             or message indicating no matches found.
             
    Example:
        >>> search_stored_links("github.com")
        '📂 **Found 3 link(s) matching "github.com":**\n\n• **github.com** (2024-01-15)...'
        
        >>> search_stored_links("nonexistent")
        '📂 No links found matching "nonexistent"'
    """
    storage_file = Path("docs/links.json")
    if not storage_file.exists():
        return "📂 No links stored yet."

    try:
        with open(storage_file, encoding="utf-8") as f:
            data = json.load(f)
            # Handle both old list format and new dict format
            if isinstance(data, list):
                links = data
            else:
                links = list(data.values())
    except (OSError, json.JSONDecodeError):
        return "📂 Error reading stored links."

    # chat_id filtering removed since we no longer store chat_id

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
        result += f"• **{domain}** ({timestamp})\n  {link['url']}\n\n"

    return result.strip()
