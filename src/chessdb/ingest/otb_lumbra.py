"""Download, extract, and load Lumbra's Gigabase OTB "Elite (ELO > 2400)"
file into the chessdb SQLite database.

Deliberately uses the Elite file, not "OTB Complete": Lumbra's Complete file
carries no rating filter at all (confirmed on the site - only Elite is
pre-filtered by strength), so it's the only variant that actually matches
this project's "master and above" scope. See docs/PLAN.md.

The file is hosted on Mega.nz (not a plain HTTP file server), so downloading
it requires `megatools` (install via `brew install megatools`). Extraction
requires `7z` (`brew install p7zip`).

Usage:
    python -m chessdb.ingest.otb_lumbra [--db PATH] [--skip-download]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import chess
import chess.pgn

from chessdb import db as chessdb_db
from chessdb import eco as eco_mod

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "chessdb.sqlite3"
DOWNLOAD_DIR = REPO_ROOT / "data" / "downloads" / "otb_lumbra"
EXTRACT_DIR = DOWNLOAD_DIR / "extracted"
ECO_DIR = REPO_ROOT / "data" / "eco"

# Lumbra's WordPress-Download-Manager page for the Elite (ELO > 2400) file.
# The wpdmdl id is stable across the file's monthly re-releases; the mega.nz
# link it redirects to is resolved fresh each run since it can change.
LUMBRA_ELITE_WPDM_URL = "https://lumbrasgigabase.com/download/otb-elite-elo-2400/?wpdmdl=10343"

SOURCE_NAME = "otb_lumbra"
MAX_OPENING_PLY = 40  # 20 full moves/side - beyond this, positions are rarely shared "theory"


def resolve_mega_link(wpdm_url: str) -> str:
    """Follow Lumbra's download-page redirect to today's mega.nz link."""
    result = subprocess.run(
        [
            "curl", "-sD", "-", "-o", "/dev/null", "--max-time", "30",
            "-H", "Range: bytes=0-100",
            "-A", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            wpdm_url,
        ],
        capture_output=True, text=True, check=True,
    )
    for line in result.stdout.splitlines():
        if line.lower().startswith("location:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError(f"no redirect found for {wpdm_url}; site layout may have changed")


def download_via_mega(mega_link: str, dest_dir: Path) -> Path:
    if shutil.which("megatools") is None:
        raise RuntimeError("megatools not found - install with `brew install megatools`")
    dest_dir.mkdir(parents=True, exist_ok=True)
    before = set(dest_dir.glob("*"))
    subprocess.run(["megatools", "dl", "--path", str(dest_dir), mega_link], check=True)
    new_files = sorted(set(dest_dir.glob("*")) - before, key=lambda p: p.stat().st_mtime)
    if not new_files:
        raise RuntimeError("megatools reported success but produced no new file")
    return new_files[-1]


def extract_7z(archive_path: Path, dest_dir: Path) -> list[Path]:
    if shutil.which("7z") is None:
        raise RuntimeError("7z not found - install with `brew install p7zip`")
    dest_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["7z", "x", f"-o{dest_dir}", "-y", str(archive_path)],
        check=True, capture_output=True,
    )
    return sorted(dest_dir.rglob("*.pgn"))


def normalized_fen_key(board: chess.Board) -> str:
    """Board + turn + castling + en-passant only, dropping the halfmove/
    fullmove counters - so transpositions land on the same explorer node."""
    return " ".join(board.fen().split(" ")[:4])


def _int_or_none(raw: str | None) -> int | None:
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def _normalize_date(raw: str | None) -> str | None:
    if not raw:
        return None
    parts = raw.split(".")
    if len(parts) != 3:
        return None
    y, m, d = parts
    if not y.isdigit():
        return None
    m = m if m.isdigit() else "01"
    d = d if d.isdigit() else "01"
    try:
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    except ValueError:
        return None


def _update_opening_tree(conn, san_moves: list[str], result: str | None) -> None:
    board = chess.Board()
    from_key = normalized_fen_key(board)
    conn.execute(
        "INSERT OR IGNORE INTO opening_positions (fen_key, ply) VALUES (?, 0)",
        (from_key,),
    )
    white_win = 1 if result == "1-0" else 0
    black_win = 1 if result == "0-1" else 0
    draw = 1 if result == "1/2-1/2" else 0

    for ply, san in enumerate(san_moves[:MAX_OPENING_PLY], start=1):
        move = board.parse_san(san)
        board.push(move)
        to_key = normalized_fen_key(board)
        conn.execute(
            "INSERT OR IGNORE INTO opening_positions (fen_key, ply) VALUES (?, ?)",
            (to_key, ply),
        )
        conn.execute(
            """INSERT INTO opening_moves
                   (from_fen_key, move_san, to_fen_key, game_count, white_wins, black_wins, draws)
               VALUES (?, ?, ?, 1, ?, ?, ?)
               ON CONFLICT(from_fen_key, move_san) DO UPDATE SET
                   game_count = game_count + 1,
                   white_wins = white_wins + excluded.white_wins,
                   black_wins = black_wins + excluded.black_wins,
                   draws = draws + excluded.draws""",
            (from_key, san, to_key, white_win, black_win, draw),
        )
        from_key = to_key


def ingest_pgn_file(
    conn, pgn_path: Path, source: str, eco_index: dict
) -> tuple[int, int, int]:
    """Returns (games_read, games_inserted, games_duplicate)."""
    games_read = games_inserted = games_duplicate = 0
    with pgn_path.open(encoding="utf-8", errors="replace") as f:
        while True:
            game = chess.pgn.read_game(f)
            if game is None:
                break
            games_read += 1

            board = game.board()
            san_moves: list[str] = []
            try:
                for move in game.mainline_moves():
                    san_moves.append(board.san(move))
                    board.push(move)
            except Exception:
                continue  # malformed game - skip it, don't abort the whole file

            if not san_moves:
                continue

            moves_san = " ".join(san_moves)
            content_hash = hashlib.sha256(moves_san.encode("utf-8")).hexdigest()
            headers = game.headers
            eco, opening_name = eco_mod.classify_prefix(eco_index, san_moves)

            cur = conn.execute(
                """INSERT OR IGNORE INTO games
                       (source, source_file, white_name, black_name, white_elo, black_elo,
                        white_title, black_title, event, site, round, date, result,
                        eco, opening_name, ply_count, moves_san, content_hash, ingested_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    source, pgn_path.name,
                    headers.get("White"), headers.get("Black"),
                    _int_or_none(headers.get("WhiteElo")), _int_or_none(headers.get("BlackElo")),
                    headers.get("WhiteTitle"), headers.get("BlackTitle"),
                    headers.get("Event"), headers.get("Site"), headers.get("Round"),
                    _normalize_date(headers.get("Date")),
                    headers.get("Result"),
                    eco, opening_name,
                    len(san_moves), moves_san, content_hash,
                    dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                ),
            )
            if cur.rowcount == 0:
                games_duplicate += 1
                continue

            games_inserted += 1
            _update_opening_tree(conn, san_moves, headers.get("Result"))

            if games_inserted % 5000 == 0:
                conn.commit()
                print(f"  ... {games_inserted:,} inserted ({games_read:,} read)", file=sys.stderr)

    conn.commit()
    return games_read, games_inserted, games_duplicate


