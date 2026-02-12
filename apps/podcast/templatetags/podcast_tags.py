from django import template

register = template.Library()


@register.filter
def duration_hhmmss(seconds: int | None) -> str:
    """Convert seconds to HH:MM:SS format for iTunes duration tag."""
    if seconds is None:
        return "00:00:00"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


@register.filter
def default_zero(value: int | None) -> int:
    """Return 0 if the value is None, otherwise return the value."""
    if value is None:
        return 0
    return value
