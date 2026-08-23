-- chessdb schema
--
-- games: one row per ingested game, from any source.
-- opening_positions / opening_moves: the materialized opening tree that
--   powers the explorer - rebuilt (incrementally extended) on each ingest.
-- eco_openings: reference table of known opening lines -> ECO code + name,
--   loaded once from the lichess-org/chess-openings dataset, not derived
--   from our own games.

CREATE TABLE IF NOT EXISTS games (
    id              INTEGER PRIMARY KEY,
    source          TEXT NOT NULL,          -- 'otb_lumbra', 'lichess', 'chesscom'
    source_file     TEXT,                   -- provenance: which downloaded file this came from
    white_name      TEXT,
    black_name      TEXT,
    white_elo       INTEGER,
    black_elo       INTEGER,
    white_title     TEXT,
    black_title     TEXT,
    event           TEXT,
    site            TEXT,
    round           TEXT,
    date            TEXT,                   -- ISO 'YYYY-MM-DD' where known, else NULL
    result          TEXT,                   -- '1-0' | '0-1' | '1/2-1/2' | '*'
    eco             TEXT,
    opening_name    TEXT,
    ply_count       INTEGER NOT NULL,
    moves_san       TEXT NOT NULL,           -- space-separated SAN, no move numbers
    content_hash    TEXT NOT NULL,           -- sha256 over (normalized moves), for dedup
    ingested_at     TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_games_content_hash ON games(content_hash);
CREATE INDEX IF NOT EXISTS idx_games_white ON games(white_name);
CREATE INDEX IF NOT EXISTS idx_games_black ON games(black_name);
CREATE INDEX IF NOT EXISTS idx_games_eco ON games(eco);
CREATE INDEX IF NOT EXISTS idx_games_date ON games(date);

CREATE TABLE IF NOT EXISTS opening_positions (
    fen_key         TEXT PRIMARY KEY,        -- board+turn+castling+ep fields of FEN only
    ply             INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS opening_moves (
    from_fen_key    TEXT NOT NULL,
    move_san        TEXT NOT NULL,
    to_fen_key      TEXT NOT NULL,
    game_count      INTEGER NOT NULL DEFAULT 0,
    white_wins      INTEGER NOT NULL DEFAULT 0,
    black_wins      INTEGER NOT NULL DEFAULT 0,
    draws           INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (from_fen_key, move_san)
);

CREATE INDEX IF NOT EXISTS idx_opening_moves_from ON opening_moves(from_fen_key);

CREATE TABLE IF NOT EXISTS eco_openings (
    moves_san       TEXT PRIMARY KEY,        -- space-separated SAN moves from the start position,
                                              -- same format as games.moves_san, so classification
                                              -- is a longest-prefix string match against this table
    eco             TEXT NOT NULL,
    name            TEXT NOT NULL
);

-- Bookkeeping: one row per completed ingestion run, for the monthly pipeline
-- to know what it's already processed and to report status.
CREATE TABLE IF NOT EXISTS ingest_runs (
    id              INTEGER PRIMARY KEY,
    source          TEXT NOT NULL,
    source_file     TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    games_read      INTEGER,
    games_inserted  INTEGER,
    games_duplicate INTEGER,
    status          TEXT NOT NULL DEFAULT 'running'  -- 'running' | 'ok' | 'failed'
);
