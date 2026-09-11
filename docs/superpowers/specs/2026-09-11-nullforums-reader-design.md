# `nf` — agent-first NullForums reader

**Status:** design, approved for spec
**Date:** 2026-09-11
**Target:** `nullforums.net` (XenForo 2, custom theme, behind Cloudflare)

## 1. Purpose

A headless, read-only CLI that lets an agent (or a human in a shell) pull structured
content out of nullforums.net: threads, resources, category listings, and title search.
Output is JSON by default so it composes with `jq` and drops straight into an agent's
context.

The tool reads. It does not write, post, log in, or download binaries.

## 2. Non-goals (hard boundaries, not omissions)

These are requirements stated negatively. Each is covered by a mechanical guard, not by
convention.

| Non-goal | Enforcement |
| --- | --- |
| **No binary, attachment, or resource-file downloading.** | The parser never extracts attachment URLs or download links into any model. No command has a download flag. Attachment metadata is not emitted at all, so the tool cannot be extended into a downloader by flipping a switch. |
| **No `/search/`** | `robots.txt` disallows it. The robots gate refuses it before a request is made. Search is served from a local sitemap index instead (§7). |
| **No login, no posting, no account mutation** | `/login/` is robots-disallowed. No command issues a non-GET request. |
| **No `/account/`, `/attachments/`, `/goto/`, `/login/`, `/misc/language`, `/misc/style`, `/whats-new/`, `/admin.php`** | Same robots gate. Matched exactly as the site states them — `/misc/` alone is *not* disallowed, so the gate must not over-block the prefix. |
| **No challenge solving or edge-protection bypass** | If a request is blocked, the tool reports the block and exits. No JS execution, no challenge solving, no retry with a different identity. |
| **No UA spoofing** | The client sends an honest, self-identifying UA (§5.1). Verified sufficient; no impersonation is needed or used. |

## 3. Evidence base

Every claim below was verified against the live site during design. These are the facts
the implementation is built on; they are not assumptions.

| Probe | Observed |
| --- | --- |
| `GET /threads/trending-and-latest-posts-api.89951/` | `200`, 57,734 bytes |
| Same URL, UA `curl/8.9.1` | `403` |
| Same URL, UA `nf/0.1 (+contact)` | **`200`** — the gate is a denylist on known tool UAs, not a browser allowlist |
| Same URL, no UA | `403` |
| `GET /api/` | `403`, 199-byte Apache body, `You don't have permission to access this resource`. The XenForo REST API is disabled at the web-server level; **no API key can unlock it.** |
| `GET /login/` | `403` |
| `GET /` | `302` redirect loop to itself (50 redirects) |
| `GET /robots.txt` | `200`, any UA |
| `GET /sitemap.xml` | `200`, sitemap index → **7 shards** |
| `GET /sitemap-{1..7}.xml` | all `200`, 4.8–7.6 MB each, ≈44 MB total |
| Sitemap shard contents | ≈28,504 `threads/`, ≈11,243 `tags/`, ≈10,167 `resources/`, 67 `forums/` per shard; nearly all entries carry `<lastmod>` |
| URL format | `/threads/<title-slug>.<id>/`, `/resources/<title-slug>.<id>/` — **title is in the slug** |
| Resource category page | `200`, 251 KB, `/resources/categories/<slug>.<id>/`, entries under `.structItem-title` |
| Resource page | `200`, `/resources/<slug>.<id>/`, `<title>AdvancedKits \| NullForums</title>` |
| Member profile | `200`, `/members/<user>.<id>/`; custom `kz-*` theme classes, but XF-standard `<time class="u-dt" datetime=…>` hooks intact |
| Author resources page | `200`, `/resources/authors/<user>.<id>/` |
| Server stack | Cloudflare, `x-powered-by: PHP/8.2.33`, `PleskLin` |
| `robots.txt` disallow list | `/account/`, `/attachments/`, `/goto/`, `/login/`, `/misc/language`, `/misc/style`, `/search/`, `/whats-new/`, `/admin.php` |

Two consequences that shape the whole design:

1. **Auth is cookie-only.** The REST API is dead at the server level, so the pluggable
   auth surface from the original brief collapses to one backend: a session cookie the
   operator supplies. Simplification, not a limitation we invented.
