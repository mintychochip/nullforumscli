"""The only module that opens a socket.

Every request passes the robots gate first, so no code path can reach a
disallowed URL. Requests are serialized with a minimum spacing, capped-retry
on 5xx/429, and cached on disk. 403 and challenge pages are terminal: they
are reported, never retried, and never worked around.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlsplit

import httpx

from nf.config import Config
from nf.errors import AuthRequired, EdgeBlocked, NetworkError, RateLimited, RobotsRefusal, UsageError
from nf.paths import classify_request
from nf.robots import RobotsPolicy, assert_allowed
from nf.usage import Ledger

MAX_ATTEMPTS = 3
MAX_REDIRECTS = 3
BACKOFF_SECONDS = (1.0, 2.0, 4.0)
ROBOTS_TTL_S = 24 * 3600
TIMEOUT_S = 20.0
MAX_BODY_BYTES = 16 * 1024 * 1024

BLOCK_MARKERS = ("just a moment", "attention required", "challenge-platform",
                 "enable javascript and cookies to continue", "cf-chl")


def _same_origin(base: str, url: str) -> bool:
    base_parts = urlsplit(base)
    url_parts = urlsplit(url)
    return (base_parts.scheme == url_parts.scheme
            and base_parts.netloc == url_parts.netloc)


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int
    text: str
    from_cache: bool
    nbytes: int
    cache_age_seconds: float | None = None


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
            follow_redirects=False,
            timeout=TIMEOUT_S,
            headers={
                "User-Agent": cfg.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        if self.cfg.cookie_header:
            for part in self.cfg.cookie_header.split(";"):
                name, _, value = part.strip().partition("=")
                if name:
                    domain = urlsplit(self.cfg.base_url).hostname
                    self._http.cookies.set(name, value, domain=domain)

        self.cfg.cache_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.cfg.cache_dir, 0o700)

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
        os.chmod(base, 0o700)
        return base / f"{key}.html", base / f"{key}.meta.json"

    def _read_cache(self, url: str, ttl_s: int) -> tuple[str, float] | None:
        body, meta = self._cache_paths(url)
        if not body.is_file() or not meta.is_file():
            return None
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if time.time() - float(info.get("stored_at_wall", 0)) > ttl_s:
            return None
        try:
            text = body.read_text(encoding="utf-8")
        except OSError:
            return None
        return text, float(info.get("stored_at_wall", 0))

    def _atomic_write(self, path: Path, text: str) -> None:
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        os.chmod(path, 0o600)

    def _write_cache(self, url: str, text: str) -> None:
        body, meta = self._cache_paths(url)
        now_mono = self._clock()
        now_wall = time.time()
        self._atomic_write(body, text)
        self._atomic_write(meta, json.dumps({
            "url_class": classify_request(url),
            "stored_at_mono": now_mono,
            "stored_at_wall": now_wall,
        }))

    # ---- robots gate ----

    def _robots(self) -> RobotsPolicy:
        if self._policy is not None:
            return self._policy
        url = f"{self.cfg.base_url}/robots.txt"
        cached = self._read_cache(url, ROBOTS_TTL_S)
        text = cached[0] if cached is not None else None
        if text is None:
            status, text, _, _ = self._raw_get(url)
            if status != 200:
                raise NetworkError(
                    f"could not read robots.txt (status {status})",
                    hint="refusing to make requests without knowing the site's rules")
        policy = RobotsPolicy.parse(text, self.cfg.user_agent)
        if not policy.rules:
            raise NetworkError("robots.txt contained no usable rules; refusing to proceed")
        if cached is None:
            self._write_cache(url, text)
        self._policy = policy
        return self._policy

    def assert_gated(self, url: str) -> None:
        assert_allowed(self._robots(), url)

    # ---- requests ----

    def _raw_get(self, url: str) -> tuple[int, str, int, str]:
        """Send one request with retries and manual same-origin redirects.

        Returns (status, text, attempt, final_url). The caller receives the
        original url; the final_url is used for cache storage.
        """
        if not _same_origin(self.cfg.base_url, url):
            raise UsageError(
                f"refusing to fetch a URL outside the configured origin "
                f"({urlsplit(url).netloc}); NF_BASE_URL / base_url is the only allowed origin")
        # Origin and redirects are checked here; the initial robots gate is the
        # caller's responsibility so _robots() can use _raw_get() to bootstrap.

        current_url = url
        redirects = 0
        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._throttle()
            headers: dict[str, str] = {}
            if _same_origin(self.cfg.base_url, current_url) and self.cfg.cookie_header:
                headers["Cookie"] = self.cfg.cookie_header
            try:
                response = self._http.get(current_url, headers=headers, follow_redirects=False)
            except httpx.HTTPError as exc:
                last_error = exc
                if self.ledger:
                    self.ledger.record(classify_request(current_url), 0, 0, "miss", attempt)
                if attempt < MAX_ATTEMPTS:
                    self._sleep(BACKOFF_SECONDS[attempt - 1])
                    continue
                raise NetworkError(
                    f"request to {current_url} failed: {exc.__class__.__name__}") from exc

            content_length = response.headers.get("Content-Length")
            cl_value = None
            if content_length:
                try:
                    cl_value = int(content_length.strip())
                except (ValueError, TypeError):
                    cl_value = None
            if cl_value is not None and cl_value > MAX_BODY_BYTES:
                raise NetworkError(
                    f"response body exceeds {MAX_BODY_BYTES} bytes",
                    hint="the page or shard is larger than this client will fetch")

            text = response.text
            nbytes = len(response.content)
            if nbytes > MAX_BODY_BYTES:
                raise NetworkError(
                    f"response body exceeds {MAX_BODY_BYTES} bytes",
                    hint="the page or shard is larger than this client will fetch")

            if self.ledger:
                self.ledger.record(
                    classify_request(current_url), response.status_code,
                    nbytes, "miss", attempt)

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

            if response.status_code in (301, 302, 303, 307, 308):
                loc = response.headers.get("Location")
                if not loc:
                    return response.status_code, text, attempt, current_url
                if redirects >= MAX_REDIRECTS:
                    raise NetworkError(
                        f"too many redirects fetching {url}",
                        hint="this path issues a redirect loop; commands target deep paths")
                current_url = urljoin(current_url, loc)
                if not _same_origin(self.cfg.base_url, current_url):
                    raise UsageError(
                        f"refusing to fetch a URL outside the configured origin "
                        f"({urlsplit(current_url).netloc}); NF_BASE_URL / base_url is the only allowed origin")
                assert_allowed(self._robots(), current_url)
                redirects += 1
                continue

            return response.status_code, text, attempt, current_url
        raise NetworkError(f"request to {current_url} failed: {last_error!r}")
    def get_own(self, url: str) -> FetchResult:
        """Documented robots override for a strictly own-data path.

        robots.txt Disallows /account/ for crawlers. The only source of the
        operator's own reaction history lives there, and this client
        refuses /account/ just as it refuses /search/: mechanically, with
        no exception baked into the policy parser. The one legitimate use —
        the operator inspecting their own account under their own session
        — is therefore expressed as an explicit, opt-in method:
        get_own() fetches exactly one operator-chosen URL, asserts the
        path starts with /account/ (so nothing else can ride the
        override), skips the HTML cache (account pages are session data),
        and still passes the gate for every other rule.
        """
        path = urlsplit(url).path or "/"
        if not path.startswith("/account/"):
            raise UsageError("get_own is only for own-account paths: /account/...")
        if not self.cfg.cookie_header:
            raise AuthRequired(
                "own-data fetch requires a session cookie",
                hint="set NF_COOKIE or cookie = ... in the config file")
        status, text, attempt, final_url = self._raw_get(url)
        if self.ledger:
            self.ledger.record(classify_request(url), status, len(text.encode("utf-8")), "miss", attempt)
        nbytes = len(text.encode("utf-8"))
        return FetchResult(url, status, text, False, nbytes)

    def get(self, url: str, *, no_cache: bool = False, refresh: bool = False) -> FetchResult:
        try:
            assert_allowed(self._robots(), url)
        except RobotsRefusal:
            # A refusal never became a request; ledger it separately so it
            # shows in `nf usage` without inflating the request count.
            if self.ledger:
                self.ledger.record_refusal(urlsplit(url).path or "/")
            raise
        if not no_cache and not refresh:
            cached = self._read_cache(url, self.cfg.cache_ttl_s)
            if cached is not None:
                text, stored_wall = cached
                if self.ledger:
                    self.ledger.record(
                        classify_request(url), 200,
                        len(text.encode("utf-8")), "hit")
                self._last_request = self._clock()
                return FetchResult(
                    url, 200, text, True, len(text.encode("utf-8")),
                    cache_age_seconds=time.time() - stored_wall)

        status, text, _, final_url = self._raw_get(url)
        if status == 200:
            self._write_cache(final_url, text)
        nbytes = len(text.encode("utf-8"))
        return FetchResult(url, status, text, False, nbytes)

    def download(self, url: str, dest: Path) -> FetchResult:
        """Stream a binary resource file to ``dest``.

        The GET cache is HTML-only by design (text decode + 16 MiB cap) and
        resource files run to hundreds of MB, so this path bypasses the
        cache entirely and enforces its own size budget. Robots-gated like
        everything else.
        """
        assert_allowed(self._robots(), url)
        try:
            self._throttle()
            headers: dict[str, str] = {}
            if self.cfg.cookie_header:
                headers["Cookie"] = self.cfg.cookie_header
            with self._http.stream(
                "GET", url, headers=headers, follow_redirects=False, timeout=TIMEOUT_S,
            ) as response:
                if response.status_code >= 500 or response.status_code == 429:
                    raise NetworkError(
                        f"download returned {response.status_code}",
                        hint="the download endpoint failed; try again later")
                ctype = response.headers.get("Content-Type", "")
                disposition = response.headers.get("Content-Disposition", "")
                if response.status_code in (403, 503) or _looks_blocked(
                        response.status_code, ctype):
                    raise EdgeBlocked(
                        f"download refused at the edge (status {response.status_code})",
                        hint="the site returned a block page; the resource may "
                             "require a Like or a purchase before downloading")
                if 300 <= response.status_code < 400:
                    raise NetworkError(
                        "download redirected more than the client allows",
                        hint="this file issues a redirect loop")
                if "text/html" in ctype or not disposition:
                    # The site answers its like-gate / purchase-gate with a
                    # 200 HTML page, not an error status: fail before writing.
                    raise EdgeBlocked(
                        "download endpoint returned a gate page, not a file",
                        hint="like the resource first: nf like <resource-url>; "
                             "then retry the download")
                nbytes = 0
                dest.parent.mkdir(parents=True, exist_ok=True)
                with open(dest, "wb") as fh:
                    for chunk in response.iter_bytes(65536):
                        nbytes += len(chunk)
                        if nbytes > MAX_BODY_BYTES:
                            raise NetworkError(
                                f"download exceeds {MAX_BODY_BYTES} bytes",
                                hint="the file is larger than this client will fetch")
                        fh.write(chunk)
            if self.ledger:
                self.ledger.record(classify_request(url), 0, nbytes, "miss", 1)
            return FetchResult(url, response.status_code, "", False, nbytes)
        except httpx.HTTPError as exc:
            raise NetworkError(
                f"download failed: {exc.__class__.__name__}") from exc

    # ---- mutation ----

    def post(self, url: str, data: dict) -> dict:
        """One gated, throttled, never-cached POST for XenForo AJAX actions.

        The GET cache is HTML-only and keyed by URL: writing a POST response
        under a GET URL would poison later reads until TTL expiry, so post()
        never touches the cache. Returns the parsed XenForo JSON envelope.
        """
        try:
            assert_allowed(self._robots(), url)
        except RobotsRefusal:
            if self.ledger:
                self.ledger.record_refusal(urlsplit(url).path or "/")
            raise
        headers: dict[str, str] = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
        }

        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._throttle()
            try:
                response = self._http.post(url, headers=headers, data=data)
            except httpx.HTTPError as exc:
                if attempt < MAX_ATTEMPTS:
                    self._sleep(BACKOFF_SECONDS[attempt - 1])
                    continue
                raise NetworkError(
                    f"POST {urlsplit(url).path} failed: {exc.__class__.__name__}") from exc
            body = response.text
            if self.ledger:
                self.ledger.record("post", response.status_code, len(response.content), "miss", attempt)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < MAX_ATTEMPTS:
                    self._sleep(BACKOFF_SECONDS[attempt - 1])
                    continue
                raise NetworkError(f"server error {response.status_code} after POST")
            # XenForo JSON error envelopes arrive on 4xx with an app-level
            # message (e.g. 403 "You cannot cancel this reaction."); those
            # are NOT edge blocks and must reach the caller for mapping.
            ctype = response.headers.get("Content-Type", "")
            if "application/json" in ctype:
                try:
                    return response.json()
                except ValueError:
                    pass
            if _looks_blocked(response.status_code, body):
                raise EdgeBlocked(
                    f"request blocked at the edge (status {response.status_code})",
                    hint="the site refused this client; no bypass is attempted")
            try:
                return response.json()
            except ValueError:
                if response.status_code >= 400:
                    raise NetworkError(f"POST {urlsplit(url).path} returned HTTP {response.status_code}",
                                       hint="the action was refused by the site")
                return {"status": "ok", "status_code": response.status_code, "text": body}
        raise NetworkError("POST failed after retries")
