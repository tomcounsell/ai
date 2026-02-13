from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from apps.podcast.models import Episode, EpisodeArtifact, Podcast


class EpisodeInline(TabularInline):
    model = Episode
    fields = ["episode_number", "title", "slug", "status", "published_at", "audio_url"]
    extra = 0
    ordering = ["episode_number"]


class EpisodeArtifactInline(TabularInline):
    model = EpisodeArtifact
    fields = ["title", "content", "metadata"]
    extra = 0
    ordering = ["title"]


@admin.register(Podcast)
class PodcastAdmin(ModelAdmin):
    list_display = ["title", "slug", "language", "is_public", "created_at"]
    list_filter = ["is_public", "language"]
    search_fields = ["title", "description"]
    prepopulated_fields = {"slug": ("title",)}
    ordering = ["title"]
    inlines = [EpisodeInline]


@admin.register(Episode)
class EpisodeAdmin(ModelAdmin):
    list_display = [
        "episode_number",
        "title",
        "podcast",
        "status",
        "published_at",
        "is_explicit",
    ]
    list_filter = ["podcast", "status", "is_explicit", "published_at"]
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
