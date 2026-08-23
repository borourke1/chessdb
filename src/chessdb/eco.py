"""ECO opening classification.

Reference data comes from https://github.com/lichess-org/chess-openings
(a.tsv..e.tsv) - a free, already-solved opening-name lookup table, not
something derived from our own games. Fetch once with
`scripts/fetch_eco_data.sh` (or re-run it any time upstream adds lines);
`load_eco_openings` loads whatever is in data/eco/ into the eco_openings
table, and `build_eco_index` / `classify_prefix` do the actual per-game
classification during ingestion.
"""
from __future__ import annotations

import csv
import re
import sqlite3
from pathlib import Path

_MOVE_NUMBER_RE = re.compile(r"\d+\.(\.\.)?\s*")


def normalize_pgn_line(pgn: str) -> str:
    """'1. e4 e5 2. Nf3' -> 'e4 e5 Nf3' - same space-separated SAN format
    games.moves_san uses, so classification is a plain string-prefix match."""
    return " ".join(_MOVE_NUMBER_RE.sub("", pgn).split())


def load_eco_openings(conn: sqlite3.Connection, eco_dir: Path) -> int:
    rows: list[tuple[str, str, str]] = []
    for tsv_path in sorted(eco_dir.glob("*.tsv")):
        with tsv_path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                moves_san = normalize_pgn_line(row["pgn"])
                if moves_san:
                    rows.append((moves_san, row["eco"], row["name"]))
    conn.executemany(
        "INSERT OR REPLACE INTO eco_openings (moves_san, eco, name) VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def build_eco_index(conn: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    """Load the whole reference table into memory once per ingestion run -
    classifying by hitting SQLite per-prefix-per-game would be a huge number
    of tiny queries at this scale."""
    return {
        moves_san: (eco, name)
        for moves_san, eco, name in conn.execute(
            "SELECT moves_san, eco, name FROM eco_openings"
        )
    }


def classify_prefix(
    eco_index: dict[str, tuple[str, str]], san_moves: list[str], max_ply: int = 40
) -> tuple[str | None, str | None]:
    """Longest known opening line that is a prefix of this game's moves."""
    for ply in range(min(len(san_moves), max_ply), 0, -1):
        hit = eco_index.get(" ".join(san_moves[:ply]))
        if hit:
            return hit
    return None, None
