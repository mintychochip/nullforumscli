# `nf` — NullForums Reader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `nf`, a headless read-only CLI that extracts structured thread, resource, and category data from nullforums.net, plus title search over a local sitemap index and a client-side request-usage report.

**Architecture:** Three pure cores (parse, model, render) surrounded by three effectful edges (http, index, cli). `parse` is pure bytes-in/model-out, which is what makes it fixture-testable. `http` is the only module that opens a socket, and it calls the robots gate on every request, so no code path can reach a disallowed URL. Search never touches the network at query time — it reads a local SQLite/FTS5 index built from the site's own sitemap.

**Tech Stack:** Python 3.14, `httpx`, `selectolax`, `typer`, stdlib `sqlite3` (FTS5), stdlib `tomllib`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-09-11-nullforums-reader-design.md`

## Global Constraints

Copied verbatim from the spec. Every task's requirements implicitly include this section.

- Python **3.14** (verified present: 3.14.3). `requires-python = ">=3.12"`.
- Runtime dependencies are **exactly** `httpx`, `selectolax`, `typer`. Search storage uses stdlib `sqlite3`; config uses stdlib `tomllib`. Do not add another dependency without changing the spec.
- All commands default to **JSON on stdout** with `"schemaVersion": 1`. `--format md|text` is for humans.
- Errors are **a single JSON object on stderr**: `{"error":{"code":…,"message":…,"hint":…}}`.
- Exit codes: `0` success, `2` usage, `3` auth/login-wall, `4` edge block, `5` robots refusal, `6` rate limited, `7` parse failure, `8` network.
- Rate limit: **1000 ms** minimum spacing, **one request in flight, ever**. No concurrency anywhere.
- **No binary, attachment, or resource-file downloading.** No command has a download flag. The parser must not emit attachment URLs into any model (see `scrub_attachments`).
- **No `/search/`** — disallowed by robots.txt and a POST-with-`_xfToken` form. Search is local (§ Task 10).
- Robots-disallowed paths: `/account/`, `/attachments/`, `/goto/`, `/login/`, `/misc/language`, `/misc/style`, `/search/`, `/whats-new/`, `/admin.php`. Match exactly as stated — `/misc/` alone is **not** disallowed.
- **No UA spoofing, no challenge solving.** The client sends `nf/<version> (read-only client; +https://github.com/jlo/nullforumscli)`, verified to pass the site's gate.
- The cookie is API-supplied by the operator, **never written, never logged, never in output**. Redaction is a tested invariant.

---

## File Structure

```
pyproject.toml                      packaging, deps, console script `nf`
README.md                           usage, limitations, non-goals
src/nf/__init__.py                  empty package marker
src/nf/errors.py                    NfError hierarchy <-> exit codes
src/nf/config.py                    Config dataclass, env/TOML loading, redact()
src/nf/paths.py                     URL classification + doc URL parsing
src/nf/robots.py                    robots.txt parsing and the allow/deny gate
src/nf/usage.py                     JSONL request ledger + windowed rollups
src/nf/http.py                      httpx client, throttle, retry, cache, gate call
src/nf/model.py                     dataclasses, SCHEMA_VERSION, envelope()
src/nf/render.py                    model -> json | md | text
src/nf/parse/__init__.py            re-exports the parse entry points
src/nf/parse/page.py                shared selectors, scrubbing, detection
src/nf/parse/thread.py              thread page -> Thread
src/nf/parse/resource.py            resource page -> Resource
src/nf/parse/listing.py             category page -> ResourceList
src/nf/index.py                     sitemap -> SQLite/FTS5, query interface
src/nf/cli.py                       typer app, error->exit-code mapping
tests/conftest.py                   fixture loaders, fake sleep/clock
tests/fixtures/*.html               captured pages (see Task 4)
tests/fixtures/sitemap-shard.xml    trimmed real shard
tests/test_errors.py                exit-code mapping
tests/test_config.py                env/TOML precedence, redaction
tests/test_robots.py                allow/deny table
tests/test_paths.py                 classification
tests/test_usage.py                 ledger + rollups
tests/test_http.py                  throttle, retry, cache, gate
tests/test_parse_page.py           scrubbing, time, detection
tests/test_parse_thread.py         thread + posts
tests/test_parse_resource.py       resource
tests/test_parse_listing.py        category listing
tests/test_index.py                shard parse, upsert, search
tests/test_cli.py                  end-to-end offline via mock transport
```

Rationale for splits: `paths.py` exists because both `http` (coarse request classes) and `index` (document types) need URL classification, and duplicating it would drift. `parse/` splits per page type because the three pages share almost no selectors. `render.py` is separate from `model.py` so that changing output format never risks changing the data shape.

---

### Task 1: Package scaffold, errors, and exit-code mapping

**Files:**
- Create: `pyproject.toml`, `src/nf/__init__.py`, `src/nf/errors.py`, `README.md`
- Test: `tests/test_errors.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `NfError` base with class attrs `exit_code: int` and `code: str`, plus `to_dict()`; subclasses `UsageError`(2/`USAGE`), `AuthRequired`(3/`AUTH_REQUIRED`), `EdgeBlocked`(4/`EDGE_BLOCKED`), `RobotsRefusal`(5/`ROBOTS_DISALLOWED`), `RateLimited`(6/`RATE_LIMITED`), `ParseFailure`(7/`PARSE_FAILURE`), `NetworkError`(8/`NETWORK_ERROR`). Constructor signature `NfError(message: str, hint: str | None = None)`.

- [ ] **Step 1: Create the venv and confirm the dependency set installs**

```bash
cd /home/jlo/dev/nullforumscli
python3 -m venv .venv
.venv/bin/python -V
```

Expected: `Python 3.14.3` (or ≥3.12).

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "nullforumscli"
version = "0.1.0"
description = "Headless read-only reader for nullforums.net"
requires-python = ">=3.12"
dependencies = ["httpx>=0.27", "selectolax>=0.3", "typer>=0.12"]

[project.optional-dependencies]
dev = ["pytest>=8"]

[project.scripts]
nf = "nf.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/nf"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

- [ ] **Step 3: Install editable with dev extras**

Run: `.venv/bin/pip install -e '.[dev]'`
Expected: `Successfully installed ... nullforumscli-0.1.0 ...`

- [ ] **Step 4: Write the failing test**

`tests/test_errors.py`:

```python
import pytest

from nf.errors import (
    AuthRequired, EdgeBlocked, NfError, ParseFailure, RateLimited,
    RobotsRefusal, UsageError, NetworkError,
)


@pytest.mark.parametrize("cls,code,exit_code", [
    (UsageError, "USAGE", 2),
    (AuthRequired, "AUTH_REQUIRED", 3),
    (EdgeBlocked, "EDGE_BLOCKED", 4),
    (RobotsRefusal, "ROBOTS_DISALLOWED", 5),
    (RateLimited, "RATE_LIMITED", 6),
    (ParseFailure, "PARSE_FAILURE", 7),
    (NetworkError, "NETWORK_ERROR", 8),
])
def test_exit_code_mapping(cls, code, exit_code):
    err = cls("boom")
    assert err.exit_code == exit_code
    assert err.code == code
    assert err.to_dict() == {"error": {"code": code, "message": "boom"}}


def test_hint_is_included_when_present():
    err = ParseFailure("selector missing", hint="run with --raw")
    assert err.to_dict()["error"]["hint"] == "run with --raw"


def test_str_is_the_message():
    assert str(UsageError("bad flag")) == "bad flag"
```

- [ ] **Step 5: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_errors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nf.errors'`

- [ ] **Step 6: Implement `src/nf/errors.py`**

```python
"""Error types, each bound to a process exit code. See spec section 8.2."""

from __future__ import annotations


class NfError(Exception):
    """Base error. ``exit_code`` is the process status; ``code`` is the wire name."""

    exit_code: int = 1
    code: str = "ERROR"

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict:
        payload: dict[str, str] = {"code": self.code, "message": self.message}
        if self.hint:
            payload["hint"] = self.hint
        return {"error": payload}


class UsageError(NfError):
    exit_code = 2
    code = "USAGE"


class AuthRequired(NfError):
    exit_code = 3
    code = "AUTH_REQUIRED"


class EdgeBlocked(NfError):
    exit_code = 4
    code = "EDGE_BLOCKED"


class RobotsRefusal(NfError):
    exit_code = 5
    code = "ROBOTS_DISALLOWED"


class RateLimited(NfError):
    exit_code = 6
    code = "RATE_LIMITED"


class ParseFailure(NfError):
    exit_code = 7
    code = "PARSE_FAILURE"


class NetworkError(NfError):
    exit_code = 8
    code = "NETWORK_ERROR"
```

- [ ] **Step 7: Create `src/nf/__init__.py` and a placeholder `README.md`**

`src/nf/__init__.py`:

```python
"""nf - headless read-only reader for nullforums.net."""
```

`README.md` may be a one-line title at this stage; Task 12 completes it.

- [ ] **Step 8: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_errors.py -v`
Expected: 9 passed

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml README.md src/nf tests/test_errors.py
git commit -m "feat: package scaffold and exit-code error hierarchy"
```

---

### Task 2: Config loading and redaction

**Files:**
- Create: `src/nf/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Config` frozen dataclass with fields `base_url: str`, `cookie: str`, `user_agent: str`, `rate_limit_ms: int`, `cache_ttl_s: int`, `cache_dir: Path`, `state_dir: Path`; method `redact(text: str) -> str`; property `cookie_header -> str | None`. Module functions `default_user_agent() -> str` and `load_config(env: Mapping[str, str] | None = None, path: Path | None = None) -> Config`.

- [ ] **Step 1: Write the failing test**

`tests/test_config.py`:

```python
from pathlib import Path

from nf.config import Config, default_user_agent, load_config

SECRET_USER = "SECRETVALUE12345"
SECRET_SESS = "OTHERSECRET67890"
COOKIE = f"xf_user={SECRET_USER}; xf_session={SECRET_SESS}"


def test_defaults_when_nothing_is_set(tmp_path):
    cfg = load_config(env={}, path=tmp_path / "missing.toml")
    assert cfg.base_url == "https://nullforums.net"
    assert cfg.cookie == ""
    assert cfg.rate_limit_ms == 1000
    assert cfg.cache_ttl_s == 900
    assert cfg.user_agent.startswith("nf/")


def test_toml_values_are_read(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        'base_url = "https://example.test"\n'
        f'cookie = "{COOKIE}"\n'
        'rate_limit_ms = 250\n',
        encoding="utf-8",
    )
    cfg = load_config(env={}, path=p)
    assert cfg.base_url == "https://example.test"
    assert cfg.cookie == COOKIE
    assert cfg.rate_limit_ms == 250


def test_env_overrides_toml(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('base_url = "https://toml.test"\n', encoding="utf-8")
    cfg = load_config(env={"NF_BASE_URL": "https://env.test"}, path=p)
    assert cfg.base_url == "https://env.test"


def test_redact_removes_the_whole_cookie_and_each_value(tmp_path):
    cfg = load_config(env={"NF_COOKIE": COOKIE}, path=tmp_path / "none.toml")
    for probe in (COOKIE, SECRET_USER, SECRET_SESS):
        assert probe not in cfg.redact(f"request failed with {probe} attached")


def test_redact_is_a_noop_without_a_cookie(tmp_path):
    cfg = load_config(env={}, path=tmp_path / "none.toml")
    assert cfg.redact("plain message") == "plain message"


def test_redact_ignores_short_values_to_avoid_mangling_text(tmp_path):
    cfg = Config(
        base_url="https://x.test", cookie="a=1", user_agent="nf/0",
        rate_limit_ms=1, cache_ttl_s=1,
        cache_dir=Path("/tmp/c"), state_dir=Path("/tmp/s"),
    )
    assert cfg.redact("a=1 in text") == "a=1 in text"


def test_default_user_agent_shape():
    ua = default_user_agent()
    assert ua.startswith("nf/")
    assert "read-only" in ua
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nf.config'`

- [ ] **Step 3: Implement `src/nf/config.py`**

```python
"""Configuration resolution: env > TOML file > built-in defaults.

The session cookie is supplied by the operator. It is never written by this
tool, never logged, and never emitted; ``Config.redact`` is the single choke
point and Task 12's CLI applies it to every error path.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Mapping

DEFAULT_BASE_URL = "https://nullforums.net"
DEFAULT_RATE_LIMIT_MS = 1000
DEFAULT_CACHE_TTL_S = 900
REDACTED = "<redacted>"
_MIN_SECRET_LEN = 4

REPO_SLUG = "jlo/nullforumscli"


def _package_version() -> str:
    try:
        return version("nullforumscli")
    except PackageNotFoundError:
        return "0.0.0"


def default_user_agent() -> str:
    """Honest, self-identifying UA. Verified to pass the site's gate."""
    return f"nf/{_package_version()} (read-only client; +https://github.com/{REPO_SLUG})"


def _xdg(env: Mapping[str, str], var: str, fallback: str) -> Path:
    base = env.get(var)
    return Path(base) if base else Path(fallback).expanduser()


def default_config_path() -> Path:
    return Path("~/.config/nullforums/config.toml").expanduser()


@dataclass(frozen=True)
class Config:
    base_url: str
    cookie: str
    user_agent: str
    rate_limit_ms: int
    cache_ttl_s: int
    cache_dir: Path
    state_dir: Path

    @property
    def cookie_header(self) -> str | None:
        return self.cookie or None

    def _secrets(self) -> list[str]:
        """The whole cookie plus each individual value, longest first."""
        secrets = [self.cookie] if self.cookie else []
        for part in self.cookie.split(";"):
            _, _, value = part.strip().partition("=")
            value = value.strip()
            if len(value) >= _MIN_SECRET_LEN:
                secrets.append(value)
        return sorted(secrets, key=len, reverse=True)

    def redact(self, text: str) -> str:
        if not text:
            return text
        for secret in self._secrets():
            text = text.replace(secret, REDACTED)
        return text


def load_config(
    env: Mapping[str, str] | None = None,
    path: Path | None = None,
) -> Config:
    env = os.environ if env is None else env
    path = default_config_path() if path is None else path

    data: dict = {}
    if path.is_file():
        with path.open("rb") as fh:
            data = tomllib.load(fh)

    def pick(env_key: str, toml_key: str, default):
        if env_key in env:
            return env[env_key]
        return data.get(toml_key, default)

    cache_dir = env.get("NF_CACHE_DIR") or data.get("cache_dir") or str(
        _xdg(env, "XDG_CACHE_HOME", "~/.cache") / "nullforums"
    )
    state_dir = env.get("NF_STATE_DIR") or data.get("state_dir") or str(
        _xdg(env, "XDG_STATE_HOME", "~/.local/state") / "nullforums"
    )

    return Config(
        base_url=str(pick("NF_BASE_URL", "base_url", DEFAULT_BASE_URL)).rstrip("/"),
        cookie=str(env.get("NF_COOKIE", data.get("cookie", ""))),
        user_agent=str(pick("NF_UA", "user_agent", "") or default_user_agent()),
        rate_limit_ms=int(pick("NF_RATE_LIMIT_MS", "rate_limit_ms", DEFAULT_RATE_LIMIT_MS)),
        cache_ttl_s=int(pick("NF_CACHE_TTL_S", "cache_ttl_s", DEFAULT_CACHE_TTL_S)),
        cache_dir=Path(cache_dir).expanduser(),
        state_dir=Path(state_dir).expanduser(),
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/nf/config.py tests/test_config.py
git commit -m "feat: config resolution and tested cookie redaction"
```

---

### Task 3: URL classification

**Files:**
- Create: `src/nf/paths.py`
- Test: `tests/test_paths.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `classify_request(path: str) -> str` returning one of `thread`, `resource`, `category`, `index`, `robots`, `other`. `parse_doc_url(url: str) -> DocRef | None` where `DocRef` is a frozen dataclass `(type: str, id: int, slug: str)`; `type` is one of `thread`, `resource`, `tag`, `forum`. `slug_to_title(slug: str) -> str`. `doc_url(base_url: str, ref: DocRef) -> str`.

- [ ] **Step 1: Write the failing test**

`tests/test_paths.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nf.paths'`

- [ ] **Step 3: Implement `src/nf/paths.py`**

```python
"""URL classification, shared by the HTTP layer and the search index.

Two different questions get asked of a URL: ``classify_request`` answers
"what kind of request is this?" for the usage ledger; ``parse_doc_url``
answers "what document is this?" for the sitemap index. Both live here
because both are pure URL string work and duplicating it would drift.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

_DOC_PATH = re.compile(r"^/(threads|resources|tags|forums)/(.+)\.(\d+)/?$")


@dataclass(frozen=True)
class DocRef:
    type: str
    id: int
    slug: str


def _path_of(url: str) -> str:
    if "://" in url:
        return urlsplit(url).path or "/"
    return urlsplit(url).path or url


def classify_request(path: str) -> str:
    p = _path_of(path)
    if p.startswith("/threads/"):
        return "thread"
    if p.startswith("/resources/categories/"):
        return "category"
    if p.startswith("/resources/"):
        return "resource"
    if p in ("/sitemap.xml",) or re.match(r"^/sitemap-\d+\.xml$", p):
        return "index"
    if p == "/robots.txt":
        return "robots"
    return "other"


def parse_doc_url(url: str) -> DocRef | None:
    p = _path_of(url)
    m = _DOC_PATH.match(p)
    if m:
        kind, slug, ident = m.groups()
        return DocRef(type=kind[:-1] if kind.endswith("s") else kind,
                      id=int(ident), slug=slug)
    m = re.match(r"^/tags/([^/]+)/?$", p)
    if m:
        return DocRef(type="tag", id=0, slug=m.group(1))
    return None


def slug_to_title(slug: str) -> str:
    """Approximate a title from a slug. Slugs are lossy; callers must mark it."""
    words = [w for w in slug.split("-") if w]
    return " ".join(w[:1].upper() + w[1:] for w in words)


def doc_url(base_url: str, ref: DocRef) -> str:
    kind = {"thread": "threads", "resource": "resources",
            "tag": "tags", "forum": "forums"}[ref.type]
    if ref.type == "tag":
        return f"{base_url}/{kind}/{ref.slug}/"
    return f"{base_url}/{kind}/{ref.slug}.{ref.id}/"
```

Note: `/resources/categories/minecraft-plugins.38/` intentionally parses as a
`resource` DocRef with a `categories/...` slug. It is a listing page, excluded from
the search index by filtering on the `categories/` slug prefix in Task 10, and
classified as `category` for request accounting by `classify_request`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_paths.py -v`
Expected: 20 passed

- [ ] **Step 5: Commit**

```bash
git add src/nf/paths.py tests/test_paths.py
git commit -m "feat: URL classification and doc URL parsing"
```

---

### Task 4: Robots gate

**Files:**
- Create: `src/nf/robots.py`
- Test: `tests/test_robots.py`

**Interfaces:**
- Consumes: `nf.errors.RobotsRefusal`.
- Produces: `RobotsPolicy.parse(text: str, user_agent: str) -> RobotsPolicy`;
  `RobotsPolicy.is_allowed(path: str) -> bool`; `RobotsPolicy.DISALLOW_TABLE` is not
  needed — the real rules come from the fetched file. Also `assert_allowed(policy, url) -> None`
  raising `RobotsRefusal`.

- [ ] **Step 1: Write the failing test**

`tests/test_robots.py`:

```python
import pytest

from nf.errors import RobotsRefusal
from nf.robots import RobotsPolicy, assert_allowed

REAL = """User-agent: *
Disallow: /account/
Disallow: /attachments/
Disallow: /goto/
Disallow: /login/
Disallow: /misc/language
Disallow: /misc/style
Disallow: /search/
Disallow: /whats-new/
Disallow: /admin.php
Allow: /

Sitemap: https://nullforums.net/sitemap.xml
"""

UA = "nf/0.1 (read-only client; +https://github.com/jlo/nullforumscli)"


@pytest.fixture
def policy():
    return RobotsPolicy.parse(REAL, UA)


@pytest.mark.parametrize("path", [
    "/account/", "/attachments/foo.zip.1/", "/goto/post?id=1", "/login/",
    "/misc/language", "/misc/style", "/search/", "/search/member?user_id=1",
    "/whats-new/", "/whats-new/posts/", "/admin.php",
])
def test_disallowed_paths_are_refused(policy, path):
    assert policy.is_allowed(path) is False


@pytest.mark.parametrize("path", [
    "/", "/threads/foo.1/", "/threads/foo.1/page-2", "/resources/advancedkits.8953/",
    "/resources/categories/minecraft-plugins.38/", "/members/foo.123/",
    "/resources/authors/foo.46705/", "/sitemap.xml", "/sitemap-1.xml", "/robots.txt",
])
def test_allowed_paths_pass(policy, path):
    assert policy.is_allowed(path) is True


def test_bare_misc_prefix_is_not_over_blocked(policy):
    """/misc/language is disallowed, but /misc/ and /miscellaneous are not."""
    assert policy.is_allowed("/misc/") is True
    assert policy.is_allowed("/miscellaneous") is True


def test_allow_beats_disallow_on_equal_length():
    p = RobotsPolicy.parse("User-agent: *\nDisallow: /x/\nAllow: /x/\n", UA)
    assert p.is_allowed("/x/") is True


def test_longest_rule_wins():
    p = RobotsPolicy.parse(
        "User-agent: *\nDisallow: /a/\nAllow: /a/public/\n", UA)
    assert p.is_allowed("/a/secret") is False
    assert p.is_allowed("/a/public/thing") is True


def test_wildcard_and_anchor():
    p = RobotsPolicy.parse("User-agent: *\nDisallow: /*.json$\n", UA)
    assert p.is_allowed("/a/b.json") is False
    assert p.is_allowed("/a/b.json?x=1") is True


def test_no_matching_rules_means_allowed():
    p = RobotsPolicy.parse("User-agent: *\nDisallow: /secret/\n", UA)
    assert p.is_allowed("/anything/else") is True


def test_specific_user_agent_group_wins_over_star():
    text = "User-agent: nf\nDisallow: /blocked/\n\nUser-agent: *\nDisallow: /\n"
    p = RobotsPolicy.parse(text, UA)
    assert p.is_allowed("/threads/foo.1/") is True
    assert p.is_allowed("/blocked/") is False


def test_assert_allowed_raises_with_the_rule_named(policy):
    with pytest.raises(RobotsRefusal) as ei:
        assert_allowed(policy, "https://nullforums.net/search/?q=x")
    assert "/search/" in ei.value.message
    assert ei.value.exit_code == 5


def test_assert_allowed_accepts_an_allowed_url(policy):
    assert_allowed(policy, "https://nullforums.net/threads/foo.1/")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_robots.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nf.robots'`

- [ ] **Step 3: Implement `src/nf/robots.py`**

```python
"""robots.txt parsing and the pre-request authorization gate.

Implements the RFC 9309 precedence rules that matter here: pick the group
whose user-agent token is the longest match (falling back to ``*``), then
within it the longest matching pattern wins, and an ``Allow`` beats a
``Disallow`` of equal length. A path matching no rule is allowed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from nf.errors import RobotsRefusal


@dataclass(frozen=True)
class _Rule:
    allow: bool
    pattern: str
    regex: re.Pattern[str]


def _compile(pattern: str) -> re.Pattern[str]:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    parts = [re.escape(p) for p in body.split("*")]
    return re.compile("^" + ".*".join(parts) + ("$" if anchored else ""))


@dataclass
class RobotsPolicy:
    rules: list[_Rule] = field(default_factory=list)
    source_agent: str = "*"

    @classmethod
    def parse(cls, text: str, user_agent: str) -> "RobotsPolicy":
        groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
        agents: list[str] = []
        rules: list[tuple[bool, str]] = []
        in_rules = False

        def flush() -> None:
            if agents:
                groups.append((list(agents), list(rules)))

        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "user-agent":
                if in_rules:
                    flush()
                    agents.clear()
                    rules.clear()
                    in_rules = False
                agents.append(value.lower())
            elif key in ("allow", "disallow"):
                if not agents:
                    continue
                in_rules = True
                if value:
                    rules.append((key == "allow", value))
        flush()

        ua = user_agent.lower()
        token = ua.split("/", 1)[0]
        best_agent: str | None = None
        best_rules: list[tuple[bool, str]] = []
        for group_agents, group_rules in groups:
            for agent in group_agents:
                if agent == "*" or agent == token or agent in ua:
                    if best_agent is None or (agent != "*" and len(agent) > len(best_agent)):
                        best_agent, best_rules = agent, group_rules
        if best_agent is None:
            return cls(rules=[], source_agent="*")
        return cls(
            rules=[_Rule(allow=a, pattern=p, regex=_compile(p)) for a, p in best_rules],
            source_agent=best_agent,
        )

    def is_allowed(self, path: str) -> bool:
        if "://" in path:
            parts = urlsplit(path)
            path = parts.path or "/"
            if parts.query:
                path = f"{path}?{parts.query}"
        if not path.startswith("/"):
            path = "/" + path
        winner: _Rule | None = None
        for rule in self.rules:
            if not rule.regex.match(path):
                continue
            key = (len(rule.pattern), rule.allow)
            if winner is None or key > (len(winner.pattern), winner.allow):
                winner = rule
        return True if winner is None else winner.allow

    def matching_rule(self, path: str) -> _Rule | None:
        winner: _Rule | None = None
        for rule in self.rules:
            if not rule.regex.match(path):
                continue
            key = (len(rule.pattern), rule.allow)
            if winner is None or key > (len(winner.pattern), winner.allow):
                winner = rule
        return winner


def assert_allowed(policy: RobotsPolicy, url: str) -> None:
    """Raise RobotsRefusal before any request is made for a disallowed path."""
    path = urlsplit(url).path or "/"
    if not policy.is_allowed(url):
        rule = policy.matching_rule(path)
        pattern = rule.pattern if rule else "/"
        raise RobotsRefusal(
            f"robots.txt disallows {path} (rule: Disallow: {pattern})",
            hint="this client only reads paths the site permits; see spec section 2",
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_robots.py -v`
Expected: all passed. If `test_specific_user_agent_group_wins_over_star` fails, the
group-selection branch is picking `*` — the `agent != "*"` guard in the comparison is
what prevents that.

- [ ] **Step 5: Commit**

```bash
git add src/nf/robots.py tests/test_robots.py
git commit -m "feat: robots.txt policy with RFC 9309 precedence and refusal gate"
```

---

### Task 5: Usage ledger

**Files:**
- Create: `src/nf/usage.py`
- Test: `tests/test_usage.py`

**Interfaces:**
- Consumes: `nf.paths.classify_request`.
- Produces: `Ledger(state_dir: Path, clock: Callable[[], datetime] | None = None)` with
  `record(path_class: str, status: int, nbytes: int, cache: str, attempt: int = 1) -> None`,
  `entries() -> list[dict]`, `rollup(window: str, limit_ms: int) -> dict`. Window strings:
  `"1h"`, `"24h"`, `"all"`. `MAX_LEDGER_BYTES = 10 * 1024 * 1024`.

- [ ] **Step 1: Write the failing test**

`tests/test_usage.py`:

```python
from datetime import datetime, timedelta, timezone

from nf.usage import Ledger, MAX_LEDGER_BYTES

T0 = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)


def make_ledger(tmp_path, times):
    """times: list of offsets in minutes from T0."""
    it = iter(times)
    return Ledger(tmp_path, clock=lambda: T0 + timedelta(minutes=next(it)))


def test_record_writes_one_jsonl_line_per_request(tmp_path):
    led = make_ledger(tmp_path, [0, 1])
    led.record("thread", 200, 1234, "miss")
    led.record("thread", 200, 999, "hit")
    rows = led.entries()
    assert len(rows) == 2
    assert rows[0]["pathClass"] == "thread"
    assert rows[0]["bytes"] == 1234
    assert rows[1]["cache"] == "hit"


def test_ledger_never_stores_a_full_url_or_cookie(tmp_path):
    led = make_ledger(tmp_path, [0])
    led.record("resource", 200, 10, "miss")
    raw = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8")
    assert "http" not in raw
    assert "cookie" not in raw.lower()


def test_rollup_window_excludes_older_entries(tmp_path):
    led = make_ledger(tmp_path, [0, 30, 120])
    led.record("thread", 200, 100, "miss")          # 2h ago
    led.record("thread", 200, 100, "miss")          # 90m ago
    led.record("resource", 404, 0, "miss")          # now
    hour = led.rollup("1h", limit_ms=1000)
    assert hour["requests"]["total"] == 1
    assert hour["requests"]["byClass"] == {"resource": 1}
    assert hour["bytes"] == 0


def test_rollup_all_counts_everything(tmp_path):
    led = make_ledger(tmp_path, [0, 1, 2])
    led.record("thread", 200, 100, "miss")
    led.record("thread", 200, 100, "hit")
    led.record("index", 200, 50, "miss")
    allw = led.rollup("all", limit_ms=1000)
    assert allw["requests"]["total"] == 3
    assert allw["cache"] == {"hits": 1, "misses": 2}
    assert allw["bytes"] == 250
    assert allw["rate"]["limitMs"] == 1000
    assert allw["rate"]["budgetUsedMs"] == 2000


def test_rollup_counts_refusals_by_status(tmp_path):
    led = make_ledger(tmp_path, [0, 1])
    led.record("thread", 403, 0, "miss")
    led.record("thread", 200, 10, "miss")
    r = led.rollup("all", limit_ms=1000)
    assert r["refusals"]["block"] == 1


def test_last_request_at_is_reported(tmp_path):
    led = make_ledger(tmp_path, [0, 5])
    led.record("thread", 200, 10, "miss")
    led.record("thread", 200, 10, "miss")
    assert led.rollup("all", 1000)["lastRequestAt"].startswith("2026-09-11T12:05")


def test_record_refusal_is_counted_but_is_not_a_request(tmp_path):
    """A robots refusal never became a request, so it must not inflate totals."""
    led = make_ledger(tmp_path, [0, 1])
    led.record_refusal("/search/")
    led.record("thread", 200, 10, "miss")
    r = led.rollup("all", limit_ms=1000)
    assert r["refusals"]["robots"] == 1
    assert r["requests"]["total"] == 1
    assert r["cache"]["misses"] == 1


def test_rollup_on_empty_ledger_is_zeroed(tmp_path):
    led = Ledger(tmp_path, clock=lambda: T0)
    r = led.rollup("24h", limit_ms=1000)
    assert r["requests"]["total"] == 0
    assert r["lastRequestAt"] is None
    assert r["bytes"] == 0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_usage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nf.usage'`

- [ ] **Step 3: Implement `src/nf/usage.py`**

```python
"""Append-only request ledger and windowed rollups.

This reports the *client's own consumption* -- requests made, cache hit rate,
bytes, and enforced spacing -- not forum-account statistics. Records hold a
coarse path class and never a full URL or any cookie material.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable

MAX_LEDGER_BYTES = 10 * 1024 * 1024
_WINDOWS = {"1h": timedelta(hours=1), "24h": timedelta(hours=24)}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Ledger:
    def __init__(self, state_dir: Path, clock: Callable[[], datetime] | None = None) -> None:
        self.path = Path(state_dir) / "ledger.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or _utcnow

    def _rotate_if_needed(self) -> None:
        try:
            if self.path.exists() and self.path.stat().st_size > MAX_LEDGER_BYTES:
                self.path.replace(self.path.with_suffix(".jsonl.1"))
        except OSError:
            pass

    def record(self, path_class: str, status: int, nbytes: int, cache: str,
               attempt: int = 1) -> None:
        self._rotate_if_needed()
        entry = {
            "ts": self._clock().astimezone().isoformat(timespec="seconds"),
            "pathClass": path_class,
            "status": int(status),
            "bytes": int(nbytes),
            "cache": cache,
            "attempt": int(attempt),
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, separators=(",", ":")) + "\n")

    def record_refusal(self, path: str) -> None:
        """Record a path refused by robots. Not a request; never counted as one."""
        self._rotate_if_needed()
        entry = {
            "ts": self._clock().astimezone().isoformat(timespec="seconds"),
            "pathClass": "refused",
            "refusal": path,
            "status": 0,
            "bytes": 0,
            "cache": "none",
            "attempt": 0,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, separators=(",", ":")) + "\n")

    def entries(self) -> list[dict]:
        if not self.path.is_file():
            return []
        rows: list[dict] = []
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows

    def _in_window(self, row: dict, window: str) -> bool:
        if window == "all":
            return True
        delta = _WINDOWS.get(window)
        if delta is None:
            return True
        try:
            ts = datetime.fromisoformat(row["ts"])
        except (KeyError, ValueError):
            return False
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return ts >= now - delta

    def rollup(self, window: str, limit_ms: int) -> dict:
        rows = [r for r in self.entries() if self._in_window(r, window)]
        refused = [r for r in rows if r.get("refusal")]
        requests = [r for r in rows if not r.get("refusal")]
        by_class: dict[str, int] = {}
        hits = misses = 0
        total_bytes = 0
        block = login_wall = 0
        for row in requests:
            by_class[row.get("pathClass", "other")] = (
                by_class.get(row.get("pathClass", "other"), 0) + 1)
            cache = row.get("cache")
            if cache == "hit":
                hits += 1
            elif cache == "miss":
                misses += 1
            total_bytes += int(row.get("bytes", 0))
            status = int(row.get("status", 0))
            if status == 403:
                block += 1
            elif status == 401:
                login_wall += 1
        last = rows[-1]["ts"] if rows else None
        return {
            "window": window,
            "requests": {"total": len(requests), "byClass": dict(sorted(by_class.items()))},
            "cache": {"hits": hits, "misses": misses},
            "bytes": total_bytes,
            "rate": {"limitMs": int(limit_ms),
                     "budgetUsedMs": max(0, len(requests) - 1) * int(limit_ms)},
            "refusals": {"robots": len(refused), "block": block, "loginWall": login_wall},
            "lastRequestAt": last,
        }
```

Note: a robots refusal is recorded by the CLI when it catches `RobotsRefusal`, and
counted separately from requests — a refusal never became a request, so it must not
inflate `requests.total` or the cache stats. The test above pins that.

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_usage.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/nf/usage.py tests/test_usage.py
git commit -m "feat: append-only request ledger with windowed rollups"
```

---

### Task 6: HTTP client — throttle, retry, cache, block detection, gate

**Files:**
- Create: `src/nf/http.py`
- Test: `tests/test_http.py`

**Interfaces:**
- Consumes: `Config`, `RobotsPolicy`/`assert_allowed`, `Ledger`, `classify_request`,
  `EdgeBlocked`/`RateLimited`/`NetworkError`.
- Produces: `FetchResult` frozen dataclass `(url, status, text, from_cache, nbytes)`;
  `Client(cfg, ledger=None, transport=None, sleep=time.sleep, clock=time.monotonic)`
  with `get(url, *, no_cache=False, refresh=False) -> FetchResult` and
  `close()`; module constant `BLOCK_MARKERS`.

- [ ] **Step 1: Write the failing test**

`tests/test_http.py`:

```python
from pathlib import Path

import httpx
import pytest

from nf.config import Config
from nf.errors import EdgeBlocked, RateLimited, RobotsRefusal
from nf.http import Client
from nf.usage import Ledger

THREAD = "https://nullforums.net/threads/foo.1/"
ROBOTS = "https://nullforums.net/robots.txt"
OK_BODY = "<html><body><h1>ok</h1></body></html>"
ROBOTS_BODY = "User-agent: *\nDisallow: /search/\nAllow: /\n"


def cfg(tmp_path):
    return Config(
        base_url="https://nullforums.net", cookie="xf_user=SECRET",
        user_agent="nf/0.1 (read-only client; +https://example.test)",
        rate_limit_ms=1000, cache_ttl_s=900,
        cache_dir=tmp_path / "cache", state_dir=tmp_path / "state",
    )


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.slept: list[float] = []

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


def make_client(tmp_path, handler, clock=None):
    clock = clock or FakeClock()
    led = Ledger(tmp_path / "state")
    client = Client(cfg(tmp_path), ledger=led, transport=httpx.MockTransport(handler),
                    sleep=clock.sleep, clock=clock.monotonic)
    return client, clock, led


def test_gate_refuses_disallowed_before_any_request(tmp_path):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, text=ROBOTS_BODY)

    client, _, _ = make_client(tmp_path, handler)
    with pytest.raises(RobotsRefusal):
        client.get("https://nullforums.net/search/?q=x")
    assert seen == [ROBOTS]      # robots fetched, target never requested


