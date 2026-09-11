"""robots.txt parsing and the pre-request authorization gate.

Implements the RFC 9309 precedence rules that matter here: pick the group
whose user-agent token is the longest match (falling back to ``*``), then
within it the longest matching pattern wins, and an ``Allow`` beats a
``Disallow`` of equal length. A path matching no rule is allowed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from nf.errors import RobotsRefusal


@dataclass(frozen=True)
class _Rule:
    allow: bool
    pattern: str
    regex: re.Pattern[str]


def _compile(pattern: str) -> re.Pattern[str]:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    parts = [re.escape(p) for p in body.split("*")]
    return re.compile("^" + ".*".join(parts) + ("$" if anchored else ""))


@dataclass
class RobotsPolicy:
    rules: list[_Rule] = field(default_factory=list)
    source_agent: str = "*"

    @classmethod
    def parse(cls, text: str, user_agent: str) -> "RobotsPolicy":
        groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
        agents: list[str] = []
        rules: list[tuple[bool, str]] = []
        in_rules = False

        def flush() -> None:
            if agents:
                groups.append((list(agents), list(rules)))

        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "user-agent":
                if in_rules:
                    flush()
                    agents.clear()
                    rules.clear()
                    in_rules = False
                agents.append(value.lower())
            elif key in ("allow", "disallow"):
                if not agents:
                    continue
                in_rules = True
                if value:
                    rules.append((key == "allow", value))
        flush()

        ua = user_agent.lower()
        token = ua.split("/", 1)[0]
        best_agent: str | None = None
        best_rules: list[tuple[bool, str]] = []
        for group_agents, group_rules in groups:
            for agent in group_agents:
                if agent == "*" or agent == token or agent in ua:
                    if best_agent is None or (agent != "*" and len(agent) > len(best_agent)):
                        best_agent, best_rules = agent, group_rules
        if best_agent is None:
            return cls(rules=[], source_agent="*")
        return cls(
            rules=[_Rule(allow=a, pattern=p, regex=_compile(p)) for a, p in best_rules],
            source_agent=best_agent,
        )

    def is_allowed(self, path: str) -> bool:
        if "://" in path:
            parts = urlsplit(path)
            path = parts.path or "/"
            if parts.query:
                path = f"{path}?{parts.query}"
        if not path.startswith("/"):
            path = "/" + path
        winner: _Rule | None = None
        for rule in self.rules:
            if not rule.regex.match(path):
                continue
            key = (len(rule.pattern), rule.allow)
            if winner is None or key > (len(winner.pattern), winner.allow):
                winner = rule
        return True if winner is None else winner.allow

    def matching_rule(self, path: str) -> _Rule | None:
        winner: _Rule | None = None
        for rule in self.rules:
            if not rule.regex.match(path):
                continue
            key = (len(rule.pattern), rule.allow)
            if winner is None or key > (len(winner.pattern), winner.allow):
                winner = rule
        return winner


def assert_allowed(policy: RobotsPolicy, url: str) -> None:
    """Raise RobotsRefusal before any request is made for a disallowed path."""
    path = urlsplit(url).path or "/"
    if not policy.is_allowed(url):
        rule = policy.matching_rule(path)
        pattern = rule.pattern if rule else "/"
        raise RobotsRefusal(
            f"robots.txt disallows {path} (rule: Disallow: {pattern})",
            hint=f"matched robots group {policy.source_agent!r}; this client only reads "
                 "paths the site permits (spec section 2)",
        )
