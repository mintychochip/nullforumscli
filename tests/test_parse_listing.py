from pathlib import Path

import pytest

from nf.parse.listing import parse_listing

FIX = Path(__file__).parent / "fixtures"
URL = "https://nullforums.net/resources/categories/minecraft-plugins.38/"


@pytest.fixture(scope="module")
def listing():
    html = (FIX / "category-38.html").read_text(encoding="utf-8", errors="replace")
    return parse_listing(html, URL)


def test_id_and_title(listing):
    assert listing.id == 38
    assert listing.title == "Minecraft Plugins"


def test_items_are_extracted(listing):
    assert len(listing.items) == 30


def test_item_fields(listing):
    item = next(i for i in listing.items if i.id == 867)
    assert item.title.startswith("X PRISON")
    assert (item.author.username or "")
    assert item.url.startswith("https://nullforums.net/resources/")


def test_label_links_are_not_treated_as_titles(listing):
    assert all(not i.title.startswith("MC Plugin") for i in listing.items)


def test_pagination(listing):
    assert listing.pagination.pages == 46


def test_version_is_optional_but_parsed_when_present(listing):
    with_version = [i for i in listing.items if i.version]
    assert with_version
    assert any(i.version == "2026.3.8.4" for i in listing.items)
