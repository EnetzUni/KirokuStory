"""[[Name]] links in chronicles, pages and scenes, and [[Name|other words]]: those words, linked to
the page called Name («la niña» to «Shayne (nómada)»).

- A page the reader can see: a link to it (with a hover preview).
- A player character of the campaign: a link to their sheet.
- Masters only: an NPC of the campaign (a template, such as one of the bestiary): a link to its sheet.
- No such page: a dotted link; masters can click it to create the page.
- A page hidden from the reader: plain text, so its existence does not leak.
"""
import re
from urllib.parse import urlencode

from django.urls import reverse
from django.utils.html import escape, format_html
from django.utils.safestring import SafeString

from .models import Character, Page
from .permissions import npc_templates_for

# [[Name]] or [[Name|the words shown]]
LINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")


def shown(match):
    """The words a link shows: its own, or the name it links to."""
    return match.group(2) or match.group(1)


def plain(text):
    """The text without the brackets: what the links show."""
    return LINK.sub(shown, text or "")


def first_sentence(text):
    first_line = plain(text).strip().split("\n")[0]
    match = re.match(r"^(.*?[.!?])(\s|$)", first_line)
    return match.group(1) if match else first_line


def targets(text):
    """The names the links of a text point to, in lower case."""
    return {match.group(1).strip().lower() for match in LINK.finditer(text or "")}


def mentions(text, title):
    return title.strip().lower() in targets(text)


class WikiResolver:
    """Resolves [[Name]] for one reader in one campaign. Built once per request."""

    def __init__(self, campaign, user, is_master):
        self.campaign = campaign
        self.is_master = is_master
        self.pages = {p.title.lower(): p for p in Page.objects.filter(campaign=campaign).only("id", "title", "slug", "revealed", "campaign_id")}
        self.characters = {
            c.name.lower(): c for c in Character.objects.filter(campaigns=campaign, is_npc=False).only("id", "name")
        }
        # NPC sheets are the master's: players never get these links.
        self.npcs = {c.name.lower(): c for c in npc_templates_for(campaign).only("id", "name")} if is_master else {}

    def _link(self, name, label=None):
        key = name.strip().lower()
        label = label or name
        page = self.pages.get(key)
        if page is not None:
            if not (self.is_master or page.revealed):
                return escape(label)
            url = reverse("world_page", args=[self.campaign.pk, page.slug])
            preview = reverse("world_preview", args=[self.campaign.pk, page.slug])
            if not page.revealed:
                return format_html('<a class="wikilink" href="{}" data-preview="{}" data-hidden="1">{}</a>', url, preview, label)
            return format_html('<a class="wikilink" href="{}" data-preview="{}">{}</a>', url, preview, label)
        character = self.characters.get(key)
        if character is not None:
            url = reverse("sheet", args=[self.campaign.pk, character.pk])
            return format_html('<a class="wikilink" href="{}">{}</a>', url, label)
        npc = self.npcs.get(key)
        if npc is not None:
            url = reverse("sheet", args=[self.campaign.pk, npc.pk])
            return format_html('<a class="wikilink" href="{}" title="{}">{}</a>', url, "Ficha de PNJ: solo la ves tú", label)
        if self.is_master:
            url = reverse("world_new", args=[self.campaign.pk]) + "?" + urlencode({"titulo": name.strip()})
            return format_html('<a class="wikilink wikilink-missing" href="{}" title="{}">{}</a>',
                               url, "Esta página no existe todavía: pulsa para crearla", label)
        return escape(label)

    def inline(self, text):
        """Text with its links, as safe HTML: everything else is escaped.
        Safe strings stay safe when they are added together."""
        out, last = SafeString(), 0
        for match in LINK.finditer(text or ""):
            out += escape(text[last:match.start()])
            out += self._link(match.group(1), match.group(2))
            last = match.end()
        return out + escape((text or "")[last:])

    def paragraphs(self, text):
        """Paragraphs separated by a blank line, as a list of safe HTML strings."""
        result = []
        for part in re.split(r"\n\s*\n", text or ""):
            lines = [self.inline(line) for line in part.strip().split("\n")] if part.strip() else []
            if lines:
                html = lines[0]
                for line in lines[1:]:
                    html = format_html("{}<br>{}", html, line)
                result.append(html)
        return result

    def render(self, text, css_class=""):
        out = SafeString()
        for paragraph in self.paragraphs(text):
            out += format_html('<p class="{}">{}</p>', css_class, paragraph) if css_class else format_html("<p>{}</p>", paragraph)
        return out
