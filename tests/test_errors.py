import pytest

from nf.errors import (
    AuthRequired, EdgeBlocked, ParseFailure, RateLimited,
    RobotsRefusal, UsageError, NetworkError,
)


@pytest.mark.parametrize("cls,code,exit_code", [
    (UsageError, "USAGE", 2),
    (AuthRequired, "AUTH_REQUIRED", 3),
    (EdgeBlocked, "EDGE_BLOCKED", 4),
    (RobotsRefusal, "ROBOTS_DISALLOWED", 5),
    (RateLimited, "RATE_LIMITED", 6),
    (ParseFailure, "PARSE_FAILURE", 7),
    (NetworkError, "NETWORK_ERROR", 8),
])
def test_exit_code_mapping(cls, code, exit_code):
    err = cls("boom")
    assert err.exit_code == exit_code
    assert err.code == code
    assert err.to_dict() == {"error": {"code": code, "message": "boom"}}


def test_hint_is_included_when_present():
    err = ParseFailure("selector missing", hint="run with --raw")
    assert err.to_dict()["error"]["hint"] == "run with --raw"


def test_str_is_the_message():
    assert str(UsageError("bad flag")) == "bad flag"
