from django.contrib import admin

from apps.book.models import Announcement


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ("title", "published_at", "created_at")
    list_filter = ("published_at",)
    search_fields = ("title", "body")
