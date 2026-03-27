from django.contrib import admin

from apps.book.models import Announcement, DraftChapter, EarlyReader, Testimonial


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ("title", "published_at", "created_at")
    list_filter = ("published_at",)
    search_fields = ("title", "body")


@admin.register(EarlyReader)
class EarlyReaderAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "company", "role", "is_confirmed", "created_at")
    list_filter = ("role", "is_confirmed", "created_at")
    search_fields = ("name", "email", "company")
    readonly_fields = ("created_at",)


@admin.register(Testimonial)
class TestimonialAdmin(admin.ModelAdmin):
    list_display = ("author_name", "company", "is_featured", "created_at")
    list_filter = ("is_featured", "created_at")
    search_fields = ("quote", "author_name", "company")


@admin.register(DraftChapter)
class DraftChapterAdmin(admin.ModelAdmin):
    list_display = ("title", "volume", "chapter_number", "published_at", "created_at")
    list_filter = ("volume", "published_at")
    search_fields = ("title", "body_markdown")
    readonly_fields = ("created_at",)
