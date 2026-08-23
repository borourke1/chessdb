"""Shared position-key helpers used by both ingestion and the web app."""
from __future__ import annotations

import chess


def normalized_fen_key(board: chess.Board) -> str:
    """Board + turn + castling + en-passant only, dropping the halfmove/
    fullmove counters - so transpositions land on the same explorer node."""
    return " ".join(board.fen().split(" ")[:4])
