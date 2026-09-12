"""Selectors and helpers shared by the page parsers.

Theme note: the site runs a bespoke ``kz-*`` theme over XenForo 2. Selectors
here use XenForo's own hooks (``.p-title-value``, ``time.u-dt``,
``article.message[data-content]``, ``.structItem-cell--main``) and never
theme-specific classes, so a theme restyle does not break parsing.
"""

from __future__ import annotations

import re

from selectolax.parser import HTMLParser

from nf.errors import AuthRequired, ParseFailure
from nf.model import Pagination

# Elements that may carry an attachment or download reference. Removed from
# any HTML we emit -- the parser must never surface attachment URLs.
_ATTACHMENT_SELECTORS = ("a", "img", "source", "video", "audio", "object", "embed")

_BLOCK_MARKERS = ("just a moment", "attention required", "challenge-platform",
                  "enable javascript and cookies to continue")

_AUTH_MARKERS = ("you must log in", "log in or register", "you do not have permission",
                 "insufficient privileges", "you must be a registered member")


def tree(html: str) -> HTMLParser:
    return HTMLParser(html)


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def iso_time(dt: str | None) -> str | None:
    if not dt:
        return None
    dt = dt.strip()
    if not dt:
        return None
    if dt.endswith("Z"):
        return dt[:-1] + "+00:00"
    return re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", dt)


def title_of(t: HTMLParser, remove: tuple[str, ...] = ()) -> str:
    node = t.css_first(".p-title-value") or t.css_first("h1")
    if node is None:
        title = t.css_first("title")
        return clean_text(title.text() if title else "")
    for selector in remove:
        for child in node.css(selector):
            child.decompose()
    return clean_text(node.text())


def breadcrumbs(t: HTMLParser) -> list[str]:
    """First breadcrumb block only; the theme renders it twice."""
    block = t.css_first(".p-breadcrumbs")
    if block is None:
        return []
    out = []
    for item in block.css("li a"):
        text = clean_text(item.text())
        if text and (not out or out[-1] != text):
            out.append(text)
    return out


def node_of(t: HTMLParser) -> str | None:
    crumbs = breadcrumbs(t)
    for candidate in reversed(crumbs):
        if candidate.lower() != "home":
            return candidate
    return None


def pagination(t: HTMLParser) -> Pagination:
    nav = t.css_first(".pageNav")
    if nav is None:
        return Pagination()
    page = 1
    pages = 1
    current = nav.css_first(".pageNav-page--current a")
    if current is not None:
        text = clean_text(current.text())
        if text.isdigit():
            page = int(text)
    for link in nav.css(".pageNav-page a"):
        text = clean_text(link.text())
        if text.isdigit():
            pages = max(pages, int(text))
    return Pagination(page=page, pages=pages)


def require(condition, selector: str, hint: str | None = None) -> None:
    if not condition:
        raise ParseFailure(
            f"expected element {selector!r} was not found",
            hint=hint or "the page shape may have changed; try nf raw <url>",
        )


def _body_inner(t: HTMLParser) -> str:
    html = (t.body.html if t.body else None) or ""
    if html.startswith("<body>") and html.endswith("</body>"):
        return html[len("<body>"):-len("</body>")]
    return html


def scrub_attachments(fragment: str) -> str:
    """Strip anything that could name an attachment or download.

    Enforces the spec's hard boundary mechanically: no attachment URL can
    reach a model, so the tool cannot be turned into a downloader.
    """
    if not fragment or not fragment.strip():
        return ""
    parser = HTMLParser(fragment)
    for node in parser.css("script, style"):
        node.decompose()
    for selector in _ATTACHMENT_SELECTORS:
        for node in parser.css(selector):
            attrs = node.attributes or {}
            haystack = " ".join(str(v) for v in attrs.values())
            if "/attachments/" in haystack or "download" in (attrs.get("class") or ""):
                node.decompose()
    for node in parser.css("div, span, li, dl"):
        classes = (node.attributes or {}).get("class") or ""
        if "attachment" in classes or "downloadButton" in classes:
            node.decompose()
    return _body_inner(parser)


def extract_body(node) -> tuple[str, str]:
    """Return (safeHtml, text) for a XenForo body node, with attachments/embedded scripts removed."""
    if node is None:
        return "", ""
    raw = node.html or ""
    if not raw.strip():
        return "", ""
    safe_html = scrub_attachments(raw)
    if not safe_html:
        return "", ""
    safe_tree = HTMLParser(safe_html)
    text = clean_text(safe_tree.body.text() if safe_tree.body else "")
    return safe_html, text


def detect_block(html: str) -> bool:
    head = html[:4000].lower()
    return any(marker in head for marker in _BLOCK_MARKERS)


def detect_auth_wall(html: str) -> bool:
    """A real login wall, not the JS-disabled notices every page carries.

    The theme puts ``.blockMessage`` on every page (a JavaScript warning, a
    browser warning, the share bar). Detection therefore requires an *error*
    block whose text is about logging in or permissions.
    """
    parser = HTMLParser(html)
    for node in parser.css(".blockMessage"):
        classes = (node.attributes or {}).get("class") or ""
        if "error" not in classes:
            continue
        text = clean_text(node.text()).lower()
        if any(marker in text for marker in _AUTH_MARKERS):
            return True
    if parser.css_first('form[action="/login/login"]') is not None:
        return True
    return False


def raise_if_walled(html: str) -> None:
    if detect_auth_wall(html):
        raise AuthRequired(
            "the site returned a login wall for this URL",
            hint="supply a valid session cookie via NF_COOKIE or the config file",
        )
