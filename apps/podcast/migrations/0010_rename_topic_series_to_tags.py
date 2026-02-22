from django.db import migrations, models


def copy_topic_series_to_tags(apps, schema_editor):
    """Copy topic_series values into the new tags field for all existing episodes."""
    Episode = apps.get_model("podcast", "Episode")
    for episode in Episode.objects.exclude(topic_series=""):
        episode.tags = episode.topic_series
        episode.save(update_fields=["tags"])


def copy_tags_to_topic_series(apps, schema_editor):
    """Reverse: copy tags back to topic_series."""
    Episode = apps.get_model("podcast", "Episode")
    for episode in Episode.objects.exclude(tags=""):
        episode.topic_series = episode.tags
        episode.save(update_fields=["topic_series"])


class Migration(migrations.Migration):

    dependencies = [
        ("podcast", "0009_add_topic_series_to_episode"),
    ]

    operations = [
        # Step 1: Add the new tags field
        migrations.AddField(
            model_name="episode",
            name="tags",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Comma-separated tags for categorizing episodes",
            ),
        ),
        # Step 2: Copy topic_series values into tags
        migrations.RunPython(
            copy_topic_series_to_tags,
            reverse_code=copy_tags_to_topic_series,
        ),
        # Step 3: Remove the old topic_series field
        migrations.RemoveField(
            model_name="episode",
            name="topic_series",
        ),
        # Step 4: Update ordering to use descending episode_number
        migrations.AlterModelOptions(
            name="episode",
            options={"ordering": ["-episode_number"]},
        ),
    ]
