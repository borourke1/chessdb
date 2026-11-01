"""Entry points for the monthly OTB ingest's two launchd jobs.

Lumbra's gates downloads behind a browser proof-of-work challenge, so the
monthly cycle is semi-automatic (see docs/PLAN.md, "Freshness"):

- `remind` runs daily. From the 10th of each month - Lumbra's releases early
  in the month - it shows an alert until that month has a successful ingest,
  telling you to download the new file.
- `ingest-new` runs whenever data/downloads/otb_lumbra/ changes (and daily as
  a catch-up). If that directory holds a .7z newer than the last ingest
  attempt, it ingests it and shows an alert with the outcome.

Alerts are modal `osascript` dialogs rather than Notification Center banners:
they reliably show from a launchd job and stay up until dismissed, so a
failed run can't scroll by unnoticed.

Usage:
    python -m chessdb.ingest.scheduled {remind,ingest-new} [--db PATH]
"""
from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import subprocess
import sys
import traceback
from pathlib import Path

from chessdb.ingest import otb_lumbra

REMIND_FROM_DAY = 10
LOG_PATH = otb_lumbra.REPO_ROOT / "data" / "logs" / "scheduled_ingest.log"
MANUAL_COMMAND = f"{otb_lumbra.REPO_ROOT}/.venv/bin/python -m chessdb.ingest.otb_lumbra"


def alert(title: str, message: str) -> None:
    # Passed as argv, not spliced into the script, so quotes in an error
    # message can't break (or inject into) the AppleScript.
    subprocess.run(
        [
            "osascript",
            "-e", "on run argv",
            "-e", "display alert (item 1 of argv) message (item 2 of argv)",
            "-e", "end run",
            title, message,
        ],
        check=False, capture_output=True,
    )


def _parse_utc(iso: str) -> dt.datetime:
    return dt.datetime.fromisoformat(iso)


def _last_run(db_path: Path, status: str | None = None) -> sqlite3.Row | None:
    if not db_path.exists():
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        sql = "SELECT * FROM ingest_runs WHERE source = ?"
        params: list = [otb_lumbra.SOURCE_NAME]
        if status:
            sql += " AND status = ?"
            params.append(status)
        return conn.execute(sql + " ORDER BY started_at DESC LIMIT 1", params).fetchone()
    except sqlite3.OperationalError:  # schema not created yet
        return None
    finally:
        conn.close()


def remind(db_path: Path, today: dt.date | None = None) -> None:
    today = today or dt.date.today()
    if today.day < REMIND_FROM_DAY:
        return

    month_start = dt.datetime(today.year, today.month, 1).astimezone(dt.timezone.utc)
    last_ok = _last_run(db_path, "ok")
    if last_ok and _parse_utc(last_ok["started_at"]) >= month_start:
        return

    message = (
        f"This month's Lumbra's Gigabase file hasn't been ingested yet.\n\n"
        f"Download \"OTB Elite - ELO > 2400\" from {otb_lumbra.LUMBRA_DOWNLOAD_PAGE} "
        f"and save the .7z into:\n{otb_lumbra.DOWNLOAD_DIR}\n\n"
        f"It is ingested automatically once saved there."
    )
    last = _last_run(db_path)
    if last and last["status"] != "ok" and _parse_utc(last["started_at"]) >= month_start:
        message += (
            f"\n\nThis month's last attempt ended with status '{last['status']}'. "
            f"See {LOG_PATH}, then retry with:\n{MANUAL_COMMAND}"
        )
    alert("chessdb: monthly download due", message)


def ingest_new(db_path: Path) -> None:
    archive = otb_lumbra.newest_archive()
    if archive is None:
        return
    archive_mtime = dt.datetime.fromtimestamp(archive.stat().st_mtime, dt.timezone.utc)
    last = _last_run(db_path)
    if last and _parse_utc(last["started_at"]) >= archive_mtime:
        return  # already attempted this archive (or a newer one)

    print(f"[{dt.datetime.now().isoformat(timespec='seconds')}] ingesting {archive}", file=sys.stderr)
    try:
        read, inserted, duplicate = otb_lumbra.run(db_path, archive, skip_extract=False)
    except BaseException as e:
        traceback.print_exc()
        alert(
            "chessdb: monthly ingest FAILED",
            f"Ingesting {archive.name} failed:\n{type(e).__name__}: {e}\n\n"
            f"Full log: {LOG_PATH}\n\nOnce fixed, retry with:\n{MANUAL_COMMAND}",
        )
        raise SystemExit(1)

    for old in otb_lumbra.DOWNLOAD_DIR.glob("*.7z"):
        if old != archive:
            old.unlink()
    alert(
        "chessdb: monthly ingest done",
        f"Ingested {archive.name}: {inserted:,} new games "
        f"({read:,} read, {duplicate:,} already in the database).",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["remind", "ingest-new"])
    parser.add_argument("--db", type=Path, default=otb_lumbra.DEFAULT_DB_PATH)
    args = parser.parse_args()
    if args.command == "remind":
        remind(args.db)
    else:
        ingest_new(args.db)


if __name__ == "__main__":
    main()
