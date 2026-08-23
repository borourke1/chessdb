# chessdb - project plan

A personal, local database of master-and-above chess games, aggregated from
free sources, for studying openings and browsing games by strong/famous
players. This document is the settled design - see decisions below - and the
starting point for anyone (human or agent) picking this project back up.

## Scope

- **"Master and above"**: title-based in principle (FM/CM/IM/GM/WFM/WIM/WGM).
  In practice, applied per source (see Filtering strategy below).
- **Primary use cases**:
  1. Opening explorer - browse a position, see what masters played next and
     how often/with what results.
  2. Game search/browse - find and replay full games, e.g. by a specific
     famous player.
- **Explicitly out of scope for v1**: engine evaluation (Stockfish or
  otherwise). v1 is master-move statistics only; engine annotation is a
  clearly separable later phase.

## Data sources

| Source | Scope | License | Filtering |
|---|---|---|---|
| [Lumbra's Gigabase](https://lumbrasgigabase.com) | OTB | CC BY-NC-SA (fine for personal, non-commercial use) | Use the **"OTB Elite (ELO > 2400)"** file, not "OTB Complete" - the complete file has *no* rating filter at all (confirmed via the site: only the Elite file is pre-filtered by strength). Elite is ~125MB vs ~1.48GB, and is the only variant that actually matches "master and above." |
| Lichess (open database / API) | Online | CC0 | Title-based - filter to actual titled usernames (see Titled-player rosters below) |
| Chess.com (official PubAPI only) | Online | Per Chess.com's User Agreement; bulk scraping/AI-dataset use is prohibited by the March 2026 ToS update, but the documented, rate-limited PubAPI used as intended is a materially different (sanctioned) access pattern - worth re-checking ToS periodically | Title-based via `/pub/titled/{title}` roster |

Explicitly **not** used: TWIC's paid full archive (rolling free window only
would be redundant with Lumbra's/KingBase-era coverage), ChessBase Online
Database (browse-only, not licensed for bulk download), FICS (unclear
license/provenance).

**Online time controls**: blitz + rapid + classical. Bullet excluded - it's
played carelessly enough (pre-moves, flagging) to be a poor signal for
opening theory.

**Titled-player rosters**: Chess.com's `/pub/titled/{title}` (clean, official)
for Chess.com; Lichess's community-run "titled players" team page
(best-effort - will miss some active titled players who never joined, judged
an acceptable gap vs. the false-match risk of fuzzy FIDE cross-referencing).

## Freshness

- Ingestion runs **monthly**, for both OTB and online sources (Lumbra's
  itself only updates monthly, on the first Tuesday, so more frequent OTB
  polling buys nothing; online sources are pinned to the same cadence purely
  to keep one pipeline schedule instead of two).
- Runs via **local-only cron/launchd** on this machine. This is a personal,
  best-effort tool - if the machine is off when a run is due, that month is
  simply skipped, not backfilled specially.
- **Failure handling**: a native macOS notification (`osascript`) fires if a
  monthly run fails, so silent staleness doesn't go unnoticed. No email/cloud
  infra.

## Architecture

- **Language**: Python (`python-chess` is the load-bearing dependency for
  PGN parsing, move/FEN handling).
- **Storage**: SQLite, single local file. Chosen over DuckDB/Postgres for
  zero-ops simplicity; both target access patterns (position-prefix lookups,
  per-game replay) map fine onto well-chosen indexes at this scale.
- **Opening tree**: a *materialized* table (`opening_moves`, keyed by
  normalized FEN) mapping position -> next-move statistics (game count,
  win/draw/loss), rebuilt as part of each monthly ingest rather than computed
  live on every query.
- **Opening names**: ECO codes/names attached via the same reference dataset
  Lichess itself uses ([lichess-org/chess-openings](https://github.com/lichess-org/chess-openings),
  a free, open lookup table - not something to build from scratch).
- **Interface**: a local web app (a clickable board with move stats next to
  it, plus full-game browsing) - deferred until the OTB ingestion slice is
  proven; see Build sequencing.

## Cross-source player identity

A given player has different identities across sources (an OTB PGN name
string, a Lichess username, a Chess.com username). v1 links these
**automatically** via fuzzy matching, but a match only merges into the
canonical player record after **manual review** - never a silent merge. A
wrong automatic merge (attributing one person's games to another) is worse
than leaving two identities unlinked, so accuracy is prioritized over
coverage. This is a later-phase concern - it only matters once more than one
source's players need reconciling, i.e. once online sources are added.

## Build sequencing

1. **OTB via Lumbra's Gigabase, end-to-end** (this phase): download -> parse
   -> load into SQLite -> materialize opening tree -> ECO classification.
   Validates the whole pipeline and schema against the simplest source
   (single bulk download, no roster-building, no per-user API pagination)
   before the harder parts (titled-roster discovery, API integration,
   cross-source identity linking) are introduced.
2. Lichess ingestion (titled-roster discovery, per-user game export,
   time-control filtering).
3. Chess.com ingestion (official PubAPI, `/pub/titled/*` roster).
4. Cross-source player identity linking + review queue.
5. Local web app (opening explorer + game browser).
6. v2, deferred: engine evaluation layer.

## Known operational risks

- **Lumbra's Gigabase files are hosted on Mega.nz**, not a plain HTTP file
  server. Automated download requires `megatools` (installed via Homebrew).
  Mega's free/anonymous tier has bandwidth quotas shared across all
  anonymous downloaders hitting the same IP - a monthly automated pull could
  intermittently fail for reasons outside our control. This is exactly what
  the failure-notification step exists to catch.
- Chess.com's ToS has changed once already in 2026 (added an explicit
  anti-AI-dataset clause); worth a periodic manual re-read before assuming
  continued access is fine.
