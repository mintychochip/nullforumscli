# nullforumscli

`nf` is a headless, read-only command line reader for the XenForo 2 forum at
`nullforums.net`. It fetches public pages and sitemap data, converts them to
structured JSON / Markdown / plain text, and politely refuses any path that the
site's `robots.txt` disallows.

## Install

```bash
pip install -e .
```

This installs the `nf` console script.

## Configuration

Create `~/.config/nullforums/config.toml` or set environment variables.

```toml
base_url = "https://nullforums.net"
cookie = "xf_user=...; xf_session=..."  # only needed for whoami
rate_limit_ms = 1000                      # minimum polite spacing
cache_dir = "~/.cache/nullforums"
state_dir = "~/.local/state/nullforums"
```

For a single shell session you can also `export NF_COOKIE='xf_user=...; xf_session=...'`.
`NF_COOKIE` is redacted from every `nf` output, including error messages.

## Commands

```bash
nf thread https://nullforums.net/threads/trending-and-latest-posts-api.89951/
nf resource --id 8953
nf category --id 38
nf whoami
nf raw https://nullforums.net/sitemap.xml
nf index --quiet
nf search advancedkits --limit 5
nf usage --window all
```

`--format json` (default), `md`, or `text` works for every command except `raw`,
which always prints HTML.

## Exit codes

| Code | Meaning                                |
|------|----------------------------------------|
| 0    | Success                                |
| 1    | Generic error (`ERROR`)                |
| 2    | Usage error (`USAGE`)                  |
| 3    | Authentication required (`AUTH_REQUIRED`) |
| 4    | Blocked by the edge (`EDGE_BLOCKED`)   |
| 5    | Disallowed by `robots.txt` (`ROBOTS_DISALLOWED`) |
| 6    | Rate limited (`RATE_LIMITED`)          |
| 7    | Parse failure (`PARSE_FAILURE`)        |
| 8    | Network error (`NETWORK_ERROR`)        |

## Limitations

- Title search only. Full-text search is not implemented because the
  `/search/` path is `Disallow`ed in the site's `robots.txt`.
- Index titles are approximations derived from slugs; `titleSource` is
  `"slug"` unless `--resolve` fetches the actual page and updates it to
  `"page"`.
- The tool does not download attachments, post content, log in, or modify
  the site. It only reads robots-allowed pages.
- `nf` refuses robots-disallowed paths rather than working around the block.
