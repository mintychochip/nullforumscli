import pytest

from nf.paths import DocRef, classify_request, parse_doc_url, slug_to_title


@pytest.mark.parametrize("path,expected", [
    ("/threads/trending-and-latest-posts-api.89951/", "thread"),
    ("/threads/foo.1/page-2", "thread"),
    ("/resources/advancedkits.8953/", "resource"),
    ("/resources/categories/minecraft-plugins.38/", "category"),
    ("/sitemap.xml", "index"),
    ("/sitemap-3.xml", "index"),
    ("/robots.txt", "robots"),
    ("/", "other"),
    ("/members/foo.123/", "other"),
])


def test_classify_request(path, expected):
    assert classify_request(path) == expected


@pytest.mark.parametrize("url,expected", [
    ("https://nullforums.net/threads/trending-and-latest-posts-api.89951/",
     DocRef("thread", 89951, "trending-and-latest-posts-api")),
    ("/threads/foo.1/", DocRef("thread", 1, "foo")),
    ("https://nullforums.net/resources/advancedkits.8953/",
     DocRef("resource", 8953, "advancedkits")),
    ("/tags/advanced/", DocRef("tag", 0, "advanced")),
    ("/forums/support-bugs.3/", DocRef("forum", 3, "support-bugs")),
    ("/members/foo.123/", None),
    ("/resources/categories/minecraft-plugins.38/",
     DocRef("resource", 38, "categories/minecraft-plugins")),
])


def test_parse_doc_url(url, expected):
    assert parse_doc_url(url) == expected


def test_parse_doc_url_ignores_query_and_fragment():
    ref = parse_doc_url("https://nullforums.net/threads/foo.7/?page=2#post-9")
    assert ref == DocRef("thread", 7, "foo")


@pytest.mark.parametrize("slug,expected", [
    ("advancedkits", "Advancedkits"),
    ("x-prison-packet-prison-core-1-13-26-2", "X Prison Packet Prison Core 1 13 26 2"),
    ("trending-and-latest-posts-api", "Trending And Latest Posts Api"),
])
def test_slug_to_title(slug, expected):
    assert slug_to_title(slug) == expected
