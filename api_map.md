# nullforums.net API surface map

Probed live 2026-09-13 with session `johnpork12` (244217), UA
`nf/0.1.0 (client; +https://github.com/jlo/nullforumscli)`. Every status is
observed, not inferred.

## Transport facts

- XenForo 2 + bespoke `kz-*` theme behind Cloudflare. The honest `nf/x` UA
  passes the edge; guest requests also pass.
- REST API (`api.php`) is **disabled**: 404. `/api/` 403. Everything is HTML
  over the normal site; JSON is available only for AJAX POST responses.
- `robots.txt` disallows: `/account/`, `/attachments/`, `/goto/`, `/login/`,
  `/misc/language`, `/misc/style`, `/search/`, `/whats-new/`, `/admin.php`.
  Everything else under `Allow: /`.
- Every authenticated page carries a per-session CSRF token
  (`_xfToken`, form `epoch,hash`) plus a reaction anchor stating whether the
  session already reacted (`has-reaction` class).

## Read surface (GET, robots-gated, cacheable)

| path | status | notes |
| --- | --- | --- |
| `/forums/` | 200 | forum + thread listings |
| `/threads/{slug}.{id}/` | 200 | posts, pagination (`?page=N`) |
| `/resources/{slug}.{id}/` | 200 | title, author, version, description, download state |
| `/resources/categories/{slug}.{id}/` | 200 | resource listings, paginated |
| `/members/{slug}.{id}/` | 200 | profile; shows `Level N` + point total + awards |
| `/help/trophies/` | 200 | no point values published |
| `/award-system/list` | 200 | award catalog; no point values |
| `/dbtech-credits/` | 200 | "Transaction List" — empty until credits move |
| `/withdraw/` | 200 | earnings calculator + gates (see Money) |
| `/withdraw/requisite/` | 200 | wallet requisites (empty) |
| `/pages/nullforums-level-system/` | 200 | level thresholds; when logged in: `225 / 400 points until level 2` |
| `/robots.txt` | 200 | gate source |
| `/sitemap.xml` | 200 | index; shards `/sitemap-N.xml` for the search index |
| `/account/reactions-given` | robots REFUSED | the only like-history source; reachable only via explicit `get_own` override (status 200 through it) |
| `/search/?q=` | robots REFUSED | full-text search is off-limits; local sitemap index instead |

Refusals are mechanical (RFC 9309 precedence) and raised before any request.

## Write surface (validated, non-idempotent)

| action | method + path | observed result |
| --- | --- | --- |
| Like a post | `POST /posts/{id}/react?reaction_id=1` with `_xfToken`, `_xfWithData=1`, `_xfResponseType=json` | 200 JSON `{html:{content:...}, reactionList:...}` |
| Like a resource | `POST /resources/{slug}.{id}/update/{updateId}/react?reaction_id=1` (same form fields) | 200 JSON, same shape |
| Repeat like | same POST again | 403 JSON `errors: ["You cannot cancel this reaction."]` — the site disables un-liking; this response means *already liked* |
| Un-like | no endpoint | reactions are one-way |

The react anchor on the page already knows the session state (`has-reaction`),
so a client can short-circuit without a POST.

## Download surface

| path | behavior |
| --- | --- |
| `GET /resources/{slug}.{id}/download` | liked → `application/octet-stream`, `Content-Disposition: attachment; filename="..."` |
| | not liked → **200 HTML gate page** ("You must like the resource before downloading") — must be detected via Content-Type, not status |
| `GET /resources/{slug}.{id}/version/{versionId}/download` | same, per version; version list on `/history` |
| robots | downloads allowed |

## Money / level facts (from `/withdraw/` + level page)

- Credits balance: wallet widget (`Credits: N`) rendered in the nav of every
  page and on `/dbtech-credits/`; currency page `/dbtech-credits/currency/credits.2/`.
- Earning: **$0.10 per leak upload requires Level 2** (400 pts);
  **$0.05 per resource update requires Level 3** (600 pts);
  minimum payout **$10.00**; **50 earning actions/day** cap.
- Points accrue from posting/threads/uploads/awards; exact per-action values
  are not published anywhere (checked trophies + award pages + profile).
- Transaction history exists structurally (`Transaction List`) but is empty
  until credits move; no endpoint exposes spend events otherwise.

## Auth facts

- Session = cookie (`xf_user` primary; `xf_csrf`/`xf_session` managed by the
  site). `whoami` check: fetch `/members/` and look for `Log out`/`js-logOut`
  + nav `data-user-id`.
- `_xfToken` is session-bound and required for every react POST; it is not
  secret material (it is page-rendered) but is replayable only for the same
  session, so the CLI never stores it beyond a command run.
