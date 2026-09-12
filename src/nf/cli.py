"""Command line interface. The only place errors become exit codes."""

from __future__ import annotations

import functools
import json
import sys

import typer

from nf.config import Config, load_config
from nf.errors import NfError, UsageError
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
    _emit(Ledger(cfg.state_dir).rollup(window, limit_ms=cfg.rate_limit_ms), format, "usage")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
