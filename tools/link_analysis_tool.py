# tools/link_analysis_tool.py
"""
Link analysis and storage tool using SQLite database.
Provides URL analysis, storage, and retrieval functionality.
"""

import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from typing import Optional, List, Dict, Any

from dotenv import load_dotenv
from openai import OpenAI

# Ensure environment variables are loaded
load_dotenv()


def get_links_db_path() -> Path:
    """Get the path to the links database."""
    return Path("links.db")


def init_links_database() -> None:
    """Initialize the links database with required tables."""
    db_path = get_links_db_path()
    
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE NOT NULL,
                domain TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                analysis_result TEXT,  -- JSON blob
                analysis_status TEXT DEFAULT 'pending',  -- 'success', 'error', 'pending'
                title TEXT,
                main_topic TEXT,
                reasons_to_care TEXT,
                error_message TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE INDEX IF NOT EXISTS idx_links_url ON links(url);
            CREATE INDEX IF NOT EXISTS idx_links_domain ON links(domain);
            CREATE INDEX IF NOT EXISTS idx_links_timestamp ON links(timestamp);
            CREATE INDEX IF NOT EXISTS idx_links_status ON links(analysis_status);
        """)


def extract_urls(text: str) -> List[str]:
    """Extract URLs from text using regex.
    
    Finds all HTTP and HTTPS URLs in the provided text using a
    comprehensive regex pattern that matches standard URL formats.
    
    Args:
        text: Text content to search for URLs.
        
    Returns:
        List[str]: List of URLs found in the text.
        
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


def analyze_url_content(url: str) -> Dict[str, Any]:
    """Analyze a URL and extract structured data using Perplexity.
    
    This function uses the Perplexity API to analyze web content at a given URL
    and extract structured information including title, main topic, and reasons
    why the content might be valuable or interesting.

    Args:
        url: The URL to analyze.

    Returns:
        Dict[str, Any]: Dict with analysis results or error information.
                       
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
    
    Saves a URL along with its AI-generated analysis to the SQLite database.
    Automatically analyzes the content and stores structured metadata for later retrieval.

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
        Overwrites existing entries for the same URL.
    """
    if not validate_url(url):
        return False

    # Initialize database if it doesn't exist
    init_links_database()
    
    # Get AI analysis of the URL
    analysis = analyze_url_content(url)
    
    # Parse URL for domain
    parsed = urlparse(url)
    domain = parsed.netloc
    
    # Determine analysis status and extract fields
    if "error" in analysis:
        status = "error"
        title = None
        main_topic = None
        reasons_to_care = None
        error_message = analysis["error"]
    else:
        status = "success"
        title = analysis.get("title")
        main_topic = analysis.get("main_topic")
        reasons_to_care = analysis.get("reasons_to_care")
        error_message = None

    try:
        db_path = get_links_db_path()
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO links 
                (url, domain, timestamp, analysis_result, analysis_status, 
                 title, main_topic, reasons_to_care, error_message, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                url, domain, datetime.now().isoformat(), str(analysis), status,
                title, main_topic, reasons_to_care, error_message, datetime.now().isoformat()
            ))

        return True
    except Exception:
        return False


def search_stored_links(query: str, chat_id: int | None = None, limit: int = 10) -> str:
    """Search stored links by domain, title, or timestamp.
    
    Searches through previously stored links to find matches based on
    domain name, URL content, title, or timestamp. Returns formatted results
    suitable for display in conversations.

    Args:
        query: Search query (domain name, title, or date pattern).
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
    # Initialize database if it doesn't exist
    init_links_database()
    
    db_path = get_links_db_path()
    if not db_path.exists():
        return "📂 No links stored yet."

    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            # Search in domain, URL, title, and main_topic
            query_lower = query.lower()
            results = conn.execute("""
                SELECT * FROM links 
                WHERE LOWER(domain) LIKE ? 
                   OR LOWER(url) LIKE ? 
                   OR LOWER(title) LIKE ?
                   OR LOWER(main_topic) LIKE ?
                   OR date(timestamp) LIKE ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (f"%{query_lower}%", f"%{query_lower}%", f"%{query_lower}%", 
                  f"%{query_lower}%", f"%{query_lower}%", limit)).fetchall()
            
    except Exception:
        return "📂 Error reading stored links."

    if not results:
        return f"📂 No links found matching '{query}'"

    # Format results
    result = f"📂 **Found {len(results)} link(s) matching '{query}':**\n\n"
    for link in results:
        timestamp = link["timestamp"][:10] if link["timestamp"] else "Unknown"  # Just date part
        domain = link["domain"] or "Unknown"
        title = link["title"] or "No title"
        status = "✅" if link["analysis_status"] == "success" else "❌"
        
        result += f"• **{domain}** ({timestamp}) {status}\n"
        result += f"  {title}\n"
        result += f"  {link['url']}\n\n"

    return result.strip()


def get_recent_links(days: int = 7, limit: int = 20) -> str:
    """Get recently stored links.
    
    Args:
        days: Number of days to look back.
        limit: Maximum number of results to return.
        
    Returns:
        str: Formatted list of recent links.
    """
    # Initialize database if it doesn't exist
    init_links_database()
    
    db_path = get_links_db_path()
    if not db_path.exists():
        return "📂 No links stored yet."

    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            results = conn.execute("""
                SELECT * FROM links 
                WHERE timestamp >= datetime('now', '-{} days')
                ORDER BY timestamp DESC
                LIMIT ?
            """.format(days), (limit,)).fetchall()
            
    except Exception:
        return "📂 Error reading stored links."

    if not results:
        return f"📂 No links found in the last {days} days"

    # Format results
    result = f"📂 **Recent links (last {days} days):**\n\n"
    for link in results:
        timestamp = link["timestamp"][:10] if link["timestamp"] else "Unknown"
        domain = link["domain"] or "Unknown"
        title = link["title"] or "No title"
        status = "✅" if link["analysis_status"] == "success" else "❌"
        
        result += f"• **{domain}** ({timestamp}) {status}\n"
        result += f"  {title}\n"
        result += f"  {link['url']}\n\n"

    return result.strip()


def get_links_by_domain(domain: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Get all links for a specific domain.
    
    Args:
        domain: Domain to search for.
        limit: Maximum number of results.
        
    Returns:
        List[Dict[str, Any]]: List of link records.
    """
    # Initialize database if it doesn't exist
    init_links_database()
    
    db_path = get_links_db_path()
    if not db_path.exists():
        return []

    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            results = conn.execute("""
                SELECT * FROM links 
                WHERE domain = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (domain, limit)).fetchall()
            
            return [dict(row) for row in results]
            
    except Exception:
        return []


def cleanup_old_links(days: int = 90) -> int:
    """Remove links older than specified days.
    
    Args:
        days: Number of days to keep.
        
    Returns:
        int: Number of links removed.
    """
    # Initialize database if it doesn't exist
    init_links_database()
    
    db_path = get_links_db_path()
    if not db_path.exists():
        return 0

    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute("""
                DELETE FROM links 
                WHERE timestamp < datetime('now', '-{} days')
            """.format(days))
            
            return cursor.rowcount
            
    except Exception:
        return 0