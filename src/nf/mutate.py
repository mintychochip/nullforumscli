"""The one module that issues non-GET requests: XenForo reactions.

Everything still flows through ``http.Client`` (robots gate, throttle,
ledger); this module only adds site-specific glue: extracting a page's
``_xfToken``, resolving a post/resource target to its react URL, and
calling :meth:`nf.http.Client.post`.

State contract, verified live (2026-09-12):

- POST ``/posts/<id>/react?reaction_id=1`` with ``_xfToken`` form fields
  returns a XenForo JSON envelope on success.
- Reacting while already reacted yields HTTP 403 with JSON
  ``errors: ["You cannot cancel this reaction."]``; the site disables
  reaction removal, so that response means "already liked", not failure.
- Resource reactions hang off the resource's *latest update*:
  ``/resources/<slug>.<id>/update/<updateId>/react?reaction_id=1``; the
  anchor is rendered on the resource page.
- The react anchor carries ``has-reaction`` in its class when this session
  already used that reaction (knowable without a POST).
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from nf.errors import AuthRequired, UsageError
from nf.http import Client

_TOKEN_RE = re.compile(r'name="_xfToken"\s+value="([^"]+)"')
# One react anchor per post action bar / resource update:
#   <a href="/posts/126843/react?reaction_id=1" class="reaction ... has-reaction?" ...>
#   <a href="/resources/sl-LUG.13409/update/29558/react?reaction_id=1" ...>
_FULL_REACT_ANCHOR = re.compile(
    r'<a\s+href="(/(?:posts|resources)/[\w\-.]+(?:/update/\d+)?/react\?reaction_id=\d+)"'
    r'[^>]*class="([^"]*)"')


class AlreadyLiked(UsageError):
    """Target already carries the operator's reaction; site state unchanged."""


def extract_token(html: str) -> str:
    """Pull the per-session CSRF token from any XenForo page."""
    m = _TOKEN_RE.search(html)
    if not m:
        raise AuthRequired(
            "page did not include a CSRF token",
            hint="guest pages cannot react; configure NF_COOKIE with a session")
    return m.group(1)


def react_anchor(html: str) -> dict | None:
    """First react anchor on the page -> {url, alreadyLiked, reactionId}.

    ``has-reaction`` in the anchor class means this session already used
    that reaction (knowable without any request).
    """
    full = _FULL_REACT_ANCHOR.search(html)
    if full is None:
        return None
    path, classes = full.group(1), full.group(2)
    rid_m = re.search(r"reaction_id=(\d+)", path)
    return {
        "url": path,
        "alreadyLiked": "has-reaction" in (classes or ""),
        "reactionId": int(rid_m.group(1)) if rid_m else None,
    }


def do_like(client: Client, base_url: str, target_url: str) -> dict:
    """Like a post or resource: GET target (robots-gated) -> extract token +
    react route -> POST react endpoint.

    Returns ``{"status": "liked", ...}`` or ``{"status": "already", ...}``.
    Already-liked is confirmed against the server (the site disables
    reaction removal, so that state is terminal, not an error).
    """
    page = client.get(target_url)
    html = page.text
    token = extract_token(html)
    anchor = react_anchor(html)
    if anchor is None:
        raise UsageError(
            "no reaction anchor on the target page",
            hint="post or resource URL expected, e.g. nf like /threads/89951/")
    if anchor["alreadyLiked"]:
        return {"status": "already", "reactionId": anchor["reactionId"],
                "target": target_url}
    url = anchor["url"] if anchor["url"].startswith("http") else base_url + anchor["url"]
    result = client.post(url, {"_xfToken": token, "_xfWithData": "1",
                               "_xfResponseType": "json"})
    errors = result.get("errors") if isinstance(result, dict) else None
    for err in (errors or []):
        text = err if isinstance(err, str) else str(err)
        if "cancel this reaction" in text.lower():
            return {"status": "already", "reactionId": anchor["reactionId"],
                    "target": target_url}
        raise UsageError(f"react failed: {text}")
    if not isinstance(result, dict) or result.get("status") == "error":
        raise UsageError(f"react failed: {result.get('errors') if isinstance(result, dict) else result}")
    return {"status": "liked", "target": target_url, "server": result}
