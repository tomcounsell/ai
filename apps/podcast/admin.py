from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from apps.podcast.models import Episode, Podcast


class EpisodeInline(TabularInline):
    model = Episode
    fields = ["episode_number", "title", "slug", "published_at", "audio_url"]
    extra = 0
    ordering = ["episode_number"]


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
        "published_at",
        "is_explicit",
    ]
    list_filter = ["podcast", "is_explicit", "published_at"]
    search_fields = ["title", "description"]
    raw_id_fields = ["podcast"]
    ordering = ["-episode_number"]
    prepopulated_fields = {"slug": ("title",)}