def test_successful_fetch_returns_body(tmp_path):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_BODY)
        return httpx.Response(200, text=OK_BODY)

    client, _, led = make_client(tmp_path, handler)
    res = client.get(THREAD)
    assert res.status == 200
    assert "ok" in res.text
    assert res.from_cache is False
    assert led.entries()[0]["pathClass"] == "thread"


def test_second_fetch_comes_from_cache(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_BODY)
        return httpx.Response(200, text=OK_BODY)

    client, _, led = make_client(tmp_path, handler)
    client.get(THREAD)
    res = client.get(THREAD)
    assert res.from_cache is True
    assert calls.count("/threads/foo.1/") == 1
    assert led.entries()[-1]["cache"] == "hit"


def test_refresh_bypasses_cache(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_BODY)
        return httpx.Response(200, text=OK_BODY)

    client, _, _ = make_client(tmp_path, handler)
    client.get(THREAD)
    client.get(THREAD, refresh=True)
    assert calls.count("/threads/foo.1/") == 2


def test_throttle_spaces_consecutive_requests(tmp_path):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_BODY)
        return httpx.Response(200, text=OK_BODY)

    client, clock, _ = make_client(tmp_path, handler)
    client.get(THREAD)
    client.get(THREAD, refresh=True)
    assert 1.0 in clock.slept


