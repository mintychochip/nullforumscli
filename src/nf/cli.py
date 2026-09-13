"""Command line interface. The only place errors become exit codes."""

import functools
import hashlib
import httpx
import json
import re
import sys
import urllib.parse
from pathlib import Path
from urllib.parse import urlsplit

import typer
from nf.config import Config, load_config
from nf.errors import AuthRequired, NfError, RobotsRefusal, UsageError
from nf.http import Client
from nf.index import DEFAULT_SEARCH_TYPES, MAX_LIMIT, SITEMAP_INDEX, build, open_index
from nf.mutate import do_like
from nf.parse import parse_listing, parse_resource, parse_thread
from nf.model import to_dict
from nf.parse.account import (
    parse_account,
    parse_level_progress,
    parse_reactions,
    parse_wallet,
)
from nf.render import render
from nf.robots import RobotsPolicy, assert_allowed
from nf.usage import Ledger


app = typer.Typer(add_completion=False, help="Headless read-only reader for nullforums.net.")

FORMAT_HELP = "Output format: json (default), md, text."


def _web(url_or_id: str, kind: str, base_url: str) -> str:
    """Accept a full URL, a path, or a bare id."""
    if url_or_id.startswith("http") or url_or_id.startswith("/"):
        return url_or_id if url_or_id.startswith("http") else base_url + url_or_id
    if not url_or_id.isdigit():
        raise UsageError(f"{url_or_id!r} is not a URL, path, or numeric id")
    prefix = {"thread": "threads", "resource": "resources", "category": "resources/categories"}[kind]
    return f"{base_url}/{prefix}/x.{url_or_id}/"


def _config() -> Config:
    """Load config per call. One process runs one command, so caching buys
    nothing and a module-level cache would make the CLI untestable."""
    return load_config()


def _emit(payload, fmt: str, kind: str, *, from_cache: bool = False,
          cache_age: float | None = None) -> None:
    data = payload if isinstance(payload, dict) else to_dict(payload)
    data["fromCache"] = from_cache
    data["cacheAgeSeconds"] = cache_age
    sys.stdout.write(render(data, fmt, kind))


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
           all_pages: bool = typer.Option(False, "--all",
                                          help="Read all pages sequentially, starting from page 1; ignores --page"),
           format: str = typer.Option("json", "--format", help=FORMAT_HELP)) -> None:
    """Read one thread and its posts."""
    cfg = _config()
    target = _web(url or id or "", "thread", cfg.base_url)
    if page > 1 and not all_pages:
        sep = "&" if "?" in target else "?"
        target = f"{target}{sep}page={page}"
    with _client(cfg) as client:
        first = client.get(target)
        parsed = parse_thread(first.text, target, cfg.base_url)
        if all_pages and parsed.pagination.pages > 1:
            base_target = target.split("?")[0]
            for n in range(2, parsed.pagination.pages + 1):
                sep = "&" if "?" in base_target else "?"
                extra = client.get(f"{base_target}{sep}page={n}")
                more = parse_thread(extra.text, target, cfg.base_url)
                for offset, post in enumerate(more.posts):
                    post.index = len(parsed.posts) + offset + 1
                parsed.posts.extend(more.posts)
        _emit(parsed, format, "thread", from_cache=first.from_cache,
              cache_age=first.cache_age_seconds)


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
        _emit(parse_resource(res.text, target, cfg.base_url), format, "resource",
              from_cache=res.from_cache, cache_age=res.cache_age_seconds)


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
        _emit(parse_listing(res.text, target, cfg.base_url), format, "category",
              from_cache=res.from_cache, cache_age=res.cache_age_seconds)


@app.command()
@_run
def whoami(format: str = typer.Option("json", "--format", help=FORMAT_HELP)) -> None:
    """Report whether the configured session cookie is accepted."""
    cfg = _config()
    if not cfg.cookie:
        raise AuthRequired("no session cookie is configured",
                           hint="set NF_COOKIE or cookie = \"...\" in the config file")
    with _client(cfg) as client:
        res = client.get(f"{cfg.base_url}/members/")
        if detect_auth_wall(res.text):
            raise AuthRequired("the site returned a login wall for this URL",
                               hint="supply a valid session cookie via NF_COOKIE or the config file")
        logged_in = "Log out" in res.text or "js-logOut" in res.text
        _emit({"authenticated": bool(logged_in),
               "loginWall": False,
               "cookieConfigured": True}, format, "whoami",
              from_cache=res.from_cache, cache_age=res.cache_age_seconds)


@app.command()
@_run
def raw(url: str = typer.Argument(..., help="URL or path to fetch"),
        refresh: bool = typer.Option(False, "--refresh")) -> None:
    """Print raw HTML. Robots-gated; for debugging selectors."""
    cfg = _config()
    target = _web(url, "thread", cfg.base_url)
    with _client(cfg) as client:
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
    _emit(stats, format, "index", from_cache=False, cache_age=None)


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
    limit = min(max(limit, 0), MAX_LIMIT)
    resolve = min(max(resolve, 0), MAX_LIMIT)
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
        resolve = min(resolve, len(hits))
        with _client(cfg) as client:
            for hit in hits[:resolve]:
                if hit["type"] not in ("thread", "resource"):
                    sys.stderr.write(f"not resolving {hit['type']} hit {hit['url']}\n")
                    continue
                page = client.get(hit["url"])
                parsed = (parse_thread(page.text, hit["url"], cfg.base_url)
                          if hit["type"] == "thread"
                          else parse_resource(page.text, hit["url"], cfg.base_url))
                hit["title"] = parsed.title
                hit["titleSource"] = "page"
                key = "thread" if hit["type"] == "thread" else "resource"
                hit[key] = to_dict(parsed)
                hit["fromCache"] = page.from_cache
                hit["cacheAgeSeconds"] = page.cache_age_seconds

    _emit({"query": query, "types": types, "hits": hits}, format, "search",
          from_cache=False, cache_age=None)


