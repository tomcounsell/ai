from django.db import models

from apps.common.behaviors import Timestampable


class PodcastConfig(Timestampable):
    """
    Production workflow configuration for a Podcast.

    Separated from Podcast (which owns published feed metadata) because:
    - Different audiences manage each (producer vs. publisher)
    - Config fields will grow independently (depth levels, scripts, gating)
    - Keeps the Podcast model focused on RSS feed requirements
    """

    class DepthLevel(models.TextChoices):
        ACCESSIBLE = "accessible", "Accessible"
        INTERMEDIATE = "intermediate", "Intermediate"
        ADVANCED = "advanced", "Advanced"

    class CompanionAccess(models.TextChoices):
        PUBLIC = "public", "Public"
        GATED = "gated", "Gated"

    podcast = models.OneToOneField(
        "podcast.Podcast",
        on_delete=models.CASCADE,
        related_name="config",
    )

    # NotebookLM script customization
    opening_script = models.TextField(
        blank=True,
        help_text="Custom opening script for NotebookLM audio generation",
    )
    closing_script = models.TextField(
        blank=True,
        help_text="Custom closing script for NotebookLM audio generation",
    )

    # Content depth/pacing
    depth_level = models.CharField(
        max_length=20,
        choices=DepthLevel.choices,
        default=DepthLevel.ACCESSIBLE,
        help_text="How much baseline knowledge to assume",
    )

    # Sponsor and monetization
    sponsor_break = models.BooleanField(
        default=True,
        help_text="Include sponsor splice point in episodes",
    )

    # Companion resource access
    companion_access = models.CharField(
        max_length=20,
        choices=CompanionAccess.choices,
        default=CompanionAccess.PUBLIC,
        help_text="Access level for companion resources",
    )

    class Meta:
        verbose_name = "Podcast Config"
        verbose_name_plural = "Podcast Configs"

    def __str__(self):
        return f"Config for {self.podcast.title}"

    def to_dict(self) -> dict:
        """Export config as dict for episode_config.json snapshot."""
        return {
            "podcast_slug": self.podcast.slug,
            "podcast_title": self.podcast.title,
            "privacy": self.podcast.privacy,
            "uses_private_bucket": self.podcast.uses_private_bucket,
            "website_url": self.podcast.website_url,
            "opening_script": self.opening_script,
            "closing_script": self.closing_script,
            "depth_level": self.depth_level,
            "sponsor_break": self.sponsor_break,
            "companion_access": self.companion_access,
        }