def test_cookie_is_sent(tmp_path):
    seen = []

    def handler(request):
        seen.append(request.headers.get("cookie", ""))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_BODY)
        return httpx.Response(200, text=OK_BODY)

    client, _, _ = make_client(tmp_path, handler)
    client.get(THREAD)
    assert any("xf_user=SECRET" in c for c in seen)


def test_user_agent_is_sent(tmp_path):
    uas = []

    def handler(request):
        uas.append(request.headers.get("user-agent", ""))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_BODY)
        return httpx.Response(200, text=OK_BODY)

    client, _, _ = make_client(tmp_path, handler)
    client.get(THREAD)
    assert all(ua.startswith("nf/0.1") for ua in uas)


def test_403_raises_edge_blocked_and_does_not_retry(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_BODY)
        return httpx.Response(403, text="Forbidden")

    client, _, _ = make_client(tmp_path, handler)
    with pytest.raises(EdgeBlocked):
        client.get(THREAD)
    assert calls.count("/threads/foo.1/") == 1


def test_cloudflare_body_raises_edge_blocked_even_on_200(tmp_path):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_BODY)
        return httpx.Response(200, text="<title>Just a moment...</title>")

    client, _, _ = make_client(tmp_path, handler)
    with pytest.raises(EdgeBlocked):
        client.get(THREAD)


