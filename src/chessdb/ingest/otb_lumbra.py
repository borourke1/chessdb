"""Extract and load Lumbra's Gigabase OTB "Elite (ELO > 2400)" file into the
chessdb SQLite database.

Deliberately uses the Elite file, not "OTB Complete": Lumbra's Complete file
carries no rating filter at all (confirmed on the site - only Elite is
pre-filtered by strength), so it's the only variant that actually matches
this project's "master and above" scope. See docs/PLAN.md.

The archive is downloaded by hand in a browser: the site's download page
gates each download behind a proof-of-work challenge meant to deter scripted
downloads, so this module does not fetch it. Save the `.7z` into
data/downloads/otb_lumbra/ - the newest `.7z` there is ingested by default,
and the scheduled job (chessdb.ingest.scheduled) picks it up automatically.
Extraction requires `7z` (`brew install p7zip`).

Usage:
    python -m chessdb.ingest.otb_lumbra [--db PATH] [--archive PATH] [--skip-extract]
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
from chessdb.positions import normalized_fen_key

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "chessdb.sqlite3"
DOWNLOAD_DIR = REPO_ROOT / "data" / "downloads" / "otb_lumbra"
EXTRACT_DIR = DOWNLOAD_DIR / "extracted"
ECO_DIR = REPO_ROOT / "data" / "eco"

LUMBRA_DOWNLOAD_PAGE = "https://lumbrasgigabase.com/en/download-area-en/"

SOURCE_NAME = "otb_lumbra"
MAX_OPENING_PLY = 40  # 20 full moves/side - beyond this, positions are rarely shared "theory"


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def newest_archive(download_dir: Path | None = None) -> Path | None:
    """The most recently modified .7z in the download dir, if any."""
    archives = sorted((download_dir or DOWNLOAD_DIR).glob("*.7z"), key=lambda p: p.stat().st_mtime)
    return archives[-1] if archives else None


def extract_7z(archive_path: Path, dest_dir: Path) -> list[Path]:
    if shutil.which("7z") is None:
        raise RuntimeError("7z not found - install with `brew install p7zip`")
    # Clear last month's extraction so a renamed .pgn inside the new archive
    # doesn't get ingested alongside the stale one.
    shutil.rmtree(dest_dir, ignore_errors=True)
    dest_dir.mkdir(parents=True)
    result = subprocess.run(
        ["7z", "x", f"-o{dest_dir}", "-y", str(archive_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        detail = [l.strip() for l in (result.stderr or result.stdout).splitlines() if l.strip()][-3:]
        raise RuntimeError(f"7z could not extract {archive_path.name}: {' '.join(detail)}")
    return sorted(dest_dir.rglob("*.pgn"))


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
    # A game can revisit a position and replay a move from it (a repetition,
    # or e.g. 1.Nf3 Nf6 2.Ng1 Ng8 back to the start), so both positions and
    # (position, move) pairs are counted at most once per game - each
    # game_count means "games that reached this position" / "games that
    # played this move here", not how many times either happened.
    reached: set[str] = set()
    counted: set[tuple[str, str]] = set()

    def reach(key: str, ply: int) -> None:
        if key in reached:
            return
        reached.add(key)
        conn.execute(
            """INSERT INTO opening_positions (fen_key, ply, game_count) VALUES (?, ?, 1)
               ON CONFLICT(fen_key) DO UPDATE SET game_count = game_count + 1""",
            (key, ply),
        )

    board = chess.Board()
    from_key = normalized_fen_key(board)
    reach(from_key, 0)
    white_win = 1 if result == "1-0" else 0
    black_win = 1 if result == "0-1" else 0
    draw = 1 if result == "1/2-1/2" else 0

    for ply, san in enumerate(san_moves[:MAX_OPENING_PLY], start=1):
        move = board.parse_san(san)
        board.push(move)
        to_key = normalized_fen_key(board)
        reach(to_key, ply)
        if (from_key, san) in counted:
            from_key = to_key
            continue
        counted.add((from_key, san))
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
                    _utc_now(),
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


def run(db_path: Path, archive: Path | None, skip_extract: bool) -> tuple[int, int, int]:
    """Ingest one archive. Returns (games_read, games_inserted, games_duplicate).

    The ingest_runs row is written before extraction, so a failed or
    interrupted attempt is still recorded - the scheduled job relies on that
    to avoid retrying the same broken archive on every trigger.
    """
    conn = chessdb_db.connect(db_path)
    chessdb_db.init_schema(conn)

    if conn.execute("SELECT COUNT(*) FROM eco_openings").fetchone()[0] == 0:
        if not ECO_DIR.exists() or not any(ECO_DIR.glob("*.tsv")):
            raise RuntimeError(
                f"no ECO reference data in {ECO_DIR} - run scripts/fetch_eco_data.sh first"
            )
        n = eco_mod.load_eco_openings(conn, ECO_DIR)
        print(f"Loaded {n:,} reference opening lines into eco_openings", file=sys.stderr)

    if not skip_extract:
        archive = archive or newest_archive()
        if archive is None:
            raise RuntimeError(
                f"no .7z archive in {DOWNLOAD_DIR} - download the OTB Elite file from "
                f"{LUMBRA_DOWNLOAD_PAGE} and save it there"
            )

    run_id = conn.execute(
        """INSERT INTO ingest_runs (source, source_file, started_at, status)
           VALUES (?, ?, ?, 'running')""",
        # Microsecond precision: the scheduled job decides "already attempted"
        # by comparing this against the archive's mtime, and launchd starts
        # it within the same second the download lands.
        (SOURCE_NAME, "extracted" if skip_extract else archive.name,
         dt.datetime.now(dt.timezone.utc).isoformat()),
    ).lastrowid
    conn.commit()

    total_read = total_inserted = total_duplicate = 0
    try:
        if skip_extract:
            pgn_files = sorted(EXTRACT_DIR.rglob("*.pgn"))
        else:
            print(f"Extracting {archive} ...", file=sys.stderr)
            pgn_files = extract_7z(archive, EXTRACT_DIR)
        if not pgn_files:
            raise RuntimeError(f"no .pgn files found under {EXTRACT_DIR}")

        eco_index = eco_mod.build_eco_index(conn)
        for pgn_path in pgn_files:
            print(f"Ingesting {pgn_path} ...", file=sys.stderr)
            read, inserted, duplicate = ingest_pgn_file(conn, pgn_path, SOURCE_NAME, eco_index)
            total_read += read
            total_inserted += inserted
            total_duplicate += duplicate
    except BaseException:
        conn.rollback()
        conn.execute(
            "UPDATE ingest_runs SET status = 'failed', finished_at = ? WHERE id = ?",
            (_utc_now(), run_id),
        )
        conn.commit()
        raise

    conn.execute(
        """UPDATE ingest_runs
           SET finished_at = ?, games_read = ?, games_inserted = ?, games_duplicate = ?, status = 'ok'
           WHERE id = ?""",
        (_utc_now(), total_read, total_inserted, total_duplicate, run_id),
    )
    conn.commit()

    print(
        f"\nDone. Read {total_read:,} games, inserted {total_inserted:,} new, "
        f"skipped {total_duplicate:,} duplicates.",
        file=sys.stderr,
    )
    return total_read, total_inserted, total_duplicate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="Path to the SQLite database")
    parser.add_argument(
        "--archive", type=Path,
        help=f"The .7z to ingest (default: the newest .7z in {DOWNLOAD_DIR})",
    )
    parser.add_argument(
        "--skip-extract", action="store_true",
        help="Re-process already-extracted .pgn files under data/downloads/otb_lumbra/extracted/",
    )
    args = parser.parse_args()
    run(args.db, args.archive, args.skip_extract)


if __name__ == "__main__":
    main()
