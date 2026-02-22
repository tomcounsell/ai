from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from apps.podcast.models import (
    Episode,
    EpisodeArtifact,
    EpisodeWorkflow,
    Podcast,
    PodcastConfig,
)


class PodcastConfigInline(TabularInline):
    model = PodcastConfig
    fields = [
        "depth_level",
        "sponsor_break",
        "companion_access",
        "opening_script",
        "closing_script",
    ]
    extra = 0
    max_num = 1
    can_delete = False


class EpisodeInline(TabularInline):
    model = Episode
    fields = [
        "episode_number",
        "title",
        "slug",
        "topic_series",
        "status",
        "published_at",
        "audio_url",
    ]
    extra = 0
    ordering = ["episode_number"]


class EpisodeArtifactInline(TabularInline):
    model = EpisodeArtifact
    fields = ["title", "content", "metadata"]
    extra = 0
    ordering = ["title"]


@admin.register(Podcast)
class PodcastAdmin(ModelAdmin):
    list_display = [
        "title",
        "slug",
        "language",
        "is_public",
        "published_at",
        "created_at",
        "owner",
        "spotify_url",
        "apple_podcasts_url",
    ]
    list_filter = ["is_public", "published_at", "language", "owner"]
    search_fields = ["title", "description"]
    prepopulated_fields = {"slug": ("title",)}
    raw_id_fields = ["owner"]
    ordering = ["title"]
    inlines = [PodcastConfigInline, EpisodeInline]


@admin.register(Episode)
class EpisodeAdmin(ModelAdmin):
    list_display = [
        "episode_number",
        "title",
        "podcast",
        "topic_series",
        "status",
        "published_at",
        "is_explicit",
    ]
    list_filter = ["podcast", "topic_series", "status", "is_explicit", "published_at"]
    search_fields = ["title", "description"]
    raw_id_fields = ["podcast"]
    ordering = ["-episode_number"]
    prepopulated_fields = {"slug": ("title",)}
    inlines = [EpisodeArtifactInline]


@admin.register(EpisodeArtifact)
class EpisodeArtifactAdmin(ModelAdmin):
    list_display = ["title", "episode", "created_at"]
    list_filter = ["episode__podcast"]
    search_fields = ["title", "content"]
    raw_id_fields = ["episode"]
    ordering = ["title"]


@admin.register(EpisodeWorkflow)
class EpisodeWorkflowAdmin(ModelAdmin):
    list_display = ["episode", "current_step", "status", "blocked_on", "created_at"]
    list_filter = ["status", "current_step"]
    search_fields = ["episode__title", "current_step", "blocked_on"]
    raw_id_fields = ["episode"]
    ordering = ["-created_at"]