def test_500_is_retried_then_succeeds(tmp_path):
    state = {"n": 0}

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_BODY)
        state["n"] += 1
        if state["n"] < 3:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, text=OK_BODY)

    client, clock, _ = make_client(tmp_path, handler)
    res = client.get(THREAD)
    assert res.status == 200
    assert 1.0 in clock.slept and 2.0 in clock.slept


def test_persistent_500_raises_network_error(tmp_path):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_BODY)
        return httpx.Response(500, text="boom")

    client, _, _ = make_client(tmp_path, handler)
    with pytest.raises(Exception) as ei:
        client.get(THREAD)
    assert ei.value.exit_code in (6, 8)


def test_429_honors_retry_after(tmp_path):
    state = {"n": 0}

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_BODY)
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429, text="slow down", headers={"Retry-After": "3"})
        return httpx.Response(200, text=OK_BODY)

    client, clock, _ = make_client(tmp_path, handler)
    client.get(THREAD)
    assert 3.0 in clock.slept


def test_persistent_429_raises_rate_limited(tmp_path):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_BODY)
        return httpx.Response(429, text="slow down", headers={"Retry-After": "0"})

    client, _, _ = make_client(tmp_path, handler)
    with pytest.raises(RateLimited):
        client.get(THREAD)


def test_transport_error_is_wrapped_as_network_error(tmp_path):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_BODY)
        raise httpx.ConnectError("no route")

    client, _, _ = make_client(tmp_path, handler)
    with pytest.raises(Exception) as ei:
        client.get(THREAD)
    assert ei.value.exit_code == 8


def test_robots_is_cached_across_clients(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_BODY)
        return httpx.Response(200, text=OK_BODY)

    c1, _, _ = make_client(tmp_path, handler)
    c1.get(THREAD)
    c2, _, _ = make_client(tmp_path, handler)
    c2.get(THREAD)
    assert calls.count("/robots.txt") == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_http.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nf.http'`

- [ ] **Step 3: Implement `src/nf/http.py`**

```python
"""The only module that opens a socket.

Every request passes the robots gate first, so no code path can reach a
disallowed URL. Requests are serialized with a minimum spacing, capped-retry
on 5xx/429, and cached on disk. 403 and challenge pages are terminal: they
are reported, never retried, and never worked around.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from nf.config import Config
from nf.errors import EdgeBlocked, NetworkError, RateLimited
from nf.paths import classify_request
from nf.robots import RobotsPolicy, assert_allowed
from nf.usage import Ledger

MAX_ATTEMPTS = 3
MAX_REDIRECTS = 3
BACKOFF_SECONDS = (1.0, 2.0, 4.0)
ROBOTS_TTL_S = 24 * 3600
TIMEOUT_S = 20.0

BLOCK_MARKERS = ("just a moment", "attention required", "challenge-platform",
                 "enable javascript and cookies to continue", "cf-chl")


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int
    text: str
    from_cache: bool
    nbytes: int


def _looks_blocked(status: int, text: str) -> bool:
    if status in (403, 503):
        return True
    head = text[:4000].lower()
    return any(marker in head for marker in BLOCK_MARKERS)


class Client:
    def __init__(
        self,
        cfg: Config,
        ledger: Ledger | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cfg = cfg
        self.ledger = ledger
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None
        self._policy: RobotsPolicy | None = None
        self._http = httpx.Client(
            transport=transport,
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
            timeout=TIMEOUT_S,
            headers={
                "User-Agent": cfg.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            cookies=self._cookies(),
        )
        self.cfg.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cookies(self) -> dict[str, str]:
        jar: dict[str, str] = {}
        for part in (self.cfg.cookie or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name and value:
                jar[name] = value
        return jar

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- rate limiting ----

    def _throttle(self) -> None:
        now = self._clock()
        if self._last_request is not None:
            wait_ms = self.cfg.rate_limit_ms - (now - self._last_request) * 1000
            if wait_ms > 0:
                self._sleep(wait_ms / 1000)
        self._last_request = self._clock()

    # ---- cache ----

    def _cache_paths(self, url: str) -> tuple[Path, Path]:
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        base = self.cfg.cache_dir / "http"
        base.mkdir(parents=True, exist_ok=True)
        return base / f"{key}.html", base / f"{key}.meta.json"

    def _read_cache(self, url: str, ttl_s: int) -> str | None:
        body, meta = self._cache_paths(url)
        if not body.is_file() or not meta.is_file():
            return None
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if self._clock() - float(info.get("stored_at", 0)) > ttl_s:
            return None
        try:
            return body.read_text(encoding="utf-8")
        except OSError:
            return None

    def _write_cache(self, url: str, text: str) -> None:
        body, meta = self._cache_paths(url)
        body.write_text(text, encoding="utf-8")
        meta.write_text(json.dumps({"url_class": classify_request(url),
                                    "stored_at": self._clock()}),
                        encoding="utf-8")

    # ---- robots gate ----

    def _robots(self) -> RobotsPolicy:
        if self._policy is not None:
            return self._policy
        url = f"{self.cfg.base_url}/robots.txt"
        text = self._read_cache(url, ROBOTS_TTL_S)
        if text is None:
            status, text, _ = self._raw_get(url, path_class="robots")
            if status != 200:
                raise NetworkError(
                    f"could not read robots.txt (status {status})",
                    hint="refusing to make requests without knowing the site's rules")
            self._write_cache(url, text)
        self._policy = RobotsPolicy.parse(text, self.cfg.user_agent)
        return self._policy

    # ---- requests ----

    def _raw_get(self, url: str, *, path_class: str) -> tuple[int, str, int]:
        """Send one request with retries. Returns (status, text, attempt)."""
        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._throttle()
            try:
                response = self._http.get(url)
            except httpx.TooManyRedirects as exc:
                if self.ledger:
                    self.ledger.record(path_class, 0, 0, "miss", attempt)
                raise NetworkError(
                    f"too many redirects fetching {url}",
                    hint="this path issues a redirect loop; commands target deep paths",
                ) from exc
            except httpx.HTTPError as exc:
                last_error = exc
                if self.ledger:
                    self.ledger.record(path_class, 0, 0, "miss", attempt)
                if attempt < MAX_ATTEMPTS:
                    self._sleep(BACKOFF_SECONDS[attempt - 1])
                    continue
                raise NetworkError(f"request to {url} failed: {exc.__class__.__name__}") from exc

            text = response.text
            if self.ledger:
                self.ledger.record(path_class, response.status_code,
                                   len(response.content), "miss", attempt)

            if response.status_code == 429 or response.status_code >= 500:
                if attempt < MAX_ATTEMPTS:
                    retry_after = response.headers.get("Retry-After")
                    delay = (float(retry_after) if (retry_after or "").strip().isdigit()
                             else BACKOFF_SECONDS[attempt - 1])
                    self._sleep(delay)
                    continue
                if response.status_code == 429:
                    raise RateLimited(
                        f"rate limited by the site after {MAX_ATTEMPTS} attempts",
                        hint="raise rate_limit_ms in the config")
                raise NetworkError(
                    f"server error {response.status_code} after {MAX_ATTEMPTS} attempts")

            if _looks_blocked(response.status_code, text):
                raise EdgeBlocked(
                    f"request blocked at the edge (status {response.status_code})",
                    hint="the site refused this client; no bypass is attempted")

            return response.status_code, text, attempt
        raise NetworkError(f"request to {url} failed: {last_error!r}")

    def get(self, url: str, *, no_cache: bool = False, refresh: bool = False) -> FetchResult:
        assert_allowed(self._robots(), url)
        path_class = classify_request(url)

        if not no_cache and not refresh:
            cached = self._read_cache(url, self.cfg.cache_ttl_s)
            if cached is not None:
                if self.ledger:
                    self.ledger.record(path_class, 200, len(cached.encode("utf-8")), "hit")
                self._last_request = self._clock()
                return FetchResult(url, 200, cached, True, len(cached.encode("utf-8")))

        status, text, _ = self._raw_get(url, path_class=path_class)
        if status == 200:
            self._write_cache(url, text)
        nbytes = len(text.encode("utf-8"))
        return FetchResult(url, status, text, False, nbytes)
```

Design notes the implementer must preserve:

- `follow_redirects=True` with `max_redirects=3`. Redirects are followed so that
  canonicalization (slugless URLs, `?page=` normalization) works, but the cap is tight
  because the site's `/` issues a 302 loop to itself. `httpx.TooManyRedirects` is an
  `HTTPError` subclass and would otherwise be retried three times before failing; it is
  caught explicitly and reported as a terminal `NetworkError` naming the loop.
- `_raw_get` records the ledger *before* deciding to retry, so retries are visible.
- The robots fetch uses `_raw_get` directly, deliberately bypassing the gate that would
  otherwise recurse.

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_http.py -v`
Expected: 16 passed

- [ ] **Step 5: Commit**

```bash
git add src/nf/http.py tests/test_http.py
git commit -m "feat: HTTP client with robots gate, throttle, retry, cache, block detection"
```

---

### Task 7: Models, envelope, and renderers

**Files:**
- Create: `src/nf/model.py`, `src/nf/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `SCHEMA_VERSION = 1`; `envelope(payload: dict) -> dict`; dataclasses
  `Author`, `Node`, `Pagination`, `Post`, `Thread`, `CategoryRef`, `Resource`,
  `ResourceItem`, `ResourceList`, `SearchHit`; `render.render(payload: dict, fmt: str, kind: str) -> str`
  where `kind` is one of `thread`, `resource`, `category`, `search`, `usage`, `whoami`.

