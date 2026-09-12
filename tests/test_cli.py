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
