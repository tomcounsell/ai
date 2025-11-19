"""
Social Tools MCP Server

This module implements MCP server for social and web-based tools:
- Web search functionality
- Calendar management 
- Content creation tools
- Knowledge base access
- Social media integration
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Union
import json

import httpx
from pydantic import BaseModel, Field, validator

from .base import MCPServer, MCPToolCapability, MCPRequest, MCPError
from .context_manager import MCPContextManager, SecurityLevel


class WebSearchResult(BaseModel):
    """Web search result structure."""
    
    title: str = Field(..., description="Search result title")
    url: str = Field(..., description="Search result URL")
    snippet: str = Field(..., description="Search result snippet/description")
    source: str = Field(..., description="Search engine or source")
    score: float = Field(default=0.0, description="Relevance score")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class CalendarEvent(BaseModel):
    """Calendar event structure."""
    
    id: str = Field(..., description="Event ID")
    title: str = Field(..., description="Event title")
    description: Optional[str] = Field(None, description="Event description")
    start_time: datetime = Field(..., description="Event start time")
    end_time: datetime = Field(..., description="Event end time")
    location: Optional[str] = Field(None, description="Event location")
    attendees: List[str] = Field(default_factory=list, description="Event attendees")
    status: str = Field(default="confirmed", description="Event status")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    @validator('end_time')
    def end_time_after_start_time(cls, v, values):
        if 'start_time' in values and v <= values['start_time']:
            raise ValueError('End time must be after start time')
        return v
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class ContentTemplate(BaseModel):
    """Content creation template."""
    
    name: str = Field(..., description="Template name")
    type: str = Field(..., description="Content type (blog, social, email, etc.)")
    template: str = Field(..., description="Template content with placeholders")
    variables: List[str] = Field(default_factory=list, description="Template variables")
    tags: List[str] = Field(default_factory=list, description="Template tags")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class KnowledgeBaseEntry(BaseModel):
    """Knowledge base entry structure."""
    
    id: str = Field(..., description="Entry ID")
    title: str = Field(..., description="Entry title")
    content: str = Field(..., description="Entry content")
    category: str = Field(..., description="Entry category")
    tags: List[str] = Field(default_factory=list, description="Entry tags")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    author: Optional[str] = Field(None, description="Entry author")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class SocialToolsServer(MCPServer):
    """
    MCP Server implementation for social and web-based tools.
    
    Provides stateless social functionality with context injection including:
    - Web search with multiple engines
    - Calendar event management
    - Content creation and templating
    - Knowledge base access and search
    """
    
    def __init__(
        self,
        name: str = "social_tools",
        version: str = "1.0.0",
        description: str = "Social and web-based tools MCP server",
        search_api_keys: Dict[str, str] = None,
        calendar_config: Dict[str, Any] = None,
        knowledge_base_path: str = None,
        **kwargs
    ):
        super().__init__(name, version, description, **kwargs)
        
        # Configuration
        self.search_api_keys = search_api_keys or {}
        self.calendar_config = calendar_config or {}
        self.knowledge_base_path = knowledge_base_path
        
        # HTTP client for external APIs
        self.http_client = httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True
        )
        
        # In-memory stores (in production, use proper databases)
        self._calendar_events: Dict[str, CalendarEvent] = {}
        self._content_templates: Dict[str, ContentTemplate] = {}
        self._knowledge_base: Dict[str, KnowledgeBaseEntry] = {}
        
        # Search engines configuration
        self._search_engines = {
            "duckduckgo": self._search_duckduckgo,
            "google": self._search_google,
            "bing": self._search_bing
        }
        
        self.logger.info(f"Social Tools Server initialized with {len(self._search_engines)} search engines")
    
    async def initialize(self) -> None:
        """Initialize the social tools server."""
        try:
            # Register all tool capabilities
            await self._register_web_search_tools()
            await self._register_calendar_tools()
            await self._register_content_tools()
            await self._register_knowledge_base_tools()
            
            # Load initial data
            await self._load_default_templates()
            if self.knowledge_base_path:
                await self._load_knowledge_base()
            
            self.logger.info("Social Tools Server initialized successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize Social Tools Server: {str(e)}")
            raise MCPError(
                f"Social tools server initialization failed: {str(e)}",
                error_code="SOCIAL_TOOLS_INIT_ERROR",
                details={"error": str(e)},
                recoverable=False
            )
    
    async def shutdown(self) -> None:
        """Shutdown the social tools server."""
        try:
            await self.http_client.aclose()
            self.logger.info("Social Tools Server shut down successfully")
            
        except Exception as e:
            self.logger.error(f"Error during Social Tools Server shutdown: {str(e)}")
            raise MCPError(
                f"Social tools server shutdown failed: {str(e)}",
                error_code="SOCIAL_TOOLS_SHUTDOWN_ERROR",
                details={"error": str(e)},
                recoverable=False
            )
    
    # Web Search Tools
    
    async def _register_web_search_tools(self) -> None:
        """Register web search tool capabilities."""
        
        # Web search tool
        search_capability = MCPToolCapability(
            name="web_search",
            description="Search the web using multiple search engines",
            parameters={
                "query": {"type": "string", "required": True, "description": "Search query"},
                "engine": {
                    "type": "string", 
                    "required": False, 
                    "default": "duckduckgo",
                    "enum": list(self._search_engines.keys()),
                    "description": "Search engine to use"
                },
                "max_results": {"type": "integer", "required": False, "default": 10, "description": "Maximum number of results"}
            },
            returns={"type": "array", "items": "WebSearchResult"},
            tags=["web", "search", "external"]
        )
        self.register_tool(search_capability, self._handle_web_search)
        
        # URL content extraction
        extract_capability = MCPToolCapability(
            name="extract_url_content",
            description="Extract content from a specific URL",
            parameters={
                "url": {"type": "string", "required": True, "description": "URL to extract content from"},
                "format": {"type": "string", "required": False, "default": "text", "enum": ["text", "html", "markdown"], "description": "Output format"}
            },
            returns={"type": "object", "properties": {"url": "string", "title": "string", "content": "string", "format": "string"}},
            tags=["web", "extraction", "external"]
        )
        self.register_tool(extract_capability, self._handle_extract_url_content)
    
    async def _handle_web_search(self, request: MCPRequest, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Handle web search requests."""
        query = request.params.get("query")
        engine = request.params.get("engine", "duckduckgo")
        max_results = request.params.get("max_results", 10)
        
        if not query:
            raise MCPError(
                "Search query is required",
                error_code="MISSING_SEARCH_QUERY",
                request_id=request.id
            )
        
        if engine not in self._search_engines:
            raise MCPError(
                f"Unknown search engine: {engine}",
                error_code="UNKNOWN_SEARCH_ENGINE",
                details={"engine": engine, "available_engines": list(self._search_engines.keys())},
                request_id=request.id
            )
        
        try:
            # Perform search using selected engine
            search_function = self._search_engines[engine]
            results = await search_function(query, max_results)
            
            self.logger.info(f"Web search completed: query='{query}', engine={engine}, results={len(results)}")
            
            return [result.dict() for result in results]
            
        except Exception as e:
            self.logger.error(f"Web search failed: {str(e)}")
            raise MCPError(
                f"Web search failed: {str(e)}",
                error_code="WEB_SEARCH_ERROR",
                details={"query": query, "engine": engine, "error": str(e)},
                request_id=request.id
            )
    
    async def _handle_extract_url_content(self, request: MCPRequest, context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle URL content extraction requests."""
        url = request.params.get("url")
        format_type = request.params.get("format", "text")
        
        if not url:
            raise MCPError(
                "URL is required",
                error_code="MISSING_URL",
                request_id=request.id
            )
        
        try:
            response = await self.http_client.get(url)
            response.raise_for_status()
            
            # Extract title from HTML if present
            title = ""
            content = response.text
            
            if "text/html" in response.headers.get("content-type", ""):
                # Simple title extraction
                import re
                title_match = re.search(r'<title[^>]*>([^<]+)</title>', content, re.IGNORECASE)
                if title_match:
                    title = title_match.group(1).strip()
                
                # Format conversion based on requested format
                if format_type == "text":
                    # Simple HTML to text conversion
                    content = re.sub(r'<[^>]+>', ' ', content)
                    content = re.sub(r'\s+', ' ', content).strip()
                elif format_type == "markdown":
                    # Basic HTML to markdown conversion
                    content = self._html_to_markdown(content)
                # For HTML format, keep as-is
            
            result = {
                "url": url,
                "title": title,
                "content": content,
                "format": format_type
            }
            
            self.logger.info(f"URL content extracted: url={url}, format={format_type}")
            
            return result
            
        except Exception as e:
            self.logger.error(f"URL content extraction failed: {str(e)}")
            raise MCPError(
                f"URL content extraction failed: {str(e)}",
                error_code="URL_EXTRACTION_ERROR",
                details={"url": url, "error": str(e)},
                request_id=request.id
            )
    
    # Calendar Tools
    
    async def _register_calendar_tools(self) -> None:
        """Register calendar tool capabilities."""
        
        # Create calendar event
        create_event_capability = MCPToolCapability(
            name="create_calendar_event",
            description="Create a new calendar event",
            parameters={
                "title": {"type": "string", "required": True, "description": "Event title"},
                "description": {"type": "string", "required": False, "description": "Event description"},
                "start_time": {"type": "string", "required": True, "description": "Event start time (ISO 8601)"},
                "end_time": {"type": "string", "required": True, "description": "Event end time (ISO 8601)"},
                "location": {"type": "string", "required": False, "description": "Event location"},
                "attendees": {"type": "array", "items": "string", "required": False, "description": "Event attendees"}
            },
            returns={"type": "object", "description": "Created calendar event"},
            tags=["calendar", "scheduling", "events"]
        )
        self.register_tool(create_event_capability, self._handle_create_calendar_event)
        
        # List calendar events
        list_events_capability = MCPToolCapability(
            name="list_calendar_events",
            description="List calendar events within a date range",
            parameters={
                "start_date": {"type": "string", "required": False, "description": "Start date (ISO 8601)"},
                "end_date": {"type": "string", "required": False, "description": "End date (ISO 8601)"},
                "limit": {"type": "integer", "required": False, "default": 50, "description": "Maximum number of events"}
            },
            returns={"type": "array", "items": "CalendarEvent"},
            tags=["calendar", "scheduling", "events"]
        )
        self.register_tool(list_events_capability, self._handle_list_calendar_events)
    
    async def _handle_create_calendar_event(self, request: MCPRequest, context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle create calendar event requests."""
        try:
            # Extract and validate parameters
            title = request.params.get("title")
            description = request.params.get("description")
            start_time_str = request.params.get("start_time")
            end_time_str = request.params.get("end_time")
            location = request.params.get("location")
            attendees = request.params.get("attendees", [])
            
            if not title or not start_time_str or not end_time_str:
                raise MCPError(
                    "Title, start_time, and end_time are required",
                    error_code="MISSING_EVENT_PARAMETERS",
                    request_id=request.id
                )
            
            # Parse timestamps
            start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
            end_time = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
            
            # Create event
            event_id = f"event_{len(self._calendar_events)}_{int(datetime.now().timestamp())}"
            event = CalendarEvent(
                id=event_id,
                title=title,
                description=description,
                start_time=start_time,
                end_time=end_time,
                location=location,
                attendees=attendees
            )
            
            self._calendar_events[event_id] = event
            
            self.logger.info(f"Calendar event created: {event_id}")
            
            return event.dict()
            
        except ValueError as e:
            raise MCPError(
                f"Invalid date/time format: {str(e)}",
                error_code="INVALID_DATETIME",
                request_id=request.id
            )
        except Exception as e:
            raise MCPError(
                f"Failed to create calendar event: {str(e)}",
                error_code="CALENDAR_EVENT_CREATE_ERROR",
                details={"error": str(e)},
                request_id=request.id
            )
    
    async def _handle_list_calendar_events(self, request: MCPRequest, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Handle list calendar events requests."""
        try:
            start_date_str = request.params.get("start_date")
            end_date_str = request.params.get("end_date")
            limit = request.params.get("limit", 50)
            
            # Parse date filters if provided
            start_date = None
            end_date = None
            
            if start_date_str:
                start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
            if end_date_str:
                end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
            
            # Filter events
            filtered_events = []
            for event in self._calendar_events.values():
                if start_date and event.start_time < start_date:
                    continue
                if end_date and event.start_time > end_date:
                    continue
                filtered_events.append(event)
            
            # Sort by start time and limit results
            filtered_events.sort(key=lambda e: e.start_time)
            limited_events = filtered_events[:limit]
            
            self.logger.info(f"Listed {len(limited_events)} calendar events")
            
            return [event.dict() for event in limited_events]
            
        except ValueError as e:
            raise MCPError(
                f"Invalid date format: {str(e)}",
                error_code="INVALID_DATE",
                request_id=request.id
            )
        except Exception as e:
            raise MCPError(
                f"Failed to list calendar events: {str(e)}",
                error_code="CALENDAR_EVENT_LIST_ERROR",
                details={"error": str(e)},
                request_id=request.id
            )
    
    # Content Creation Tools
    
    async def _register_content_tools(self) -> None:
        """Register content creation tool capabilities."""
        
        # Generate content from template
        generate_content_capability = MCPToolCapability(
            name="generate_content",
            description="Generate content using templates",
            parameters={
                "template_name": {"type": "string", "required": True, "description": "Template name"},
                "variables": {"type": "object", "required": True, "description": "Template variables"}
            },
            returns={"type": "object", "properties": {"content": "string", "template": "string"}},
            tags=["content", "generation", "templates"]
        )
        self.register_tool(generate_content_capability, self._handle_generate_content)
        
        # List available templates
        list_templates_capability = MCPToolCapability(
            name="list_content_templates",
            description="List available content templates",
            parameters={
                "type": {"type": "string", "required": False, "description": "Filter by template type"}
            },
            returns={"type": "array", "items": "ContentTemplate"},
            tags=["content", "templates"]
        )
        self.register_tool(list_templates_capability, self._handle_list_content_templates)
    
    async def _handle_generate_content(self, request: MCPRequest, context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle content generation requests."""
        template_name = request.params.get("template_name")
        variables = request.params.get("variables", {})
        
        if not template_name:
            raise MCPError(
                "Template name is required",
                error_code="MISSING_TEMPLATE_NAME",
                request_id=request.id
            )
        
        if template_name not in self._content_templates:
            raise MCPError(
                f"Template '{template_name}' not found",
                error_code="TEMPLATE_NOT_FOUND",
                details={"template_name": template_name},
                request_id=request.id
            )
        
        try:
            template = self._content_templates[template_name]
            
            # Simple template variable substitution
            content = template.template
            for var_name, var_value in variables.items():
                content = content.replace(f"{{{var_name}}}", str(var_value))
            
            result = {
                "content": content,
                "template": template_name
            }
            
            self.logger.info(f"Content generated using template: {template_name}")
            
            return result
            
        except Exception as e:
            raise MCPError(
                f"Content generation failed: {str(e)}",
                error_code="CONTENT_GENERATION_ERROR",
                details={"template_name": template_name, "error": str(e)},
                request_id=request.id
            )
    
    async def _handle_list_content_templates(self, request: MCPRequest, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Handle list content templates requests."""
        template_type = request.params.get("type")
        
        templates = list(self._content_templates.values())
        
        if template_type:
            templates = [t for t in templates if t.type == template_type]
        
        self.logger.info(f"Listed {len(templates)} content templates")
        
        return [template.dict() for template in templates]
    
    # Knowledge Base Tools
    
    async def _register_knowledge_base_tools(self) -> None:
        """Register knowledge base tool capabilities."""
        
        # Search knowledge base
        search_kb_capability = MCPToolCapability(
            name="search_knowledge_base",
            description="Search the knowledge base",
            parameters={
                "query": {"type": "string", "required": True, "description": "Search query"},
                "category": {"type": "string", "required": False, "description": "Filter by category"},
                "max_results": {"type": "integer", "required": False, "default": 10, "description": "Maximum number of results"}
            },
            returns={"type": "array", "items": "KnowledgeBaseEntry"},
            tags=["knowledge", "search", "documentation"]
        )
        self.register_tool(search_kb_capability, self._handle_search_knowledge_base)
    
    async def _handle_search_knowledge_base(self, request: MCPRequest, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Handle knowledge base search requests."""
        query = request.params.get("query", "").lower()
        category = request.params.get("category")
        max_results = request.params.get("max_results", 10)
        
        if not query:
            raise MCPError(
                "Search query is required",
                error_code="MISSING_SEARCH_QUERY",
                request_id=request.id
            )
        
        try:
            # Simple text-based search
            results = []
            for entry in self._knowledge_base.values():
                if category and entry.category != category:
                    continue
                
                # Check if query matches title, content, or tags
                if (query in entry.title.lower() or 
                    query in entry.content.lower() or
                    any(query in tag.lower() for tag in entry.tags)):
                    results.append(entry)
            
            # Sort by relevance (simple: title match first, then content)
            results.sort(key=lambda e: (
                query not in e.title.lower(),  # Title matches first
                -len([tag for tag in e.tags if query in tag.lower()])  # More tag matches first
            ))
            
            # Limit results
            results = results[:max_results]
            
            self.logger.info(f"Knowledge base search: query='{query}', results={len(results)}")
            
            return [entry.dict() for entry in results]
            
        except Exception as e:
            raise MCPError(
                f"Knowledge base search failed: {str(e)}",
                error_code="KNOWLEDGE_BASE_SEARCH_ERROR",
                details={"query": query, "error": str(e)},
                request_id=request.id
            )
    
    # Search engine implementations
    
    async def _search_duckduckgo(self, query: str, max_results: int) -> List[WebSearchResult]:
        """Search using DuckDuckGo (free API)."""
        try:
            # DuckDuckGo Instant Answer API
            params = {
                "q": query,
                "format": "json",
                "no_html": "1",
                "skip_disambig": "1"
            }
            
            response = await self.http_client.get(
                "https://api.duckduckgo.com/",
                params=params
            )
            response.raise_for_status()
            data = response.json()
            
            results = []
            
            # Add instant answer if available
            if data.get("Abstract"):
                results.append(WebSearchResult(
                    title=data.get("AbstractText", "DuckDuckGo Result")[:100],
                    url=data.get("AbstractURL", ""),
                    snippet=data.get("Abstract", "")[:500],
                    source="duckduckgo",
                    score=1.0
                ))
            
            # Add related topics
            for topic in data.get("RelatedTopics", [])[:max_results-len(results)]:
                if isinstance(topic, dict) and topic.get("Text"):
                    results.append(WebSearchResult(
                        title=topic.get("Text", "")[:100],
                        url=topic.get("FirstURL", ""),
                        snippet=topic.get("Text", "")[:500],
                        source="duckduckgo",
                        score=0.8
                    ))
            
            return results[:max_results]
            
        except Exception as e:
            self.logger.error(f"DuckDuckGo search failed: {str(e)}")
            return []
    
    async def _search_google(self, query: str, max_results: int) -> List[WebSearchResult]:
        """Search using Google (requires API key)."""
        api_key = self.search_api_keys.get("google")
        if not api_key:
            raise MCPError(
                "Google API key not configured",
                error_code="GOOGLE_API_KEY_MISSING"
            )
        
        try:
            # Google Custom Search API would be used here
            # This is a placeholder implementation
            return [
                WebSearchResult(
                    title=f"Google Search Result for '{query}'",
                    url="https://www.google.com/search?q=" + query.replace(" ", "+"),
                    snippet=f"Google search results for query: {query}",
                    source="google",
                    score=1.0
                )
            ]
            
        except Exception as e:
            self.logger.error(f"Google search failed: {str(e)}")
            return []
    
    async def _search_bing(self, query: str, max_results: int) -> List[WebSearchResult]:
        """Search using Bing (requires API key)."""
        api_key = self.search_api_keys.get("bing")
        if not api_key:
            raise MCPError(
                "Bing API key not configured",
                error_code="BING_API_KEY_MISSING"
            )
        
        try:
            # Bing Web Search API would be used here
            # This is a placeholder implementation
            return [
                WebSearchResult(
                    title=f"Bing Search Result for '{query}'",
                    url="https://www.bing.com/search?q=" + query.replace(" ", "+"),
                    snippet=f"Bing search results for query: {query}",
                    source="bing",
                    score=1.0
                )
            ]
            
        except Exception as e:
            self.logger.error(f"Bing search failed: {str(e)}")
            return []
    
    # Helper methods
    
    async def _load_default_templates(self) -> None:
        """Load default content templates."""
        default_templates = [
            ContentTemplate(
                name="blog_post",
                type="blog",
                template="""# {title}

{introduction}

## Main Content

{content}

## Conclusion

{conclusion}

---
Published on {date}
Author: {author}""",
                variables=["title", "introduction", "content", "conclusion", "date", "author"],
                tags=["blog", "article", "post"]
            ),
            
            ContentTemplate(
                name="social_media_post",
                type="social",
                template="""{content}

{hashtags}

#SocialMedia #{platform}""",
                variables=["content", "hashtags", "platform"],
                tags=["social", "twitter", "facebook", "linkedin"]
            ),
            
            ContentTemplate(
                name="email_template",
                type="email",
                template="""Subject: {subject}

Dear {recipient_name},

{greeting}

{body}

{closing}

Best regards,
{sender_name}""",
                variables=["subject", "recipient_name", "greeting", "body", "closing", "sender_name"],
                tags=["email", "communication", "business"]
            )
        ]
        
        for template in default_templates:
            self._content_templates[template.name] = template
        
        self.logger.info(f"Loaded {len(default_templates)} default content templates")
    
    async def _load_knowledge_base(self) -> None:
        """Load knowledge base entries from file."""
        try:
            if self.knowledge_base_path:
                # In a real implementation, this would load from a file or database
                # For now, create some sample entries
                sample_entries = [
                    KnowledgeBaseEntry(
                        id="kb_001",
                        title="MCP Server Development Guide",
                        content="Guide for developing MCP servers with proper context injection and stateless design patterns.",
                        category="development",
                        tags=["mcp", "server", "development", "guide"],
                        author="System"
                    ),
                    KnowledgeBaseEntry(
                        id="kb_002",
                        title="Social Tools Usage",
                        content="Documentation for using social tools including web search, calendar management, and content generation.",
                        category="documentation",
                        tags=["social", "tools", "documentation", "usage"],
                        author="System"
                    )
                ]
                
                for entry in sample_entries:
                    self._knowledge_base[entry.id] = entry
                
                self.logger.info(f"Loaded {len(sample_entries)} knowledge base entries")
        
        except Exception as e:
            self.logger.error(f"Failed to load knowledge base: {str(e)}")
    
    def _html_to_markdown(self, html_content: str) -> str:
        """Simple HTML to Markdown conversion."""
        import re
        
        # Basic HTML to Markdown conversion
        content = html_content
        
        # Headers
        content = re.sub(r'<h1[^>]*>([^<]+)</h1>', r'# \1', content)
        content = re.sub(r'<h2[^>]*>([^<]+)</h2>', r'## \1', content)
        content = re.sub(r'<h3[^>]*>([^<]+)</h3>', r'### \1', content)
        
        # Links
        content = re.sub(r'<a[^>]+href="([^"]+)"[^>]*>([^<]+)</a>', r'[\2](\1)', content)
        
        # Bold and italic
        content = re.sub(r'<strong[^>]*>([^<]+)</strong>', r'**\1**', content)
        content = re.sub(r'<em[^>]*>([^<]+)</em>', r'*\1*', content)
        
        # Paragraphs
        content = re.sub(r'<p[^>]*>', '', content)
        content = re.sub(r'</p>', '\n\n', content)
        
        # Remove remaining HTML tags
        content = re.sub(r'<[^>]+>', ' ', content)
        
        # Clean up whitespace
        content = re.sub(r'\n\s*\n', '\n\n', content)
        content = re.sub(r' +', ' ', content)
        
        return content.strip()


# Export the server class
__all__ = ["SocialToolsServer"]