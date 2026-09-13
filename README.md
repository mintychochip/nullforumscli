# nullforumscli

`nf` is a headless command-line client for the XenForo 2 forum at
`nullforums.net`, written in Rust. It reads threads, resources, and your own
account state (wallet, level progress, like history), can like posts and
resources, and downloads resource files — all gated by the site's
`robots.txt`, with honest self-identifying requests.

The full endpoint surface this client talks to is documented in
[`api_map.md`](api_map.md) (probed live; every status observed, not guessed).

## Build

Requires Rust 1.75+.

```bash
cd nf-rust
cargo build --release
# binary at nf-rust/target/release/nf
```

## Configuration

Create `~/.config/nullforums/config.toml` or set environment variables.

```toml
base_url = "https://nullforums.net"
cookie = "xf_user=...; xf_session=..."   # needed for me/likes/like/download/whoami
rate_limit_ms = 1000                      # minimum polite spacing
```

Env overrides: `NF_BASE_URL`, `NF_COOKIE`, `NF_RATE_LIMIT_MS`,
`NF_CACHE_DIR`, `NF_STATE_DIR`, `NF_CONFIG` (custom config path).

`NF_COOKIE` is redacted from every error message; it is never logged or
written by the tool.

## Commands

```bash
nf thread  <url|id>            # read a thread and its posts (JSON)
nf resource <url|id>           # read a resource's metadata (JSON)
nf me                          # identity, credits balance, level progress
nf whoami                      # session check
nf likes [--page N]            # list reactions you've handed out
nf like <url|id>               # like a post or resource (one-way on this site)
nf download <resource> [-o f]  # stream a resource file to disk
```

All output is JSON.

## Behavior notes

- **robots.txt is a hard gate** for reads: `/account/`, `/attachments/`,
  `/goto/`, `/login/`, `/search/`, `/whats-new/` etc. are refused before any
  request. One documented exception: `likes` fetches `/account/reactions-given`
  (the only endpoint for your own like history) through an explicit
  `/account/`-only override, under your own session.
- **Likes are one-way** — the site disables reaction removal; re-liking
  returns `"already"`.
- **Downloads** are gated by a Like on most resources. When the server
  answers with its gate page (200 HTML), the client fails fast with a hint
  instead of saving garbage.
- Requests are rate-limited (default 1s spacing) and same-origin enforced.
- Level math: uploads earn $0.10 at Level 2 (400 pts), updates $0.05 at
  Level 3 (600 pts), minimum payout $10.00, 50 earning actions/day.

## Exit codes

`0` success · `1` generic error · auth/edge/network failures print a JSON
error object to stderr and exit non-zero.
