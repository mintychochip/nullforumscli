"""Thread page -> Thread."""

from __future__ import annotations

import re

from nf.errors import ParseFailure
from nf.model import Author, Post, Thread
from nf.parse.page import (clean_text, extract_body, iso_time, node_of,
                           pagination, require, title_of, tree)

_ID_IN_URL = re.compile(r"\.(\d+)(?:/|$)")
_POST_CONTENT = re.compile(r"post-(\d+)")


def _id_from_url(url: str) -> int | None:
    m = _ID_IN_URL.search(url)
    return int(m.group(1)) if m else None


def _author_of(article, base_url: str) -> Author:
    username = article.attributes.get("data-author")
    user_id = None
    url = None
    link = article.css_first(".message-name a, .message-userDetails a.username")
    if link is not None:
        username = username or clean_text(link.text())
        raw_id = (link.attributes or {}).get("data-user-id")
        if raw_id and str(raw_id).isdigit():
            user_id = int(raw_id)
        href = (link.attributes or {}).get("href")
        if href:
            url = href if href.startswith("http") else base_url + href
    return Author(username=clean_text(username) or None, userId=user_id, url=url)


def parse_posts(t, url: str, base_url: str) -> list[Post]:
    posts: list[Post] = []
    for index, article in enumerate(t.css("article.message"), start=1):
        attrs = article.attributes or {}
        raw_id = attrs.get("data-content") or ""
        m = _POST_CONTENT.match(raw_id)
        post_id = int(m.group(1)) if m else None
        when = article.css_first("time.u-dt")
        body = article.css_first(".message-body .bbWrapper") or article.css_first(".bbWrapper")
        permalink = article.css_first('.message-attribution-opposite a[href*="/post-"]')
        href = (permalink.attributes or {}).get("href") if permalink is not None else None
        if href:
            post_url = href if href.startswith("http") else base_url + href
        else:
            post_url = f"{url}#post-{post_id}" if post_id else None
        body_html, body_text = extract_body(body)
        posts.append(Post(
            id=post_id,
            index=index,
            url=post_url,
            author=_author_of(article, base_url),
            postedAt=iso_time((when.attributes or {}).get("datetime") if when else None),
            bodyText=body_text,
            bodyHtml=body_html,
        ))
    return posts


def parse_thread(html: str, url: str, base_url: str = "https://nullforums.net") -> Thread:
    if not html or not html.strip():
        raise ParseFailure("empty response body", hint="try --refresh")
    t = tree(html)
    posts = parse_posts(t, url, base_url)
    require(posts, "article.message", hint="no posts found; a login wall or a changed theme")

    node = node_of(t)
    page = pagination(t)

    first = t.css_first("time.u-dt")
    return Thread(
        id=_id_from_url(url),
        url=url,
        title=title_of(t),
        node=node,
        author=posts[0].author,
        createdAt=posts[0].postedAt,
        updatedAt=iso_time((first.attributes or {}).get("datetime")) if first else None,
        tags=[clean_text(a.text()) for a in t.css(".tagList a")],
        posts=posts,
        pagination=page,
    )
