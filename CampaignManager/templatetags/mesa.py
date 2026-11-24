"""Template helpers for «Kiroku Story»."""
import hashlib
import os

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

from .. import wiki as wiki_module

register = template.Library()


@register.simple_tag
def asset(path):
    """URL of our own CSS or JS with the file's modification time («mesa.js?v=…»),
    so browsers load it again when it changes instead of keeping an old copy."""
    url = static(path)
    found = finders.find(path)
    return f"{url}?v={int(os.path.getmtime(found))}" if found else url

# Avatar colors when there is no portrait (contrast >= 4.5:1 with white).
AVATAR_COLORS = ["#5B3E96", "#8C3B1E", "#1F4E89", "#1D6158", "#7A4F00", "#6B2142", "#4B5D3A", "#4A4640"]


@register.simple_tag(takes_context=True)
def wiki(context, text, css_class=""):
    """Render text with [[links]] for the current reader."""
    resolver = context.get("wiki")
    if resolver is None:
        return wiki_module.plain(text)
    return resolver.render(text, css_class)


@register.simple_tag(takes_context=True)
def wiki_inline(context, text):
    resolver = context.get("wiki")
    return resolver.inline(text) if resolver else wiki_module.plain(text)


@register.filter
def plain(text):
    return wiki_module.plain(text)


@register.filter
def signed(value):
    """+2, −1 (with a real minus sign)."""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return value
    return f"+{value}" if value >= 0 else f"−{abs(value)}"


@register.simple_tag
def pct(part, whole):
    try:
        part, whole = float(part or 0), float(whole or 0)
    except (TypeError, ValueError):
        return 0
    return 0 if whole <= 0 else max(0, min(100, round(100 * part / whole)))


@register.filter
def initial(name):
    return (name or "?").strip()[:1].upper()


@register.filter
def avatar_color(obj):
    key = f"{type(obj).__name__}:{getattr(obj, 'pk', obj)}"
    return AVATAR_COLORS[int(hashlib.md5(key.encode(), usedforsecurity=False).hexdigest(), 16) % len(AVATAR_COLORS)]


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def times(n):
    try:
        return range(int(n))
    except (TypeError, ValueError):
        return range(0)


@register.filter
def first_paragraph(text):
    """Up to the first blank line."""
    import re
    parts = [p for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    return parts[0] if parts else ""


@register.filter
def field_named(form, name):
    """`form[name]`, or None if the form has no such field (the editor hides some for NPCs)."""
    return form[name] if name in form.fields else None