2. **Search must be local.** `/search/` is both disallowed and a POST-with-`_xfToken`
   form. The sitemap is the substitute: ≈350k URLs whose slugs contain the titles.
3. **Theme is custom but XF hooks survive.** Selectors must prefer XF-standard
   attributes (`time.u-dt`, `data-content="post-…"`, `.structItem-title`,
   `dl.pairs`) and treat theme-specific `kz-*` classes as optional, or the parser breaks
   on the next theme update.

## 4. Architecture

Language: **Python 3.14**, verified present (3.14.3).

Dependencies, all verified to install and import on 3.14.3: **`httpx`** (HTTP, cookie
handling), **`selectolax`** (fast HTML parsing), **`typer`** (CLI). Search storage uses
**stdlib `sqlite3`** — FTS5 with porter stemming verified available (SQLite 3.51.2), so
no search dependency is needed.

```
nf/
  cli.py         subcommands, flag parsing, exit codes, output dispatch
  config.py      config file + env resolution, cookie loading, redaction
  http.py        httpx client, rate limiter, retry/backoff, disk cache, UA
  robots.py      robots.txt fetch + cache, path authorization gate
  parse/
    thread.py    thread page   -> Thread
    resource.py  resource page -> Resource
    listing.py   category page -> ResourceList
    page.py      shared: title, pagination, login-wall / block detection
  model.py       dataclasses + JSON serialization (schemaVersion 1)
  index.py       sitemap fetch, XML streaming, SQLite/FTS5 store, query
  usage.py       request ledger, windowed rollups
  render.py      model -> json | md | text
```

Layer rules:

- `http` is the **only** module that opens a socket. Everything else calls it.
- `robots` gate is invoked by `http` before every request, so a disallowed path cannot be
  reached even by a code path that forgets to check.
- `parse` is pure: bytes in, model out. No I/O. This is what makes fixtures testable.
- `render` is pure: model in, string out.

## 5. Configuration and auth

### 5.1 Identity

- Default UA: `nf/<version> (read-only client; +https://github.com/jlo/nullforumscli)`,
  with `<version>` read from package metadata at runtime so it cannot drift, and the repo
  slug overridable at build time. This exact shape (`nf/X (…)`) was verified to pass the
  site's gate. Overridable via `user_agent` / `NF_UA`.
- No browser impersonation, no UA rotation.

### 5.2 Sources, in precedence order

1. Environment: `NF_COOKIE`, `NF_BASE_URL`, `NF_UA`, `NF_RATE_LIMIT_MS`, `NF_CACHE_DIR`
2. Config file: `~/.config/nullforums/config.toml` (read with stdlib `tomllib`)
3. Built-in defaults

```toml
# ~/.config/nullforums/config.toml
base_url      = "https://nullforums.net"
cookie        = ""            # e.g. "xf_user=…; xf_session=…"
user_agent    = ""            # empty = built-in honest UA
rate_limit_ms = 1000
cache_ttl_s   = 900
```

- The cookie is the operator's own session, supplied by them. It is never written by the
  tool, never logged, and never included in any output.
- **Redaction is a tested invariant**: a `redact()` helper is applied to all error
  messages, debug output, and ledger entries. A unit test asserts the configured cookie
  value cannot appear in any emitted string, including `nf raw` errors and exceptions.
- No login flow. If the cookie is absent or expired, the tool reports
  `AUTH_REQUIRED` / `AUTH_EXPIRED` and stops.

## 6. Politeness

- One request in flight, ever. The client is used from a single thread; no concurrency.
- Minimum spacing between requests, `rate_limit_ms`, default 1000 ms.
- `Retry-After` honored on 429/503.
- Retries: at most 3, exponential backoff (1s, 2s, 4s), only on 5xx/429/network errors.
  Never on 403/404 — those are terminal and reported.
- Disk cache: `~/.cache/nullforums/http/<sha256(url)>.{html,meta.json}`, TTL 900 s,
  bypassed by `--no-cache`, refreshed by `--refresh`.
- Robots gate: `robots.txt` fetched once per run, cached on disk for 24 h. Any path
  matching a `Disallow` is refused locally with exit code 5 and a message naming the rule.
- `nf index` makes exactly 7 requests for a full rebuild; incremental uses the index's
  stored shard `lastmod` and refetches only changed shards.

## 7. Search: sitemap-backed local index

