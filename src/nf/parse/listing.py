"""Resource category page -> ResourceList."""

from __future__ import annotations

import re

from nf.errors import ParseFailure
from nf.model import Author, ResourceItem, ResourceList
from nf.parse.page import (
    clean_text,
    iso_time,
    pagination as read_pagination,
    require,
    tree,
)

_ID_IN_URL = re.compile(r"\.(\d+)(?:/|$)")


def _item_of(cell, base_url: str) -> ResourceItem | None:
    title_link = None
    for link in cell.css(".structItem-title a"):
        classes = (link.attributes or {}).get("class") or ""
        if "labelLink" in classes:
            continue
        if (link.attributes or {}).get("href"):
            title_link = link
            break
    if title_link is None:
        return None

    href = title_link.attributes.get("href") or ""
    url = href if href.startswith("http") else base_url + href
    id_match = _ID_IN_URL.search(href)

    version_node = cell.css_first(".structItem-title .u-muted")
    author = Author()
    link = cell.css_first(".structItem-minor .username") or cell.css_first(".username")
    if link is not None:
        raw_id = (link.attributes or {}).get("data-user-id")
        author_href = (link.attributes or {}).get("href")
        author = Author(
            username=clean_text(link.text()) or None,
            userId=int(raw_id) if str(raw_id or "").isdigit() else None,
            url=(author_href if author_href and author_href.startswith("http")
                 else (base_url + author_href) if author_href else None),
        )

    when = cell.css_first("time.u-dt")
    return ResourceItem(
        id=int(id_match.group(1)) if id_match else None,
        url=url,
        title=clean_text(title_link.text()),
        author=author,
        version=clean_text(version_node.text()) if version_node is not None else None,
        lastUpdated=iso_time((when.attributes or {}).get("datetime")) if when else None,
    )


def parse_listing(html: str, url: str, base_url: str = "https://nullforums.net") -> ResourceList:
    if not html or not html.strip():
        raise ParseFailure("empty response body", hint="try --refresh")
    t = tree(html)
    cells = t.css(".structItem-cell--main")
    require(cells, ".structItem-cell--main", hint="no resource items found on this page")

    items = [item for item in (_item_of(c, base_url) for c in cells) if item is not None]
    require(items, ".structItem-title a", hint="items present but no titles parsed")

    heading = t.css_first(".p-title-value") or t.css_first("h1")
    id_match = _ID_IN_URL.search(url)
    page = read_pagination(t)
    return ResourceList(
        id=int(id_match.group(1)) if id_match else None,
        url=url,
        title=clean_text(heading.text()) if heading is not None else "",
        items=items,
        pagination=page,
    )
