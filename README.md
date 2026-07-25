# boxd-bridge

Export your Plex watch history to a CSV that Letterboxd imports, and keep doing
it as new watches accumulate.

## The problem

You watch films on Plex. You keep a diary on Letterboxd. You want the first to
show up in the second without typing anything.

That is what "scrobbling" means, and it is what everybody actually wants: watch a
film, and it appears in your diary. Every project in this space starts there.

## Why true scrobbling is not possible

Letterboxd has no open write API. Access is a **request-only closed beta**
([letterboxd.com/api-beta](https://letterboxd.com/api-beta/)): you email them,
and if approved you get an `api_key` and `api_secret` and sign your requests
with HMAC. There is no public signup, and no way to build a tool that strangers
can self-host against it.

This is the wall every Plex-to-Letterboxd project hits, and it is why almost all
of them on GitHub are abandoned. The ones that work at all require API
credentials their users cannot obtain. Nothing in this repository changes that,
and you should be suspicious of any project claiming otherwise.

## Why not browser automation

Driving a headless browser is the only remaining path to true automation, and it
is a deliberate no.

The stronger objection is **correctness**. Automating the import means
auto-confirming Letterboxd's match review screen. For any film matched by title
rather than by ID, that screen is the only thing standing between a wrong guess
and a wrong diary entry. A tool that clicks through it will eventually log a
film you never watched, quietly, and you will not find out. Silent corruption of
a personal record is worse than a manual step.

The second objection is that it requires storing your Letterboxd password, in
plaintext or near enough, so a background job can log in as you. That is a bad
trade for a convenience feature.

## How this solves it

Letterboxd's CSV importer is **idempotent**. From
[their import documentation](https://letterboxd.com/about/importing-data/):

> The importer updates existing Diary Entries if a film is imported with a
> `WatchedDate` that matches an existing entry for the same film already in your
> Diary.

> Multiple lines containing the same film with the same `WatchedDate` will be
> combined into a single entry when the imported data is saved.

**That single property is the whole idea.** Re-importing history you have
already imported updates those entries instead of duplicating them. So the CSV
importer, which is documented as a one-time migration tool, is safe to run
repeatedly, which makes it a sync mechanism.

Everything follows from it. There is no watermark, no "last synced" timestamp,
no dedupe table, and no database, because none of them are needed to avoid
duplicates. Comparable tools carry that machinery to solve a problem the
importer already solved.

### Keeping the review screen short

Safe to re-import is not the same as pleasant to re-import: Letterboxd shows a
review row for every line in the file, so exporting your entire history forever
means the tool gets more tedious the longer you use it.

So exports take an optional `since` date:

```
/api/export.csv?since=2026-07-25
```

After any preview the UI shows a ready-made link with today's date already in
it. Bookmark it, and every future run exports only what is new. Because nothing
is stored server-side, that bookmark **is** the sync state, held by you rather
than by this application.

First run: export everything and migrate your history. After that: use the
bookmark.

## What it does

- **Movies only.** Episodes and tracks are dropped; Letterboxd is film-only.
- **Exact ID matching.** Emits `tmdbID` and `imdbID` from Plex's metadata so
  films match precisely instead of by title guess, falling back to
  `Title` + `Year` when no IDs exist.
- **Rewatch detection.** Groups by film, sorts by date, and marks everything
  after the first viewing as `Rewatch=true`. Rewatch status is computed across
  your whole history, so a `since` window still reports a rewatch correctly even
  when the first viewing falls outside it.
- **Correct dates.** Converts to your timezone *before* truncating to a calendar
  date, so a film finished at 11pm does not land on tomorrow.
- **Completion threshold.** Skips abandoned plays (Tautulli source only).
- **Star ratings, off by default.** Opt in with `EXPORT_RATINGS=true`. Read the
  attribution caveat below first: it is the reason this is not on by default.
- **1MB splitting.** Letterboxd caps uploads at 1MB; larger exports split into
  parts, each with its own header row.

Exported columns: `tmdbID`, `imdbID`, `Title`, `Year`, `WatchedDate`, `Rewatch`,
plus `Rating10` when ratings are enabled. When they are not, that column is
absent from the file entirely rather than present and blank.

## Ratings, and why they are off by default

Plex stores a personal star rating per account, and Letterboxd's importer accepts
one. The obvious move is to export it. There is a trap.

**Neither source can return a *specific* user's rating.** Verified against a live
Tautulli and a live Plex Media Server:

- Tautulli's `get_metadata` ignores `user_id`. The same call returns the same
  `user_rating` with no user, with `user_id=0`, and with an unrelated user id.
- Plex's `/library/metadata/<key>` ignores `accountID`. `userRating` is identical
  for `accountID=1` and `accountID=2`.

Both return the rating belonging to whoever owns the **token**. On a shared
server that is the admin, not the person whose history is being exported. So the
naive implementation writes the server owner's rating into their friends'
diaries. A missing rating is an empty cell; a wrong rating is a false statement
in someone's public record, and they would have no reason to suspect it.

So ratings are gated two ways:

1. `EXPORT_RATINGS` defaults to `false`, and the column is omitted entirely.
2. Even when enabled, a rating is attached **only to rows belonging to the
   account that owns the token**:
   - `plex-oauth` mode: every visitor authenticates as themselves, so the token
     is theirs and all of their rows can carry ratings.
   - `env` mode with Tautulli: only the Tautulli admin's own plays are rated.
     Other users' rows get an empty cell.
   - `env` mode with Plex directly: only the server owner's rows (Plex
     `accountID` 1) are rated.

Enabling `EXPORT_RATINGS` is therefore safe, but on a shared server it will only
populate ratings for your own watches. That is the honest ceiling, not a bug.

Ratings use `Rating10` (integers 1 to 10) because Plex already stores 0 to 10,
which makes the mapping exact rather than a lossy conversion to Letterboxd's
half-star `Rating` column. A rating of 0 means unrated in Plex and produces an
empty cell, never a literal `0`, which would import as a real rating of zero.

## Honest limitations

**It still requires a manual click.** Preview, download, upload to Letterboxd,
confirm. Roughly twenty seconds. This is not automatic and this page will not
pretend otherwise.

**Deleted films lose their IDs.** Tautulli keeps a play in its history after you
remove the file, but the metadata lookup no longer resolves, so those rows fall
back to `Title` + `Year` and import via Letterboxd's fuzzy match. On the
author's real library this was 37 of 65 rows. They import fine; they are the
ones worth a glance on the review screen.

**Movies only.** TV watches are dropped entirely. Letterboxd does not track
them.

**Partial watches are excluded.** The default threshold is 85% watched,
configurable via `COMPLETION_THRESHOLD`. Abandoning a film 20 minutes in does not
create a diary entry. This only applies to the Tautulli source: Plex's history
records that something was viewed without a completion percentage.

**Ratings need opting in, and only cover your own watches.** See the ratings
section above. On a shared server, other users' rows will have an empty rating
cell no matter what, because the API cannot tell us what they rated.

**Reviews and tags are not exported.** Neither source stores them.

## Why this is probably as good as it gets

The manual step is Letterboxd's constraint, not a gap in this implementation. A
better-engineered tool would not remove it: without API access, the only way to
delete that click is browser automation, and that trades a silent-wrong-entry
risk plus your password for twenty seconds. That is a bad trade.

So the honest ceiling for a tool like this is: make the export correct, make it
safe to repeat, and make each repeat small. That is what this does.

If Letterboxd ever opens their write API, the right move is to rewrite the
export layer against it. The source adapters and transform logic here would
carry over unchanged.

## Quick start (self-hosted)

```bash
git clone https://github.com/pete-builds/boxd-bridge.git
cd boxd-bridge
cp .env.example .env
# edit .env: set TAUTULLI_URL + TAUTULLI_APIKEY (or PLEX_URL + PLEX_TOKEN)
docker compose up -d --build
```

Open <http://localhost:8724>, press **Preview**, then **Download CSV**, then
upload it at [letterboxd.com/import](https://letterboxd.com/import/).

### Choosing a source

| | Tautulli | Plex direct |
|---|---|---|
| History depth | Full, kept independently | Plex prunes its own |
| Per-user attribution | Clean, with a user picker | Admin token sees its own account |
| Completion filtering | Yes, via `percent_complete` | No, an entry means "viewed" |

Tautulli is preferred, and wins if both are configured. Find its API key under
*Settings, Web Interface, API*.

### Plex and host networking

Plex enforces a host-header allowlist. A request from another machine over plain
LAN gets an **empty reply**, which looks like a hang rather than an error. If
Plex runs on the same host as this container, use `network_mode: host` and
`PLEX_URL=http://localhost:32400`. `docker-compose.yml` ships a commented block
for that case.

## Hosted mode (multi-tenant)

Set `AUTH_MODE=plex-oauth` and anyone can sign in with their own Plex account
and export their own history.

```env
AUTH_MODE=plex-oauth
SESSION_SECRET=<32+ random characters>
PUBLIC_BASE_URL=https://boxd.example.com
```

- Sign-in uses Plex's official PIN flow (the forwarding variant).
- **Your Plex token is never written to the server's disk.** It lives in a
  signed and encrypted session cookie in your own browser with a short TTL.
- Server discovery filters plex.tv's connection list to **non-local** addresses.
  A hosted instance using a `local` URI would be reaching its own LAN, not
  yours.
- PIN creation is rate limited per IP.
- Hosted mode always uses the Plex source. You cannot OAuth into someone else's
  Tautulli, so Tautulli is single-tenant only.

`PUBLIC_BASE_URL` must be HTTPS in practice, since cookies carry the `Secure`
flag. Set `COOKIE_SECURE=false` only for local HTTP testing.

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `AUTH_MODE` | `env` | `env` or `plex-oauth` |
| `TAUTULLI_URL` | | e.g. `http://192.168.1.10:8181` |
| `TAUTULLI_APIKEY` | | Settings, Web Interface, API |
| `PLEX_URL` | | **Env-only.** Never read from the browser |
| `PLEX_TOKEN` | | Plex auth token |
| `DISPLAY_TIMEZONE` | `UTC` | IANA name, e.g. `America/New_York` |
| `COMPLETION_THRESHOLD` | `85` | Percent watched. Tautulli source only |
| `EXPORT_RATINGS` | `false` | Adds the `Rating10` column. Only populates rows owned by the token holder; see above |
| `CSV_CHUNK_BYTES` | `900000` | Split threshold, under Letterboxd's 1MB cap |
| `SESSION_SECRET` | | Required for `plex-oauth`, 32+ chars |
| `SESSION_TTL_SECONDS` | `1800` | Session cookie lifetime |
| `COOKIE_SECURE` | `true` | Set false only for local HTTP |
| `PUBLIC_BASE_URL` | | Required for `plex-oauth` |
| `RATE_LIMIT_REQUESTS` | `5` | Sign-in attempts per window, per IP |
| `RATE_LIMIT_WINDOW_SECONDS` | `300` | |

The app refuses to start if the selected mode is missing something it needs,
rather than serving a half-working instance.

### Why `PLEX_URL` is env-only

If a hosted instance let a visitor type a server address, the server would fetch
whatever they typed. That is server-side request forgery. In hosted mode, server
addresses come only from the authenticated plex.tv `/resources` response.

## API

| Endpoint | Purpose |
|---|---|
| `GET /` | UI |
| `GET /healthz` | Liveness. Auth-exempt, exposes no configuration |
| `GET /api/preview` | Row counts plus the first 10 entries, as JSON |
| `GET /api/export.csv` | The CSV |
| `GET /api/users` | Selectable users (env mode + Tautulli only) |
| `POST /auth/plex/start` | Begin Plex sign-in (`plex-oauth` only) |
| `GET /auth/plex/callback` | Plex forwards back here (`plex-oauth` only) |
| `POST /auth/logout` | Clear the session (`plex-oauth` only) |

Query parameters on `/api/preview` and `/api/export.csv`:

| Parameter | Notes |
|---|---|
| `since` | `YYYY-MM-DD`. Inclusive lower bound on the **local** watch date. A malformed value is a 400; a future value returns a valid header-only CSV |
| `user_id` | Restricts to one user. Ignored in hosted mode, where you only ever get your own history |
| `part` | Which part of a split export to download, starting at 1 |

`/api/export.csv` sets `X-Boxd-Rows`, `X-Boxd-Total-Rows`, `X-Boxd-Parts`,
`X-Boxd-Next-Since`, `X-Boxd-Ratings` (`on`/`off`), and `X-Boxd-Since` when a
window is active.

## Development

```bash
uv sync --all-groups
uv run pytest
```

Python 3.14, pinned identically in `pyproject.toml`, `uv.lock`,
`.python-version`, the `Dockerfile`, and CI.

## Security posture

- Runs as a non-root user, on a read-only root filesystem.
- No database, and no secrets at rest beyond your own `.env`.
- Session cookies are `HttpOnly`, `Secure`, `SameSite=Lax`, encrypted and
  authenticated, with an enforced TTL.
- Rate limiting on the endpoint that talks to plex.tv.
- Dependabot is enabled without auto-merge; a human reviews every update.

## License

MIT
