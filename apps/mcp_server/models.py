from django.contrib.auth import get_user_model
from django.db import models

from apps.common.behaviors import Timestampable

User = get_user_model()


class Organization(Timestampable, models.Model):
    """B2B organization that uses the QuickBooks MCP server."""
    
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    is_active = models.BooleanField(default=True)
    
    # Subscription info
    subscription_tier = models.CharField(
        max_length=50,
        choices=[
            ("free", "Free"),
            ("starter", "Starter"),
            ("professional", "Professional"),
            ("enterprise", "Enterprise"),
        ],
        default="free",
    )
    subscription_expires_at = models.DateTimeField(null=True, blank=True)
    
    # Limits
    api_calls_limit = models.IntegerField(default=1000)
    api_calls_used = models.IntegerField(default=0)
    
    class Meta:
        ordering = ["name"]
        
    def __str__(self):
        return self.name


class QuickBooksConnection(Timestampable, models.Model):
    """QuickBooks API connection for an organization."""
    
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="quickbooks_connections",
    )
    company_id = models.CharField(max_length=255)
    company_name = models.CharField(max_length=255)
    
    # OAuth tokens
    access_token = models.TextField()
    refresh_token = models.TextField()
    token_expires_at = models.DateTimeField()
    
    # Connection status
    is_active = models.BooleanField(default=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ["-created_at"]
        unique_together = [["organization", "company_id"]]
        
    def __str__(self):
        return f"{self.organization.name} - {self.company_name}"


class MCPSession(Timestampable, models.Model):
    """MCP protocol session for client connections."""
    
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="mcp_sessions",
    )
    session_id = models.UUIDField(unique=True)
    client_info = models.JSONField(default=dict)
    
    # Session status
    is_active = models.BooleanField(default=True)
    connected_at = models.DateTimeField(auto_now_add=True)
    disconnected_at = models.DateTimeField(null=True, blank=True)
    
    # Usage tracking
    requests_count = models.IntegerField(default=0)
    tokens_used = models.IntegerField(default=0)
    
    class Meta:
        ordering = ["-connected_at"]
        
    def __str__(self):
        return f"Session {self.session_id} - {self.organization.name}"


class APIKey(Timestampable, models.Model):
    """API keys for organization authentication."""
    
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="api_keys",
    )
    name = models.CharField(max_length=255)
    key = models.CharField(max_length=255, unique=True)
    is_active = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    
    # Permissions
    permissions = models.JSONField(
        default=list,
        help_text="List of allowed MCP tools/resources",
    )
    
    class Meta:
        ordering = ["-created_at"]
        verbose_name = "API Key"
        verbose_name_plural = "API Keys"
        
    def __str__(self):
        return f"{self.name} - {self.organization.name}"