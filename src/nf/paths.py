"""URL classification, shared by the HTTP layer and the search index.

Two different questions get asked of a URL: ``classify_request`` answers
"what kind of request is this?" for the usage ledger; ``parse_doc_url``
answers "what document is this?" for the sitemap index. Both live here
because both are pure URL string work and duplicating it would drift.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

_DOC_PATH = re.compile(r"^/(threads|resources|tags|forums)/(.+)\.(\d+)/?$")


@dataclass(frozen=True)
class DocRef:
    type: str
    id: int
    slug: str


def _path_of(url: str) -> str:
    if "://" in url:
        return urlsplit(url).path or "/"
    return urlsplit(url).path or url


def classify_request(path: str) -> str:
    p = _path_of(path)
    if p.startswith("/threads/"):
        return "thread"
    if p.startswith("/resources/categories/"):
        return "category"
    if p.startswith("/resources/"):
        return "resource"
    if p.startswith("/account/") or p.startswith("/posts/") or p.startswith("/dbtech-credits/"):
        return "account-data"
    if p in ("/sitemap.xml",) or re.match(r"^/sitemap-\d+\.xml$", p):
        return "index"
    if p == "/robots.txt":
        return "robots"
    return "other"


def parse_doc_url(url: str) -> DocRef | None:
    p = _path_of(url)
    m = _DOC_PATH.match(p)
    if m:
        kind, slug, ident = m.groups()
        return DocRef(type=kind[:-1] if kind.endswith("s") else kind,
                      id=int(ident), slug=slug)
    m = re.match(r"^/tags/([^/]+)/?$", p)
    if m:
        return DocRef(type="tag", id=0, slug=m.group(1))
    return None


def slug_to_title(slug: str) -> str:
    """Approximate a title from a slug. Slugs are lossy; callers must mark it."""
    words = [w for w in slug.split("-") if w]
    return " ".join(w[:1].upper() + w[1:] for w in words)
