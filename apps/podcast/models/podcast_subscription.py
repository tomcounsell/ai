from django.db import models

from apps.common.behaviors import Timestampable


class PodcastSubscription(Timestampable):
    """
    Links a paying subscriber (via common.Subscription) to a specific Podcast.

    Stores podcast-specific subscription preferences that don't belong on the
    generic billing Subscription model. Subscriber email and name are
    denormalized here to avoid requiring a Django User account — MVP subscribers
    are email-only.

    OneToOne on both subscription and podcast is a deliberate MVP constraint.
    One billing record serves one podcast; one podcast has one active
    subscription in MVP scope. See the parent PRD for the multi-subscriber
    roadmap.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CHURNED = "churned", "Churned"

    class Cadence(models.TextChoices):
        WEEKLY = "weekly", "Weekly"
        BIWEEKLY = "biweekly", "Biweekly"

    subscription = models.OneToOneField(
        "common.Subscription",
        on_delete=models.CASCADE,
        related_name="podcast_subscription",
        help_text="Billing subscription record from Stripe",
    )

    podcast = models.OneToOneField(
        "podcast.Podcast",
        on_delete=models.CASCADE,
        related_name="podcast_subscription",
        help_text="The podcast this subscriber receives",
    )

    # Denormalized from Stripe — avoids requiring a User account for MVP
    subscriber_email = models.EmailField(
        db_index=True,
        help_text="Subscriber's email address (denormalized from Stripe)",
    )
    subscriber_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Subscriber's full name (denormalized from Stripe)",
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        help_text="Subscription lifecycle status",
    )

    cadence = models.CharField(
        max_length=20,
        choices=Cadence.choices,
        default=Cadence.WEEKLY,
        help_text="How often the subscriber receives new episodes",
    )

    length_minutes = models.PositiveIntegerField(
        default=15,
        help_text="Target episode length in minutes",
    )

    topic_focus = models.TextField(
        blank=True,
        help_text="Subscriber-specified topic focus for episode curation",
    )

    next_drop_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Scheduled datetime for the next episode delivery",
    )

    do_not_email = models.BooleanField(
        default=False,
        help_text="Suppress all outbound emails for this subscriber",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Podcast Subscription"
        verbose_name_plural = "Podcast Subscriptions"

    def __str__(self) -> str:
        return f"{self.subscriber_email} — {self.podcast}"
