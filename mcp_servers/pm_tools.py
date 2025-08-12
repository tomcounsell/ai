"""
Project Management Tools MCP Server

This module implements MCP server for project management and development tools:
- GitHub integration (repositories, issues, pull requests, releases)
- Linear integration (issues, projects, workflows)
- Documentation management
- Project tracking and analytics
"""

import asyncio
import base64
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Union
import json

import httpx
from pydantic import BaseModel, Field, validator

from .base import MCPServer, MCPToolCapability, MCPRequest, MCPError
from .context_manager import MCPContextManager, SecurityLevel


class GitHubRepository(BaseModel):
    """GitHub repository structure."""
    
    id: int = Field(..., description="Repository ID")
    name: str = Field(..., description="Repository name")
    full_name: str = Field(..., description="Full repository name (owner/repo)")
    description: Optional[str] = Field(None, description="Repository description")
    private: bool = Field(default=False, description="Whether repository is private")
    html_url: str = Field(..., description="Repository URL")
    clone_url: str = Field(..., description="Clone URL")
    default_branch: str = Field(default="main", description="Default branch")
    
    # Statistics
    stargazers_count: int = Field(default=0, description="Number of stars")
    forks_count: int = Field(default=0, description="Number of forks")
    open_issues_count: int = Field(default=0, description="Number of open issues")
    
    # Timestamps
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class GitHubIssue(BaseModel):
    """GitHub issue structure."""
    
    id: int = Field(..., description="Issue ID")
    number: int = Field(..., description="Issue number")
    title: str = Field(..., description="Issue title")
    body: Optional[str] = Field(None, description="Issue body/description")
    state: str = Field(..., description="Issue state (open, closed)")
    html_url: str = Field(..., description="Issue URL")
    
    # User information
    user_login: Optional[str] = Field(None, description="Issue creator login")
    assignee_login: Optional[str] = Field(None, description="Assignee login")
    
    # Labels and metadata
    labels: List[str] = Field(default_factory=list, description="Issue labels")
    milestone: Optional[str] = Field(None, description="Issue milestone")
    
    # Comments
    comments: int = Field(default=0, description="Number of comments")
    
    # Timestamps
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    closed_at: Optional[datetime] = Field(None, description="Close timestamp")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class LinearIssue(BaseModel):
    """Linear issue structure."""
    
    id: str = Field(..., description="Issue ID")
    identifier: str = Field(..., description="Issue identifier (e.g., ENG-123)")
    title: str = Field(..., description="Issue title")
    description: Optional[str] = Field(None, description="Issue description")
    
    # Status and priority
    state: str = Field(..., description="Issue state")
    priority: int = Field(default=0, description="Issue priority (0-4)")
    
    # Assignment
    assignee_name: Optional[str] = Field(None, description="Assignee name")
    team_name: Optional[str] = Field(None, description="Team name")
    
    # Project information
    project_name: Optional[str] = Field(None, description="Project name")
    cycle_name: Optional[str] = Field(None, description="Cycle name")
    
    # Labels
    labels: List[str] = Field(default_factory=list, description="Issue labels")
    
    # Timestamps
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class ProjectMetrics(BaseModel):
    """Project metrics and analytics."""
    
    project_name: str = Field(..., description="Project name")
    
    # Issue metrics
    total_issues: int = Field(default=0, description="Total number of issues")
    open_issues: int = Field(default=0, description="Number of open issues")
    closed_issues: int = Field(default=0, description="Number of closed issues")
    
    # Velocity metrics
    issues_created_this_week: int = Field(default=0, description="Issues created this week")
    issues_closed_this_week: int = Field(default=0, description="Issues closed this week")
    
    # Time metrics
    average_close_time_days: float = Field(default=0.0, description="Average time to close issues (days)")
    
    # Team metrics
    active_contributors: int = Field(default=0, description="Number of active contributors")
    
    # Repository metrics (for GitHub projects)
    commits_this_week: Optional[int] = Field(None, description="Commits this week")
    pull_requests_this_week: Optional[int] = Field(None, description="Pull requests this week")
    
    # Generated timestamp
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class DocumentationPage(BaseModel):
    """Documentation page structure."""
    
    id: str = Field(..., description="Page ID")
    title: str = Field(..., description="Page title")
    content: str = Field(..., description="Page content (markdown)")
    path: str = Field(..., description="Page path/URL")
    
    # Metadata
    category: str = Field(default="general", description="Page category")
    tags: List[str] = Field(default_factory=list, description="Page tags")
    author: Optional[str] = Field(None, description="Page author")
    
    # Status
    published: bool = Field(default=False, description="Whether page is published")
    version: str = Field(default="1.0.0", description="Page version")
    
    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class ProjectManagementServer(MCPServer):
    """
    MCP Server implementation for project management tools.
    
    Provides stateless project management functionality with context injection including:
    - GitHub repository and issue management
    - Linear issue and project tracking
    - Documentation management
    - Project analytics and reporting
    """
    
    def __init__(
        self,
        name: str = "project_management",
        version: str = "1.0.0", 
        description: str = "Project management tools MCP server",
        github_token: str = None,
        linear_token: str = None,
        documentation_path: str = None,
        **kwargs
    ):
        super().__init__(name, version, description, **kwargs)
        
        # API tokens
        self.github_token = github_token
        self.linear_token = linear_token
        self.documentation_path = documentation_path
        
        # HTTP client for external APIs
        self.http_client = httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={
                "User-Agent": f"ProjectManagement-MCP-Server/{version}"
            }
        )
        
        # In-memory stores (in production, use proper databases)
        self._documentation_pages: Dict[str, DocumentationPage] = {}
        self._project_metrics: Dict[str, ProjectMetrics] = {}
        
        # API configurations
        self._github_api_base = "https://api.github.com"
        self._linear_api_base = "https://api.linear.app/graphql"
        
        self.logger.info("Project Management Server initialized")
    
    async def initialize(self) -> None:
        """Initialize the project management server."""
        try:
            # Register all tool capabilities
            await self._register_github_tools()
            await self._register_linear_tools()
            await self._register_documentation_tools()
            await self._register_analytics_tools()
            
            # Load documentation if path is provided
            if self.documentation_path:
                await self._load_documentation()
            
            self.logger.info("Project Management Server initialized successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize Project Management Server: {str(e)}")
            raise MCPError(
                f"Project management server initialization failed: {str(e)}",
                error_code="PM_INIT_ERROR",
                details={"error": str(e)},
                recoverable=False
            )
    
    async def shutdown(self) -> None:
        """Shutdown the project management server."""
        try:
            await self.http_client.aclose()
            self.logger.info("Project Management Server shut down successfully")
            
        except Exception as e:
            self.logger.error(f"Error during Project Management Server shutdown: {str(e)}")
    
    # GitHub Tools
    
    async def _register_github_tools(self) -> None:
        """Register GitHub tool capabilities."""
        
        # List repositories
        list_repos_capability = MCPToolCapability(
            name="github_list_repositories",
            description="List GitHub repositories for a user or organization",
            parameters={
                "owner": {"type": "string", "required": True, "description": "Repository owner (user or organization)"},
                "type": {"type": "string", "required": False, "default": "all", "enum": ["all", "public", "private"], "description": "Repository type filter"},
                "sort": {"type": "string", "required": False, "default": "updated", "enum": ["created", "updated", "pushed", "full_name"], "description": "Sort order"}
            },
            returns={"type": "array", "items": "GitHubRepository"},
            tags=["github", "repositories", "external"]
        )
        self.register_tool(list_repos_capability, self._handle_github_list_repositories)
        
        # Get repository details
        get_repo_capability = MCPToolCapability(
            name="github_get_repository",
            description="Get detailed information about a specific GitHub repository",
            parameters={
                "owner": {"type": "string", "required": True, "description": "Repository owner"},
                "repo": {"type": "string", "required": True, "description": "Repository name"}
            },
            returns={"type": "object", "description": "Repository details"},
            tags=["github", "repositories", "external"]
        )
        self.register_tool(get_repo_capability, self._handle_github_get_repository)
        
        # List issues
        list_issues_capability = MCPToolCapability(
            name="github_list_issues",
            description="List GitHub issues for a repository",
            parameters={
                "owner": {"type": "string", "required": True, "description": "Repository owner"},
                "repo": {"type": "string", "required": True, "description": "Repository name"},
                "state": {"type": "string", "required": False, "default": "open", "enum": ["open", "closed", "all"], "description": "Issue state filter"},
                "labels": {"type": "string", "required": False, "description": "Comma-separated list of labels"},
                "sort": {"type": "string", "required": False, "default": "created", "enum": ["created", "updated", "comments"], "description": "Sort field"}
            },
            returns={"type": "array", "items": "GitHubIssue"},
            tags=["github", "issues", "external"]
        )
        self.register_tool(list_issues_capability, self._handle_github_list_issues)
        
        # Create issue
        create_issue_capability = MCPToolCapability(
            name="github_create_issue",
            description="Create a new GitHub issue",
            parameters={
                "owner": {"type": "string", "required": True, "description": "Repository owner"},
                "repo": {"type": "string", "required": True, "description": "Repository name"},
                "title": {"type": "string", "required": True, "description": "Issue title"},
                "body": {"type": "string", "required": False, "description": "Issue body/description"},
                "labels": {"type": "array", "items": "string", "required": False, "description": "Issue labels"},
                "assignees": {"type": "array", "items": "string", "required": False, "description": "Issue assignees"}
            },
            returns={"type": "object", "description": "Created issue"},
            tags=["github", "issues", "create", "external"]
        )
        self.register_tool(create_issue_capability, self._handle_github_create_issue)
    
    async def _handle_github_list_repositories(self, request: MCPRequest, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Handle GitHub list repositories requests."""
        if not self.github_token:
            raise MCPError(
                "GitHub token not configured",
                error_code="GITHUB_TOKEN_MISSING",
                request_id=request.id
            )
        
        owner = request.params.get("owner")
        repo_type = request.params.get("type", "all")
        sort = request.params.get("sort", "updated")
        
        if not owner:
            raise MCPError(
                "Owner is required",
                error_code="MISSING_OWNER",
                request_id=request.id
            )
        
        try:
            headers = {
                "Authorization": f"token {self.github_token}",
                "Accept": "application/vnd.github.v3+json"
            }
            
            params = {
                "type": repo_type,
                "sort": sort,
                "per_page": 50
            }
            
            url = f"{self._github_api_base}/users/{owner}/repos"
            response = await self.http_client.get(url, headers=headers, params=params)
            response.raise_for_status()
            
            repos_data = response.json()
            repositories = []
            
            for repo_data in repos_data:
                repo = GitHubRepository(
                    id=repo_data["id"],
                    name=repo_data["name"],
                    full_name=repo_data["full_name"],
                    description=repo_data.get("description"),
                    private=repo_data["private"],
                    html_url=repo_data["html_url"],
                    clone_url=repo_data["clone_url"],
                    default_branch=repo_data["default_branch"],
                    stargazers_count=repo_data["stargazers_count"],
                    forks_count=repo_data["forks_count"],
                    open_issues_count=repo_data["open_issues_count"],
                    created_at=datetime.fromisoformat(repo_data["created_at"].replace('Z', '+00:00')),
                    updated_at=datetime.fromisoformat(repo_data["updated_at"].replace('Z', '+00:00'))
                )
                repositories.append(repo)
            
            self.logger.info(f"Listed {len(repositories)} GitHub repositories for {owner}")
            
            return [repo.dict() for repo in repositories]
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise MCPError(
                    f"GitHub user or organization '{owner}' not found",
                    error_code="GITHUB_OWNER_NOT_FOUND",
                    details={"owner": owner},
                    request_id=request.id
                )
            else:
                raise MCPError(
                    f"GitHub API error: {e.response.status_code}",
                    error_code="GITHUB_API_ERROR",
                    details={"status_code": e.response.status_code, "response": e.response.text},
                    request_id=request.id
                )
        except Exception as e:
            raise MCPError(
                f"Failed to list GitHub repositories: {str(e)}",
                error_code="GITHUB_LIST_REPOS_ERROR",
                details={"owner": owner, "error": str(e)},
                request_id=request.id
            )
    
    async def _handle_github_get_repository(self, request: MCPRequest, context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle GitHub get repository requests."""
        if not self.github_token:
            raise MCPError(
                "GitHub token not configured",
                error_code="GITHUB_TOKEN_MISSING",
                request_id=request.id
            )
        
        owner = request.params.get("owner")
        repo = request.params.get("repo")
        
        if not owner or not repo:
            raise MCPError(
                "Owner and repository name are required",
                error_code="MISSING_REPO_PARAMS",
                request_id=request.id
            )
        
        try:
            headers = {
                "Authorization": f"token {self.github_token}",
                "Accept": "application/vnd.github.v3+json"
            }
            
            url = f"{self._github_api_base}/repos/{owner}/{repo}"
            response = await self.http_client.get(url, headers=headers)
            response.raise_for_status()
            
            repo_data = response.json()
            
            repository = GitHubRepository(
                id=repo_data["id"],
                name=repo_data["name"],
                full_name=repo_data["full_name"],
                description=repo_data.get("description"),
                private=repo_data["private"],
                html_url=repo_data["html_url"],
                clone_url=repo_data["clone_url"],
                default_branch=repo_data["default_branch"],
                stargazers_count=repo_data["stargazers_count"],
                forks_count=repo_data["forks_count"],
                open_issues_count=repo_data["open_issues_count"],
                created_at=datetime.fromisoformat(repo_data["created_at"].replace('Z', '+00:00')),
                updated_at=datetime.fromisoformat(repo_data["updated_at"].replace('Z', '+00:00'))
            )
            
            self.logger.info(f"Retrieved GitHub repository: {owner}/{repo}")
            
            return repository.dict()
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise MCPError(
                    f"GitHub repository '{owner}/{repo}' not found",
                    error_code="GITHUB_REPO_NOT_FOUND",
                    details={"owner": owner, "repo": repo},
                    request_id=request.id
                )
            else:
                raise MCPError(
                    f"GitHub API error: {e.response.status_code}",
                    error_code="GITHUB_API_ERROR",
                    details={"status_code": e.response.status_code, "response": e.response.text},
                    request_id=request.id
                )
        except Exception as e:
            raise MCPError(
                f"Failed to get GitHub repository: {str(e)}",
                error_code="GITHUB_GET_REPO_ERROR",
                details={"owner": owner, "repo": repo, "error": str(e)},
                request_id=request.id
            )
    
    async def _handle_github_list_issues(self, request: MCPRequest, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Handle GitHub list issues requests."""
        if not self.github_token:
            raise MCPError(
                "GitHub token not configured",
                error_code="GITHUB_TOKEN_MISSING",
                request_id=request.id
            )
        
        owner = request.params.get("owner")
        repo = request.params.get("repo")
        state = request.params.get("state", "open")
        labels = request.params.get("labels")
        sort = request.params.get("sort", "created")
        
        if not owner or not repo:
            raise MCPError(
                "Owner and repository name are required",
                error_code="MISSING_REPO_PARAMS",
                request_id=request.id
            )
        
        try:
            headers = {
                "Authorization": f"token {self.github_token}",
                "Accept": "application/vnd.github.v3+json"
            }
            
            params = {
                "state": state,
                "sort": sort,
                "per_page": 50
            }
            
            if labels:
                params["labels"] = labels
            
            url = f"{self._github_api_base}/repos/{owner}/{repo}/issues"
            response = await self.http_client.get(url, headers=headers, params=params)
            response.raise_for_status()
            
            issues_data = response.json()
            issues = []
            
            for issue_data in issues_data:
                # Skip pull requests (they appear as issues in GitHub API)
                if issue_data.get("pull_request"):
                    continue
                
                issue = GitHubIssue(
                    id=issue_data["id"],
                    number=issue_data["number"],
                    title=issue_data["title"],
                    body=issue_data.get("body"),
                    state=issue_data["state"],
                    html_url=issue_data["html_url"],
                    user_login=issue_data["user"]["login"] if issue_data.get("user") else None,
                    assignee_login=issue_data["assignee"]["login"] if issue_data.get("assignee") else None,
                    labels=[label["name"] for label in issue_data.get("labels", [])],
                    milestone=issue_data["milestone"]["title"] if issue_data.get("milestone") else None,
                    comments=issue_data["comments"],
                    created_at=datetime.fromisoformat(issue_data["created_at"].replace('Z', '+00:00')),
                    updated_at=datetime.fromisoformat(issue_data["updated_at"].replace('Z', '+00:00')),
                    closed_at=datetime.fromisoformat(issue_data["closed_at"].replace('Z', '+00:00')) if issue_data.get("closed_at") else None
                )
                issues.append(issue)
            
            self.logger.info(f"Listed {len(issues)} GitHub issues for {owner}/{repo}")
            
            return [issue.dict() for issue in issues]
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise MCPError(
                    f"GitHub repository '{owner}/{repo}' not found",
                    error_code="GITHUB_REPO_NOT_FOUND",
                    details={"owner": owner, "repo": repo},
                    request_id=request.id
                )
            else:
                raise MCPError(
                    f"GitHub API error: {e.response.status_code}",
                    error_code="GITHUB_API_ERROR",
                    details={"status_code": e.response.status_code, "response": e.response.text},
                    request_id=request.id
                )
        except Exception as e:
            raise MCPError(
                f"Failed to list GitHub issues: {str(e)}",
                error_code="GITHUB_LIST_ISSUES_ERROR",
                details={"owner": owner, "repo": repo, "error": str(e)},
                request_id=request.id
            )
    
    async def _handle_github_create_issue(self, request: MCPRequest, context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle GitHub create issue requests."""
        if not self.github_token:
            raise MCPError(
                "GitHub token not configured",
                error_code="GITHUB_TOKEN_MISSING",
                request_id=request.id
            )
        
        owner = request.params.get("owner")
        repo = request.params.get("repo")
        title = request.params.get("title")
        body = request.params.get("body")
        labels = request.params.get("labels", [])
        assignees = request.params.get("assignees", [])
        
        if not owner or not repo or not title:
            raise MCPError(
                "Owner, repository name, and title are required",
                error_code="MISSING_ISSUE_PARAMS",
                request_id=request.id
            )
        
        try:
            headers = {
                "Authorization": f"token {self.github_token}",
                "Accept": "application/vnd.github.v3+json",
                "Content-Type": "application/json"
            }
            
            issue_data = {
                "title": title
            }
            
            if body:
                issue_data["body"] = body
            if labels:
                issue_data["labels"] = labels
            if assignees:
                issue_data["assignees"] = assignees
            
            url = f"{self._github_api_base}/repos/{owner}/{repo}/issues"
            response = await self.http_client.post(url, headers=headers, json=issue_data)
            response.raise_for_status()
            
            created_issue_data = response.json()
            
            issue = GitHubIssue(
                id=created_issue_data["id"],
                number=created_issue_data["number"],
                title=created_issue_data["title"],
                body=created_issue_data.get("body"),
                state=created_issue_data["state"],
                html_url=created_issue_data["html_url"],
                user_login=created_issue_data["user"]["login"] if created_issue_data.get("user") else None,
                assignee_login=created_issue_data["assignee"]["login"] if created_issue_data.get("assignee") else None,
                labels=[label["name"] for label in created_issue_data.get("labels", [])],
                milestone=created_issue_data["milestone"]["title"] if created_issue_data.get("milestone") else None,
                comments=created_issue_data["comments"],
                created_at=datetime.fromisoformat(created_issue_data["created_at"].replace('Z', '+00:00')),
                updated_at=datetime.fromisoformat(created_issue_data["updated_at"].replace('Z', '+00:00'))
            )
            
            self.logger.info(f"Created GitHub issue: {owner}/{repo}#{issue.number}")
            
            return issue.dict()
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise MCPError(
                    f"GitHub repository '{owner}/{repo}' not found",
                    error_code="GITHUB_REPO_NOT_FOUND",
                    details={"owner": owner, "repo": repo},
                    request_id=request.id
                )
            else:
                raise MCPError(
                    f"GitHub API error: {e.response.status_code}",
                    error_code="GITHUB_API_ERROR",
                    details={"status_code": e.response.status_code, "response": e.response.text},
                    request_id=request.id
                )
        except Exception as e:
            raise MCPError(
                f"Failed to create GitHub issue: {str(e)}",
                error_code="GITHUB_CREATE_ISSUE_ERROR",
                details={"owner": owner, "repo": repo, "title": title, "error": str(e)},
                request_id=request.id
            )
    
    # Linear Tools (simplified GraphQL implementation)
    
    async def _register_linear_tools(self) -> None:
        """Register Linear tool capabilities."""
        
        # List Linear issues
        list_linear_issues_capability = MCPToolCapability(
            name="linear_list_issues",
            description="List Linear issues with filtering options",
            parameters={
                "team_key": {"type": "string", "required": False, "description": "Team key filter"},
                "state": {"type": "string", "required": False, "description": "Issue state filter"},
                "assignee": {"type": "string", "required": False, "description": "Assignee filter"},
                "limit": {"type": "integer", "required": False, "default": 50, "description": "Maximum number of issues"}
            },
            returns={"type": "array", "items": "LinearIssue"},
            tags=["linear", "issues", "external"]
        )
        self.register_tool(list_linear_issues_capability, self._handle_linear_list_issues)
    
    async def _handle_linear_list_issues(self, request: MCPRequest, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Handle Linear list issues requests."""
        if not self.linear_token:
            raise MCPError(
                "Linear token not configured",
                error_code="LINEAR_TOKEN_MISSING",
                request_id=request.id
            )
        
        # This is a simplified implementation
        # In a real implementation, you would use Linear's GraphQL API
        team_key = request.params.get("team_key")
        state = request.params.get("state")
        assignee = request.params.get("assignee")
        limit = request.params.get("limit", 50)
        
        try:
            # Placeholder for Linear GraphQL query
            # Real implementation would construct and execute GraphQL query
            sample_issues = [
                LinearIssue(
                    id="linear_001",
                    identifier="ENG-123",
                    title="Sample Linear Issue",
                    description="This is a sample Linear issue for demonstration",
                    state="In Progress",
                    priority=2,
                    assignee_name="John Doe",
                    team_name="Engineering",
                    created_at=datetime.now(timezone.utc) - timedelta(days=5),
                    updated_at=datetime.now(timezone.utc) - timedelta(days=1)
                )
            ]
            
            # Apply filters
            filtered_issues = sample_issues
            if team_key:
                filtered_issues = [i for i in filtered_issues if team_key.lower() in (i.team_name or "").lower()]
            if state:
                filtered_issues = [i for i in filtered_issues if state.lower() in i.state.lower()]
            if assignee:
                filtered_issues = [i for i in filtered_issues if assignee.lower() in (i.assignee_name or "").lower()]
            
            # Limit results
            filtered_issues = filtered_issues[:limit]
            
            self.logger.info(f"Listed {len(filtered_issues)} Linear issues")
            
            return [issue.dict() for issue in filtered_issues]
            
        except Exception as e:
            raise MCPError(
                f"Failed to list Linear issues: {str(e)}",
                error_code="LINEAR_LIST_ISSUES_ERROR",
                details={"error": str(e)},
                request_id=request.id
            )
    
    # Documentation Tools
    
    async def _register_documentation_tools(self) -> None:
        """Register documentation tool capabilities."""
        
        # Create documentation page
        create_doc_capability = MCPToolCapability(
            name="create_documentation_page",
            description="Create a new documentation page",
            parameters={
                "title": {"type": "string", "required": True, "description": "Page title"},
                "content": {"type": "string", "required": True, "description": "Page content (markdown)"},
                "category": {"type": "string", "required": False, "default": "general", "description": "Page category"},
                "tags": {"type": "array", "items": "string", "required": False, "description": "Page tags"},
                "published": {"type": "boolean", "required": False, "default": False, "description": "Whether to publish immediately"}
            },
            returns={"type": "object", "description": "Created documentation page"},
            tags=["documentation", "content", "creation"]
        )
        self.register_tool(create_doc_capability, self._handle_create_documentation_page)
        
        # Search documentation
        search_docs_capability = MCPToolCapability(
            name="search_documentation",
            description="Search documentation pages",
            parameters={
                "query": {"type": "string", "required": True, "description": "Search query"},
                "category": {"type": "string", "required": False, "description": "Category filter"},
                "published_only": {"type": "boolean", "required": False, "default": True, "description": "Search only published pages"}
            },
            returns={"type": "array", "items": "DocumentationPage"},
            tags=["documentation", "search"]
        )
        self.register_tool(search_docs_capability, self._handle_search_documentation)
    
    async def _handle_create_documentation_page(self, request: MCPRequest, context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle create documentation page requests."""
        title = request.params.get("title")
        content = request.params.get("content")
        category = request.params.get("category", "general")
        tags = request.params.get("tags", [])
        published = request.params.get("published", False)
        
        if not title or not content:
            raise MCPError(
                "Title and content are required",
                error_code="MISSING_DOC_PARAMS",
                request_id=request.id
            )
        
        try:
            # Create documentation page
            page_id = f"doc_{len(self._documentation_pages)}_{int(datetime.now().timestamp())}"
            path = f"/{category}/{title.lower().replace(' ', '-')}"
            
            # Get author from context if available
            author = None
            if context.get("user"):
                author = context["user"].get("username") or context["user"].get("display_name")
            
            page = DocumentationPage(
                id=page_id,
                title=title,
                content=content,
                path=path,
                category=category,
                tags=tags,
                author=author,
                published=published
            )
            
            self._documentation_pages[page_id] = page
            
            self.logger.info(f"Created documentation page: {page_id}")
            
            return page.dict()
            
        except Exception as e:
            raise MCPError(
                f"Failed to create documentation page: {str(e)}",
                error_code="DOC_CREATE_ERROR",
                details={"title": title, "error": str(e)},
                request_id=request.id
            )
    
    async def _handle_search_documentation(self, request: MCPRequest, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Handle search documentation requests."""
        query = request.params.get("query", "").lower()
        category = request.params.get("category")
        published_only = request.params.get("published_only", True)
        
        if not query:
            raise MCPError(
                "Search query is required",
                error_code="MISSING_SEARCH_QUERY",
                request_id=request.id
            )
        
        try:
            # Search documentation pages
            results = []
            for page in self._documentation_pages.values():
                if published_only and not page.published:
                    continue
                
                if category and page.category != category:
                    continue
                
                # Check if query matches title, content, or tags
                if (query in page.title.lower() or 
                    query in page.content.lower() or
                    any(query in tag.lower() for tag in page.tags)):
                    results.append(page)
            
            # Sort by relevance (simple: title match first)
            results.sort(key=lambda p: (
                query not in p.title.lower(),  # Title matches first
                -len([tag for tag in p.tags if query in tag.lower()])  # More tag matches first
            ))
            
            self.logger.info(f"Documentation search: query='{query}', results={len(results)}")
            
            return [page.dict() for page in results]
            
        except Exception as e:
            raise MCPError(
                f"Documentation search failed: {str(e)}",
                error_code="DOC_SEARCH_ERROR",
                details={"query": query, "error": str(e)},
                request_id=request.id
            )
    
    # Analytics Tools
    
    async def _register_analytics_tools(self) -> None:
        """Register analytics tool capabilities."""
        
        # Generate project metrics
        project_metrics_capability = MCPToolCapability(
            name="generate_project_metrics",
            description="Generate project metrics and analytics",
            parameters={
                "project_name": {"type": "string", "required": True, "description": "Project name"},
                "github_repo": {"type": "string", "required": False, "description": "GitHub repository (owner/repo)"},
                "linear_team": {"type": "string", "required": False, "description": "Linear team key"}
            },
            returns={"type": "object", "description": "Project metrics"},
            tags=["analytics", "metrics", "reporting"]
        )
        self.register_tool(project_metrics_capability, self._handle_generate_project_metrics)
    
    async def _handle_generate_project_metrics(self, request: MCPRequest, context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle generate project metrics requests."""
        project_name = request.params.get("project_name")
        github_repo = request.params.get("github_repo")
        linear_team = request.params.get("linear_team")
        
        if not project_name:
            raise MCPError(
                "Project name is required",
                error_code="MISSING_PROJECT_NAME",
                request_id=request.id
            )
        
        try:
            metrics = ProjectMetrics(project_name=project_name)
            
            # Gather GitHub metrics if repository is provided
            if github_repo and self.github_token:
                owner, repo = github_repo.split("/", 1)
                
                # Get issue count
                try:
                    issue_request = MCPRequest(
                        method="github_list_issues",
                        params={"owner": owner, "repo": repo, "state": "all"}
                    )
                    issues_result = await self._handle_github_list_issues(issue_request, context)
                    
                    total_issues = len(issues_result)
                    open_issues = len([i for i in issues_result if i["state"] == "open"])
                    closed_issues = total_issues - open_issues
                    
                    # Calculate issues created/closed this week
                    one_week_ago = datetime.now(timezone.utc) - timedelta(days=7)
                    issues_created_this_week = len([
                        i for i in issues_result 
                        if datetime.fromisoformat(i["created_at"]) > one_week_ago
                    ])
                    issues_closed_this_week = len([
                        i for i in issues_result 
                        if i["state"] == "closed" and 
                        i.get("closed_at") and 
                        datetime.fromisoformat(i["closed_at"]) > one_week_ago
                    ])
                    
                    metrics.total_issues = total_issues
                    metrics.open_issues = open_issues
                    metrics.closed_issues = closed_issues
                    metrics.issues_created_this_week = issues_created_this_week
                    metrics.issues_closed_this_week = issues_closed_this_week
                    
                except Exception as e:
                    self.logger.warning(f"Failed to gather GitHub metrics: {str(e)}")
            
            # Gather Linear metrics if team is provided
            if linear_team and self.linear_token:
                try:
                    linear_request = MCPRequest(
                        method="linear_list_issues",
                        params={"team_key": linear_team}
                    )
                    linear_issues = await self._handle_linear_list_issues(linear_request, context)
                    
                    # Update metrics with Linear data
                    metrics.total_issues += len(linear_issues)
                    
                except Exception as e:
                    self.logger.warning(f"Failed to gather Linear metrics: {str(e)}")
            
            # Cache metrics
            self._project_metrics[project_name] = metrics
            
            self.logger.info(f"Generated project metrics for: {project_name}")
            
            return metrics.dict()
            
        except Exception as e:
            raise MCPError(
                f"Failed to generate project metrics: {str(e)}",
                error_code="PROJECT_METRICS_ERROR",
                details={"project_name": project_name, "error": str(e)},
                request_id=request.id
            )
    
    # Helper methods
    
    async def _load_documentation(self) -> None:
        """Load documentation pages from file system."""
        try:
            # In a real implementation, this would load documentation
            # from files or a content management system
            sample_docs = [
                DocumentationPage(
                    id="doc_001",
                    title="MCP Server Development Guide",
                    content="""# MCP Server Development Guide

This guide covers the development of MCP (Model Context Protocol) servers.

## Getting Started

1. Install the MCP framework
2. Create your server class
3. Register tool capabilities
4. Implement tool handlers

## Best Practices

- Use stateless design
- Implement proper context injection
- Handle errors gracefully
- Add comprehensive logging
""",
                    path="/development/mcp-server-guide",
                    category="development",
                    tags=["mcp", "development", "guide"],
                    published=True,
                    author="System"
                ),
                
                DocumentationPage(
                    id="doc_002", 
                    title="Project Management Workflow",
                    content="""# Project Management Workflow

This document describes the standard workflow for project management.

## GitHub Integration

- Create issues for tasks
- Use labels for categorization
- Assign issues to team members
- Link pull requests to issues

## Linear Integration

- Track projects and cycles
- Monitor team velocity
- Generate reports

## Documentation

- Keep docs up to date
- Use consistent formatting
- Include examples
""",
                    path="/processes/project-management",
                    category="processes",
                    tags=["project", "management", "workflow"],
                    published=True,
                    author="System"
                )
            ]
            
            for doc in sample_docs:
                self._documentation_pages[doc.id] = doc
            
            self.logger.info(f"Loaded {len(sample_docs)} documentation pages")
            
        except Exception as e:
            self.logger.error(f"Failed to load documentation: {str(e)}")


# Export the server class
__all__ = ["ProjectManagementServer"]