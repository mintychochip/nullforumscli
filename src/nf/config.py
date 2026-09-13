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
    """Honest, self-identifying UA. Verified to pass the site's edge gate;
    updated after the CLI grew write operations (like) beyond read-only."""
    return f"nf/{_package_version()} (client; +https://github.com/{REPO_SLUG})"


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
