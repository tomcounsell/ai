from apps.common.models import BlogPost
from apps.common.utilities.forms import BaseModelForm


class BlogPostForm(BaseModelForm):
    """Form for creating and editing blog posts with enhanced validation."""

    # Define required fields for automatic validation
    required_fields = ["title", "content"]

    class Meta:
        model = BlogPost
        fields = [
            "title",
            "subtitle",
            "content",
            "featured_image",
            "reading_time_minutes",
            "tags",
            "address",
            "is_author_anonymous",
        ]

    def validate_form(self, cleaned_data):
        """Perform additional complex validations beyond simple required fields."""
        title = cleaned_data.get("title", "")
        subtitle = cleaned_data.get("subtitle", "")
        content = cleaned_data.get("content", "")

        # Validate title length
        if len(title) < 5:
            self.add_error("title", "Title must be at least 5 characters long")

        # Validate content length
        if len(content) < 100:
            self.add_error("content", "Content must be at least 100 characters long")

        # Validate relationships between fields
        if subtitle and len(subtitle) > len(title):
            self.add_error("subtitle", "Subtitle should be shorter than the title")

        # Validate reading time based on content length
        if cleaned_data.get("reading_time_minutes") is not None:
            content_length = len(content)
            estimated_time = max(
                1, content_length // 1000
            )  # Rough estimate: 1000 chars ≈ 1 minute
            user_time = cleaned_data.get("reading_time_minutes")

            if user_time < estimated_time / 2 or user_time > estimated_time * 2:
                self.add_error(
                    "reading_time_minutes",
                    f"Reading time seems off. Based on content length, "
                    f"we estimate around {estimated_time} minutes.",
                )

    def pre_save(self, instance, is_create):
        """Perform operations before saving the instance."""
        # Automatically generate reading time if not provided
        if not instance.reading_time_minutes and instance.content:
            # Rough estimate: 1000 characters ≈ 1 minute reading time
            instance.reading_time_minutes = max(1, len(instance.content) // 1000)

        return instance

    def post_save(self, instance, is_create):
        """Perform operations after saving the instance."""
        # Example: Log the creation/update action
        if is_create:
            # You could trigger notifications, index for search, etc.
            pass

        return instance