- [ ] **Step 1: Write the failing test**

`tests/test_render.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nf.model'`

- [ ] **Step 3: Implement `src/nf/model.py`**

```python
"""Data shapes produced by the parsers. Pure data; no I/O, no HTML."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

SCHEMA_VERSION = 1


def envelope(payload: dict) -> dict:
    return {"schemaVersion": SCHEMA_VERSION, **payload}


@dataclass
class Author:
    username: str | None = None
    userId: int | None = None
    url: str | None = None


@dataclass
class Pagination:
    page: int = 1
    pages: int = 1
    perPage: int | None = None
    total: int | None = None


@dataclass
class Post:
    id: int | None = None
    index: int = 1
    url: str | None = None
    author: Author = field(default_factory=Author)
    postedAt: str | None = None
    bodyText: str = ""
    bodyHtml: str = ""


@dataclass
class Thread:
    id: int | None = None
    url: str | None = None
    title: str = ""
    node: str | None = None
    author: Author = field(default_factory=Author)
    createdAt: str | None = None
    updatedAt: str | None = None
    tags: list[str] = field(default_factory=list)
    posts: list[Post] = field(default_factory=list)
    pagination: Pagination = field(default_factory=Pagination)


@dataclass
class CategoryRef:
    id: int | None = None
    title: str | None = None
    url: str | None = None


@dataclass
class Resource:
    id: int | None = None
    url: str | None = None
    title: str = ""
    author: Author = field(default_factory=Author)
    version: str | None = None
    tagLine: str | None = None
    description: dict = field(default_factory=lambda: {"html": "", "text": ""})
    createdAt: str | None = None
    lastUpdated: str | None = None
    category: CategoryRef | None = None
    discussionThread: dict | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class ResourceItem:
    id: int | None = None
    url: str | None = None
    title: str = ""
    author: Author = field(default_factory=Author)
    version: str | None = None
    lastUpdated: str | None = None


@dataclass
class ResourceList:
    id: int | None = None
    url: str | None = None
    title: str = ""
    items: list[ResourceItem] = field(default_factory=list)
    pagination: Pagination = field(default_factory=Pagination)


@dataclass
class SearchHit:
    type: str = ""
    id: int = 0
    slug: str = ""
    title: str = ""
    titleSource: str = "slug"
    url: str = ""
    lastmod: str | None = None
    score: float = 0.0
    resource: Resource | None = None
    thread: Thread | None = None


def to_dict(model) -> dict:
    return asdict(model)
```

- [ ] **Step 4: Implement `src/nf/render.py`**

```python
"""Model -> output string. Pure; changing a format cannot change the data."""

from __future__ import annotations

import json

from nf.errors import UsageError
from nf.model import envelope, to_dict

FORMATS = ("json", "md", "text")


def _json(payload: dict) -> str:
    return json.dumps(envelope(payload), indent=2, ensure_ascii=False)


def _thread_md(d: dict) -> str:
    lines = [f"# {d['title']}", ""]
    meta = [f"**URL:** {d['url']}"]
    if d.get("node"):
        meta.append(f"**Node:** {d['node']}")
    if (d.get("author") or {}).get("username"):
        meta.append(f"**Author:** {d['author']['username']}")
    if d.get("createdAt"):
        meta.append(f"**Created:** {d['createdAt']}")
    if d.get("tags"):
        meta.append(f"**Tags:** {', '.join(d['tags'])}")
    lines += ["  \n".join(meta), ""]
    for post in d.get("posts", []):
        who = (post.get("author") or {}).get("username") or "unknown"
        lines += [f"## Post {post['index']} - {who} - {post.get('postedAt') or ''}", "",
                  post.get("bodyText", ""), ""]
    return "\n".join(lines).rstrip() + "\n"


def _thread_text(d: dict) -> str:
    lines = [d["title"], "=" * len(d["title"]), ""]
    for post in d.get("posts", []):
        who = (post.get("author") or {}).get("username") or "unknown"
        lines += [f"[{post['index']}] {who} {post.get('postedAt') or ''}".rstrip(),
                  post.get("bodyText", ""), ""]
    return "\n".join(lines).rstrip() + "\n"


def _resource_md(d: dict) -> str:
    lines = [f"# {d['title']}", ""]
    meta = []
    if (d.get("author") or {}).get("username"):
        meta.append(f"**Author:** {d['author']['username']}")
    if d.get("version"):
        meta.append(f"**Version:** {d['version']}")
    if (d.get("category") or {}).get("title"):
        meta.append(f"**Category:** {d['category']['title']}")
    if d.get("lastUpdated"):
        meta.append(f"**Last updated:** {d['lastUpdated']}")
    if d.get("tags"):
        meta.append(f"**Tags:** {', '.join(d['tags'])}")
    lines += ["  \n".join(meta), "", d.get("description", {}).get("text", "")]
    return "\n".join(lines).rstrip() + "\n"


def _category_md(d: dict) -> str:
    lines = [f"# {d['title']}", ""]
    for item in d.get("items", []):
        who = (item.get("author") or {}).get("username") or ""
        ver = f" - {item['version']}" if item.get("version") else ""
        lines.append(f"- [{item['title']}]({item['url']}){ver}{' - ' + who if who else ''}")
    return "\n".join(lines).rstrip() + "\n"


def _search_md(d: dict) -> str:
    lines = [f"# Search: {d.get('query', '')}", ""]
    for hit in d.get("hits", []):
        lines.append(f"- `{hit['type']}` [{hit['title']}]({hit['url']}) "
                     f"({hit.get('lastmod') or 'no date'})")
    return "\n".join(lines).rstrip() + "\n"


def _usage_md(d: dict) -> str:
    lines = [f"# Usage ({d['window']})", "",
             f"- Requests: {d['requests']['total']}",
             f"- By class: {d['requests']['byClass']}",
             f"- Cache: {d['cache']}",
             f"- Bytes: {d['bytes']}",
             f"- Rate: {d['rate']}",
             f"- Refusals: {d['refusals']}",
             f"- Last request: {d['lastRequestAt']}"]
    return "\n".join(lines) + "\n"


def _flat_text(d: dict) -> str:
    return json.dumps(d, indent=2, ensure_ascii=False) + "\n"


_MD = {"thread": _thread_md, "resource": _resource_md, "category": _category_md,
       "search": _search_md, "usage": _usage_md}
_TEXT = {"thread": _thread_text}


def render(payload, fmt: str, kind: str) -> str:
    if fmt not in FORMATS:
        raise UsageError(f"unknown format {fmt!r}",
                         hint=f"choose one of {', '.join(FORMATS)}")
    data = payload if isinstance(payload, dict) else to_dict(payload)
    if fmt == "json":
        return _json(data)
    if fmt == "md":
        fn = _MD.get(kind)
        return fn(data) if fn else _flat_text(data)
    fn = _TEXT.get(kind)
    return fn(data) if fn else _flat_text(data)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_render.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add src/nf/model.py src/nf/render.py tests/test_render.py
git commit -m "feat: data models, schema envelope, and renderers"
```

---

### Task 8: Shared parsing primitives

**Files:**
- Create: `src/nf/parse/__init__.py`, `src/nf/parse/page.py`
- Test: `tests/test_parse_page.py`

**Interfaces:**
- Consumes: `selectolax.parser.HTMLParser`, `nf.errors.AuthRequired`/`ParseFailure`.
- Produces: `tree(html) -> HTMLParser`; `clean_text(s) -> str`;
  `iso_time(dt: str | None) -> str | None`; `title_of(t, remove=()) -> str`;
  `breadcrumbs(t) -> list[str]`; `node_of(t) -> str | None`;
  `pagination(t) -> Pagination`; `scrub_attachments(fragment: str) -> str`;
  `detect_block(html: str) -> bool`; `detect_auth_wall(html: str) -> bool`;
  `require(cond, selector) -> None` (raises `ParseFailure` naming the selector).

- [ ] **Step 1: Capture the fixtures**

```bash
cd /home/jlo/dev/nullforumscli
mkdir -p tests/fixtures
UA='Mozilla/5.0 (compatible; nf-fixture-capture)'
cp /tmp/nf_thread.html tests/fixtures/thread-89951.html
cp /tmp/nf_res.html    tests/fixtures/resource-8953.html
cp /tmp/nf_cat.html    tests/fixtures/category-38.html
shasum -a 256 tests/fixtures/*.html
```

Record the shasums in `tests/fixtures/README.md` along with the source URL and fetch
date for each file. Fixtures are the ground truth for the parsers; a silent change to
them must be visible in review.

- [ ] **Step 2: Add the two synthetic fixtures**

The site cannot be asked for these, because producing a real login wall or a real edge
block requires either an unauthorized request or a cookie we do not have in tests. Both
are therefore **constructed**, and their filenames say so.

`tests/fixtures/SYNTHETIC-login-wall.html`:

```html
<!DOCTYPE html><html><head><title>NullForums</title></head><body>
<div class="p-body">
  <div class="p-body-main p-body-main--empty">
    <div class="blockMessage blockMessage--error">
      You must log in or register to view this content.
    </div>
    <form action="/login/login" method="post"><input name="_xfToken" value="x"></form>
  </div>
</div></body></html>
```

`tests/fixtures/SYNTHETIC-edge-block.html`:

```html
<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>
<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1"></script>
</body></html>
```

- [ ] **Step 3: Write the failing test**

`tests/test_parse_page.py`:

```python
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
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_parse_page.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nf.parse'`

- [ ] **Step 5: Implement `src/nf/parse/page.py`**

