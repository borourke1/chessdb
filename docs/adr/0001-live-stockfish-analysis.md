# Live Stockfish analysis on the game-replay page

`docs/PLAN.md` originally deferred all engine evaluation to a separable v2
phase, to keep v1 scoped to master-move statistics only. We're pulling that
forward: the game-replay page now shows a live Stockfish recommendation
panel (top 3 candidate moves, depth 18, with a short principal variation
each) for whatever position is currently on the board. The opening explorer
page is untouched - it stays purely master-statistics, on purpose, since
"what masters played" and "what an engine likes" are different questions.

Two implementation choices worth recording:

- **No persistence.** Every view runs a fresh Stockfish process
  (`chess.engine.SimpleEngine.popen_uci`) rather than caching results in
  SQLite. Master games are typically replayed once, so a cache would mostly
  add schema and invalidation complexity before it ever pays for itself.
  Revisit if usage shows the same positions getting re-analyzed often.

- **Decoupled from navigation.** The app is otherwise fully server-rendered:
  every ply change is a plain `<a href>` full-page reload. Running Stockfish
  synchronously in that same request would make every click pause for
  however long a multipv-3 search takes - directly undermining the point of
  watching recommendations "as you play through" a game. Instead, navigation
  is untouched and a small JSON endpoint (`/game/<id>/analysis?ply=`) is
  fetched client-side after the page has already rendered, filling the panel
  in when it's ready. This is the app's first fetch-based JS; everything
  else remains link-based and works without JS. The fetch response carries
  the `ply` it was computed for, and the client discards it if the page has
  since navigated to a different ply before it lands.

Depth started at 25, which measured at 9.6-18s per call even with the
Threads/Hash tuning below - too slow to feel "live" in practice, even though
decoupling meant it never blocked navigation. Depth was lowered to 18 to hit
a hard ~2s budget: sampled across opening, middlegame, and tactical
positions, 18 stayed under ~1.3s with real margin, 19 already touched 1.84s,
and 20 crossed 2s on one sample.
