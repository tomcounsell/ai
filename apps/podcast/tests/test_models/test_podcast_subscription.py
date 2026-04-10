import pytest

from apps.common.models import Subscription
from apps.common.tests.factories import PodcastSubscriptionFactory
from apps.podcast.models import Podcast, PodcastSubscription


@pytest.mark.django_db
class TestPodcastSubscriptionCreate:
    def test_create_podcast_subscription(self):
        ps = PodcastSubscriptionFactory.create()
        assert ps.pk is not None
        assert ps.subscriber_email
        assert ps.subscriber_name == "Test Subscriber"
        assert ps.status == PodcastSubscription.Status.ACTIVE
        assert ps.cadence == PodcastSubscription.Cadence.WEEKLY
        assert ps.length_minutes == 15
        assert ps.topic_focus == "AI and technology"
        assert ps.next_drop_at is None
        assert ps.do_not_email is False

    def test_str_representation(self):
        ps = PodcastSubscriptionFactory.create(
            subscriber_email="alice@example.com",
        )
        result = str(ps)
        assert "alice@example.com" in result
        assert str(ps.podcast) in result


@pytest.mark.django_db
class TestPodcastSubscriptionStatusTransition:
    def test_status_transition_active_to_churned(self):
        ps = PodcastSubscriptionFactory.create()
        assert ps.status == PodcastSubscription.Status.ACTIVE

        ps.status = PodcastSubscription.Status.CHURNED
        ps.save()

        ps.refresh_from_db()
        assert ps.status == PodcastSubscription.Status.CHURNED
        assert ps.status == "churned"


@pytest.mark.django_db
class TestPodcastSubscriptionCadence:
    def test_default_cadence_is_weekly(self):
        ps = PodcastSubscriptionFactory.create()
        assert ps.cadence == PodcastSubscription.Cadence.WEEKLY
        assert ps.cadence == "weekly"

    def test_set_cadence_to_biweekly(self):
        ps = PodcastSubscriptionFactory.create()
        ps.cadence = PodcastSubscription.Cadence.BIWEEKLY
        ps.save()

        ps.refresh_from_db()
        assert ps.cadence == PodcastSubscription.Cadence.BIWEEKLY
        assert ps.cadence == "biweekly"


@pytest.mark.django_db
class TestPodcastSubscriptionRelationships:
    def test_subscription_link(self):
        ps = PodcastSubscriptionFactory.create()
        assert isinstance(ps.subscription, Subscription)

    def test_podcast_link(self):
        ps = PodcastSubscriptionFactory.create()
        assert isinstance(ps.podcast, Podcast)

    def test_reverse_relation_from_subscription(self):
        ps = PodcastSubscriptionFactory.create()
        assert ps.subscription.podcast_subscription == ps

    def test_reverse_relation_from_podcast(self):
        ps = PodcastSubscriptionFactory.create()
        assert ps.podcast.podcast_subscription == ps


@pytest.mark.django_db
class TestPodcastSubscriptionEdgeCases:
    def test_blank_topic_focus_is_valid(self):
        ps = PodcastSubscriptionFactory.create(topic_focus="")
        ps.refresh_from_db()
        assert ps.topic_focus == ""

    def test_next_drop_at_accepts_none(self):
        ps = PodcastSubscriptionFactory.create(next_drop_at=None)
        assert ps.next_drop_at is None
