"""Admin configuration for AI app models."""

from django.contrib import admin
from unfold.admin import ModelAdmin

from apps.ai.models import ChatFeedback, ChatMessage, ChatSession


@admin.register(ChatSession)
class ChatSessionAdmin(ModelAdmin):
    """Admin for ChatSession model."""

    list_display = [
        "id",
        "title",
        "user",
        "is_active",
        "message_count",
        "created_at",
        "modified_at",
    ]
    list_filter = ["is_active", "created_at", "modified_at"]
    search_fields = ["id", "title", "user__username", "user__email"]
    readonly_fields = ["id", "created_at", "modified_at"]
    date_hierarchy = "created_at"

    fieldsets = (
        (None, {"fields": ("id", "user", "title", "is_active")}),
        ("Metadata", {"fields": ("metadata",), "classes": ("collapse",)}),
        (
            "Timestamps",
            {
                "fields": ("created_at", "modified_at"),
            },
        ),
    )

    @admin.display(description="Messages")
    def message_count(self, obj):
        """Display the number of messages in the session."""
        return obj.message_count


@admin.register(ChatMessage)
class ChatMessageAdmin(ModelAdmin):
    """Admin for ChatMessage model."""

    list_display = [
        "id",
        "session",
        "role",
        "truncated_content",
        "is_processed",
        "created_at",
    ]
    list_filter = ["role", "is_processed", "created_at"]
    search_fields = ["id", "content", "session__id", "session__title"]
    readonly_fields = ["id", "created_at", "modified_at"]
    date_hierarchy = "created_at"
    raw_id_fields = ["session"]

    fieldsets = (
        (None, {"fields": ("id", "session", "role", "content", "is_processed")}),
        ("Metadata", {"fields": ("metadata",), "classes": ("collapse",)}),
        (
            "Timestamps",
            {
                "fields": ("created_at", "modified_at"),
            },
        ),
    )

    @admin.display(description="Content")
    def truncated_content(self, obj):
        """Display truncated content."""
        max_length = 100
        if len(obj.content) > max_length:
            return f"{obj.content[:max_length]}..."
        return obj.content


@admin.register(ChatFeedback)
class ChatFeedbackAdmin(ModelAdmin):
    """Admin for ChatFeedback model."""

    list_display = ["id", "message", "user", "rating", "is_helpful", "created_at"]
    list_filter = ["rating", "is_helpful", "created_at"]
    search_fields = ["id", "comment", "user__username", "message__id"]
    readonly_fields = ["id", "created_at", "modified_at"]
    date_hierarchy = "created_at"
    raw_id_fields = ["message", "user"]

    fieldsets = (
        (None, {"fields": ("id", "message", "user")}),
        ("Feedback", {"fields": ("rating", "is_helpful", "comment")}),
        (
            "Timestamps",
            {
                "fields": ("created_at", "modified_at"),
            },
        ),
    )
