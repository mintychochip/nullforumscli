"""Data shapes produced by the parsers. Pure data; no I/O, no HTML."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

SCHEMA_VERSION = 1


def envelope(payload: dict) -> dict:
    return {"schemaVersion": SCHEMA_VERSION, **payload}


@dataclass
class Author:
    username: str | None = None
    userId: int | None = None
    url: str | None = None


@dataclass
class Pagination:
    page: int = 1
    pages: int = 1
    perPage: int | None = None
    total: int | None = None


@dataclass
class Post:
    id: int | None = None
    index: int = 1
    url: str | None = None
    author: Author = field(default_factory=Author)
    postedAt: str | None = None
    bodyText: str = ""
    bodyHtml: str = ""


@dataclass
class Thread:
    id: int | None = None
    url: str | None = None
    title: str = ""
    node: str | None = None
    author: Author = field(default_factory=Author)
    createdAt: str | None = None
    updatedAt: str | None = None
    tags: list[str] = field(default_factory=list)
    posts: list[Post] = field(default_factory=list)
    pagination: Pagination = field(default_factory=Pagination)


@dataclass
class CategoryRef:
    id: int | None = None
    title: str | None = None
    url: str | None = None


@dataclass
class Resource:
    id: int | None = None
    url: str | None = None
    title: str = ""
    author: Author = field(default_factory=Author)
    version: str | None = None
    tagLine: str | None = None
    description: dict = field(default_factory=lambda: {"html": "", "text": ""})
    createdAt: str | None = None
    lastUpdated: str | None = None
    category: CategoryRef | None = None
    discussionThread: dict | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class ResourceItem:
    id: int | None = None
    url: str | None = None
    title: str = ""
    author: Author = field(default_factory=Author)
    version: str | None = None
    lastUpdated: str | None = None


@dataclass
class ResourceList:
    id: int | None = None
    url: str | None = None
    title: str = ""
    items: list[ResourceItem] = field(default_factory=list)
    pagination: Pagination = field(default_factory=Pagination)


@dataclass
class ReactionItem:
    """One entry in the reactions-given list: who reacted to what."""
    reactByUserId: int | None = None
    reactByUsername: str | None = None
    targetUrl: str | None = None
    targetTitle: str | None = None
    reactionId: int | None = None
    reactedAt: str | None = None


@dataclass
class ReactionsGiven:
    items: list[ReactionItem] = field(default_factory=list)
    total: int | None = None
    pagination: Pagination = field(default_factory=Pagination)


@dataclass
class Account:
    """Visitor identity plus wallet balance, scraped from allowed pages
    (nav + /dbtech-credits/); the /account/* namespace is robots-disallowed.
    ``levelX`` fields track Null Level progress (points toward the next
    level, on which credits-earning eligibility hangs)."""
    userId: int | None = None
    username: str | None = None
    avatarUrl: str | None = None
    creditsBalance: int | None = None
    reactionsGiven: int | None = None
    levelPoints: int | None = None
    levelPointsNeeded: int | None = None
    levelNext: int | None = None


def to_dict(model) -> dict:
    return asdict(model)
