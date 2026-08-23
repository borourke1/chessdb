"""Local web app: opening explorer + game browser over the chessdb SQLite
database. Fully server-rendered - every move/link is a plain GET - so it
works with nothing but a browser, no client-side JS required.

Run with:
    .venv/bin/python -m chessdb.web.app [--db PATH] [--port PORT]

Opens the database read-only, so it's safe to run this while an ingestion
run is writing to the same file (SQLite WAL mode allows concurrent readers).
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import chess
from flask import Flask, abort, g, render_template_string, request, url_for

from chessdb.positions import normalized_fen_key

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "chessdb.sqlite3"

app = Flask(__name__)
app.config["DB_PATH"] = DEFAULT_DB_PATH

# Deliberately the *filled* glyph for every piece type, for both colors.
# The alternative - hollow "white" glyphs (U+2654-2659) for White and filled
# glyphs for Black - depends on fonts consistently distinguishing hollow vs
# filled across the whole chess symbol block, which they don't (verified:
# renders as an inconsistent mix of hollow/filled within the same rank).
# Coloring one consistent shape via CSS is font-independent and reliable.
PIECE_GLYPHS = {
    "p": "♟", "n": "♞", "b": "♝", "r": "♜", "q": "♛", "k": "♚",
}

BASE_STYLE = """
<style>
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 860px;
         margin: 2rem auto; padding: 0 1rem; line-height: 1.4; }
  nav a { margin-right: 1rem; font-weight: 600; }
  table.board { border-collapse: collapse; margin: 1rem 0; }
  table.board td { width: 2.6rem; height: 2.6rem; text-align: center; font-size: 2.1rem;
                    padding: 0; font-family: "Apple Symbols", "Segoe UI Symbol",
                    "Noto Sans Symbols2", sans-serif; }
  td.sq-light { background: #eeeed2; }
  td.sq-dark  { background: #769656; }
  .piece-white { color: #fbfbf7; -webkit-text-stroke: 1.25px #202020; paint-order: stroke fill;
                 text-shadow: 0 0 2px #000a; }
  .piece-black { color: #1a1a1a; -webkit-text-stroke: 1.25px #f5f5f0; paint-order: stroke fill;
                 text-shadow: 0 0 2px #fffa; }
  table.data { border-collapse: collapse; margin: 0.5rem 0 1.5rem; width: 100%; }
  table.data th, table.data td { text-align: left; padding: 0.3rem 0.6rem; border-bottom: 1px solid #8883; }
  table.data th { font-size: 0.85rem; opacity: 0.7; }
  .bar { display: inline-block; height: 0.8em; vertical-align: middle; }
  .muted { opacity: 0.65; font-size: 0.9rem; }
  input[type=text] { padding: 0.3rem 0.5rem; }
  .pill { display:inline-block; padding: 0.1rem 0.5rem; border-radius: 1rem; background:#8882; font-size:0.85rem; }
</style>
"""

NAV = f"{BASE_STYLE}<nav><a href=\"/explorer\">Opening Explorer</a><a href=\"/games\">Browse Games</a></nav>"

EXPLORER_TEMPLATE = NAV + """
<h1>Opening Explorer</h1>
<p class="muted">{{ opening_label }}{% if san_moves %} &middot; ply {{ san_moves|length }}{% endif %}</p>
{{ board_html | safe }}
<p>
  {% if san_moves %}
    <a href="{{ url_for('explorer', moves=(san_moves[:-1]|join(' '))) }}">&larr; back one move</a> &middot;
  {% endif %}
  <a href="{{ url_for('explorer') }}">reset to start</a>
</p>

<h2>Next moves &mdash; {{ total_games }} games reached this position</h2>
{% if next_moves %}
<table class="data">
  <tr><th>Move</th><th>Games</th><th>White</th><th>Draw</th><th>Black</th></tr>
  {% for m in next_moves %}
  <tr>
    <td><a href="{{ url_for('explorer', moves=(san_moves + [m.move_san])|join(' ')) }}">{{ m.move_san }}</a></td>
    <td>{{ m.game_count }}</td>
    <td>{{ "%.0f"|format(m.white_pct) }}%</td>
    <td>{{ "%.0f"|format(m.draw_pct) }}%</td>
    <td>{{ "%.0f"|format(m.black_pct) }}%</td>
  </tr>
  {% endfor %}
</table>
{% else %}
<p class="muted">No games in the database reach this position yet.</p>
{% endif %}

<h2>Sample games from here</h2>
{% if sample_games %}
<ul>
  {% for gm in sample_games %}
  <li><a href="{{ url_for('game_view', game_id=gm.id) }}">{{ gm.white_name }} ({{ gm.white_elo or '?' }})
      &ndash; {{ gm.black_name }} ({{ gm.black_elo or '?' }})</a>
      <span class="pill">{{ gm.result }}</span>
      <span class="muted">{{ gm.date or '' }} {{ gm.event or '' }}</span></li>
  {% endfor %}
</ul>
{% else %}
<p class="muted">No sample games at this exact position yet.</p>
{% endif %}
"""

GAMES_TEMPLATE = NAV + """
<h1>Browse Games</h1>
<p class="muted">{{ total }} games in the database.</p>
<form method="get">
  <input type="text" name="player" placeholder="player name" value="{{ player }}">
  <input type="text" name="eco" placeholder="ECO code, e.g. B90" value="{{ eco }}" size="8">
  <button type="submit">Search</button>
</form>
<table class="data">
  <tr><th>White</th><th>Black</th><th>Result</th><th>Date</th><th>ECO</th><th>Opening</th></tr>
  {% for gm in rows %}
  <tr>
    <td><a href="{{ url_for('game_view', game_id=gm.id) }}">{{ gm.white_name }} ({{ gm.white_elo or '?' }})</a></td>
    <td>{{ gm.black_name }} ({{ gm.black_elo or '?' }})</td>
    <td>{{ gm.result }}</td>
    <td>{{ gm.date or '' }}</td>
    <td>{{ gm.eco or '' }}</td>
    <td>{{ gm.opening_name or '' }}</td>
  </tr>
  {% endfor %}
</table>
{% if not rows %}<p class="muted">No games match.</p>{% endif %}
"""

GAME_TEMPLATE = NAV + """
<h1>{{ game.white_name }} ({{ game.white_elo or '?' }}) vs {{ game.black_name }} ({{ game.black_elo or '?' }})</h1>
<p class="muted">{{ game.event or '' }} &middot; {{ game.date or '' }} &middot; {{ game.result }}
   &middot; {{ game.eco or '' }} {{ game.opening_name or '' }}</p>
{{ board_html | safe }}
<p>
  <a href="{{ url_for('game_view', game_id=game.id, ply=0) }}">&laquo; start</a>
  {% if ply > 0 %}<a href="{{ url_for('game_view', game_id=game.id, ply=ply-1) }}">&larr; prev</a>{% endif %}
  ply {{ ply }} / {{ ply_count }}
  {% if ply < ply_count %}<a href="{{ url_for('game_view', game_id=game.id, ply=ply+1) }}">next &rarr;</a>{% endif %}
  <a href="{{ url_for('game_view', game_id=game.id, ply=ply_count) }}">end &raquo;</a>
  &middot; <a href="{{ url_for('explorer', moves=moves[:ply]|join(' ')) }}">open this position in the explorer</a>
</p>
<p>
  {% for san in moves %}
  <a href="{{ url_for('game_view', game_id=game.id, ply=loop.index) }}"
     style="{{ 'font-weight:bold' if loop.index == ply else '' }}">{{ san }}</a>{{ ' ' }}
  {% endfor %}
</p>
"""


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(f"file:{app.config['DB_PATH']}?mode=ro", uri=True)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def render_board_html(board: chess.Board) -> str:
    rows = []
    for rank in range(7, -1, -1):
        cells = []
        for file_ in range(8):
            square = chess.square(file_, rank)
            piece = board.piece_at(square)
            css_class = "sq-light" if (file_ + rank) % 2 == 1 else "sq-dark"
            if piece:
                glyph = PIECE_GLYPHS[piece.symbol().lower()]
                piece_class = "piece-white" if piece.symbol().isupper() else "piece-black"
                cell_html = f'<span class="{piece_class}">{glyph}</span>'
            else:
                cell_html = ""
            cells.append(f'<td class="{css_class}">{cell_html}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return '<table class="board">' + "".join(rows) + "</table>"


def parse_move_path(moves_param: str) -> tuple[chess.Board, list[str]]:
    board = chess.Board()
    san_moves = [m for m in moves_param.split() if m]
    for san in san_moves:
        try:
            board.push_san(san)
        except ValueError:
            abort(400, f"illegal move in path: {san!r}")
    return board, san_moves


@app.route("/")
@app.route("/explorer")
def explorer():
    db = get_db()
    board, san_moves = parse_move_path(request.args.get("moves", ""))
    fen_key = normalized_fen_key(board)

    move_rows = db.execute(
        """SELECT move_san, game_count, white_wins, black_wins, draws
           FROM opening_moves WHERE from_fen_key = ? ORDER BY game_count DESC""",
        (fen_key,),
    ).fetchall()
    next_moves = []
    total_games = 0
    for r in move_rows:
        gc = r["game_count"]
        total_games += gc
        next_moves.append({
            "move_san": r["move_san"],
            "game_count": gc,
            "white_pct": 100 * r["white_wins"] / gc if gc else 0,
            "draw_pct": 100 * r["draws"] / gc if gc else 0,
            "black_pct": 100 * r["black_wins"] / gc if gc else 0,
        })

    opening_label = "Starting position"
    if san_moves:
        line = " ".join(san_moves)
        eco_row = db.execute(
            "SELECT eco, name FROM eco_openings WHERE moves_san = ?", (line,)
        ).fetchone()
        opening_label = f"{eco_row['eco']} — {eco_row['name']}" if eco_row else line

    line = " ".join(san_moves)
    sample_games = db.execute(
        """SELECT id, white_name, black_name, white_elo, black_elo, result, date, event
           FROM games WHERE moves_san = ? OR moves_san LIKE ?
           ORDER BY (COALESCE(white_elo,0) + COALESCE(black_elo,0)) DESC LIMIT 8""",
        (line, line + " %"),
    ).fetchall()

    return render_template_string(
        EXPLORER_TEMPLATE,
        board_html=render_board_html(board),
        san_moves=san_moves,
        next_moves=next_moves,
        total_games=total_games,
        opening_label=opening_label,
        sample_games=sample_games,
    )


@app.route("/games")
def games_list():
    db = get_db()
    player = request.args.get("player", "").strip()
    eco = request.args.get("eco", "").strip()

    query = """SELECT id, white_name, black_name, white_elo, black_elo, result, date, eco, opening_name
               FROM games WHERE 1=1"""
    params: list = []
    if player:
        query += " AND (white_name LIKE ? OR black_name LIKE ?)"
        params += [f"%{player}%", f"%{player}%"]
    if eco:
        query += " AND eco = ?"
        params.append(eco.upper())
    query += " ORDER BY date DESC LIMIT 100"

    rows = db.execute(query, params).fetchall()
    total = db.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    return render_template_string(GAMES_TEMPLATE, rows=rows, player=player, eco=eco, total=total)


@app.route("/game/<int:game_id>")
def game_view(game_id: int):
    db = get_db()
    row = db.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    if row is None:
        abort(404)

    all_moves = row["moves_san"].split()
    ply = request.args.get("ply", type=int, default=row["ply_count"])
    ply = max(0, min(ply, row["ply_count"]))

    board = chess.Board()
    for san in all_moves[:ply]:
        board.push_san(san)

    return render_template_string(
        GAME_TEMPLATE,
        game=row,
        board_html=render_board_html(board),
        ply=ply,
        ply_count=row["ply_count"],
        moves=all_moves,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    app.config["DB_PATH"] = args.db
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
