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

    client, _, led = make_client(tmp_path, handler)
    with pytest.raises(RobotsRefusal):
        client.get("https://nullforums.net/search/?q=x")
    assert seen == [ROBOTS]      # robots fetched, target never requested
    refusal = led.entries()[-1]
    assert refusal["refusal"] == "/search/"
    assert led.rollup("all", 1000)["requests"]["total"] == 1   # robots only


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
    # The robots fetch is ledgered first, so the target is the LAST entry.
    assert led.entries()[-1]["pathClass"] == "thread"


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


def test_malformed_content_length_is_ignored_and_fetch_succeeds(tmp_path):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_BODY)
        return httpx.Response(200, text=OK_BODY, headers={"Content-Length": "abc"})

    client, _, _ = make_client(tmp_path, handler)
    res = client.get(THREAD)
    assert res.status == 200
    assert res.text == OK_BODY