"""Local web app: opening explorer + game browser over the chessdb SQLite
database. Server-rendered - every move/link is a plain GET, including ply
navigation - so it works with nothing but a browser; a small unobtrusive
script adds keyboard-arrow navigation on the game-replay page, but every
link underneath it works fine without JS too. The one exception is the
game-replay page's engine-recommendation panel: it's filled in by a
client-side fetch against /game/<id>/analysis after the page has already
rendered, so a slow Stockfish call never delays navigation (see
docs/adr/0001-live-stockfish-analysis.md).

Run with:
    .venv/bin/python -m chessdb.web.app [--db PATH] [--port PORT]

Opens the database read-only, so it's safe to run this while an ingestion
run is writing to the same file (SQLite WAL mode allows concurrent readers).

Piece art: static/pieces/*.svg is the "Cburnett" set (CC BY-SA 3.0, by Colin
M.L. Burnett), the same set Wikipedia and Lichess use - not a copy of any
site's proprietary artwork.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import chess
from flask import Flask, abort, g, jsonify, render_template_string, request, url_for

from chessdb import engine as chessdb_engine
from chessdb.positions import normalized_fen_key

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "chessdb.sqlite3"

app = Flask(__name__)
app.config["DB_PATH"] = DEFAULT_DB_PATH
# This app's static assets (piece art) have gotten edited in place under the
# same filenames during development - default browser caching served stale
# versions after each change. Disabling it trades a little performance for
# never wondering again whether a fix actually reached the browser.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

FILES = "abcdefgh"

BASE_STYLE = """
<style>
  :root {
    color-scheme: light dark;
    --bg: #f0f2f5; --card: #ffffff; --text: #1b1b1f; --muted: #6b7280;
    --border: #e2e5ea; --accent: #4a7c2f; --accent-bg: #e8f0e0;
    --sq-light: #eeeed2; --sq-dark: #769656; --coord: #9a9a9a;
    --highlight: #f6f669;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #16181c; --card: #22252b; --text: #eceff2; --muted: #9aa1ab;
      --border: #33373f; --accent: #8fce5a; --accent-bg: #2c3a22;
      --sq-light: #eeeed2; --sq-dark: #6f9450; --coord: #7c828c;
      --highlight: #6b6b1f;
    }
  }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         max-width: 1100px; margin: 0 auto; padding: 0 1.25rem 3rem; line-height: 1.4;
         background: var(--bg); color: var(--text); }
  h1, h2 { font-weight: 700; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  nav { display: flex; gap: 1.5rem; padding: 1.25rem 0; margin-bottom: 0.5rem;
        border-bottom: 1px solid var(--border); }
  nav a { font-weight: 700; color: var(--text); }
  nav a:hover { color: var(--accent); }
  .muted { color: var(--muted); font-size: 0.9rem; }
  .pill { display: inline-block; padding: 0.1rem 0.6rem; border-radius: 1rem;
          background: var(--accent-bg); color: var(--accent); font-size: 0.8rem; font-weight: 600; }

  /* board */
  .board-shell { display: inline-block; border-radius: 8px; overflow: hidden;
                 border: 1px solid var(--border); }
  table.board { border-collapse: collapse; background: var(--card); }
  table.board td { width: 54px; height: 54px; padding: 0; text-align: center; vertical-align: middle; }
  td.sq-light { background: var(--sq-light); }
  td.sq-dark  { background: var(--sq-dark); }
  td.coord { width: 22px; height: 54px; font-size: 0.68rem; color: var(--coord);
             background: var(--card); }
  td.file-row .coord, td.corner { height: 22px; width: 54px; }
  td.corner { width: 22px; }
  /* Fixed px, not %: percentage height on a replaced element inside a table
     cell resolves inconsistently across browsers (confirmed: WebKit fell
     back to the image's - wrongly guessed - intrinsic aspect ratio and
     rendered it squashed). aspect-ratio is a second safety net. */
  .piece { width: 37px; height: 37px; aspect-ratio: 1 / 1; display: block; margin: 0 auto; }

  table.data { border-collapse: collapse; margin: 0.5rem 0 1.5rem; width: 100%; }
  table.data th, table.data td { text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid var(--border); }
  table.data th { font-size: 0.8rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.02em; }
  input[type=text] { padding: 0.4rem 0.6rem; border: 1px solid var(--border); border-radius: 6px;
                      background: var(--card); color: var(--text); }
  button { padding: 0.4rem 0.9rem; border: none; border-radius: 6px; background: var(--accent);
           color: #fff; font-weight: 600; cursor: pointer; }

  /* game replay layout */
  .replay { display: flex; gap: 1.75rem; align-items: flex-start; flex-wrap: wrap; margin-top: 1rem; }
  .replay .board-col { flex: 0 0 auto; }
  .player-bar { display: flex; align-items: center; gap: 0.6rem; padding: 0.5rem 0.1rem; }
  .avatar { width: 2rem; height: 2rem; border-radius: 50%; background: var(--accent-bg);
            color: var(--accent); display: flex; align-items: center; justify-content: center;
            font-weight: 700; font-size: 0.85rem; flex-shrink: 0; }
  .player-name { font-weight: 700; }
  .player-rating { color: var(--muted); font-size: 0.9rem; }

  .side-col { flex: 1 1 300px; max-width: 380px; background: var(--card); border: 1px solid var(--border);
              border-radius: 10px; padding: 1rem 1.1rem; }
  .side-col h3 { margin: 0 0 0.6rem; font-size: 0.95rem; }
  .move-list { max-height: 380px; overflow-y: auto; border-top: 1px solid var(--border);
               border-bottom: 1px solid var(--border); margin-bottom: 0.9rem; }
  .move-row { display: grid; grid-template-columns: 2.4rem 1fr 1fr; padding: 0.25rem 0; }
  .move-row:nth-child(odd) { background: color-mix(in srgb, var(--border) 35%, transparent); }
  .move-num { color: var(--muted); padding-left: 0.4rem; }
  .move-row a { color: var(--text); padding: 0.1rem 0.4rem; border-radius: 4px; }
  .move-row a.current { background: var(--highlight); color: #111; font-weight: 700; }

  .nav-controls { display: flex; gap: 0.5rem; }
  .nav-controls a { flex: 1; text-align: center; padding: 0.5rem 0; border: 1px solid var(--border);
                     border-radius: 6px; background: var(--bg); color: var(--text); font-size: 1.1rem;
                     font-weight: 700; }
  .nav-controls a.disabled { opacity: 0.3; pointer-events: none; }
  .nav-controls a:hover { border-color: var(--accent); }
  .meta-line { margin: 0.75rem 0; }

  .engine-col { margin-top: 1rem; padding-top: 0.9rem; border-top: 1px solid var(--border); }
  .engine-line { display: flex; gap: 0.6rem; padding: 0.3rem 0; font-size: 0.88rem; }
  .engine-eval { flex: 0 0 3.6rem; font-weight: 700; color: var(--accent); }
  .engine-pv b { font-weight: 700; }
</style>
"""

NAV = f"{BASE_STYLE}<nav><a href=\"/explorer\">Opening Explorer</a><a href=\"/games\">Browse Games</a></nav>"

EXPLORER_TEMPLATE = NAV + """
<h1>Opening Explorer</h1>
<p class="muted">{{ opening_label }}{% if san_moves %} &middot; ply {{ san_moves|length }}{% endif %}</p>
<div class="board-shell">{{ board_html | safe }}</div>
<p class="meta-line">
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
<h1>{{ game.white_name }} <span class="muted">vs</span> {{ game.black_name }}</h1>
<p class="muted">{{ game.event or '' }} &middot; {{ game.date or '' }}
   &middot; {{ game.eco or '' }} {{ game.opening_name or '' }}</p>

<div class="replay">
  <div class="board-col">
    <div class="player-bar">
      <div class="avatar">{{ game.black_name[:1] }}</div>
      <div>
        <div class="player-name">{{ game.black_name }}{% if game.black_title %} <span class="pill">{{ game.black_title }}</span>{% endif %}</div>
        <div class="player-rating">{{ game.black_elo or 'unrated' }}</div>
      </div>
    </div>
    <div class="board-shell">{{ board_html | safe }}</div>
    <div class="player-bar">
      <div class="avatar">{{ game.white_name[:1] }}</div>
      <div>
        <div class="player-name">{{ game.white_name }}{% if game.white_title %} <span class="pill">{{ game.white_title }}</span>{% endif %}</div>
        <div class="player-rating">{{ game.white_elo or 'unrated' }}</div>
      </div>
    </div>
  </div>

  <div class="side-col">
    <h3>Result: {{ game.result }}</h3>
    <div class="move-list" id="move-list">
      {% for num, w, b in move_pairs %}
      <div class="move-row">
        <span class="move-num">{{ num }}.</span>
        {% if w %}<a href="{{ url_for('game_view', game_id=game.id, ply=w.ply) }}"
                      class="{{ 'current' if w.ply == ply else '' }}" id="ply-{{ w.ply }}">{{ w.san }}</a>{% else %}<span></span>{% endif %}
        {% if b %}<a href="{{ url_for('game_view', game_id=game.id, ply=b.ply) }}"
                      class="{{ 'current' if b.ply == ply else '' }}" id="ply-{{ b.ply }}">{{ b.san }}</a>{% else %}<span></span>{% endif %}
      </div>
      {% endfor %}
    </div>
    <div class="nav-controls">
      <a href="{{ url_for('game_view', game_id=game.id, ply=0) }}" class="{{ 'disabled' if ply == 0 else '' }}" title="start">&#9198;</a>
      <a href="{{ url_for('game_view', game_id=game.id, ply=ply-1) }}" class="{{ 'disabled' if ply == 0 else '' }}" title="prev (&larr;)">&#9664;</a>
      <a href="{{ url_for('game_view', game_id=game.id, ply=ply+1) }}" class="{{ 'disabled' if ply == ply_count else '' }}" title="next (&rarr;)">&#9654;</a>
      <a href="{{ url_for('game_view', game_id=game.id, ply=ply_count) }}" class="{{ 'disabled' if ply == ply_count else '' }}" title="end">&#9197;</a>
    </div>
    <p class="muted" style="margin-top:0.75rem">
      ply {{ ply }} / {{ ply_count }} &middot;
      <a href="{{ url_for('explorer', moves=moves[:ply]|join(' ')) }}">open this position in the explorer</a>
    </p>

    <div class="engine-col">
      <h3>Engine recommendations <span class="muted" style="font-weight:400">(Stockfish, depth 25)</span></h3>
      <div id="engine-lines" class="muted">Analyzing&hellip;</div>
    </div>
  </div>
</div>

<script>
  // Progressive enhancement only - every link above works without this.
  document.addEventListener('keydown', function (e) {
    if (e.target.tagName === 'INPUT') return;
    var url = null;
    if (e.key === 'ArrowLeft') url = {{ prev_url|tojson }};
    if (e.key === 'ArrowRight') url = {{ next_url|tojson }};
    if (url) { window.location.href = url; }
  });
  var current = document.querySelector('.move-row a.current');
  if (current) current.scrollIntoView({ block: 'nearest' });

  // Engine panel: fetched after the page has rendered so a slow analysis
  // never delays navigation. Ply nav is a full page reload, so any stale
  // response can only arrive if the page has been left before it lands -
  // the ply check below is a defensive no-op in that case, and a real
  // guard against showing analysis for the wrong position otherwise.
  (function () {
    var ply = {{ ply }};
    var container = document.getElementById('engine-lines');
    fetch({{ url_for('game_analysis', game_id=game.id, ply=ply)|tojson }})
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.ply !== ply) return;
        if (data.error) { container.textContent = data.error; return; }
        if (!data.lines.length) { container.textContent = 'No analysis for this position.'; return; }
        container.innerHTML = data.lines.map(function (line) {
          var first = line.pv[0] || '';
          var rest = line.pv.slice(1).join(' ');
          return '<div class="engine-line"><span class="engine-eval">' + line.eval + '</span>' +
                 '<span class="engine-pv"><b>' + first + '</b>' + (rest ? ' ' + rest : '') + '</span></div>';
        }).join('');
      })
      .catch(function () { container.textContent = 'Engine analysis unavailable.'; });
  })();
