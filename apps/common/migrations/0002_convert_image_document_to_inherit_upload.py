"""
Convert Image and Document from ForeignKey composition to multi-table inheritance.

Image and Document previously had a ForeignKey to Upload. This migration converts
them to inherit from Upload using Django's multi-table inheritance pattern.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("common", "0001_initial"),
    ]

    operations = [
        # ── Image: FK → inheritance ──────────────────────────────────
        # 1. Remove BlogPost.featured_image FK that references Image
        migrations.RemoveField(
            model_name="blogpost",
            name="featured_image",
        ),
        # 2. Drop the old Image table (no data to preserve)
        migrations.DeleteModel(
            name="Image",
        ),
        # 3. Recreate Image as a child of Upload (multi-table inheritance)
        migrations.CreateModel(
            name="Image",
            fields=[
                (
                    "upload_ptr",
                    models.OneToOneField(
                        auto_created=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        parent_link=True,
                        primary_key=True,
                        serialize=False,
                        to="common.upload",
                    ),
                ),
                (
                    "thumbnail_url",
                    models.URLField(blank=True, default="", null=True),
                ),
            ],
            bases=("common.upload",),
        ),
        # 4. Re-add BlogPost.featured_image FK pointing to the new Image
        migrations.AddField(
            model_name="blogpost",
            name="featured_image",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="featured_blog_posts",
                to="common.image",
            ),
        ),
        # ── Document: FK → inheritance ───────────────────────────────
        # 1. Remove PDF first (it depends on Document)
        migrations.DeleteModel(
            name="PDF",
        ),
        # 2. Drop the old Document table
        migrations.DeleteModel(
            name="Document",
        ),
        # 3. Recreate Document as a child of Upload
        migrations.CreateModel(
            name="Document",
            fields=[
                (
                    "upload_ptr",
                    models.OneToOneField(
                        auto_created=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        parent_link=True,
                        primary_key=True,
                        serialize=False,
                        to="common.upload",
                    ),
                ),
            ],
            bases=("common.upload",),
        ),
        # 4. Recreate PDF as a child of Document
        migrations.CreateModel(
            name="PDF",
            fields=[
                (
                    "document_ptr",
                    models.OneToOneField(
                        auto_created=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        parent_link=True,
                        primary_key=True,
                        serialize=False,
                        to="common.document",
                    ),
                ),
            ],
            bases=("common.document",),
        ),
    ]
