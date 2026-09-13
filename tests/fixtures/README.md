# Page fixtures

All real fixtures were captured from nullforums.net on 2026-09-11 and checked for
`just a moment` / `challenge-platform` markers before use.

**PII note:** the four 2026-09-12/13 fixtures (`reactions-given-page-1`,
`dbtech-credits`, `resource-4918-liked`, `thread-89951-liked`) captured the
operator's own account and have been pseudonymized in place
(`johnpork12` → `scrubuser`, id `244217` → `100200300`). Scrub new fixtures
with live account identifiers before committing. Like-history targets (other
users' resource URLs) are third-party data and stay as captured.

| fixture | source | sha256 |
| --- | --- | --- |
| `thread-89951.html` | `https://nullforums.net/threads/trending-and-latest-posts-api.89951/` | `a6cbd8a16fde5269aab01d087808082b09d7279a7ee1bb4abdc15136d4eef7c2` |
| `resource-8953.html` | `https://nullforums.net/resources/advancedkits.8953/` | `bcd4c4464515fb3a2ee60abf154c58c36799667516c12111c99483aebf8e53de` |
| `category-38.html` | `https://nullforums.net/resources/categories/minecraft-plugins.38/` | `3fd3a3cb44511d84247b5b26a6000a10406e991dfaaa9ac8e0d3232fa53fb79c` |
| `reactions-given-page-1.html` | `/account/reactions-given`, operator session, PII-scrubbed | — |
| `dbtech-credits.html` | `/dbtech-credits/` (wallet widget), PII-scrubbed | — |
| `resource-4918-liked.html` | `blockus.4918` rendered after the operator liked it (`has-reaction`) | — |
| `thread-89951-liked.html` | thread 89951 after liking post 126843 (`has-reaction`) | — |

Synthetic fixtures:

| fixture | purpose | sha256 |
| --- | --- | --- |
| `SYNTHETIC-login-wall.html` | constructed login wall / permission page for `detect_auth_wall` tests | `6db6ee269d56e24a6a287f7e058d6b5377e1c3f7b7cd326bd1b1fd351aa8afee` |
| `SYNTHETIC-edge-block.html` | constructed Cloudflare challenge page for `detect_block` tests | `bf5ebc724b77172072a046663b8316565a2363dc9789e8fa10a6531d5ed55c50` |