</script>
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
    """8x8 board plus a rank column (left) and file row (bottom), all as one
    table so the coordinate labels stay pixel-aligned with the squares."""
    rows = ['<table class="board">']
    for rank in range(7, -1, -1):
        cells = [f'<td class="coord">{rank + 1}</td>']
        for file_ in range(8):
            square = chess.square(file_, rank)
            piece = board.piece_at(square)
            css_class = "sq-light" if (file_ + rank) % 2 == 1 else "sq-dark"
            piece_html = ""
            if piece:
                color = "w" if piece.symbol().isupper() else "b"
                src = url_for("static", filename=f"pieces/{color}{piece.symbol().upper()}.svg")
                piece_html = f'<img class="piece" src="{src}" alt="{piece.symbol()}">'
            cells.append(f'<td class="{css_class}">{piece_html}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
    file_cells = ['<td class="coord corner"></td>']
    file_cells += [f'<td class="coord">{f}</td>' for f in FILES]
    rows.append('<tr class="file-row">' + "".join(file_cells) + "</tr>")
    rows.append("</table>")
    return "".join(rows)


def _board_at_ply(all_moves: list[str], ply: int) -> chess.Board:
    board = chess.Board()
    for san in all_moves[:ply]:
        board.push_san(san)
    return board


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

    board = _board_at_ply(all_moves, ply)

    # Pair up moves as (move_number, white_move, black_move) for a two-column list.
    move_pairs = []
    for i in range(0, len(all_moves), 2):
        w = {"san": all_moves[i], "ply": i + 1}
        b = {"san": all_moves[i + 1], "ply": i + 2} if i + 1 < len(all_moves) else None
        move_pairs.append((i // 2 + 1, w, b))

    prev_url = url_for("game_view", game_id=game_id, ply=ply - 1) if ply > 0 else None
    next_url = url_for("game_view", game_id=game_id, ply=ply + 1) if ply < row["ply_count"] else None

    return render_template_string(
        GAME_TEMPLATE,
        game=row,
        board_html=render_board_html(board),
        ply=ply,
        ply_count=row["ply_count"],
        moves=all_moves,
        move_pairs=move_pairs,
        prev_url=prev_url,
        next_url=next_url,
    )


@app.route("/game/<int:game_id>/analysis")
def game_analysis(game_id: int):
    """Fetched client-side by the game-replay page after it has already
    rendered - see docs/adr/0001-live-stockfish-analysis.md. Ephemeral: no
    caching, a fresh Stockfish run per call."""
    db = get_db()
    row = db.execute(
        "SELECT moves_san, ply_count FROM games WHERE id = ?", (game_id,)
    ).fetchone()
    if row is None:
        abort(404)

    all_moves = row["moves_san"].split()
    ply = request.args.get("ply", type=int, default=row["ply_count"])
    ply = max(0, min(ply, row["ply_count"]))
    board = _board_at_ply(all_moves, ply)

    try:
        lines = chessdb_engine.analyze(board)
    except chessdb_engine.EngineUnavailable as exc:
        return jsonify(ply=ply, error=str(exc))

    return jsonify(ply=ply, lines=lines)


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
