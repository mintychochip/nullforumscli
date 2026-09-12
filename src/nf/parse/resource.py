"""Resource (XFRM) page -> Resource."""

from __future__ import annotations

import re

from nf.errors import ParseFailure
from nf.model import Author, CategoryRef, Resource
from nf.parse.page import (
    breadcrumbs,
    clean_text,
    extract_body,
    iso_time,
    is_safe_url,
    require,
    raise_if_walled,
    title_of,
    tree,
)

_ID_IN_URL = re.compile(r"\.(\d+)(?:/|$)")

# The resource title element also contains a prefix label, the version, and
# the tagline. Decompose those before reading the title text.
_TITLE_NOISE = (".label", ".label-append", ".u-muted", ".structItem-resourceTagLine")


def parse_resource(html: str, url: str, base_url: str = "https://nullforums.net") -> Resource:
    """Parse a resource page."""
    if not html or not html.strip():
        raise ParseFailure("empty response body", hint="try --refresh")
    raise_if_walled(html)
    t = tree(html)

    title_node = t.css_first(".p-title-value")
    require(title_node is not None, ".p-title-value")
    version_node = title_node.css_first(".u-muted span") or title_node.css_first(".u-muted")
    version = clean_text(version_node.text()) if version_node is not None else None

    title = title_of(t, remove=_TITLE_NOISE)
    require(bool(title), ".p-title-value (title text)")

    author = Author()
    desc = t.css_first(".p-description")
    if desc is not None:
        link = desc.css_first("a.username")
        if link is not None:
            attrs = link.attributes or {}
            raw_id = str(attrs.get("data-user-id", ""))
            href = attrs.get("href") or ""
            author = Author(
                username=clean_text(link.text()) or None,
                userId=int(raw_id) if raw_id.isdigit() else None,
                url=is_safe_url(href, base_url),
            )

    category = None
    crumbs = [c for c in breadcrumbs(t) if c.lower() != "home"]
    if crumbs:
        category = CategoryRef(id=None, title=crumbs[-1], url=None)

    body = t.css_first(".resourceBody") or t.css_first(".js-resourceBody")
    body_html = ""
    body_text = ""
    if body is not None:
        content = body.css_first(".bbWrapper") or body
        body_html, body_text = extract_body(content)

    times = [iso_time((n.attributes or {}).get("datetime")) for n in t.css("time.u-dt")]
    times = [x for x in times if x]
    created = times[0] if times else None
    updated = times[-1] if times else None

    tagline_node = t.css_first(".structItem-resourceTagLine")

    thread = None
    for link in t.css('a[href*="/threads/"]'):
        href = (link.attributes or {}).get("href") or ""
        safe = is_safe_url(href, base_url)
        if safe:
            m = re.search(r"\.(\d+)", href)
            if m:
                thread = {"id": int(m.group(1)), "url": safe}
                break

    id_match = _ID_IN_URL.search(url)
    return Resource(
        id=int(id_match.group(1)) if id_match else None,
        url=url,
        title=title,
        author=author,
        version=version,
        tagLine=clean_text(tagline_node.text()) if tagline_node is not None else None,
        description={"html": body_html, "text": body_text},
        createdAt=created,
        lastUpdated=updated,
        category=category,
        discussionThread=thread,
        tags=[clean_text(a.text()) for a in t.css(".tagList a")],
    )
