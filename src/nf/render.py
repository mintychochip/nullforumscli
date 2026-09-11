"""Model -> output string. Pure; changing a format cannot change the data."""

from __future__ import annotations

import json

from nf.errors import UsageError
from nf.model import envelope, to_dict

FORMATS = ("json", "md", "text")


def _json(payload: dict) -> str:
    return json.dumps(envelope(payload), indent=2, ensure_ascii=False)


def _thread_md(d: dict) -> str:
    lines = [f"# {d['title']}", ""]
    meta = [f"**URL:** {d['url']}"]
    if d.get("node"):
        meta.append(f"**Node:** {d['node']}")
    if (d.get("author") or {}).get("username"):
        meta.append(f"**Author:** {d['author']['username']}")
    if d.get("createdAt"):
        meta.append(f"**Created:** {d['createdAt']}")
    if d.get("tags"):
        meta.append(f"**Tags:** {', '.join(d['tags'])}")
    lines += ["  \n".join(meta), ""]
    for post in d.get("posts", []):
        who = (post.get("author") or {}).get("username") or "unknown"
        lines += [f"## Post {post['index']} - {who} - {post.get('postedAt') or ''}", "",
                  post.get("bodyText", ""), ""]
    return "\n".join(lines).rstrip() + "\n"


def _thread_text(d: dict) -> str:
    lines = [d["title"], "=" * len(d["title"]), ""]
    for post in d.get("posts", []):
        who = (post.get("author") or {}).get("username") or "unknown"
        lines += [f"[{post['index']}] {who} {post.get('postedAt') or ''}".rstrip(),
                  post.get("bodyText", ""), ""]
    return "\n".join(lines).rstrip() + "\n"


def _resource_md(d: dict) -> str:
    lines = [f"# {d['title']}", ""]
    meta = []
    if (d.get("author") or {}).get("username"):
        meta.append(f"**Author:** {d['author']['username']}")
    if d.get("version"):
        meta.append(f"**Version:** {d['version']}")
    if (d.get("category") or {}).get("title"):
        meta.append(f"**Category:** {d['category']['title']}")
    if d.get("lastUpdated"):
        meta.append(f"**Last updated:** {d['lastUpdated']}")
    if d.get("tags"):
        meta.append(f"**Tags:** {', '.join(d['tags'])}")
    lines += ["  \n".join(meta), "", d.get("description", {}).get("text", "")]
    return "\n".join(lines).rstrip() + "\n"


def _category_md(d: dict) -> str:
    lines = [f"# {d['title']}", ""]
    for item in d.get("items", []):
        who = (item.get("author") or {}).get("username") or ""
        ver = f" - {item['version']}" if item.get("version") else ""
        lines.append(f"- [{item['title']}]({item['url']}){ver}{' - ' + who if who else ''}")
    return "\n".join(lines).rstrip() + "\n"


def _search_md(d: dict) -> str:
    lines = [f"# Search: {d.get('query', '')}", ""]
    for hit in d.get("hits", []):
        lines.append(f"- `{hit['type']}` [{hit['title']}]({hit['url']}) "
                     f"({hit.get('lastmod') or 'no date'})")
    return "\n".join(lines).rstrip() + "\n"


def _usage_md(d: dict) -> str:
    lines = [f"# Usage ({d['window']})", "",
             f"- Requests: {d['requests']['total']}",
             f"- By class: {d['requests']['byClass']}",
             f"- Cache: {d['cache']}",
             f"- Bytes: {d['bytes']}",
             f"- Rate: {d['rate']}",
             f"- Refusals: {d['refusals']}",
             f"- Last request: {d['lastRequestAt']}"]
    return "\n".join(lines) + "\n"


def _flat_text(d: dict) -> str:
    return json.dumps(d, indent=2, ensure_ascii=False) + "\n"


_MD = {"thread": _thread_md, "resource": _resource_md, "category": _category_md,
       "search": _search_md, "usage": _usage_md}
_TEXT = {"thread": _thread_text}


def render(payload, fmt: str, kind: str) -> str:
    if fmt not in FORMATS:
        raise UsageError(f"unknown format {fmt!r}",
                         hint=f"choose one of {', '.join(FORMATS)}")
    data = payload if isinstance(payload, dict) else to_dict(payload)
    if fmt == "json":
        return _json(data)
    if fmt == "md":
        fn = _MD.get(kind)
        return fn(data) if fn else _flat_text(data)
    fn = _TEXT.get(kind)
    return fn(data) if fn else _flat_text(data)
