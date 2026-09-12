from pathlib import Path

import pytest

from nf.index import Index, iter_shard, shard_urls

FIX = Path(__file__).parent / "fixtures"
INDEX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://nullforums.net/sitemap-1.xml</loc>
  <lastmod>2026-09-11T05:37:57+00:00</lastmod></sitemap>
  <sitemap><loc>https://nullforums.net/sitemap-2.xml</loc>
  <lastmod>2026-09-01T00:00:00+00:00</lastmod></sitemap>
</sitemapindex>
"""


def test_shard_urls_reads_locs_and_lastmods():
    shards = shard_urls(INDEX_XML)
    assert shards == [
        ("https://nullforums.net/sitemap-1.xml", "2026-09-11T05:37:57+00:00"),
        ("https://nullforums.net/sitemap-2.xml", "2026-09-01T00:00:00+00:00"),
    ]


def test_iter_shard_classifies_and_titles():
    xml = (FIX / "sitemap-shard.xml").read_text(encoding="utf-8")
    rows = list(iter_shard(xml))
    assert rows
    by_type = {}
    for row in rows:
        by_type[row["type"]] = by_type.get(row["type"], 0) + 1
        assert row["title"]
        assert row["url"].startswith("https://nullforums.net/")
    assert by_type == {"forum": 3, "thread": 3, "resource": 3, "tag": 3}


def test_iter_shard_skips_category_listings():
    """Inline XML, because the trimmed fixture has no categories URL to exercise this."""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://nullforums.net/resources/categories/minecraft-plugins.38/</loc></url>
  <url><loc>https://nullforums.net/resources/advancedkits.8953/</loc></url>
</urlset>
"""
    rows = list(iter_shard(xml))
    assert [r["id"] for r in rows] == [8953]


def test_upsert_is_idempotent_and_updates(tmp_path):
    idx = Index(tmp_path / "i.sqlite")
    row = {"type": "thread", "id": 1, "slug": "a-b", "title": "A B",
           "url": "https://nullforums.net/threads/a-b.1/", "lastmod": "2026-01-01"}
    idx.upsert_many([row])
    idx.upsert_many([row])
    assert idx.count() == 1
    idx.upsert_many([{**row, "title": "A C", "lastmod": "2026-02-02"}])
    assert idx.count() == 1
    assert idx.search("c", types=["thread"], since=None, limit=10)[0]["title"] == "A C"


def test_distinct_tags_do_not_collide(tmp_path):
    """Tags carry no numeric id (all id=0), so they must not key on (type, id)."""
    idx = Index(tmp_path / "i.sqlite")
    idx.upsert_many([
        {"type": "tag", "id": 0, "slug": "minecraft", "title": "Minecraft",
         "url": "https://nullforums.net/tags/minecraft/", "lastmod": "2026-01-01"},
        {"type": "tag", "id": 0, "slug": "gwarppro", "title": "Gwarppro",
         "url": "https://nullforums.net/tags/gwarppro/", "lastmod": "2026-01-01"},
        {"type": "tag", "id": 0, "slug": "minecraft-plugins", "title": "Minecraft Plugins",
         "url": "https://nullforums.net/tags/minecraft-plugins/", "lastmod": "2026-01-01"},
    ])
    assert idx.count() == 3
    assert len(idx.search("minecraft", types=["tag"], since=None, limit=10)) == 2

    # Re-upsert the two minecraft tags; the table must not grow and the two
    # hits must remain visible.
    idx.upsert_many([
        {"type": "tag", "id": 0, "slug": "minecraft", "title": "Minecraft",
         "url": "https://nullforums.net/tags/minecraft/", "lastmod": "2026-01-02"},
        {"type": "tag", "id": 0, "slug": "minecraft-plugins", "title": "Minecraft Plugins",
         "url": "https://nullforums.net/tags/minecraft-plugins/", "lastmod": "2026-01-02"},
    ])
    assert idx.count() == 3
    assert len(idx.search("minecraft", types=["tag"], since=None, limit=10)) == 2

