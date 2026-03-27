from django import forms

from apps.book.models import EarlyReader


class EarlyReaderSignupForm(forms.ModelForm):
    """Form for early reader signup on the book landing page."""

    class Meta:
        model = EarlyReader
        fields = ["name", "email", "company", "role", "research_question"]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "placeholder": "Your full name",
                    "class": "w-full px-4 py-3 rounded border text-sm",
                }
            ),
            "email": forms.EmailInput(
                attrs={
                    "placeholder": "you@company.com",
                    "class": "w-full px-4 py-3 rounded border text-sm",
                }
            ),
            "company": forms.TextInput(
                attrs={
                    "placeholder": "Company name (optional)",
                    "class": "w-full px-4 py-3 rounded border text-sm",
                }
            ),
            "role": forms.Select(
                attrs={
                    "class": "w-full px-4 py-3 rounded border text-sm",
                }
            ),
            "research_question": forms.Textarea(
                attrs={
                    "placeholder": "What question do you most want the book to address?",
                    "class": "w-full px-4 py-3 rounded border text-sm",
                    "rows": 3,
                }
            ),
        }