@app.command()
@_run
def usage(window: str = typer.Option("24h", "--window", help="1h, 24h, or all"),
          format: str = typer.Option("json", "--format", help=FORMAT_HELP)) -> None:
    """Report this client's own request usage from the local ledger."""
    cfg = _config()
    _emit(Ledger(cfg.state_dir).rollup(window, limit_ms=cfg.rate_limit_ms), format, "usage",
          from_cache=False, cache_age=None)


@app.command()
@_run
def me(format: str = typer.Option("json", "--format", help=FORMAT_HELP)) -> None:
    """Report the signed-in visitor: identity, wallet, and level progress."""
    cfg = _config()
    if not cfg.cookie:
        raise AuthRequired("no session cookie is configured",
                           hint="set NF_COOKIE or cookie = \"...\" in the config file")
    with _client(cfg) as client:
        res = client.get(f"{cfg.base_url}/dbtech-credits/")
        account = parse_account(res.text)
        parse_wallet(res.text, account)
        if account.userId is None:
            raise AuthRequired(
                "could not find the visitor identity block",
                hint="the session cookie may be stale; re-run nf whoami")
        lvl = client.get(f"{cfg.base_url}/pages/nullforums-level-system/")
        progress = parse_level_progress(lvl.text)
        if progress:
            account.levelPoints = progress["points"]
            account.levelPointsNeeded = progress["threshold"]
            account.levelNext = progress["nextLevel"]
    # The site's real gates (pulled off /withdraw/): $0.10/upload from L2,
    # $0.05/update from L3, $10 minimum payout, 50 earning actions/day.
    level_hint = None
    if account.levelPoints is not None:
        if account.levelPoints >= 400:
            level_hint = "level 2 reached: uploads earn $0.10 each (updates $0.05 from L3)"
        else:
            level_hint = (f"level {account.levelNext} in "
                          f"{account.levelPointsNeeded - account.levelPoints} pts: "
                          "unlocks $0.10/upload earnings")
    _emit(to_dict(account) | {"authenticated": True, "levelHint": level_hint}, format, "me")


@app.command()
@_run
def likes(page: int = typer.Option(1, "--page"),
          format: str = typer.Option("json", "--format", help=FORMAT_HELP)) -> None:
    """List reactions the configured session has handed out (one page).

    The only endpoint for this data is /account/reactions-given, and
    robots.txt Disallows /account/. This command fetches that *exact* path
    under your own session with an explicit in-code override - the
    operator inspecting their own data, not crawling. Requires a cookie.
    """
    cfg = _config()
    if not cfg.cookie:
        raise AuthRequired("no session cookie is configured",
                           hint="set NF_COOKIE or cookie = \"...\" in the config file")
    target = f"{cfg.base_url}/account/reactions-given?reaction_id=0"
    if page > 1:
        target = f"{target}&page={page}"
    with _client(cfg) as client:
        res = client.get_own(target)  # documented override, see http.Client.get_own
        parsed = parse_reactions(res.text, target, cfg.base_url)
    _emit(parsed, format, "likes", from_cache=res.from_cache,
          cache_age=res.cache_age_seconds)


@app.command()
@_run
def like(target: str = typer.Argument(..., help="Thread/post or resource URL"),
         format: str = typer.Option("json", "--format", help=FORMAT_HELP)) -> None:
    """Like a post or resource on the site (one-shot mutation)."""
    cfg = _config()
    if not cfg.cookie:
        raise AuthRequired("no session cookie is configured",
                           hint="set NF_COOKIE or cookie = \"...\" in the config file")
    target_url = _web(target, "thread", cfg.base_url)
    with _client(cfg) as client:
        result = do_like(client, cfg.base_url, target_url)
    _emit(result, format, "like", from_cache=False, cache_age=None)


@app.command()
@_run
def download(url: str = typer.Argument(..., help="Resource URL or id"),
             out: str = typer.Argument("", help="Output file path"),
             format: str = typer.Option(None, "--format", help=FORMAT_HELP)) -> None:
    """Stream a resource file to disk (binary; bypasses the HTML cache)."""
    cfg = _config()
    if not cfg.cookie:
        raise AuthRequired("no session cookie is configured",
                           hint="set NF_COOKIE or cookie = \"...\" in the config file")
    base = _web(url, "resource", cfg.base_url)
    file_url = base.rstrip("/") + "/download"
    with _client(cfg) as client:
        dest = Path(out) if out else Path(suggested_filename(client, file_url))
        result = client.download(file_url, dest)
    _emit({"url": file_url, "file": str(dest), "bytes": result.nbytes,
           "sha256": hashlib.sha256(dest.read_bytes()).hexdigest()},
          format or "json", "download")


def suggested_filename(client: Client, file_url: str) -> str:
    """HEAD the download URL and read filename= from Content-Disposition."""
    request = client._http.build_request("HEAD", file_url)
    try:
        response = client._http.send(request)
    except httpx.HTTPError as exc:
        raise NetworkError(f"{exc.__class__.__name__} probing {urlsplit(file_url).path}")
    disposition = response.headers.get("Content-Disposition", "")
    m = re.search(r'filename="([^";]+)"|filename=([^;]+)$', disposition)
    if m:
        return urllib.parse.unquote((m.group(1) or m.group(2)).strip())
    slug_m = re.search(r"/resources/([\w\-]+?)(?:\.\d+)?/?$", urlsplit(file_url).path)
    return (slug_m.group(1) if slug_m else "resource") + ".bin"



def main() -> None:
    app()


if __name__ == "__main__":
    main()
