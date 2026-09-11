"""Error types, each bound to a process exit code. See spec section 8.2."""

from __future__ import annotations


class NfError(Exception):
    """Base error. ``exit_code`` is the process status; ``code`` is the wire name."""

    exit_code: int = 1
    code: str = "ERROR"

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict:
        payload: dict[str, str] = {"code": self.code, "message": self.message}
        if self.hint:
            payload["hint"] = self.hint
        return {"error": payload}


class UsageError(NfError):
    exit_code = 2
    code = "USAGE"


class AuthRequired(NfError):
    exit_code = 3
    code = "AUTH_REQUIRED"


class EdgeBlocked(NfError):
    exit_code = 4
    code = "EDGE_BLOCKED"


class RobotsRefusal(NfError):
    exit_code = 5
    code = "ROBOTS_DISALLOWED"


class RateLimited(NfError):
    exit_code = 6
    code = "RATE_LIMITED"


class ParseFailure(NfError):
    exit_code = 7
    code = "PARSE_FAILURE"


class NetworkError(NfError):
    exit_code = 8
    code = "NETWORK_ERROR"
