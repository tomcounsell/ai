from django.views.generic import TemplateView


class ComponentExamplesView(TemplateView):
    """
    Design system reference page.

    This view renders the complete design system documentation including typography,
    colors, spacing, and all UI components with brand-compliant examples.
    """

    template_name = "examples.html"
