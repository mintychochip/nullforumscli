"""Sitemap-backed search index.

The site\'s ``/search/`` is robots-disallowed and is a POST form, so search
runs against a local index of the site\'s own sitemap. Slugs carry titles,
so title search is exact-ish; post bodies are not indexed, and callers must
not pretend otherwise.
"""

from __future__ import annotations

import io
import re
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, Iterable, Iterator

from nf.config import Config
from nf.errors import ParseFailure
from nf.paths import parse_doc_url, slug_to_title

SITEMAP_INDEX = "/sitemap.xml"
_SM = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
INDEXABLE_TYPES = ("thread", "resource", "tag", "forum")
DEFAULT_SEARCH_TYPES = ("thread", "resource")
MAX_LIMIT = 200

SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(
    type UNINDEXED, id UNINDEXED, slug UNINDEXED, title, url UNINDEXED,
    lastmod UNINDEXED, tokenize='porter unicode61'
);
CREATE TABLE IF NOT EXISTS doc_key (key TEXT PRIMARY KEY, doc_rowid INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS shards (url TEXT PRIMARY KEY, lastmod TEXT);
"""


def doc_key(row: dict) -> str:
    """Stable unique key for a document.

    Threads/resources/forums have a real numeric id. Tags do not: the site\'s tag
    URLs carry no id, so parse_doc_url yields id=0 for all of them, and every tag
    would otherwise collide on (type, id). Tags are therefore keyed by slug.
    """
    if row["type"] == "tag" or not row["id"]:
        return f"{row['type']}:{row['slug']}"
    return f"{row['type']}:{row['id']}"


def _is_valid_shard_url(url: str, base_url: str) -> bool:
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    base = urlsplit(base_url)
    if parts.netloc != base.netloc:
        return False
    return re.match(r"^/sitemap-\d+\.xml$", parts.path or "/") is not None


def shard_urls(index_xml: str) -> list[tuple[str, str | None]]:
    try:
        root = ET.fromstring(index_xml)
    except ET.ParseError as exc:
        raise ParseFailure(f"could not parse the sitemap index: {exc}") from exc
    out: list[tuple[str, str | None]] = []
    for node in root.iter(f"{_SM}sitemap"):
        loc = node.findtext(f"{_SM}loc")
        if not loc:
            continue
        out.append((loc.strip(), (node.findtext(f"{_SM}lastmod") or "").strip() or None))
    if not out:
        raise ParseFailure("sitemap index contained no shards")
    return out


def iter_shard(xml_text: str) -> Iterator[dict]:
    """Stream a shard. Shards reach 7.6 MB, so this is iterator-based."""
    if re.search(r"<!DOCTYPE", xml_text, re.I) or re.search(r"<!ENTITY", xml_text, re.I):
        raise ParseFailure("sitemap shard contains a DOCTYPE or entity declaration; refusing to parse")
    try:
        context = ET.iterparse(io.StringIO(xml_text), events=("end",))
        context = iter(context)
        event, root = next(context)
    except ET.ParseError as exc:
        raise ParseFailure(f"could not parse a sitemap shard: {exc}") from exc
    for event, node in context:
        if node.tag == f"{_SM}url":
            loc = (node.findtext(f"{_SM}loc") or "").strip()
            if loc:
                ref = parse_doc_url(loc)
                if ref is not None and ref.type in INDEXABLE_TYPES:
                    if not (ref.type == "resource" and ref.slug.startswith("categories/")):
                        yield {
                            "type": ref.type,
                            "id": ref.id,
                            "slug": ref.slug,
                            "title": slug_to_title(ref.slug.split("/")[-1]),
                            "url": loc,
                            "lastmod": (node.findtext(f"{_SM}lastmod") or "").strip() or None,
                        }
            node.clear()
            if root is not None:
                root.clear()


class Index:
    def __init__(self, db_path: Path | str) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Index":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def upsert_many(self, rows: Iterable[dict]) -> int:
        count = 0
        for row in rows:
            key = doc_key(row)
            found = self.conn.execute(
                "SELECT doc_rowid FROM doc_key WHERE key = ?", (key,)).fetchone()
            if found is not None:
                # Delete by rowid (O(1)); searching by the UNINDEXED type/id
                # columns would full-scan the FTS5 table on every row.
                self.conn.execute("DELETE FROM docs WHERE rowid = ?", (found[0],))
                self.conn.execute(
                    "INSERT INTO docs (rowid, type, id, slug, title, url, lastmod) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (found[0], row["type"], row["id"], row["slug"], row["title"],
                     row["url"], row["lastmod"]))
            else:
                cur = self.conn.execute(
                    "INSERT INTO docs (type, id, slug, title, url, lastmod) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (row["type"], row["id"], row["slug"], row["title"],
                     row["url"], row["lastmod"]))
                self.conn.execute(
                    "INSERT INTO doc_key (key, doc_rowid) VALUES (?, ?)",
                    (key, cur.lastrowid))
            count += 1
        self.conn.commit()
        return count

    def count(self) -> int:
        return self.conn.execute("SELECT count(*) FROM docs").fetchone()[0]

    def shard_state(self) -> dict[str, str]:
        return {url: lastmod or "" for url, lastmod in
                self.conn.execute("SELECT url, lastmod FROM shards")}

    def set_shard_state(self, url: str, lastmod: str | None) -> None:
        self.conn.execute("INSERT INTO shards (url, lastmod) VALUES (?, ?) "
                          "ON CONFLICT(url) DO UPDATE SET lastmod = excluded.lastmod",
                          (url, lastmod or ""))
        self.conn.commit()

    @staticmethod
    def _fts_query(query: str, operator: str) -> str:
        tokens = [t for t in re.split(r"\W+", query) if t]
        return f" {operator} ".join(f'"{t}"' for t in tokens)

    def search(self, query: str, types: Iterable[str] | None = None,
               since: str | None = None, limit: int = 20) -> list[dict]:
        limit = min(max(limit, 0), MAX_LIMIT)
        tokens = [t for t in re.split(r"\W+", query) if t]
        if not tokens:
            return []
        types = list(types or DEFAULT_SEARCH_TYPES)
        placeholders = ",".join("?" for _ in types)
        clauses = [f"type IN ({placeholders})"]
        params: list = list(types)
        if since:
            clauses.append("(lastmod IS NULL OR lastmod >= ?)")
            params.append(since)

        for operator in ("AND", "OR"):
            sql = (f"SELECT type, id, slug, title, url, lastmod, bm25(docs) AS score "
                   f"FROM docs WHERE docs MATCH ? AND {' AND '.join(clauses)} "
                   f"ORDER BY score, lastmod DESC LIMIT ?")
            try:
                rows = self.conn.execute(
                    sql, [self._fts_query(query, operator), *params, limit]).fetchall()
            except sqlite3.OperationalError as exc:
                raise ParseFailure(
                    f"search query failed: {exc}",
                    hint=f"the search index may be corrupt; delete {self.path} and re-run nf index")
            if rows:
                return [
                    {"type": r[0], "id": r[1], "slug": r[2], "title": r[3],
                     "titleSource": "slug", "url": r[4], "lastmod": r[5],
                     "score": r[6]}
                    for r in rows
                ]
        return []


def open_index(cfg: Config) -> Index:
    return Index(cfg.cache_dir / "index.sqlite")


def build(client, cfg: Config, *, rebuild: bool = False,
          progress: Callable[[str], None] | None = None) -> dict:
    """Fetch the sitemap index and refetch only shards whose lastmod changed."""
    say = progress or (lambda _msg: None)
    index = open_index(cfg)
    try:
        status, text, _, _ = client._raw_get(f"{cfg.base_url}{SITEMAP_INDEX}")
        if status != 200:
            raise ParseFailure(f"sitemap index returned status {status}")
        shards = shard_urls(text)
        known = index.shard_state()
        fetched = skipped = 0
        total = 0
        for url, lastmod in shards:
            if not _is_valid_shard_url(url, cfg.base_url):
                say(f"skipping invalid shard URL: {url}")
                continue
            client.assert_gated(url)
            if not rebuild and known.get(url) == (lastmod or ""):
                skipped += 1
                continue
            say(f"fetching {url}")
            st, body, _, _ = client._raw_get(url)
            if st != 200:
                raise ParseFailure(f"sitemap shard {url} returned status {st}")
            total += index.upsert_many(iter_shard(body))
            index.set_shard_state(url, lastmod)
            fetched += 1
        return {"shards": len(shards), "fetched": fetched, "skipped": skipped,
                "indexed": total, "docs": index.count()}
    finally:
        index.close()
