from django.views.generic import TemplateView


class ComponentExamplesView(TemplateView):
    """
    View to render the component examples page.

    This view demonstrates all available UI components with examples and usage notes.
    It serves as both documentation and a visual reference for developers.
    """

    template_name = "examples.html"
