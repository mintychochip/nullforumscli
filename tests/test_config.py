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


def test_redact_ignores_short_values_to_avoid_mangling_text():
    """Short values are not individually redacted, or ordinary text gets mangled."""
    cfg = Config(
        base_url="https://x.test", cookie=f"a=1; xf_user={SECRET_USER}",
        user_agent="nf/0",
        rate_limit_ms=1, cache_ttl_s=1,
        cache_dir=Path("/tmp/c"), state_dir=Path("/tmp/s"),
    )
    assert cfg.redact("a=1 in text") == "a=1 in text"
    assert SECRET_USER not in cfg.redact(f"token {SECRET_USER}")


def test_default_user_agent_shape():
    """nf is no longer strictly read-only (nf like exists), but the UA must
    stay an honest self-identification in the canonical shape."""
    ua = default_user_agent()
    assert ua.startswith("nf/")
    assert "(client; +https://github.com/" in ua
    assert "read-only" not in ua
