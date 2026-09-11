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
