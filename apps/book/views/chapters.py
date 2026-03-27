from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils import timezone
from django.views.generic import DetailView, ListView

from apps.book.models import DraftChapter


class ChapterListView(LoginRequiredMixin, ListView):
    """List published draft chapters for early readers (login required)."""

    template_name = "book/chapters.html"
    context_object_name = "chapters"
    login_url = "/admin/login/"

    def get_queryset(self):
        return DraftChapter.objects.filter(
            published_at__isnull=False,
            published_at__lte=timezone.now(),
        )


class ChapterDetailView(LoginRequiredMixin, DetailView):
    """Render a single draft chapter with Markdown-to-HTML conversion."""

    template_name = "book/chapter_detail.html"
    context_object_name = "chapter"
    login_url = "/admin/login/"

    def get_queryset(self):
        return DraftChapter.objects.filter(
            published_at__isnull=False,
            published_at__lte=timezone.now(),
        )
