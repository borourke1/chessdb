# chessdb

A personal, local database of master-and-above chess games, for studying
openings and browsing/replaying games by strong and famous players.

Full design decisions and rationale: [docs/PLAN.md](docs/PLAN.md).

## Status

**Phase 1 in progress**: OTB ingestion from Lumbra's Gigabase, end-to-end,
into a local SQLite database. Lichess/Chess.com ingestion, cross-source
player identity linking, and the web UI come after this phase is validated
(see "Build sequencing" in the plan doc).

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
scripts/fetch_eco_data.sh   # pulls the lichess-org/chess-openings reference tables
```

Requires two Homebrew tools for the OTB ingest specifically (Lumbra's
Gigabase is hosted on Mega.nz and distributed as `.7z`):

```sh
brew install megatools p7zip
```

The web app's live engine-recommendation panel requires Stockfish on PATH:

```sh
brew install stockfish
```

## Running the OTB ingest

```sh
.venv/bin/python -m chessdb.ingest.otb_lumbra
```

Downloads Lumbra's Gigabase "OTB Elite (ELO > 2400)" file, extracts it,
parses every game with `python-chess`, and loads it into
`data/chessdb.sqlite3` - both the raw `games` table and the materialized
`opening_moves` explorer table (position -> next-move stats), classifying
each game's opening via longest-prefix match against `eco_openings`.

Re-running is safe: games are deduplicated by a content hash of their move
sequence (`INSERT OR IGNORE`), so a re-run only adds genuinely new games.

Pass `--skip-download` to re-process already-extracted `.pgn` files under
`data/downloads/otb_lumbra/extracted/` without re-downloading.

## Layout

```
docs/PLAN.md              full design decisions
src/chessdb/db/           SQLite schema + connection helpers
src/chessdb/eco.py        ECO opening classification (lichess-org/chess-openings)
src/chessdb/ingest/       per-source ingestion pipelines (otb_lumbra.py so far)
scripts/                  one-off/setup scripts
data/                     gitignored - downloads, the sqlite db, eco reference tables
```
