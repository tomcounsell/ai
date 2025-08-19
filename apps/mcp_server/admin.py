from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import APIKey, MCPSession, Organization, QuickBooksConnection


@admin.register(Organization)
class OrganizationAdmin(ModelAdmin):
    list_display = ["name", "slug", "subscription_tier", "is_active", "created_at"]
    list_filter = ["subscription_tier", "is_active", "created_at"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ["name"]}
    readonly_fields = ["created_at", "modified_at", "api_calls_used"]


@admin.register(QuickBooksConnection)
class QuickBooksConnectionAdmin(ModelAdmin):
    list_display = ["company_name", "organization", "is_active", "last_sync_at", "created_at"]
    list_filter = ["is_active", "created_at", "last_sync_at"]
    search_fields = ["company_name", "company_id", "organization__name"]
    readonly_fields = ["created_at", "modified_at"]
    raw_id_fields = ["organization"]


@admin.register(MCPSession)
class MCPSessionAdmin(ModelAdmin):
    list_display = ["session_id", "organization", "is_active", "connected_at", "requests_count"]
    list_filter = ["is_active", "connected_at"]
    search_fields = ["session_id", "organization__name"]
    readonly_fields = ["created_at", "modified_at", "session_id", "connected_at", "requests_count", "tokens_used"]
    raw_id_fields = ["organization"]


@admin.register(APIKey)
class APIKeyAdmin(ModelAdmin):
    list_display = ["name", "organization", "is_active", "last_used_at", "expires_at"]
    list_filter = ["is_active", "created_at", "expires_at"]
    search_fields = ["name", "organization__name", "key"]
    readonly_fields = ["created_at", "modified_at", "key", "last_used_at"]
    raw_id_fields = ["organization"]