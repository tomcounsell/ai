from django import forms

from apps.podcast.models import Episode


class EpisodeForm(forms.ModelForm):
    """Form for creating a new episode with title, description, and tags."""

    class Meta:
        model = Episode
        fields = ["title", "description", "tags"]
        widgets = {
            "title": forms.TextInput(
                attrs={
                    "class": "input-brand w-full",
                    "placeholder": "e.g. The Future of AI Alignment",
                }
            ),
            "description": forms.Textarea(
                attrs={
                    "class": "input-brand w-full",
                    "rows": 6,
                    "placeholder": "Describe what this episode should cover. This becomes the research prompt for the AI pipeline.",
                }
            ),
            "tags": forms.TextInput(
                attrs={
                    "class": "input-brand w-full",
                    "placeholder": "e.g. AI, productivity, deep-dive",
                }
            ),
        }
        labels = {
            "title": "Episode Title",
            "description": "Description / Topic",
            "tags": "Tags (comma-separated, optional)",
        }
        help_texts = {
            "description": "This becomes the research prompt for the AI pipeline. Be specific about what you want the episode to cover.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make title and description required
        self.fields["title"].required = True
        self.fields["description"].required = True
        self.fields["tags"].required = False
