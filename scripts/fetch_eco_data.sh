#!/usr/bin/env bash
# Fetch the lichess-org/chess-openings reference TSVs into data/eco/.
# This is reference data (opening names), not game data - it doesn't need to
# be refreshed on the monthly ingest cadence, just re-run by hand if you want
# the latest naming.
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p data/eco
for f in a b c d e; do
  curl -sL "https://raw.githubusercontent.com/lichess-org/chess-openings/master/${f}.tsv" \
    -o "data/eco/${f}.tsv"
  echo "data/eco/${f}.tsv: $(wc -l < "data/eco/${f}.tsv") lines"
done
