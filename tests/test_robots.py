import pytest

from nf.errors import RobotsRefusal
from nf.robots import RobotsPolicy, assert_allowed

REAL = """User-agent: *
Disallow: /account/
Disallow: /attachments/
Disallow: /goto/
Disallow: /login/
Disallow: /misc/language
Disallow: /misc/style
Disallow: /search/
Disallow: /whats-new/
Disallow: /admin.php
Allow: /

Sitemap: https://nullforums.net/sitemap.xml
"""

UA = "nf/0.1 (read-only client; +https://github.com/jlo/nullforumscli)"


@pytest.fixture
def policy():
    return RobotsPolicy.parse(REAL, UA)


@pytest.mark.parametrize("path", [
    "/account/", "/attachments/foo.zip.1/", "/goto/post?id=1", "/login/",
    "/misc/language", "/misc/style", "/search/", "/search/member?user_id=1",
    "/whats-new/", "/whats-new/posts/", "/admin.php",
])
def test_disallowed_paths_are_refused(policy, path):
    assert policy.is_allowed(path) is False


@pytest.mark.parametrize("path", [
    "/", "/threads/foo.1/", "/threads/foo.1/page-2", "/resources/advancedkits.8953/",
    "/resources/categories/minecraft-plugins.38/", "/members/foo.123/",
    "/resources/authors/foo.46705/", "/sitemap.xml", "/sitemap-1.xml", "/robots.txt",
])
def test_allowed_paths_pass(policy, path):
    assert policy.is_allowed(path) is True


def test_bare_misc_prefix_is_not_over_blocked(policy):
    """/misc/language is disallowed, but /misc/ and /miscellaneous are not."""
    assert policy.is_allowed("/misc/") is True
    assert policy.is_allowed("/miscellaneous") is True


def test_allow_beats_disallow_on_equal_length():
    p = RobotsPolicy.parse("User-agent: *\nDisallow: /x/\nAllow: /x/\n", UA)
    assert p.is_allowed("/x/") is True


def test_longest_rule_wins():
    p = RobotsPolicy.parse(
        "User-agent: *\nDisallow: /a/\nAllow: /a/public/\n", UA)
    assert p.is_allowed("/a/secret") is False
    assert p.is_allowed("/a/public/thing") is True


def test_wildcard_and_anchor():
    p = RobotsPolicy.parse("User-agent: *\nDisallow: /*.json$\n", UA)
    # The anchor matches the path only, so a query string does not bypass it.
    assert p.is_allowed("/a/b.json") is False
    assert p.is_allowed("/a/b.json?x=1") is False
    # A path that does not actually end in .json is still allowed.
    assert p.is_allowed("/a/b.jsonx") is True


def test_no_matching_rules_means_allowed():
    p = RobotsPolicy.parse("User-agent: *\nDisallow: /secret/\n", UA)
    assert p.is_allowed("/anything/else") is True


def test_specific_user_agent_group_wins_over_star():
    text = "User-agent: nf\nDisallow: /blocked/\n\nUser-agent: *\nDisallow: /\n"
    p = RobotsPolicy.parse(text, UA)
    assert p.is_allowed("/threads/foo.1/") is True
    assert p.is_allowed("/blocked/") is False


def test_assert_allowed_raises_with_the_rule_named(policy):
    with pytest.raises(RobotsRefusal) as ei:
        assert_allowed(policy, "https://nullforums.net/search/?q=x")
    assert "/search/" in ei.value.message
    assert ei.value.exit_code == 5


def test_assert_allowed_accepts_an_allowed_url(policy):
    assert_allowed(policy, "https://nullforums.net/threads/foo.1/")
