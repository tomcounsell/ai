from django.db import models
from settings import AUTH_USER_MODEL


class Authorable(models.Model):
    author = models.ForeignKey(AUTH_USER_MODEL, related_name="%(class)ss", on_delete=models.CASCADE)
    author_anonymous = models.BooleanField(default=False)
    authored_at = models.DateTimeField(null=True, blank=True)


    @property
    def author_display_name(self):
        if self.author_anonymous:
            return "Anonymous"
        else:
            return self.author.full_name


    class Meta:
        abstract = True