Because `/search/` is off-limits, search runs against a locally built index of the
site's own sitemap.

**Build (`nf index`, also run implicitly on first `nf search`):**

1. Fetch `sitemap.xml`, read shard URLs and their `lastmod`.
2. Refetch only shards whose `lastmod` differs from the stored value (`--rebuild` forces
   all).
3. Stream-parse each shard with `xml.etree.ElementTree.iterparse` — shards are up to
   7.6 MB and must not be held as a DOM.
4. Classify each URL by path prefix into `thread` / `resource` / `tag` / `forum` /
   `other`; parse `<id>` and `<slug>` from the path; derive `title` by de-hyphenating and
   title-casing the slug. **Index titles are slug-derived approximations**: slugs are
   lowercased and punctuation-stripped, so `c++ tutorial` indexes as `C Tutorial`. Real
   titles come from `--resolve`, which fetches the page. `nf search` marks slug-derived
   titles with `"titleSource": "slug"` and `--resolve` output with `"titleSource": "page"`
   so a consumer can tell them apart rather than being misled.
5. Upsert into SQLite at `~/.cache/nullforums/index.sqlite`:

```sql
CREATE VIRTUAL TABLE docs USING fts5(
    type UNINDEXED, id UNINDEXED, slug UNINDEXED, title, url UNINDEXED,
    lastmod UNINDEXED, tokenize='porter unicode61'
);
CREATE TABLE shards (url TEXT PRIMARY KEY, lastmod TEXT);
```

A single FTS5 table carries both the text and the metadata, rather than an external-content
table with triggers. Contentless-external FTS5 requires trigger maintenance and rowid
bookkeeping, and its failure mode is a silently stale index; `upsert_many` here is
delete-then-insert and is tested to be idempotent. `lastmod` is stored with `UNINDEXED`
so date filters can use it without it polluting relevance scoring.

Search defaults to `thread,resource`. Tags, forums, and other node types are indexable
and selectable with `--type`, but are not returned by default.

**Query (`nf search <query>`):**

- FTS5 `MATCH` over titles, ranked by `bm25()`; recency (`lastmod`) as tiebreak.
- `--type thread|resource|tag|forum` filters.
- `--since YYYY-MM-DD` filters on `lastmod` (index-side, not post-side).
- `--limit N`, default 20, max 200.
- `--resolve N` hydrates the top N hits into full `Thread`/`Resource` objects via
  `parse`, sequentially rate-limited. One request per hydrated hit, and the count is
  printed to stderr when `--resolve` is used with `--format text|md`.
- Default emits the index records only (title, URL, type, id, lastmod) — zero extra
  requests.

**Stated limitation, to be documented in the README and printed by `nf search --help`:**
this is **title search, not full-text search.** Slugs carry titles, not post bodies.
Matching inside post content requires either `/search/` (disallowed) or fetching pages
(which `--resolve` does on a bounded, explicit set).

## 8. Commands

All commands default to JSON on stdout with `"schemaVersion": 1`. `--format md|text` is
for humans. Errors are a single JSON object on stderr.

| Command | Behavior |
| --- | --- |
| `nf thread <url\|id> [--page N] [--all]` | Thread title, id, URL, node, author, created/updated, tags, posts with author and body text. `--all` walks pagination sequentially. |
| `nf resource <url\|id>` | Resource title, id, URL, author, version, tag line, last-updated, description as both HTML and extracted text, category, and the discussion thread URL if the theme exposes one. |
| `nf category <url\|id> [--page N]` | Resource listing: for each entry, title, URL, id, author, version if present. The discovery surface that replaces `/search/` browsing. |
| `nf whoami` | Validates the cookie and reports the logged-in username and user id. Distinct from `usage`. |
| `nf raw <url>` | HTML passthrough for debugging selectors. Still robots-gated; still no attachment/download extraction. |
| `nf index [--rebuild] [--quiet]` | Builds or incrementally updates the search index. |
| `nf search <query> [--type] [--since] [--limit] [--resolve N]` | Title search over the local index. |
| `nf usage [--window 1h\|24h\|all]` | The client's **own request usage**: request counts, cache hits and misses, bytes transferred, rate budget consumed, robots refusals, block and login-wall hits, and last-request time, from the local ledger. |

`<url|id>` accepts a full URL, a path (`/threads/foo.123/`), or a bare id (`123`).