```python
"""Selectors and helpers shared by the page parsers.

Theme note: the site runs a bespoke ``kz-*`` theme over XenForo 2. Selectors
here use XenForo's own hooks (``.p-title-value``, ``time.u-dt``,
``article.message[data-content]``, ``.structItem-cell--main``) and never
theme-specific classes, so a theme restyle does not break parsing.
"""

from __future__ import annotations

import re

from selectolax.parser import HTMLParser

from nf.errors import AuthRequired, ParseFailure
from nf.model import Pagination

# Elements that may carry an attachment or download reference. Removed from
# any HTML we emit -- the parser must never surface attachment URLs.
_ATTACHMENT_SELECTORS = ("a", "img", "source", "video", "audio", "object", "embed")

_BLOCK_MARKERS = ("just a moment", "attention required", "challenge-platform",
                  "enable javascript and cookies to continue")

_AUTH_MARKERS = ("you must log in", "log in or register", "you do not have permission",
                 "insufficient privileges", "you must be a registered member")


def tree(html: str) -> HTMLParser:
    return HTMLParser(html)


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def iso_time(dt: str | None) -> str | None:
    if not dt:
        return None
    dt = dt.strip()
    if not dt:
        return None
    if dt.endswith("Z"):
        return dt[:-1] + "+00:00"
    return re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", dt)


def title_of(t: HTMLParser, remove: tuple[str, ...] = ()) -> str:
    node = t.css_first(".p-title-value") or t.css_first("h1")
    if node is None:
        title = t.css_first("title")
        return clean_text(title.text() if title else "")
    for selector in remove:
        for child in node.css(selector):
            child.decompose()
    return clean_text(node.text())


def breadcrumbs(t: HTMLParser) -> list[str]:
    """First breadcrumb block only; the theme renders it twice."""
    block = t.css_first(".p-breadcrumbs")
    if block is None:
        return []
    out = []
    for item in block.css("li a"):
        text = clean_text(item.text())
        if text and (not out or out[-1] != text):
            out.append(text)
    return out


def node_of(t: HTMLParser) -> str | None:
    crumbs = breadcrumbs(t)
    for candidate in reversed(crumbs):
        if candidate.lower() != "home":
            return candidate
    return None


def pagination(t: HTMLParser) -> Pagination:
    nav = t.css_first(".pageNav")
    if nav is None:
        return Pagination()
    page = 1
    pages = 1
    current = nav.css_first(".pageNav-page--current a")
    if current is not None:
        text = clean_text(current.text())
        if text.isdigit():
            page = int(text)
    for link in nav.css(".pageNav-page a"):
        text = clean_text(link.text())
        if text.isdigit():
            pages = max(pages, int(text))
    return Pagination(page=page, pages=pages)


def require(condition, selector: str, hint: str | None = None) -> None:
    if not condition:
        raise ParseFailure(
            f"expected element {selector!r} was not found",
            hint=hint or "the page shape may have changed; try nf raw <url>",
        )


def _body_inner(t: HTMLParser) -> str:
    html = (t.body.html if t.body else None) or ""
    if html.startswith("<body>") and html.endswith("</body>"):
        return html[len("<body>"):-len("</body>")]
    return html


def scrub_attachments(fragment: str) -> str:
    """Strip anything that could name an attachment or download.

    Enforces the spec's hard boundary mechanically: no attachment URL can
    reach a model, so the tool cannot be turned into a downloader.
    """
    if not fragment or not fragment.strip():
        return ""
    parser = HTMLParser(fragment)
    for selector in _ATTACHMENT_SELECTORS:
        for node in parser.css(selector):
            attrs = node.attributes or {}
            haystack = " ".join(str(v) for v in attrs.values())
            if "/attachments/" in haystack or "download" in (attrs.get("class") or ""):
                node.decompose()
    for node in parser.css("div, span, li, dl"):
        classes = (node.attributes or {}).get("class") or ""
        if "attachment" in classes or "downloadButton" in classes:
            node.decompose()
    return _body_inner(parser)


def detect_block(html: str) -> bool:
    head = html[:4000].lower()
    return any(marker in head for marker in _BLOCK_MARKERS)


def detect_auth_wall(html: str) -> bool:
    """A real login wall, not the JS-disabled notices every page carries.

    The theme puts ``.blockMessage`` on every page (a JavaScript warning, a
    browser warning, the share bar). Detection therefore requires an *error*
    block whose text is about logging in or permissions.
    """
    parser = HTMLParser(html)
    for node in parser.css(".blockMessage"):
        classes = (node.attributes or {}).get("class") or ""
        if "error" not in classes:
            continue
        text = clean_text(node.text()).lower()
        if any(marker in text for marker in _AUTH_MARKERS):
            return True
    if parser.css_first('form[action="/login/login"]') is not None:
        return True
    return False


def raise_if_walled(html: str) -> None:
    if detect_auth_wall(html):
        raise AuthRequired(
            "the site returned a login wall for this URL",
            hint="supply a valid session cookie via NF_COOKIE or the config file",
        )
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_parse_page.py -v`
Expected: 17 passed

- [ ] **Step 7: Commit**

```bash
git add src/nf/parse tests/fixtures tests/test_parse_page.py
git commit -m "feat: shared parsing primitives with attachment scrubbing and wall detection"
```

---

### Task 9: Thread parser

**Files:**
- Create: `src/nf/parse/thread.py`; Modify: `src/nf/parse/__init__.py`
- Test: `tests/test_parse_thread.py`

**Interfaces:**
- Consumes: `nf.parse.page` helpers, `nf.model.Thread`/`Post`/`Author`.
- Produces: `parse_thread(html: str, url: str) -> Thread`.

- [ ] **Step 1: Write the failing test**

`tests/test_parse_thread.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_parse_thread.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nf.parse.thread'`

- [ ] **Step 3: Implement `src/nf/parse/thread.py`**

```python
"""Thread page -> Thread."""

from __future__ import annotations

import re

from nf.errors import ParseFailure
from nf.model import Author, Pagination, Post, Thread
from nf.parse.page import (breadcrumbs, clean_text, iso_time, node_of, pagination,
                           require, scrub_attachments, title_of, tree)

_ID_IN_URL = re.compile(r"\.(\d+)(?:/|$)")
_POST_CONTENT = re.compile(r"post-(\d+)")


def _id_from_url(url: str) -> int | None:
    m = _ID_IN_URL.search(url)
    return int(m.group(1)) if m else None


def _author_of(article, base_url: str) -> Author:
    username = article.attributes.get("data-author")
    user_id = None
    url = None
    link = article.css_first(".message-name a, .message-userDetails a.username")
    if link is not None:
        username = username or clean_text(link.text())
        raw_id = (link.attributes or {}).get("data-user-id")
        if raw_id and str(raw_id).isdigit():
            user_id = int(raw_id)
        href = (link.attributes or {}).get("href")
        if href:
            url = href if href.startswith("http") else base_url + href
    return Author(username=clean_text(username) or None, userId=user_id, url=url)


def parse_posts(t, url: str, base_url: str) -> list[Post]:
    posts: list[Post] = []
    for index, article in enumerate(t.css("article.message"), start=1):
        attrs = article.attributes or {}
        raw_id = attrs.get("data-content") or ""
        m = _POST_CONTENT.match(raw_id)
        post_id = int(m.group(1)) if m else None
        when = article.css_first("time.u-dt")
        body = article.css_first(".message-body .bbWrapper") or article.css_first(".bbWrapper")
        permalink = article.css_first('.message-attribution-opposite a[href*="/post-"]')
        href = (permalink.attributes or {}).get("href") if permalink is not None else None
        if href:
            post_url = href if href.startswith("http") else base_url + href
        else:
            post_url = f"{url}#post-{post_id}" if post_id else None
        posts.append(Post(
            id=post_id,
            index=index,
            url=post_url,
            author=_author_of(article, base_url),
            postedAt=iso_time((when.attributes or {}).get("datetime") if when else None),
            bodyText=clean_text(body.text()) if body is not None else "",
            bodyHtml=scrub_attachments(body.html or "") if body is not None else "",
        ))
    return posts


def parse_thread(html: str, url: str, base_url: str = "https://nullforums.net") -> Thread:
    if not html or not html.strip():
        raise ParseFailure("empty response body", hint="try --refresh")
    t = tree(html)
    posts = parse_posts(t, url, base_url)
    require(posts, "article.message", hint="no posts found; a login wall or a changed theme")

    node = node_of(t)
    page = pagination(t)
    if not isinstance(page, Pagination):
        page = Pagination()

    first = t.css_first("time.u-dt")
    return Thread(
        id=_id_from_url(url),
        url=url,
        title=title_of(t),
        node=node,
        author=posts[0].author,
        createdAt=posts[0].postedAt,
        updatedAt=iso_time((first.attributes or {}).get("datetime")) if first else None,
        tags=[clean_text(a.text()) for a in t.css(".tagList a")],
        posts=posts,
        pagination=page,
    )
```

Note: `createdAt` is the first post's timestamp and `updatedAt` is the thread's first
`time.u-dt`, which on this theme is the same element. Both are kept because the resource
page distinguishes them and a consumer should not have to care which page it came from.
Task 12's live run confirms the values are sensible.

- [ ] **Step 4: Write `src/nf/parse/__init__.py`**

```python
"""Page parsers. Pure: HTML in, model out, no I/O."""

from nf.parse.thread import parse_thread

__all__ = ["parse_thread"]
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_parse_thread.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add src/nf/parse/thread.py src/nf/parse/__init__.py tests/test_parse_thread.py
git commit -m "feat: thread page parser"
```

---

### Task 10: Resource and category parsers

**Files:**
- Create: `src/nf/parse/resource.py`, `src/nf/parse/listing.py`;
  Modify: `src/nf/parse/__init__.py`
- Test: `tests/test_parse_resource.py`, `tests/test_parse_listing.py`

**Interfaces:**
- Consumes: `nf.parse.page`, `nf.model`.
- Produces: `parse_resource(html: str, url: str, base_url="https://nullforums.net") -> Resource`;
  `parse_listing(html: str, url: str, base_url="https://nullforums.net") -> ResourceList`.

- [ ] **Step 1: Write the failing tests**

`tests/test_parse_resource.py`:

```python
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
```

`tests/test_parse_listing.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_parse_resource.py tests/test_parse_listing.py -v`
Expected: FAIL — modules not found

- [ ] **Step 3: Implement `src/nf/parse/resource.py`**

```python
"""Resource (XFRM) page -> Resource."""

from __future__ import annotations

import re

from nf.errors import ParseFailure
from nf.model import Author, CategoryRef, Resource
from nf.parse.page import (breadcrumbs, clean_text, iso_time, require,
                           scrub_attachments, title_of, tree)

_ID_IN_URL = re.compile(r"\.(\d+)(?:/|$)")

# The resource title element also contains a prefix label, the version, and
# the tagline. Decompose those before reading the title text.
_TITLE_NOISE = (".label", ".label-append", ".u-muted", ".structItem-resourceTagLine")


def parse_resource(html: str, url: str, base_url: str = "https://nullforums.net") -> Resource:
    if not html or not html.strip():
        raise ParseFailure("empty response body", hint="try --refresh")
    t = tree(html)

    title_node = t.css_first(".p-title-value")
    require(title_node is not None, ".p-title-value")
    version_node = title_node.css_first(".u-muted span") or title_node.css_first(".u-muted")
    version = clean_text(version_node.text()) if version_node is not None else None

    title = title_of(t, remove=_TITLE_NOISE)
    require(bool(title), ".p-title-value (title text)")

    author = Author()
    desc = t.css_first(".p-description")
    if desc is not None:
        link = desc.css_first("a.username")
        if link is not None:
            author = Author(
                username=clean_text(link.text()) or None,
                userId=int(link.attributes["data-user-id"])
                if str((link.attributes or {}).get("data-user-id", "")).isdigit() else None,
                url=(lambda h: h if h.startswith("http") else base_url + h)(
                    (link.attributes or {}).get("href") or ""),
            )

    category = None
    crumbs = [c for c in breadcrumbs(t) if c.lower() != "home"]
    if crumbs:
        category = CategoryRef(id=None, title=crumbs[-1], url=None)

    body = t.css_first(".resourceBody") or t.css_first(".js-resourceBody")
    body_html = ""
    body_text = ""
    if body is not None:
        content = body.css_first(".bbWrapper") or body
        body_html = scrub_attachments(content.html or "")
        body_text = clean_text(content.text())

    times = [iso_time((n.attributes or {}).get("datetime")) for n in t.css("time.u-dt")]
    times = [x for x in times if x]
    created = times[0] if times else None
    updated = times[-1] if times else None

    thread = None
    for link in t.css('a[href*="/threads/"]'):
        href = (link.attributes or {}).get("href") or ""
        m = re.search(r"\.(\d+)", href)
        if m:
            thread = {"id": int(m.group(1)),
                      "url": href if href.startswith("http") else base_url + href}
            break

    id_match = _ID_IN_URL.search(url)
    return Resource(
        id=int(id_match.group(1)) if id_match else None,
        url=url,
        title=title,
        author=author,
        version=version,
        tagLine=clean_text((t.css_first(".structItem-resourceTagLine") or _null()).text())
        if t.css_first(".structItem-resourceTagLine") is not None else None,
        description={"html": body_html, "text": body_text},
        createdAt=created,
        lastUpdated=updated,
        category=category,
        discussionThread=thread,
        tags=[clean_text(a.text()) for a in t.css(".tagList a")],
    )


class _null:
    @staticmethod
    def text() -> str:
        return ""
```

Placement note: `_null` is a small sentinel used to keep the `tagLine` expression from
needing a second lookup. If the reviewer prefers, assign
`tagline_node = t.css_first(".structItem-resourceTagLine")` once and use
`clean_text(tagline_node.text()) if tagline_node else None` — that is clearer and should
be preferred over `_null` if there is any doubt.

- [ ] **Step 4: Implement `src/nf/parse/listing.py`**

