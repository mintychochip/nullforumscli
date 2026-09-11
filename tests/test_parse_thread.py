from pathlib import Path

import pytest

from nf.parse.thread import parse_thread

FIX = Path(__file__).parent / "fixtures"
URL = "https://nullforums.net/threads/trending-and-latest-posts-api.89951/"


@pytest.fixture(scope="module")
def thread():
    html = (FIX / "thread-89951.html").read_text(encoding="utf-8", errors="replace")
    return parse_thread(html, URL)


def test_id_comes_from_the_url(thread):
    assert thread.id == 89951


def test_title_and_node(thread):
    assert thread.title == "Trending and latest posts API"
    assert thread.node == "Xenforo RSS"


def test_post_is_extracted(thread):
    assert len(thread.posts) == 1
    post = thread.posts[0]
    assert post.id == 126843
    assert post.index == 1
    assert post.author.username == "stromb0li"
    assert post.postedAt == "2024-10-28T10:52:04-04:00"


def test_post_body_text_is_present(thread):
    body = thread.posts[0].bodyText
    assert "trending posts" in body
    assert body == body.strip()


def test_thread_author_is_the_first_post_author(thread):
    assert thread.author.username == "stromb0li"


def test_no_attachment_reference_survives_serialization(thread):
    from nf.model import to_dict
    import json
    blob = json.dumps(to_dict(thread))
    assert "/attachments/" not in blob
    assert "download" not in blob.lower()
