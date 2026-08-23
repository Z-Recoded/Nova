#!/bin/bash
# Checks whether the multi-JSON-tool-call-per-turn pattern found in the scrabble-score
# progress-framing spot-check also shows up in plain BASELINE runs (no progress-framing),
# across a handful of slugs known to hit max_turns_reached for 3B. If it never shows up in
# baseline, that's real evidence the pattern is progress-framing-specific, not just sampling
# noise. Companion investigation to 86bbjzguh's scrabble-score finding.
set -e
cd /c/Nova
mkdir -p logs/spotcheck

SLUGS="bowling error-handling octal secret-handshake zebra-puzzle ledger crypto-square sgf-parsing"

for slug in $SLUGS; do
  echo "=== $slug ==="
  ./nova-env/Scripts/python.exe nova_aci_harness.py "$slug" --model qwen2.5-coder:3b-instruct-fp16 --verbose \
    > "logs/spotcheck/baseline_multicheck_${slug}.log" 2>&1
done

echo ALL_DONE