```python
"""Resource category page -> ResourceList."""

from __future__ import annotations

import re

from nf.errors import ParseFailure
from nf.model import Author, Pagination, ResourceItem, ResourceList
from nf.parse.page import clean_text, iso_time, pagination as read_pagination, require, tree

_ID_IN_URL = re.compile(r"\.(\d+)(?:/|$)")


def _item_of(cell, base_url: str) -> ResourceItem | None:
    title_link = None
    for link in cell.css(".structItem-title a"):
        classes = (link.attributes or {}).get("class") or ""
        if "labelLink" in classes:
            continue
        if (link.attributes or {}).get("href"):
            title_link = link
            break
    if title_link is None:
        return None

    href = title_link.attributes.get("href") or ""
    url = href if href.startswith("http") else base_url + href
    id_match = _ID_IN_URL.search(href)

    version_node = cell.css_first(".structItem-title .u-muted")
    author = Author()
    link = cell.css_first(".structItem-minor .username") or cell.css_first(".username")
    if link is not None:
        raw_id = (link.attributes or {}).get("data-user-id")
        author_href = (link.attributes or {}).get("href")
        author = Author(
            username=clean_text(link.text()) or None,
            userId=int(raw_id) if str(raw_id or "").isdigit() else None,
            url=(author_href if author_href and author_href.startswith("http")
                 else (base_url + author_href) if author_href else None),
        )

    when = cell.css_first("time.u-dt")
    return ResourceItem(
        id=int(id_match.group(1)) if id_match else None,
        url=url,
        title=clean_text(title_link.text()),
        author=author,
        version=clean_text(version_node.text()) if version_node is not None else None,
        lastUpdated=iso_time((when.attributes or {}).get("datetime")) if when else None,
    )


def parse_listing(html: str, url: str, base_url: str = "https://nullforums.net") -> ResourceList:
    if not html or not html.strip():
        raise ParseFailure("empty response body", hint="try --refresh")
    t = tree(html)
    cells = t.css(".structItem-cell--main")
    require(cells, ".structItem-cell--main", hint="no resource items found on this page")

    items = [item for item in (_item_of(c, base_url) for c in cells) if item is not None]
    require(items, ".structItem-title a", hint="items present but no titles parsed")

    heading = t.css_first(".p-title-value") or t.css_first("h1")
    id_match = _ID_IN_URL.search(url)
    page = read_pagination(t)
    if not isinstance(page, Pagination):
        page = Pagination()
    return ResourceList(
        id=int(id_match.group(1)) if id_match else None,
        url=url,
        title=clean_text(heading.text()) if heading is not None else "",
        items=items,
        pagination=page,
    )
```

- [ ] **Step 5: Update `src/nf/parse/__init__.py`**

```python
"""Page parsers. Pure: HTML in, model out, no I/O."""

from nf.parse.listing import parse_listing
from nf.parse.resource import parse_resource
from nf.parse.thread import parse_thread

__all__ = ["parse_listing", "parse_resource", "parse_thread"]
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_parse_resource.py tests/test_parse_listing.py -v`
Expected: all passed. If the resource title assertion fails because the version is
missing, inspect `title_node.css_first(".u-muted")` — the version lives in a `<span>`
nested inside `.u-muted` on this theme.

- [ ] **Step 7: Commit**

```bash
git add src/nf/parse tests/test_parse_resource.py tests/test_parse_listing.py
git commit -m "feat: resource and category listing parsers"
```

---

### Task 11: Search index

**Files:**
- Create: `src/nf/index.py`
- Test: `tests/test_index.py`

**Interfaces:**
- Consumes: `Client`, `nf.paths.parse_doc_url`/`slug_to_title`/`doc_url`, `nf.config.Config`.
- Produces: `shard_urls(index_xml: str) -> list[tuple[str, str | None]]`;
  `iter_shard(xml_text: str) -> Iterator[dict]`; `Index(db_path)` with
  `upsert_many(rows)`, `count()`, `search(query, types, since, limit) -> list[dict]`,
  `shard_state() -> dict[str, str]`, `set_shard_state(url, lastmod)`;
  `build(client, cfg, db_path, rebuild=False, progress=None) -> dict`;
  `open_index(cfg) -> Index`.

- [ ] **Step 1: Write the failing test**

`tests/test_index.py`:

```python
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
        assert row["id"] >= 0
        assert row["title"]
        assert row["url"].startswith("https://nullforums.net/")
    assert by_type.get("thread", 0) > 0
    assert by_type.get("resource", 0) > 0


def test_iter_shard_skips_category_listings():
    xml = (FIX / "sitemap-shard.xml").read_text(encoding="utf-8")
    for row in iter_shard(xml):
        assert not row["slug"].startswith("categories/")


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


def test_shard_state_roundtrip(tmp_path):
    idx = Index(tmp_path / "i.sqlite")
    assert idx.shard_state() == {}
    idx.set_shard_state("https://nullforums.net/sitemap-1.xml", "2026-09-11T05:37:57+00:00")
    assert idx.shard_state() == {
        "https://nullforums.net/sitemap-1.xml": "2026-09-11T05:37:57+00:00"}
```

- [ ] **Step 2: Create `tests/fixtures/sitemap-shard.xml`**

Trim the captured `/tmp/nf_sm1.xml` to a representative slice so the fixture stays small
and reviewable, keeping real entries and their real `lastmod` values:

```bash
cd /home/jlo/dev/nullforumscli
{
  printf '<?xml version="1.0" encoding="UTF-8"?>\n'
  printf '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
  python3 - <<'PY'
import re
raw = open('/tmp/nf_sm1.xml', encoding='utf-8', errors='replace').read()
entries = re.findall(r'<url>.*?</url>', raw, re.S)
wanted = []
for e in entries:
    if len(wanted) >= 12:
        break
    if '/resources/categories/' in e:
        continue            # listing pages are filtered out of the index anyway
    if re.search(r'/(threads|resources|tags|forums)/', e):
        wanted.append(e)
print("\n".join(wanted))
PY
  printf '</urlset>\n'
} > tests/fixtures/sitemap-shard.xml
wc -l tests/fixtures/sitemap-shard.xml
```

Verify the fixture contains at least one `threads/`, one `resources/`, and one `tags/`
entry, and that no `lastmod` was altered.

- [ ] **Step 3: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_index.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nf.index'`

- [ ] **Step 4: Implement `src/nf/index.py`**

```python
"""Sitemap-backed search index.

The site's ``/search/`` is robots-disallowed and is a POST form, so search
runs against a local index of the site's own sitemap. Slugs carry titles,
so title search is exact-ish; post bodies are not indexed, and callers must
not pretend otherwise.
"""

from __future__ import annotations

import re
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, Iterable, Iterator

from nf.config import Config
from nf.errors import ParseFailure
from nf.paths import DocRef, doc_url, parse_doc_url, slug_to_title

SITEMAP_INDEX = "/sitemap.xml"
_SM = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
INDEXABLE_TYPES = ("thread", "resource", "tag", "forum")
DEFAULT_SEARCH_TYPES = ("thread", "resource")

SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(
    type UNINDEXED, id UNINDEXED, slug UNINDEXED, title, url UNINDEXED,
    lastmod UNINDEXED, tokenize='porter unicode61'
);
CREATE TABLE IF NOT EXISTS shards (url TEXT PRIMARY KEY, lastmod TEXT);
"""


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
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ParseFailure(f"could not parse a sitemap shard: {exc}") from exc
    for node in root.iter(f"{_SM}url"):
        loc = (node.findtext(f"{_SM}loc") or "").strip()
        if not loc:
            continue
        ref = parse_doc_url(loc)
        if ref is None or ref.type not in INDEXABLE_TYPES:
            continue
        if ref.type == "resource" and ref.slug.startswith("categories/"):
            continue
        yield {
            "type": ref.type,
            "id": ref.id,
            "slug": ref.slug,
            "title": slug_to_title(ref.slug.split("/")[-1]),
            "url": loc,
            "lastmod": (node.findtext(f"{_SM}lastmod") or "").strip() or None,
        }


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
            self.conn.execute("DELETE FROM docs WHERE type = ? AND id = ?",
                              (row["type"], row["id"]))
            self.conn.execute(
                "INSERT INTO docs (type, id, slug, title, url, lastmod) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (row["type"], row["id"], row["slug"], row["title"],
                 row["url"], row["lastmod"]))
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
            except sqlite3.OperationalError:
                return []
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
        status, text, _ = client._raw_get(f"{cfg.base_url}{SITEMAP_INDEX}",
                                          path_class="index")
        if status != 200:
            raise ParseFailure(f"sitemap index returned status {status}")
        shards = shard_urls(text)
        known = index.shard_state()
        fetched = skipped = 0
        total = 0
        for url, lastmod in shards:
            if not rebuild and known.get(url) == (lastmod or ""):
                skipped += 1
                continue
            say(f"fetching {url}")
            st, body, _ = client._raw_get(url, path_class="index")
            if st != 200:
                raise ParseFailure(f"sitemap shard {url} returned status {st}")
            rows = list(iter_shard(body))
            index.upsert_many(rows)
            index.set_shard_state(url, lastmod)
            fetched += 1
            total += len(rows)
        return {"shards": len(shards), "fetched": fetched, "skipped": skipped,
                "indexed": total, "docs": index.count()}
    finally:
        index.close()
```

Note: `build` calls `client._raw_get` directly. `/sitemap.xml` is not in the robots
disallow list, so the gate would pass it anyway; bypassing avoids re-reading robots seven
times for what is a single logical operation. The CLI's `nf index` runs the gate once up
front via `assert_allowed` to keep the invariant visible.

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_index.py -v`
Expected: 12 passed

- [ ] **Step 6: Commit**

```bash
git add src/nf/index.py tests/test_index.py tests/fixtures/sitemap-shard.xml
git commit -m "feat: sitemap-backed FTS5 search index"
```

---

### Task 12: CLI, README, and live verification

**Files:**
- Create: `src/nf/cli.py`
- Modify: `README.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `main() -> None` (console-script entry), `app` (typer.Typer).
  Commands: `thread`, `resource`, `category`, `whoami`, `raw`, `index`, `search`, `usage`.

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:

```python
import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

import nf.http
from nf.cli import app

FIX = Path(__file__).parent / "fixtures"
ROBOTS = "User-agent: *\nDisallow: /search/\nAllow: /\n"
THREAD_URL = "https://nullforums.net/threads/trending-and-latest-posts-api.89951/"


def fixture(name):
    return (FIX / name).read_text(encoding="utf-8", errors="replace")


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    def handler(request):
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS)
        if path.startswith("/threads/"):
            return httpx.Response(200, text=fixture("thread-89951.html"))
        if path.startswith("/resources/categories/"):
            return httpx.Response(200, text=fixture("category-38.html"))
        if path.startswith("/resources/"):
            return httpx.Response(200, text=fixture("resource-8953.html"))
        return httpx.Response(404, text="nope")

    transport = httpx.MockTransport(handler)
    real_init = nf.http.Client.__init__

    def patched(self, cfg, ledger=None, transport=None, **kw):
        real_init(self, cfg, ledger=ledger, transport=transport or globals_transport, **kw)

    globals_transport = transport
    monkeypatch.setattr(nf.http.Client, "__init__", patched)
    monkeypatch.setenv("NF_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("NF_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("NF_COOKIE", "xf_user=SECRET")
    monkeypatch.setenv("NF_BASE_URL", "https://nullforums.net")
    return tmp_path


def test_thread_json_output(cli_env):
    result = CliRunner().invoke(app, ["thread", THREAD_URL])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["schemaVersion"] == 1
    assert data["title"] == "Trending and latest posts API"


def test_resource_json_output(cli_env):
    result = CliRunner().invoke(app, ["resource", "--id", "8953"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["title"] == "AdvancedKits"


def test_category_json_output(cli_env):
    result = CliRunner().invoke(app, ["category", "--id", "38"])
    assert result.exit_code == 0, result.output
    assert len(json.loads(result.stdout)["items"]) == 30


def test_robots_refusal_exits_5(cli_env):
    result = CliRunner().invoke(app, ["raw", "https://nullforums.net/search/?q=x"])
    assert result.exit_code == 5
    err = json.loads(result.stderr)
    assert err["error"]["code"] == "ROBOTS_DISALLOWED"


def test_usage_reports_requests(cli_env):
    CliRunner().invoke(app, ["thread", THREAD_URL])
    result = CliRunner().invoke(app, ["usage", "--window", "all"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["requests"]["total"] >= 1


def test_cookie_never_appears_in_output(cli_env):
    result = CliRunner().invoke(app, ["thread", THREAD_URL])
    assert "SECRET" not in result.stdout
    assert "SECRET" not in result.stderr


def test_unknown_format_exits_2(cli_env):
    result = CliRunner().invoke(app, ["thread", THREAD_URL, "--format", "yaml"])
    assert result.exit_code == 2


def test_markdown_format(cli_env):
    result = CliRunner().invoke(app, ["thread", THREAD_URL, "--format", "md"])
    assert result.exit_code == 0
    assert result.stdout.startswith("# Trending and latest posts API")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nf.cli'`

