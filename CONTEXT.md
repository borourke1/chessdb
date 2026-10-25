# chessdb

A personal, local database of master-and-above chess games, aggregated from
free sources, for studying openings and browsing/replaying games by strong
and famous players.

## Language

**Master-strength player**:
The umbrella intent behind chessdb's scope - a player strong enough to be
worth studying. Not a single checkable property: it's proxied differently
per source (see **Rating-eligible** and **Titled** below), and those proxies
are not equivalent to each other.
_Avoid_: "Master", "titled player" (as a stand-in for the general concept)

**Rating-eligible**:
The OTB-source proxy for master-strength: appearing in Lumbra's Gigabase
"OTB Elite" file (ELO > 2400). Carries no title information at all - an
untitled 2400+ player is rating-eligible.
_Avoid_: Master, titled

**Titled**:
The online-source proxy for master-strength: holding an actual
FIDE/site title (FM/CM/IM/GM/WFM/WIM/WGM), sourced from a titled-player
roster (Chess.com's `/pub/titled/{title}`, Lichess's community titled-players
team page). Only enforced for Lichess and Chess.com ingestion, never for OTB.
_Avoid_: Master, rating-eligible
