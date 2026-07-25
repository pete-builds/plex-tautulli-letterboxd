# plex-tautulli-letterboxd

Export Plex or Tautulli watch history to a CSV that Letterboxd imports.

- Reads Tautulli or Plex
- Exact `tmdbID`/`imdbID` matching, `Rewatch` flags, timezone-correct dates
- `since` filter so repeat imports stay small
- Self-hosted for one user, or multi-tenant with Plex sign-in

## Prior art

[`plex2letterboxd`](https://github.com/mtimkovich/plex2letterboxd) reads Plex and
writes a Letterboxd CSV with title, year, IMDb id, rating and watched date. It is
maintained and it works. **If that covers you, use it.**

[`tautulli-watched-sync`](https://github.com/JvSomeren/tautulli-watched-sync)
needs approved Letterboxd API credentials most people can't get, and hasn't been
updated since 2020.

How this one differs (checked against `plex2letterboxd`'s source, not its
README):

| | `plex2letterboxd` | this |
|---|---|---|
| Source | Plex | Plex **or Tautulli** |
| Id columns | `imdbID` | `tmdbID` and `imdbID` |
| `Rewatch` flags | not emitted | yes, computed across full history |
| Timezone | no conversion in the source | converts before truncating to a date |
| Repeat exports | whole history every run | `since` filter |
| Ratings | always on | opt-in, and only rows the token owner actually rated |
| Form | run-once CLI | container with a web UI |

Reading Tautulli is the one that matters most in practice. Tautulli keeps its own
play records, so history survives after you delete a file from the library.

## How it works

Letterboxd's CSV importer is idempotent. From their
[import docs](https://letterboxd.com/about/importing-data/):

> The importer updates existing Diary Entries if a film is imported with a
> `WatchedDate` that matches an existing entry for the same film already in your
> Diary.

Re-importing history you already imported updates those entries instead of
duplicating them, which makes a one-time migration tool safe to run repeatedly.
That is why there is no watermark, no last-synced timestamp, no dedupe table and
no database here. None are needed.

Safe to repeat isn't the same as pleasant to repeat, though, since Letterboxd
shows a review row per line. So exports take an optional `since` date:

```
/api/export.csv?since=2026-07-25
```

After a preview the UI gives you that link with today's date filled in. Bookmark
it and each run exports only what's new. Nothing is stored server-side, so the
bookmark is the sync state.

First run exports everything. After that, use the bookmark.

## What it exports

Columns: `tmdbID`, `imdbID`, `Title`, `Year`, `WatchedDate`, `Rewatch`, plus
`Rating10` when ratings are on.

- Movies only. Episodes and tracks are dropped.
- `tmdbID`/`imdbID` from Plex metadata for exact matching, falling back to
  `Title` + `Year` when no ids exist.
- `Rewatch` computed across your whole history, so a `since` window still flags a
  rewatch when the first viewing falls outside it.
- Dates converted to `DISPLAY_TIMEZONE` before truncating, so a film finished at
  11pm doesn't land on tomorrow.
- Plays under `COMPLETION_THRESHOLD` skipped (Tautulli only).
- Exports over 1MB split into parts, each with its own header.

## Why the import is manual

Letterboxd has no open write API. Access is a request-only closed beta
([api-beta](https://letterboxd.com/api-beta/)), so nothing can create diary
entries for you. Browser automation is the only way around that, and it means
auto-confirming the match screen and storing your Letterboxd password. Not worth
it to save twenty seconds of clicking.

## Limitations

- **Manual import.** Preview, download, upload, confirm.
- **Deleted films lose their ids.** Tautulli keeps the play after you remove the
  file, but metadata no longer resolves, so those rows fall back to fuzzy title
  matching. On the author's library that was 37 of 65 rows.
- **Movies only.** Letterboxd doesn't track TV.
- **Partial watches excluded** below 85% by default. Tautulli only; Plex history
  records a view without a percentage.
- **Reviews and tags aren't exported.** Neither source stores them.
- **Ratings are opt-in and only cover your own watches.** See below.

## Ratings

Off by default, enabled with `EXPORT_RATINGS=true`, because neither source can
return a *specific* user's rating. Verified against live instances:

- Tautulli's `get_metadata` ignores `user_id`. Same `user_rating` with no user,
  with `user_id=0`, and with an unrelated id.
- Plex's `/library/metadata/<key>` ignores `accountID`. `userRating` is identical
  for `accountID=1` and `accountID=2`.

Both return the rating of whoever owns the token. On a shared server that's the
admin, so the naive version writes the server owner's rating into their friends'
diaries. Instead, ratings attach only to rows owned by the token holder: every
row in `plex-oauth` mode, the Tautulli admin's own plays in `env` mode, or Plex
`accountID` 1. Everyone else gets an empty cell.

Uses `Rating10` since Plex already stores 0 to 10, so no lossy conversion. A 0
means unrated and yields an empty cell, never a literal `0`.

## Quick start

```bash
git clone https://github.com/pete-builds/plex-tautulli-letterboxd.git
cd plex-tautulli-letterboxd
cp .env.example .env
# set TAUTULLI_URL + TAUTULLI_APIKEY, or PLEX_URL + PLEX_TOKEN
docker compose up -d --build
```

Open <http://localhost:8724>, press **Preview**, then **Download CSV**, then
upload at [letterboxd.com/import](https://letterboxd.com/import/).

### Choosing a source

| | Tautulli | Plex direct |
|---|---|---|
| History depth | Kept independently of Plex | Plex prunes its own |
| Per-user attribution | Clean, with a user picker | Token's own account |
| Completion filtering | Yes, via `percent_complete` | No |

Tautulli wins if both are configured. Its API key is under *Settings, Web
Interface, API*.

### Plex host networking

Plex enforces a host-header allowlist, so a request from another machine over
plain LAN gets an empty reply that looks like a hang. If Plex runs on the same
host as this container, use `network_mode: host` and
`PLEX_URL=http://localhost:32400`. `docker-compose.yml` ships a commented block
for it.

## Hosted mode

`AUTH_MODE=plex-oauth` lets anyone sign in with their own Plex account and export
their own history.

```env
AUTH_MODE=plex-oauth
SESSION_SECRET=<32+ random characters>
PUBLIC_BASE_URL=https://boxd.example.com
```

- Sign-in uses Plex's official PIN flow (forwarding variant).
- Your Plex token is never written to disk. It lives in a signed, encrypted
  session cookie in your own browser with a short TTL.
- Server discovery filters plex.tv's connection list to non-local addresses,
  since a hosted instance using a `local` URI would reach its own LAN.
- PIN creation is rate limited per IP.
- Always uses the Plex source. You can't OAuth into someone else's Tautulli.

`PUBLIC_BASE_URL` needs HTTPS because cookies carry `Secure`. Set
`COOKIE_SECURE=false` only for local HTTP testing.

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `AUTH_MODE` | `env` | `env` or `plex-oauth` |
| `TAUTULLI_URL` | | e.g. `http://192.168.1.10:8181` |
| `TAUTULLI_APIKEY` | | Settings, Web Interface, API |
| `PLEX_URL` | | Env-only, never read from the browser |
| `PLEX_TOKEN` | | Plex auth token |
| `DISPLAY_TIMEZONE` | `UTC` | IANA name, e.g. `America/New_York` |
| `COMPLETION_THRESHOLD` | `85` | Percent watched. Tautulli only |
| `EXPORT_RATINGS` | `false` | Adds `Rating10`. Token holder's rows only |
| `CSV_CHUNK_BYTES` | `900000` | Split threshold, under Letterboxd's 1MB cap |
| `SESSION_SECRET` | | Required for `plex-oauth`, 32+ chars |
| `SESSION_TTL_SECONDS` | `1800` | Session cookie lifetime |
| `COOKIE_SECURE` | `true` | False only for local HTTP |
| `PUBLIC_BASE_URL` | | Required for `plex-oauth` |
| `RATE_LIMIT_REQUESTS` | `5` | Sign-in attempts per window, per IP |
| `RATE_LIMIT_WINDOW_SECONDS` | `300` | |

The app refuses to start if the selected mode is missing something it needs.

`PLEX_URL` is env-only because a hosted instance letting visitors type a server
address would fetch whatever they typed, which is SSRF. In hosted mode addresses
come only from the authenticated plex.tv `/resources` response.

## API

| Endpoint | Purpose |
|---|---|
| `GET /` | UI |
| `GET /healthz` | Liveness. Auth-exempt, exposes no config |
| `GET /api/preview` | Row counts plus first 10 entries, JSON |
| `GET /api/export.csv` | The CSV |
| `GET /api/users` | Selectable users (env mode + Tautulli) |
| `POST /auth/plex/start` | Begin sign-in (`plex-oauth`) |
| `GET /auth/plex/callback` | Plex forwards back here (`plex-oauth`) |
| `POST /auth/logout` | Clear the session (`plex-oauth`) |

Query parameters on `/api/preview` and `/api/export.csv`:

| Parameter | Notes |
|---|---|
| `since` | `YYYY-MM-DD`, inclusive lower bound on the local watch date. Malformed is a 400; a future date returns a header-only CSV |
| `user_id` | Restricts to one user. Ignored in hosted mode |
| `part` | Which part of a split export, starting at 1 |

`/api/export.csv` sets `X-Boxd-Rows`, `X-Boxd-Total-Rows`, `X-Boxd-Parts`,
`X-Boxd-Next-Since`, `X-Boxd-Ratings`, and `X-Boxd-Since` when a window is active.

## Development

```bash
uv sync --all-groups
uv run pytest
```

Python 3.14, pinned identically in `pyproject.toml`, `uv.lock`,
`.python-version`, the `Dockerfile` and CI.

## Security

- Non-root user, read-only root filesystem.
- No database, no secrets at rest beyond your own `.env`.
- Session cookies are `HttpOnly`, `Secure`, `SameSite=Lax`, encrypted and
  authenticated, with an enforced TTL.
- Rate limiting on the endpoint that talks to plex.tv.
- Dependabot enabled without auto-merge.

## License

MIT