def test_search_stems_words(tmp_path):
    idx = Index(tmp_path / "i.sqlite")
    idx.upsert_many([
        {"type": "resource", "id": 1, "slug": "advanced-crates", "title": "Advanced Crates",
         "url": "https://nullforums.net/resources/advanced-crates.1/", "lastmod": "2026-01-01"},
    ])
    hits = idx.search("crate", types=["resource"], since=None, limit=10)
    assert len(hits) == 1


def test_search_filters_by_type(tmp_path):
    idx = Index(tmp_path / "i.sqlite")
    idx.upsert_many([
        {"type": "thread", "id": 1, "slug": "kit", "title": "Kit Thread",
         "url": "https://nullforums.net/threads/kit.1/", "lastmod": "2026-01-01"},
        {"type": "resource", "id": 2, "slug": "kit-res", "title": "Kit Resource",
         "url": "https://nullforums.net/resources/kit-res.2/", "lastmod": "2026-01-01"},
    ])
    only_threads = idx.search("kit", types=["thread"], since=None, limit=10)
    assert [h["type"] for h in only_threads] == ["thread"]


def test_search_filters_by_since(tmp_path):
    idx = Index(tmp_path / "i.sqlite")
    idx.upsert_many([
        {"type": "thread", "id": 1, "slug": "old", "title": "Old Kit",
         "url": "https://nullforums.net/threads/old.1/", "lastmod": "2020-01-01"},
        {"type": "thread", "id": 2, "slug": "new", "title": "New Kit",
         "url": "https://nullforums.net/threads/new.2/", "lastmod": "2026-05-05"},
    ])
    hits = idx.search("kit", types=["thread"], since="2026-01-01", limit=10)
    assert [h["id"] for h in hits] == [2]


def test_search_respects_limit(tmp_path):
    idx = Index(tmp_path / "i.sqlite")
    idx.upsert_many([
        {"type": "thread", "id": i, "slug": f"kit-{i}", "title": f"Kit {i}",
         "url": f"https://nullforums.net/threads/kit-{i}.{i}/", "lastmod": "2026-01-01"}
        for i in range(1, 6)
    ])
    assert len(idx.search("kit", types=["thread"], since=None, limit=2)) == 2


def test_search_falls_back_to_or_when_and_finds_nothing(tmp_path):
    idx = Index(tmp_path / "i.sqlite")
    idx.upsert_many([
        {"type": "thread", "id": 1, "slug": "alpha", "title": "Alpha Plugin",
         "url": "https://nullforums.net/threads/alpha.1/", "lastmod": "2026-01-01"},
        {"type": "thread", "id": 2, "slug": "beta", "title": "Beta Mod",
         "url": "https://nullforums.net/threads/beta.2/", "lastmod": "2026-01-01"},
    ])
    assert idx.search("alpha mod", types=["thread"], since=None, limit=10)


def test_search_escapes_fts_syntax(tmp_path):
    idx = Index(tmp_path / "i.sqlite")
    idx.upsert_many([
        {"type": "thread", "id": 1, "slug": "x", "title": "X Plugin",
         "url": "https://nullforums.net/threads/x.1/", "lastmod": "2026-01-01"},
    ])
    assert idx.search('"unbalanced AND OR', types=["thread"], since=None, limit=10) == []

def test_operator_words_are_matched_literally(tmp_path):
    """Quoting is what makes FTS operator words match as terms. Without it this
    query is an FTS syntax error, so a non-empty result proves the quoting."""
    idx = Index(tmp_path / "i.sqlite")
    idx.upsert_many([
        {"type": "thread", "id": 1, "slug": "not-alpha", "title": "Not Alpha Bot",
         "url": "https://nullforums.net/threads/not-alpha.1/", "lastmod": "2026-01-01"},
    ])
    hits = idx.search("NOT alpha", types=["thread"], since=None, limit=10)
    assert [h["id"] for h in hits] == [1]


def test_shard_state_roundtrip(tmp_path):
    idx = Index(tmp_path / "i.sqlite")
    assert idx.shard_state() == {}
    idx.set_shard_state("https://nullforums.net/sitemap-1.xml", "2026-09-11T05:37:57+00:00")
    assert idx.shard_state() == {
        "https://nullforums.net/sitemap-1.xml": "2026-09-11T05:37:57+00:00"}
