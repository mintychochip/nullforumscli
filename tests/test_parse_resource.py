import json
from pathlib import Path

import pytest

from nf.model import to_dict
from nf.parse.resource import parse_resource

FIX = Path(__file__).parent / "fixtures"
URL = "https://nullforums.net/resources/advancedkits.8953/"


@pytest.fixture(scope="module")
def res():
    html = (FIX / "resource-8953.html").read_text(encoding="utf-8", errors="replace")
    return parse_resource(html, URL)


def test_id_title_and_version(res):
    assert res.id == 8953
    assert res.title == "AdvancedKits"
    assert res.version == "1.23.32"


def test_label_span_is_not_part_of_the_title(res):
    assert "MC Plugin" not in res.title


def test_author_and_category(res):
    assert res.author.username == "shanruto"
    assert res.author.userId == 46705
    assert res.category is not None
    assert res.category.title == "Minecraft Plugins"


def test_tags(res):
    assert "advancedkits" in res.tags


def test_description_has_text_and_html(res):
    assert res.description["text"]
    assert "attachment" not in res.description["html"].lower()


def test_no_attachment_reference_survives(res):
    blob = json.dumps(to_dict(res))
    assert "/attachments/" not in blob
