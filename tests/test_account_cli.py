"""CLI-level tests for the account commands, following test_cli.py conventions:
MockTransport-backed Client, tmp state dirs, and the same fake cookie."""
import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

import nf.http
from nf.cli import app

FIX = Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIX / name).read_text(encoding="utf-8", errors="replace")


@pytest.fixture
def account_env(tmp_path, monkeypatch):
    def handler(request):
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /account/\nAllow: /\n")
        if path.startswith("/dbtech-credits/"):
            return httpx.Response(200, text=fixture("dbtech-credits.html"))
        if path.startswith("/account/reactions-given"):
            return httpx.Response(200, text=fixture("reactions-given-page-1.html"))
        if path.endswith("/react"):
            return httpx.Response(200, json={"html": {"content": "ok"}, "visitor": {}})
        if path.startswith("/resources/"):
            return httpx.Response(200, text=fixture("resource-4918-liked.html"))
        if path.startswith("/threads/"):
            return httpx.Response(200, text=fixture("thread-89951-liked.html"))
        return httpx.Response(404, text="nope")

    transport = httpx.MockTransport(handler)
    real_init = nf.http.Client.__init__

    def patched(self, cfg, ledger=None, transport=None, **kw):
        real_init(self, cfg, ledger=ledger, transport=transport or transport_[0], **kw)

    transport_[0] = transport
    monkeypatch.setattr(nf.http.Client, "__init__", patched)
    monkeypatch.setenv("NF_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("NF_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("NF_COOKIE", "xf_user=SECRET")
    monkeypatch.setenv("NF_BASE_URL", "https://nullforums.net")
    return tmp_path


transport_ = [None]


def test_me_json_output(account_env):
    result = CliRunner().invoke(app, ["me"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["userId"] == 100200300
    assert data["username"] == "scrubuser"
    assert data["creditsBalance"] == 0
    assert data["authenticated"] is True


def test_likes_json_output_and_cookies_redacted(account_env):
    result = CliRunner().invoke(app, ["likes"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["total"] == 43
    assert data["items"][0]["reactByUserId"] == 100200300
    # cookie material must never leak into output
    assert "SECRET" not in result.stdout
    assert "SECRET" not in result.stderr


def test_likes_page2_hits_paged_url(account_env):
    result = CliRunner().invoke(app, ["likes", "--page", "2"])
    assert result.exit_code == 0, result.stderr or result.stdout
    data = json.loads(result.stdout)
    assert len(data["items"]) == 20
    assert data["pagination"]["page"] == 2


def test_like_reports_already_reaction(account_env):
    # Fixture page renders has-reaction; do_like reports already without POST.
    result = CliRunner().invoke(app, ["like", "/threads/trending-and-latest-posts-api.89951/"])
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert data["status"] == "already"
    assert data["reactionId"] == 1

def test_like_requires_cookie(account_env, monkeypatch):
    monkeypatch.delenv("NF_COOKIE")
    import nf.config as cfgmod
    monkeypatch.setattr(cfgmod, "default_config_path",
                        lambda: account_env / "no-config.toml")
    result = CliRunner().invoke(app, ["like", "/threads/x.89951/"])
    assert result.exit_code == 3
    assert json.loads(result.stderr)["error"]["code"] == "AUTH_REQUIRED"


def test_likes_requires_cookie(account_env, monkeypatch):
    monkeypatch.delenv("NF_COOKIE")
    import nf.config as cfgmod
    monkeypatch.setattr(cfgmod, "default_config_path",
                        lambda: account_env / "no-config.toml")
    result = CliRunner().invoke(app, ["likes"])
    assert result.exit_code == 3


def test_me_requires_cookie(account_env, monkeypatch):
    monkeypatch.delenv("NF_COOKIE")
    import nf.config as cfgmod
    monkeypatch.setattr(cfgmod, "default_config_path",
                        lambda: account_env / "no-config.toml")
    result = CliRunner().invoke(app, ["me"])
    assert result.exit_code == 3


def test_download_streams_binary_to_disk(account_env):
    """The download path must write real bytes to disk, not cache text."""
    payload = b"PK\x03\x04" + b"nulldata-" * 20

    def handler(request):
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if path.endswith("/download"):
            return httpx.Response(200, content=payload, headers={
                "Content-Disposition": 'attachment; filename="AdvancedKits.jar"',
                "Content-Type": "application/octet-stream"})
        return httpx.Response(404)

    dest = account_env / "AdvancedKits.jar"
    from nf.http import Client
    custom = httpx.MockTransport(handler)
    with Client(_load_config_for(account_env), transport=custom) as c:
        result = c.download("https://nullforums.net/resources/advancedkits.8953/download", dest)
    assert dest.is_file()
    assert dest.read_bytes() == payload
    assert result.nbytes == len(payload)


def _load_config_for(env_dir):
    """A Config bound to the tmp dirs with a seeded cookie (like cli_env)."""
    from nf.config import Config
    return Config(
        base_url="https://nullforums.net", cookie="xf_user=SECRET",
        user_agent="nf/0.1 (read-only client; +https://example.test)",
        rate_limit_ms=1000, cache_ttl_s=900,
        cache_dir=env_dir / "cache", state_dir=env_dir / "state")

