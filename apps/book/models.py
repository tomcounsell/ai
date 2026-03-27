import markdown as md
from django.db import models
from django.utils import timezone


class Announcement(models.Model):
    """A public announcement about the Blended Workforce book."""

    title: models.CharField = models.CharField(max_length=255)
    body: models.TextField = models.TextField()
    published_at: models.DateTimeField = models.DateTimeField(
        null=True, blank=True, help_text="Leave blank to keep as draft."
    )
    created_at: models.DateTimeField = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-published_at"]

    def __str__(self) -> str:
        return self.title

    @property
    def is_published(self) -> bool:
        return self.published_at is not None and self.published_at <= timezone.now()


class EarlyReader(models.Model):
    """A visitor who signed up as an early reader for the book."""

    ROLE_CHOICES = [
        ("ceo", "CEO"),
        ("founder", "Founder"),
        ("head_of", "Head of Department"),
        ("other", "Other"),
    ]

    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    company = models.CharField(max_length=255, blank=True, default="")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="other")
    research_question = models.TextField(
        blank=True,
        default="",
        help_text="Optional question the reader wants the book to address.",
    )
    is_confirmed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} <{self.email}>"


class Testimonial(models.Model):
    """A testimonial from an early reader or reviewer."""

    quote = models.TextField()
    author_name = models.CharField(max_length=255)
    company = models.CharField(max_length=255, blank=True, default="")
    role = models.CharField(max_length=255, blank=True, default="")
    is_featured = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f'"{self.quote[:50]}..." - {self.author_name}'


class DraftChapter(models.Model):
    """A draft chapter available to early readers."""

    title = models.CharField(max_length=255)
    volume = models.IntegerField(default=1)
    chapter_number = models.IntegerField()
    body_markdown = models.TextField(
        blank=True,
        default="",
        help_text="Chapter content in Markdown format.",
    )
    published_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Leave blank to keep as draft. Set to publish for early readers.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["volume", "chapter_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["volume", "chapter_number"],
                name="unique_volume_chapter",
            )
        ]

    def __str__(self) -> str:
        return f"Vol. {self.volume}, Ch. {self.chapter_number}: {self.title}"

    @property
    def is_published(self) -> bool:
        return self.published_at is not None and self.published_at <= timezone.now()

    @property
    def body_html(self) -> str:
        """Render the markdown body to HTML."""
        if not self.body_markdown:
            return ""
        return md.markdown(
            self.body_markdown,
            extensions=["extra", "codehilite", "toc"],
        )
