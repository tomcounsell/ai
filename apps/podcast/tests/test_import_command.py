from io import StringIO
from unittest import mock

from django.test import TestCase

from apps.podcast.management.commands.import_podcast_feed import parse_duration
from apps.podcast.models import Episode, Podcast

SAMPLE_FEED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Test Podcast</title>
    <link>https://example.com</link>
    <description>A test podcast</description>
    <language>en</language>
    <itunes:author>Test Author</itunes:author>
    <itunes:image href="https://example.com/cover.jpg"/>
    <itunes:owner>
      <itunes:name>Test Author</itunes:name>
      <itunes:email>test@example.com</itunes:email>
    </itunes:owner>
    <itunes:category text="Technology"/>
    <item>
      <title>Episode One</title>
      <description>First episode</description>
      <enclosure url="https://example.com/ep1.mp3" length="1000000" type="audio/mpeg"/>
      <pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>
      <itunes:duration>10:30</itunes:duration>
      <itunes:explicit>false</itunes:explicit>
    </item>
    <item>
      <title>Episode Two</title>
      <description>Second episode</description>
      <enclosure url="https://example.com/ep2.mp3" length="2000000" type="audio/mpeg"/>
      <pubDate>Tue, 02 Jan 2024 00:00:00 +0000</pubDate>
      <itunes:duration>1:05:00</itunes:duration>
      <itunes:explicit>true</itunes:explicit>
    </item>
  </channel>
</rss>"""


def _mock_urlopen(url):
    """Return a context manager that yields a bytes response for the sample feed XML."""
    response = mock.MagicMock()
    response.read.return_value = SAMPLE_FEED_XML.encode("utf-8")
    response.__enter__ = mock.MagicMock(return_value=response)
    response.__exit__ = mock.MagicMock(return_value=False)
    return response


class ImportPodcastFeedTestCase(TestCase):
    """Tests for the import_podcast_feed management command."""

    @mock.patch("urllib.request.urlopen", side_effect=_mock_urlopen)
    def test_import_creates_podcast(self, mock_url):
        """Verify podcast record created with correct fields."""
        from django.core.management import call_command

        out = StringIO()
        call_command("import_podcast_feed", stdout=out)

        self.assertEqual(Podcast.objects.count(), 1)
        podcast = Podcast.objects.first()
        self.assertEqual(podcast.title, "Test Podcast")
        self.assertEqual(podcast.slug, "test-podcast")
        self.assertEqual(podcast.description, "A test podcast")
        self.assertEqual(podcast.author_name, "Test Author")
        self.assertEqual(podcast.author_email, "test@example.com")
        self.assertEqual(podcast.cover_image_url, "https://example.com/cover.jpg")
        self.assertEqual(podcast.language, "en")
        self.assertEqual(podcast.categories, ["Technology"])
        self.assertEqual(podcast.website_url, "https://example.com")
        self.assertTrue(podcast.is_public)

    @mock.patch("urllib.request.urlopen", side_effect=_mock_urlopen)
    def test_import_creates_episodes(self, mock_url):
        """Verify episodes created with correct fields."""
        from django.core.management import call_command

        out = StringIO()
        call_command("import_podcast_feed", stdout=out)

        podcast = Podcast.objects.first()
        episodes = Episode.objects.filter(podcast=podcast).order_by("episode_number")
        self.assertEqual(episodes.count(), 2)

        ep1 = episodes[0]
        self.assertEqual(ep1.title, "Episode One")
        self.assertEqual(ep1.description, "First episode")
        self.assertEqual(ep1.audio_url, "https://example.com/ep1.mp3")
        self.assertEqual(ep1.audio_file_size_bytes, 1000000)
        self.assertEqual(ep1.audio_duration_seconds, 630)  # 10:30
        self.assertFalse(ep1.is_explicit)
        self.assertIsNotNone(ep1.published_at)

        ep2 = episodes[1]
        self.assertEqual(ep2.title, "Episode Two")
        self.assertEqual(ep2.description, "Second episode")
        self.assertEqual(ep2.audio_url, "https://example.com/ep2.mp3")
        self.assertEqual(ep2.audio_file_size_bytes, 2000000)
        self.assertEqual(ep2.audio_duration_seconds, 3900)  # 1:05:00
        self.assertTrue(ep2.is_explicit)

    @mock.patch("urllib.request.urlopen", side_effect=_mock_urlopen)
    def test_import_episode_ordering(self, mock_url):
        """Episodes numbered 1, 2 by chronological pubDate."""
        from django.core.management import call_command

        out = StringIO()
        call_command("import_podcast_feed", stdout=out)

        episodes = Episode.objects.order_by("episode_number")
        self.assertEqual(episodes[0].episode_number, 1)
        self.assertEqual(episodes[0].title, "Episode One")
        self.assertEqual(episodes[1].episode_number, 2)
        self.assertEqual(episodes[1].title, "Episode Two")

    @mock.patch("urllib.request.urlopen", side_effect=_mock_urlopen)
    def test_import_idempotent(self, mock_url):
        """Run twice, second run skips existing episodes."""
        from django.core.management import call_command

        out = StringIO()
        call_command("import_podcast_feed", stdout=out)
        self.assertEqual(Episode.objects.count(), 2)

        out2 = StringIO()
        call_command("import_podcast_feed", stdout=out2)
        self.assertEqual(Episode.objects.count(), 2)

    @mock.patch("urllib.request.urlopen", side_effect=_mock_urlopen)
    def test_import_dry_run(self, mock_url):
        """With --dry-run, no records created."""
        from django.core.management import call_command

        out = StringIO()
        call_command("import_podcast_feed", "--dry-run", stdout=out)

        self.assertEqual(Podcast.objects.count(), 0)
        self.assertEqual(Episode.objects.count(), 0)

    def test_import_duration_parsing(self):
        """Test the parse_duration helper with HH:MM:SS, MM:SS, and seconds."""
        # HH:MM:SS
        self.assertEqual(parse_duration("1:05:00"), 3900)
        self.assertEqual(parse_duration("0:10:30"), 630)
        self.assertEqual(parse_duration("2:00:00"), 7200)

        # MM:SS
        self.assertEqual(parse_duration("10:30"), 630)
        self.assertEqual(parse_duration("5:00"), 300)
        self.assertEqual(parse_duration("0:45"), 45)

        # Raw seconds
        self.assertEqual(parse_duration("630"), 630)
        self.assertEqual(parse_duration("3600"), 3600)

        # None / empty
        self.assertIsNone(parse_duration(None))
        self.assertIsNone(parse_duration(""))

        # Invalid
        self.assertIsNone(parse_duration("abc"))
