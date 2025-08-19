from typing import Any, Dict

from django.core.validators import validate_slug
from django.db import models
from django.db.models.signals import pre_save
from django.dispatch import receiver
from django.utils.text import slugify


class Permalinkable(models.Model):
    """
    A behavior mixin that adds permalink/slug functionality to a model.

    This mixin provides a slug field for models that need SEO-friendly URLs
    or human-readable identifiers. It includes automatic slug generation from
    a designated source field when the object is saved.

    Attributes:
        slug (SlugField): A unique, URL-friendly string derived from the model's content.
            This field is used in URLs and as a human-readable identifier. If not explicitly
            set, it will be automatically generated from the 'slug_source' attribute
            of the model during save.

    Methods:
        get_url_kwargs(**kwargs): Builds URL keyword arguments for use in URL patterns.
            Useful for generating URLs in templates and views.

    Example:
        ```python
        class Article(Permalinkable, models.Model):
            title = models.CharField(max_length=200)
            content = models.TextField()

            @property
            def slug_source(self):
                return self.title

            # The slug will automatically be generated from the title
        ```

    Note:
        This model is abstract and should be used as a mixin in other models.
        For automatic slug generation, the model should define a 'slug_source' property
        or attribute that returns the string to be slugified.
    """

    slug = models.SlugField(
        null=True,
        blank=True,
        validators=[validate_slug],
        unique=True,
        help_text="URL-friendly version of the name. Auto-generated if blank.",
    )

    class Meta:
        abstract = True

    def get_url_kwargs(self, **kwargs) -> dict[str, Any]:
        """
        Get URL keyword arguments for use in URL patterns.

        This method combines any provided keyword arguments with those defined
        in the model's url_kwargs attribute (if it exists).

        Args:
            **kwargs: Additional keyword arguments to include

        Returns:
            Dict[str, Any]: Combined keyword arguments for URL generation
        """
        kwargs.update(getattr(self, "url_kwargs", {}))
        return kwargs

    # @models.permalink  # Deprecated in Django 2.0+
    # def get_absolute_url(self):
    #     url_kwargs = self.get_url_kwargs(slug=self.slug)
    #     return (self.url_name, (), url_kwargs)


@receiver(pre_save)
def pre_save_slug(sender, instance, *args, **kwargs):
    """
    Signal handler that automatically generates a slug before saving.

    If the model inherits from Permalinkable and has a 'slug_source' attribute
    but no slug, this handler will generate a slug from the source.

    Args:
        sender: The model class
        instance: The model instance being saved
        *args, **kwargs: Additional arguments
    """
    if hasattr(sender, "mro") and Permalinkable in sender.mro():
        if not instance.slug and hasattr(instance, "slug_source"):
            instance.slug = slugify(instance.slug_source)
