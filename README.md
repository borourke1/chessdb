# chessdb

A personal, local database of master-and-above chess games, for studying
openings and browsing/replaying games by strong and famous players.

Full design decisions and rationale: [docs/PLAN.md](docs/PLAN.md).

## Status

**Built so far**: OTB ingestion from Lumbra's Gigabase, end-to-end, into a
local SQLite database, plus a local web app (opening explorer, game browser,
and game replay with a live Stockfish recommendation panel).

**Still to come**: Lichess/Chess.com ingestion and cross-source player
identity linking (see "Build sequencing" in the plan doc).

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
scripts/fetch_eco_data.sh   # pulls the lichess-org/chess-openings reference tables
```

The OTB ingest needs `7z` to extract Lumbra's Gigabase archives:

```sh
brew install p7zip
```

The web app's live engine-recommendation panel requires Stockfish on PATH:

```sh
brew install stockfish
```

## Running the OTB ingest

Lumbra's download page makes the browser solve a proof-of-work challenge
before each download (meant to deter scripted downloads), so the file is
downloaded by hand: get "OTB Elite - ELO > 2400" from the
[download area](https://lumbrasgigabase.com/en/download-area-en/) and save the
`.7z` into `data/downloads/otb_lumbra/`. Then:

```sh
.venv/bin/python -m chessdb.ingest.otb_lumbra
```

Extracts the newest `.7z` in that directory, parses every game with
`python-chess`, and loads it into `data/chessdb.sqlite3` - both the raw
`games` table and the materialized `opening_moves` explorer table
(position -> next-move stats), classifying each game's opening via
longest-prefix match against `eco_openings`. A full ingest takes a few hours.

Re-running is safe: games are deduplicated by a content hash of their move
sequence (`INSERT OR IGNORE`), so a re-run only adds genuinely new games.

Pass `--archive PATH` to ingest a specific `.7z`, or `--skip-extract` to
re-process already-extracted `.pgn` files under
`data/downloads/otb_lumbra/extracted/`.

## Monthly schedule

```sh
scripts/install_schedule.sh               # install / update
scripts/install_schedule.sh --uninstall   # remove
```

Installs two launchd agents (`com.chessdb.otb-remind`,
`com.chessdb.otb-ingest-new`):

- From the 10th of each month, a daily alert reminds you to download the new
  file until that month has a successful ingest.
- Saving a new `.7z` into `data/downloads/otb_lumbra/` starts the ingest
  automatically; an alert reports the result, including failures. Older
  archives there are deleted after a successful ingest.

Save the file into the repo, not `~/Downloads`: macOS blocks background
jobs from reading `~/Downloads`. Logs go to `data/logs/scheduled_ingest.log`.
Re-run the install script if you move the checkout.

## Running the web app

```sh
.venv/bin/python -m chessdb.web.app
```

Serves the game browser/replay UI at http://127.0.0.1:8431. Options:
`--port PORT` (default 8431), `--host HOST` (default `127.0.0.1`), and
`--db PATH` (default `data/chessdb.sqlite3`).

## Layout

```
docs/PLAN.md              full design decisions
src/chessdb/db/           SQLite schema + connection helpers
src/chessdb/eco.py        ECO opening classification (lichess-org/chess-openings)
src/chessdb/ingest/       per-source ingestion pipelines (otb_lumbra.py so far)
src/chessdb/web/          Flask game browser/replay UI
scripts/                  one-off/setup scripts
data/                     gitignored - downloads, the sqlite db, eco reference tables
```