def run(db_path: Path, skip_download: bool) -> None:
    conn = chessdb_db.connect(db_path)
    chessdb_db.init_schema(conn)

    if conn.execute("SELECT COUNT(*) FROM eco_openings").fetchone()[0] == 0:
        if not ECO_DIR.exists() or not any(ECO_DIR.glob("*.tsv")):
            raise RuntimeError(
                f"no ECO reference data in {ECO_DIR} - run scripts/fetch_eco_data.sh first"
            )
        n = eco_mod.load_eco_openings(conn, ECO_DIR)
        print(f"Loaded {n:,} reference opening lines into eco_openings", file=sys.stderr)

    pgn_files = sorted(EXTRACT_DIR.rglob("*.pgn")) if skip_download else []
    if not pgn_files:
        print("Resolving current download link from lumbrasgigabase.com ...", file=sys.stderr)
        mega_link = resolve_mega_link(LUMBRA_ELITE_WPDM_URL)
        print(f"  -> {mega_link}", file=sys.stderr)

        print("Downloading via megatools (this is a ~125MB file) ...", file=sys.stderr)
        archive_path = download_via_mega(mega_link, DOWNLOAD_DIR)
        print(f"  -> {archive_path}", file=sys.stderr)

        print("Extracting ...", file=sys.stderr)
        pgn_files = extract_7z(archive_path, EXTRACT_DIR)

    if not pgn_files:
        raise RuntimeError("no .pgn files found after extraction")

    run_id = conn.execute(
        """INSERT INTO ingest_runs (source, source_file, started_at, status)
           VALUES (?, ?, ?, 'running')""",
        (SOURCE_NAME, ",".join(p.name for p in pgn_files),
         dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")),
    ).lastrowid
    conn.commit()

    eco_index = eco_mod.build_eco_index(conn)

    total_read = total_inserted = total_duplicate = 0
    try:
        for pgn_path in pgn_files:
            print(f"Ingesting {pgn_path} ...", file=sys.stderr)
            read, inserted, duplicate = ingest_pgn_file(conn, pgn_path, SOURCE_NAME, eco_index)
            total_read += read
            total_inserted += inserted
            total_duplicate += duplicate
    except Exception:
        conn.execute(
            "UPDATE ingest_runs SET status = 'failed', finished_at = ? WHERE id = ?",
            (dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), run_id),
        )
        conn.commit()
        raise

    conn.execute(
        """UPDATE ingest_runs
           SET finished_at = ?, games_read = ?, games_inserted = ?, games_duplicate = ?, status = 'ok'
           WHERE id = ?""",
        (
            dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            total_read, total_inserted, total_duplicate, run_id,
        ),
    )
    conn.commit()

    print(
        f"\nDone. Read {total_read:,} games, inserted {total_inserted:,} new, "
        f"skipped {total_duplicate:,} duplicates.",
        file=sys.stderr,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="Path to the SQLite database")
    parser.add_argument(
        "--skip-download", action="store_true",
        help="Reuse already-extracted .pgn files under data/downloads/otb_lumbra/extracted/",
    )
    args = parser.parse_args()
    run(args.db, args.skip_download)


if __name__ == "__main__":
    main()
