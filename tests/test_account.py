from pathlib import Path

from nf.mutate import extract_token, react_anchor
from nf.parse.account import (
    parse_account,
    parse_reactions,
    parse_reactions_total,
    parse_wallet,
)

FIX = Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIX / name).read_text(encoding="utf-8", errors="replace")


def test_reactions_given_parses_rows_and_total():
    html = fixture("reactions-given-page-1.html")
    res = parse_reactions(html, "https://nullforums.net/account/reactions-given?reaction_id=0&page=1")
    assert res.total == 43
    assert res.pagination.page == 1
    assert res.pagination.perPage == 20
    first = res.items[0]
    assert first.reactByUserId == 100200300
    assert first.reactByUsername == "scrubuser"
    assert first.reactionId == 1
    assert first.targetUrl == "https://nullforums.net/resources/shopgui-1-8-1-21.171/"
    # target text no longer carries unread entities or label artifacts
    assert "MC Plugin" in first.targetTitle
    assert "ShopGUI+" in first.targetTitle
    assert "&amp;" not in first.targetTitle
    # date normalizes +0700 to +07:00
    assert first.reactedAt.endswith("-07:00")


def test_reactions_total_counts_tab():
    assert parse_reactions_total(fixture("reactions-given-page-1.html")) == 43


def test_account_parses_nav_and_wallet_balance():
    html = fixture("dbtech-credits.html")
    acc = parse_account(html)
    assert acc.userId == 100200300
    assert acc.username == "scrubuser"
    assert acc.reactionsGiven is None
    from nf.model import Account
    acc2 = parse_wallet(html, Account())
    assert acc2.creditsBalance == 0


def test_react_anchor_sees_has_reaction_state():
    liked_thread = fixture("thread-89951-liked.html")
    a = react_anchor(liked_thread)
    assert a["url"] == "/posts/126843/react?reaction_id=1"
    assert a["alreadyLiked"] is True
    assert a["reactionId"] == 1


def test_react_anchor_resource_points_to_update():
    html = fixture("resource-4918-liked.html")
    a = react_anchor(html)
    assert a and "/update/12801/react?reaction_id=1" in a["url"]
    assert a["alreadyLiked"] is True


def test_extract_token_finds_xf_token():
    html = fixture("resource-8953.html")
    token = extract_token(html)
    assert "," in token  # 'epoch,hash' shape


def test_guest_session_page_rejects():
    # The abandoned fixture carries a token but no react anchor for guests:
    html = fixture("resource-8953.html")
    assert react_anchor(html) is None
