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
from urllib.parse import urlsplit

import httpx

from nf.config import Config
from nf.errors import EdgeBlocked, NetworkError, RateLimited, RobotsRefusal
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
        try:
            assert_allowed(self._robots(), url)
        except RobotsRefusal:
            # A refusal never became a request; ledger it separately so it
            # shows in `nf usage` without inflating the request count.
            if self.ledger:
                self.ledger.record_refusal(urlsplit(url).path or "/")
            raise
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