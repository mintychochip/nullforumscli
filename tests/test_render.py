import json

from nf.model import (Author, Pagination, Post, Thread, envelope, SCHEMA_VERSION)
from nf.render import render


def sample_thread():
    return Thread(
        id=89951,
        url="https://nullforums.net/threads/trending-and-latest-posts-api.89951/",
        title="Trending and latest posts API",
        node="Xenforo RSS",
        author=Author(username="stromb0li", userId=None,
                      url="https://nullforums.net/members/stromb0li.1/"),
        createdAt="2024-10-28T10:52:04-04:00",
        updatedAt=None,
        tags=["xenforo"],
        posts=[Post(id=126843, index=1, url="https://x/#post-126843",
                    author=Author(username="stromb0li", userId=None, url=None),
                    postedAt="2024-10-28T10:52:04-04:00",
                    bodyText="Is there a way to get the list of trending posts?",
                    bodyHtml="<p>Is there a way to get the list of trending posts?</p>")],
        pagination=Pagination(page=1, pages=1, perPage=20, total=1),
    )


def test_envelope_carries_schema_version():
    assert envelope({"a": 1}) == {"schemaVersion": SCHEMA_VERSION, "a": 1}


def test_json_render_is_parseable_and_versioned():
    out = render(sample_thread(), "json", "thread")
    data = json.loads(out)
    assert data["schemaVersion"] == 1
    assert data["id"] == 89951
    assert data["posts"][0]["id"] == 126843


def test_markdown_render_has_title_and_body():
    out = render(sample_thread(), "md", "thread")
    assert "# Trending and latest posts API" in out
    assert "stromb0li" in out
    assert "Is there a way to get the list of trending posts?" in out


def test_text_render_is_plain():
    out = render(sample_thread(), "text", "thread")
    assert "#" not in out
    assert "Trending and latest posts API" in out


def test_unknown_format_raises_usage_error():
    import pytest
    from nf.errors import UsageError
    with pytest.raises(UsageError):
        render(sample_thread(), "yaml", "thread")