- [ ] **Step 3: Implement `src/nf/cli.py`**

```python
"""Command line interface. The only place errors become exit codes."""

from __future__ import annotations

import functools
import json
import sys

import typer

from nf.config import Config, load_config
from nf.errors import NfError, RobotsRefusal, UsageError
from nf.http import Client
from nf.index import DEFAULT_SEARCH_TYPES, SITEMAP_INDEX, build, open_index
from nf.model import to_dict
from nf.parse import parse_listing, parse_resource, parse_thread
from nf.paths import classify_request
from nf.render import render
from nf.robots import assert_allowed
from nf.usage import Ledger

app = typer.Typer(add_completion=False, help="Headless read-only reader for nullforums.net.")

FORMAT_HELP = "Output format: json (default), md, text."
_robots_refusals = 0


def _web(url_or_id: str, kind: str, base_url: str) -> str:
    """Accept a full URL, a path, or a bare id."""
    if url_or_id.startswith("http") or url_or_id.startswith("/"):
        return url_or_id if url_or_id.startswith("http") else base_url + url_or_id
    if not url_or_id.isdigit():
        raise UsageError(f"{url_or_id!r} is not a URL, path, or numeric id")
    prefix = {"thread": "threads", "resource": "resources", "category": "resources/categories"}[kind]
    return f"{base_url}/{prefix}/x.{url_or_id}/"


@functools.lru_cache(maxsize=1)
def _config() -> Config:
    return load_config()


def _emit(payload, fmt: str, kind: str) -> None:
    sys.stdout.write(render(payload, fmt, kind))


def _run(fn):
    """Turn NfError into a JSON stderr message and the mapped exit code."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        cfg = _config()
        try:
            return fn(*args, **kwargs)
        except NfError as exc:
            message = cfg.redact(exc.message)
            hint = cfg.redact(exc.hint) if exc.hint else None
            err = NfError(message, hint=hint)
            err.code = exc.code
            sys.stderr.write(json.dumps(err.to_dict()) + "\n")
            raise typer.Exit(exc.exit_code)
    return wrapper


def _client(cfg: Config) -> Client:
    return Client(cfg, ledger=Ledger(cfg.state_dir))


@app.command()
@_run
def thread(url: str = typer.Argument(None, help="Thread URL, path, or id"),
           id: str = typer.Option(None, "--id", help="Thread id"),
           page: int = typer.Option(1, "--page"),
           all_pages: bool = typer.Option(False, "--all"),
           format: str = typer.Option("json", "--format", help=FORMAT_HELP)) -> None:
    """Read one thread and its posts."""
    cfg = _config()
    target = _web(url or id or "", "thread", cfg.base_url)
    if page > 1:
        sep = "&" if "?" in target else "?"
        target = f"{target}{sep}page={page}"
    with _client(cfg) as client:
        first = client.get(target)
        parsed = parse_thread(first.text, target, cfg.base_url)
        if all_pages and parsed.pagination.pages > 1:
            for n in range(2, parsed.pagination.pages + 1):
                sep = "&" if "?" in target else "?"
                extra = client.get(f"{target}{sep}page={n}")
                more = parse_thread(extra.text, target, cfg.base_url)
                for offset, post in enumerate(more.posts):
                    post.index = len(parsed.posts) + offset + 1
                parsed.posts.extend(more.posts)
        _emit(parsed, format, "thread")


@app.command()
@_run
def resource(id: str = typer.Option(None, "--id"),
             url: str = typer.Argument(None),
             format: str = typer.Option("json", "--format", help=FORMAT_HELP)) -> None:
    """Read one resource: title, author, version, description."""
    cfg = _config()
    target = _web(url or id or "", "resource", cfg.base_url)
    with _client(cfg) as client:
        res = client.get(target)
        _emit(parse_resource(res.text, target, cfg.base_url), format, "resource")


@app.command()
@_run
def category(id: str = typer.Option(None, "--id"),
             url: str = typer.Argument(None),
             page: int = typer.Option(1, "--page"),
             format: str = typer.Option("json", "--format", help=FORMAT_HELP)) -> None:
    """List the resources in a category."""
    cfg = _config()
    target = _web(url or id or "", "category", cfg.base_url)
    if page > 1:
        sep = "&" if "?" in target else "?"
        target = f"{target}{sep}page={page}"
    with _client(cfg) as client:
        res = client.get(target)
        _emit(parse_listing(res.text, target, cfg.base_url), format, "category")


@app.command()
@_run
def whoami(format: str = typer.Option("json", "--format", help=FORMAT_HELP)) -> None:
    """Report whether the configured session cookie is accepted."""
    cfg = _config()
    if not cfg.cookie:
        raise NfError("no session cookie is configured",
                      hint="set NF_COOKIE or cookie = \"...\" in the config file")
    with _client(cfg) as client:
        res = client.get(f"{cfg.base_url}/members/")
        from nf.parse.page import detect_auth_wall, tree
        walled = detect_auth_wall(res.text)
        logged_in = "Log out" in res.text or "js-logOut" in res.text
        _emit({"authenticated": bool(logged_in and not walled),
               "loginWall": walled,
               "cookieConfigured": True}, format, "whoami")


@app.command()
@_run
def raw(url: str = typer.Argument(..., help="URL or path to fetch"),
        refresh: bool = typer.Option(False, "--refresh")) -> None:
    """Print raw HTML. Robots-gated; for debugging selectors."""
    cfg = _config()
    target = _web(url, "thread", cfg.base_url)
    with _client(cfg) as client:
        assert_allowed(client._robots(), target)
        res = client.get(target, refresh=refresh)
        sys.stdout.write(res.text)


@app.command()
@_run
def index(rebuild: bool = typer.Option(False, "--rebuild"),
          quiet: bool = typer.Option(False, "--quiet"),
          format: str = typer.Option("json", "--format", help=FORMAT_HELP)) -> None:
    """Build or update the local search index from the site sitemap."""
    cfg = _config()
    with _client(cfg) as client:
        assert_allowed(client._robots(), f"{cfg.base_url}{SITEMAP_INDEX}")
        stats = build(client, cfg, rebuild=rebuild,
                      progress=None if quiet else lambda m: sys.stderr.write(m + "\n"))
    _emit(stats, format, "index")


@app.command()
@_run
def search(query: str = typer.Argument(...),
           type: str = typer.Option(None, "--type",
                                    help="Comma list: thread,resource,tag,forum"),
           since: str = typer.Option(None, "--since", help="YYYY-MM-DD"),
           limit: int = typer.Option(20, "--limit"),
           resolve: int = typer.Option(0, "--resolve",
                                       help="Fetch full objects for the top N hits"),
           format: str = typer.Option("json", "--format", help=FORMAT_HELP)) -> None:
    """Title search over the local sitemap index. Not full-text."""
    cfg = _config()
    types = [t.strip() for t in type.split(",")] if type else list(DEFAULT_SEARCH_TYPES)
    idx = open_index(cfg)
    try:
        if idx.count() == 0:
            with _client(cfg) as client:
                assert_allowed(client._robots(), f"{cfg.base_url}{SITEMAP_INDEX}")
                build(client, cfg, progress=lambda m: sys.stderr.write(m + "\n"))
        hits = idx.search(query, types=types, since=since, limit=limit)
    finally:
        idx.close()

    if resolve and hits:
        with _client(cfg) as client:
            for hit in hits[:resolve]:
                page = client.get(hit["url"])
                parsed = (parse_thread(page.text, hit["url"], cfg.base_url)
                          if hit["type"] == "thread"
                          else parse_resource(page.text, hit["url"], cfg.base_url))
                hit["title"] = parsed.title
                hit["titleSource"] = "page"
                key = "thread" if hit["type"] == "thread" else "resource"
                hit[key] = to_dict(parsed)

    _emit({"query": query, "types": types, "hits": hits}, format, "search")


@app.command()
@_run
def usage(window: str = typer.Option("24h", "--window", help="1h, 24h, or all"),
          format: str = typer.Option("json", "--format", help=FORMAT_HELP)) -> None:
    """Report this client's own request usage from the local ledger."""
    cfg = _config()
    stats = Ledger(cfg.state_dir).rollup(window, limit_ms=cfg.rate_limit_ms)
    stats["refusals"]["robots"] = _robots_refusals
    _emit(stats, format, "usage")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cli.py -v`
Expected: 8 passed

- [ ] **Step 5: Run the whole offline suite**

Run: `.venv/bin/python -m pytest`
Expected: all passed, no network access.

- [ ] **Step 6: Complete `README.md`**

It must state, in this order: what the tool does; the install line; the config file
location and `NF_COOKIE`; each command with one example; the exit-code table; and a
**Limitations** section covering (a) title search only, not full-text, because `/search/`
is disallowed, (b) slug-derived index titles are approximations, marked `titleSource`,
(c) no downloads, attachments, posting, or login, (d) the tool refuses robots-disallowed
paths rather than working around them.

- [ ] **Step 7: Verify against the live site**

Set a real session cookie, then run each of these and confirm the stated outcome:

```bash
export NF_COOKIE='<your xf_user and xf_session values>'
nf thread https://nullforums.net/threads/trending-and-latest-posts-api.89951/ | jq '.title, (.posts|length)'
nf resource --id 8953 | jq '.title, .author.username, .version'
nf index --quiet | jq '.docs'
nf search advancedkits --limit 5 | jq '.hits[] | {type,title,url}'
nf usage --window all | jq '.requests.total, .cache'
```

Acceptance: `thread` yields a non-empty title and ≥1 post; `resource` yields title,
author, and version; `index` reports a doc count in the hundreds of thousands;
`search advancedkits` returns hits whose slugs contain the term; `usage` reports a
request total consistent with the calls just made. Then confirm the boundary holds:

```bash
nf raw https://nullforums.net/search/?q=x; echo "exit=$?"   # expect exit=5, JSON refusal
```

- [ ] **Step 8: Commit**

```bash
git add src/nf/cli.py tests/test_cli.py README.md
git commit -m "feat: CLI commands, README, and live verification"
```

---

## Self-Review

**Spec coverage.** Every spec section maps to a task: §5.1 identity → Task 2;
§5.2 config/auth → Task 2; §6 politeness (throttle, retries, cache, robots) → Tasks 4, 6;
§7 search → Tasks 11, 12; §8 commands → Task 12; §8.1 models → Task 7; §8.2 exit codes →
Task 1; §9 ledger → Task 5; §10 verification → Tasks 3 (fixtures), 12 (live);
§2 non-goals → enforced by Task 8's `scrub_attachments` (tested) and Task 4's gate
(tested). §4's parser purity → Tasks 8–10 have no I/O.

**Deliberate refinement vs the spec.** The spec's §7 sketch used FTS5 with
`content='docs'` plus triggers. The plan uses a single FTS5 table with `UNINDEXED`
metadata columns instead, because contentless-external FTS5 requires trigger maintenance
and rowid bookkeeping whose failure mode is a silently stale index. The plan's version
is simpler and its `upsert_many` is idempotent-tested. The spec's schema block should be
updated to match, and `SearchHit.type`'s default filter is now `thread,resource`.

**Known gaps, stated rather than hidden.** `whoami`'s logged-in check keys on the
presence of a logout affordance in the page; if the theme hides it, `whoami` will report
`authenticated: false` while other commands still work. Task 12's live run should
confirm it, and the check should be widened to an XF-standard marker if it fails.
`nf thread --all` on a thread with 46 pages makes 45 sequential requests at 1 req/s —
about 45 seconds. That is the cost of the rate limit and is not a bug.
