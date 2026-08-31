"""Live Stockfish analysis for the game-replay page.

Ephemeral only - no persistence, no caching. Every call spawns Stockfish,
runs one fixed-depth analysis, and quits it; see
docs/adr/0001-live-stockfish-analysis.md for why this stays this simple.

Requires the `stockfish` binary on PATH (`brew install stockfish`).
"""
from __future__ import annotations

import shutil

import chess
import chess.engine

STOCKFISH_PATH = shutil.which("stockfish")
# Depth 25 measured at 9.6-18s per call - too slow in practice even
# decoupled from navigation. 18 keeps every sampled position (opening
# through tactical middlegame, at Threads=1/Hash=256 below) under ~1.3s,
# with real margin under the 2s budget; 19 already touched 1.84s and 20
# crossed 2s on one sample, so 18 is the safe ceiling, not just a guess.
ANALYSIS_DEPTH = 18
MULTIPV = 3
PV_PLY_LIMIT = 5

# Measured on this machine (10 cores): more threads make a fixed-depth,
# multipv=3 search *slower* here (Lazy SMP overhead isn't paid back at
# depth 25/multipv 3), so this stays single-threaded rather than scaling
# with core count. A larger hash table did help - 256MB over the engine's
# 16MB default cut this same search from ~13.9s to ~9.5s, reproducibly.
UCI_OPTIONS = {"Threads": 1, "Hash": 256}


class EngineUnavailable(RuntimeError):
    pass


def analyze(board: chess.Board) -> list[dict]:
    """Top MULTIPV ranked lines for `board`, each as
    {"eval": "+0.32" | "#-3", "pv": ["Nf3", "Nf6", ...]} - eval is always
    from White's point of view, PGN convention, regardless of whose turn
    it is to move."""
    if STOCKFISH_PATH is None:
        raise EngineUnavailable(
            "stockfish not found on PATH - install with `brew install stockfish`"
        )
    if board.is_game_over():
        return []

    with chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH) as sf:
        sf.configure(UCI_OPTIONS)
        infos = sf.analyse(
            board, chess.engine.Limit(depth=ANALYSIS_DEPTH), multipv=MULTIPV
        )

    lines = []
    for info in infos:
        pv = info.get("pv") or []
        if not pv:
            continue
        lines.append({
            "eval": _format_score(info["score"].white()),
            "pv": _pv_to_san(board, pv[:PV_PLY_LIMIT]),
        })
    return lines


def _format_score(score: chess.engine.PovScore) -> str:
    if score.is_mate():
        return f"#{score.mate():+d}"
    return f"{score.score() / 100:+.2f}"


def _pv_to_san(board: chess.Board, pv: list[chess.Move]) -> list[str]:
    b = board.copy()
    sans = []
    for move in pv:
        sans.append(b.san(move))
        b.push(move)
    return sans