### 8.1 Models (JSON shapes)

```
Thread    { id, url, title, node{id,title,url}, author{username,userId,url},
            createdAt, updatedAt, tags[], posts[Post],
            pagination{page, pages, perPage, total} }
Post      { id, index, url, author{username,userId,url}, postedAt, bodyText, bodyHtml }
Resource  { id, url, title, author{username,userId,url}, version, tagLine,
            description{html,text}, createdAt, lastUpdated,
            category{id,title,url}, discussionThread{id,url}|null }
ResourceList { items[{id,url,title,author{username,userId,url},version}],
               pagination{page, pages, total} }
Usage     { window, requests{total,byClass}, cache{hits,misses}, bytes,
            rate{limitMs,budgetUsedMs}, refusals{robots,block,loginWall},
            lastRequestAt }
SearchHit { type, id, slug, title, titleSource:"slug"|"page", url, lastmod,
            score, resource?:Resource, thread?:Thread }
```

Timestamps are ISO 8601 with offset, taken from `<time class="u-dt" datetime=…>`.

### 8.2 Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success |
| 2 | Usage error (bad flag, unparseable argument) |
| 3 | Login wall / `AUTH_REQUIRED` / `AUTH_EXPIRED` |
| 4 | Edge block (403 from the CDN/WAF) |
| 5 | Robots refusal |
| 6 | Rate limited after retries |
| 7 | Parse failure (page shape changed) |
| 8 | Network error |

Exit 7 is the canary for theme changes and must name the selector that failed.

## 9. Usage ledger

Append-only JSONL at `~/.local/state/nullforums/ledger.jsonl`, one record per request:

```json
{"ts":"2026-09-11T16:20:00-04:00","pathClass":"thread","status":200,"bytes":57734,
 "cache":"miss","attempt":1}
```

- `pathClass` is a coarse bucket (`thread`, `resource`, `category`, `index`, `robots`,
  `other`) — never a full URL with a query string, and never any cookie material.
- `nf usage` reads the ledger and rolls up the requested window.
- Ledger is rotated at 10 MB to keep it bounded.

This is deliberately *the CLI's own consumption*, not forum-account statistics.

## 10. Testing and verification

**Unit, offline, no network:**

- `parse/` against committed HTML fixtures — one thread, one resource, one category, one
  login wall, one block page. Filenames describe provenance (`thread-89951.html`).
- `robots` matcher: table-driven over the real disallow list, plus prefix-vs-substring
  edge cases (`/misc/` vs `/miscellaneous`).
- `redact`: asserts the cookie value never survives into emitted strings, including
  exception paths.
- `index`: fixture sitemap shard → expected rows; incremental rebuild check driven by
  `lastmod`.
- `usage`: fixture ledger → expected rollups across window boundaries.

Conventions: `pytest`, deterministic, no network in the default run. Live tests are
gated behind `NF_LIVE=1` and make at most a handful of requests.

**Verification for the build (the stopping condition):** run the real binary against the
live site and show output for

1. `nf thread <real thread url>` → non-empty title and at least one post,
2. `nf resource <real resource url>` → title, author, version populated,
3. `nf index` then `nf search <term>` → hits whose slugs match the term,
4. `nf usage` → non-zero request count consistent with the requests just made.

Parser fixtures are captured from the same live pages, so a pass on fixtures plus a pass
live is meaningful rather than circular.

## 11. Risks and limitations

- **Custom theme.** The site runs a bespoke `kz-*` theme layered on XenForo. Selectors
  lean on XF-standard hooks; theme-specific classes are optional. A theme change can
  still break parsing, which is what exit code 7 and the selector-naming error exist for.
- **Sitemap completeness.** Search coverage is exactly what the site publishes in its
  sitemap. Unlisted threads will not appear. Incremental updates depend on the site
  refreshing `<lastmod>`.
- **Cookie lifetime.** Session cookies expire. The tool reports expiry; refreshing it is
  the operator's job and is out of scope.
- **No full-text search.** See §7.
- **Rate limiting is the implementation's own courtesy**, not a negotiated quota. Default
  1 req/s; raise it only with care.

## 12. Deliberately excluded from v1

Thread pagination beyond `--all`, sub-forum traversal, tag browsing, TUI, plugins, multi-forum
profiles, and any write operation. Each is additive to the design above and none is
required for the stated goal.
