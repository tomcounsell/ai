from django import template

register = template.Library()


@register.simple_tag
def component_css_dependencies():
    """Placeholder for component CSS dependencies. Not used in this project."""
    return ""
