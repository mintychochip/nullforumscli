"""Visitor-visible pages -> Account / ReactionsGiven.

Sources, all static HTML:

- The logged-in nav block (``.p-navgroup``) identifies the visitor: a
  ``data-user-id`` on the avatar and the display name in the ``title``
  attribute. Any authenticated page carries it.
- ``/dbtech-credits/`` renders the wallet widget the nav links to
  (``Credits: N`` -> ``/withdraw/``).
- ``/account/reactions-given`` is the only endpoint listing reactions the
  operator handed out; robots.txt disallows ``/account/``, so ``nf likes``
  requires an explicit per-run override (see cli.likes).

Row semantics on the reactions page: the figure avatar is the *content
owner*; the ``.username`` anchor right before "reacted to" is the *reactor*.
"""

from __future__ import annotations

import html as htmllib
import re

from nf.errors import ParseFailure
from nf.model import Account, Pagination, ReactionItem, ReactionsGiven
from nf.parse.page import clean_text, iso_time, raise_if_walled, tree

_ROW = re.compile(r'<li class="block-row block-row--separated">([\s\S]*?)</li>')
_TARGET_HREF = re.compile(r'reacted to [\s\S]{0,250}?<a href="([^"]+)"')
_TARGET_TEXT = re.compile(r'reacted to [\s\S]{0,300}?<a href="[^"]+">([\s\S]*?)</a>')
_REACT_ANCHOR = re.compile(r'<a\s+href="/members/[^"]+"[^>]*class="username[^"]*"[^>]*data-user-id="(\d+)"[\s\S]{0,300}?</a>', re.I)
_DATETIME = re.compile(r'datetime="([^"]+)"')
_REACTION_ID = re.compile(r'data-reaction-id="(\d+)"')
_TAB_TOTAL = re.compile(r'<bdi>All</bdi>\s*\((\d+)\)')
_CREDITS = re.compile(r"Credits:\s*([\d,\.]+)")
_PAGE_LINK = re.compile(r'page=(\d+)')



def parse_account(html: str) -> Account:
    """Visitor identity from any authenticated page's nav block."""
    if not html or not html.strip():
        raise ParseFailure("empty response body", hint="try --refresh")
    raise_if_walled(html)
    t = tree(html)

    account = Account()
    probe = t.css_first(".p-navgroup-link--user")
    if probe is not None:
        avatar = probe.css_first(".avatar") or probe.css_first("span")
        if avatar is not None:
            account.userId = _int((avatar.attributes or {}).get("data-user-id"))
        account.username = clean_text((probe.attributes or {}).get("title"))
        img = probe.css_first("img")
        if img is not None:
            account.avatarUrl = (img.attributes or {}).get("src")
    return account


def parse_wallet(html: str, account: Account) -> Account:
    """Pull ``Credits: N`` from a page nav (or /dbtech-credits/) onto the account.

    The widget anchors to ``/withdraw/``; some themes render an empty twin,
    so the first anchor that actually says ``Credits: N`` wins.
    """
    t = tree(html)
    for node in t.css('a[href*="/withdraw/"]'):
        m = _CREDITS.search(clean_text(node.text()))
        if m:
            account.creditsBalance = _int(m.group(1))
            break
    return account


def parse_reactions_total(html: str) -> int | None:
    """The 'All (N)' tab total: reactions the operator has given in full."""
    t = tree(html)
    for node in t.css("a.tabs-tab"):
        m = re.match(r"^All\s*\((\d+)\)$", clean_text(node.text()))
        if m:
            return int(m.group(1))
    return None


def parse_reactions(html: str, url: str, base_url: str = "https://nullforums.net") -> ReactionsGiven:
    """Parse /account/reactions-given -> ReactionsGiven (one page)."""
    if not html or not html.strip():
        raise ParseFailure("empty response body", hint="try --refresh")
    raise_if_walled(html)

    items: list[ReactionItem] = []
    for row_html in _ROW.findall(html):
        reactor = _REACT_ANCHOR.search(row_html)
        username = clean_text(re.sub(r"<[^>]+>", "", reactor.group(0))) if reactor else None
        target = _TARGET_HREF.search(row_html)
        label = _TARGET_TEXT.search(row_html)
        rid = _REACTION_ID.search(row_html)
        date = _DATETIME.search(row_html)

        target_url = None
        if target:
            href = target.group(1)
            if href.startswith("http"):
                target_url = href
            elif href.startswith("/"):
                target_url = base_url + href
        target_title = None
        if label:
            # Row text mixes "reacted to {@reactor} <a>[label] Title</a>";
            target_title = htmllib.unescape(clean_text(re.sub(r"<[^>]+>", " ", label.group(1))))
        items.append(ReactionItem(
            reactByUsername=username or None,
            reactByUserId=int(reactor.group(1)) if reactor else None,
            targetUrl=target_url,
            targetTitle=target_title,
            reactionId=int(rid.group(1)) if rid else None,
            reactedAt=iso_time(date.group(1)) if date else None,
        ))

    total = parse_reactions_total(html)
    return ReactionsGiven(
        items=items,
        total=total,
        pagination=Pagination(
            page=_current_page(url),
            pages=_page_count(html),
            perPage=len(items) or None,
            total=total,
        ),
    )


def _current_page(url: str) -> int:
    matches = [int(m) for m in re.findall(r"[?&]page=(\d+)", url)]
    return matches[-1] if matches else 1


def _page_count(html: str) -> int:
    # Matches ?page=N, &page=N, &amp;page=N alike; page 1 may not be linked.
    pages = {int(m) for m in re.findall(r"page=(\d+)", html)}
    pages.add(1)
    return max(pages)


def _int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).strip().replace(",", ""))
    except (ValueError, TypeError):
        return None


def parse_level_progress(html: str) -> dict | None:
    """Null Level progress from /pages/nullforums-level-system/ (PII-free)."""
    t = tree(html)
    nums = [clean_text(n.text()) for n in t.css("span#nfLvlPointData")]
    vals = [_int(x) for x in nums]
    if len(vals) < 3:
        return None
    return {"points": vals[0], "threshold": vals[1], "nextLevel": vals[2]}