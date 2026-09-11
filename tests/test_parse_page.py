from pathlib import Path

import pytest

from nf.errors import AuthRequired, ParseFailure
from nf.parse.page import (breadcrumbs, clean_text, detect_auth_wall, detect_block,
                           iso_time, node_of, pagination, require, scrub_attachments,
                           title_of, tree)

FIX = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8", errors="replace")


@pytest.mark.parametrize("raw,expected", [
    ("2024-10-28T10:52:04-0400", "2024-10-28T10:52:04-04:00"),
    ("2026-09-11T08:09:24+0000", "2026-09-11T08:09:24+00:00"),
    ("2024-10-28T10:52:04Z", "2024-10-28T10:52:04+00:00"),
    (None, None),
    ("", None),
])
def test_iso_time(raw, expected):
    assert iso_time(raw) == expected


def test_clean_text_collapses_whitespace():
    assert clean_text("  a\n\n  b\tc  ") == "a b c"


def test_title_from_thread_fixture():
    t = tree(load("thread-89951.html"))
    assert title_of(t) == "Trending and latest posts API"


def test_breadcrumbs_and_node_are_deduplicated():
    t = tree(load("thread-89951.html"))
    crumbs = breadcrumbs(t)
    assert crumbs[0] == "Home"
    assert node_of(t) == "Xenforo RSS"


def test_pagination_defaults_to_single_page_when_absent():
    t = tree(load("thread-89951.html"))
    p = pagination(t)
    assert p.page == 1 and p.pages == 1


def test_pagination_reads_category_page():
    t = tree(load("category-38.html"))
    p = pagination(t)
    assert p.pages == 46


def test_scrub_attachments_removes_links_and_images():
    html = ('<p>keep me</p><a href="/attachments/x.zip.1/">x.zip</a>'
            '<img src="/attachments/y.png.2/">')
    out = scrub_attachments(html)
    assert "keep me" in out
    assert "/attachments/" not in out
    assert "x.zip" not in out


def test_scrub_attachments_removes_attachment_blocks():
    html = ('<p>keep</p><div class="attachment"><span>file.jar</span>'
            '<a href="/anything">dl</a></div>')
    out = scrub_attachments(html)
    assert "keep" in out
    assert "file.jar" not in out


def test_scrub_attachments_keeps_ordinary_links():
    out = scrub_attachments('<p>see <a href="/threads/other.2/">other</a></p>')
    assert '/threads/other.2/' in out


def test_scrub_attachments_handles_empty_input():
    assert scrub_attachments("") == ""


def test_detect_block_recognizes_challenge_page():
    assert detect_block(load("SYNTHETIC-edge-block.html")) is True


def test_detect_block_is_false_for_normal_pages():
    assert detect_block(load("thread-89951.html")) is False


def test_detect_auth_wall_recognizes_login_prompt():
    assert detect_auth_wall(load("SYNTHETIC-login-wall.html")) is True


def test_detect_auth_wall_is_false_for_normal_pages():
    """Every normal page carries .blockMessage notices, so detection must be specific."""
    assert detect_auth_wall(load("thread-89951.html")) is False
    assert detect_auth_wall(load("category-38.html")) is False


def test_require_raises_parse_failure_naming_the_selector():
    with pytest.raises(ParseFailure) as ei:
        require(None, ".message-body")
    assert ".message-body" in ei.value.message
    assert ei.value.exit_code == 7
