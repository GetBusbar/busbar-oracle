#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Busbar Inc and contributors
#
# Script-driver cell for the FIXTURE PRODUCT: ask the binary to name itself.
#
# The cheapest cell there is, and the one that says the most about whether a product can be recorded
# at all -- a binary that cannot answer `--version` cannot be booted, cannot be minted against, and
# would produce an empty recording that only the replay's zero-rows guard would notice.
#
# Env from the recorder: BUSBAR_BIN RAW
set -uo pipefail
RAW="${RAW:?RAW is the directory the capture is written to}"
BIN="${BUSBAR_BIN:?BUSBAR_BIN is the product binary under test}"

# `fail` marks a give-up as harness_error, so record.sh cannot write a PASS row over it. Without the
# marker a status of 1 is indistinguishable from a product that really does exit 1, and the golden
# would freeze this driver's own failure in as the contract.
fail() { python3 -c '
import json, sys
json.dump({"status": int(sys.argv[1]), "headers": {}, "body": sys.argv[2],
           "effects": {"harness_error": sys.argv[2]}}, open(sys.argv[3], "w"), sort_keys=True)' \
  "$1" "$2" "$RAW/captured.json"; exit 0; }

out="$("$BIN" --version 2>"$RAW/version.err")" || fail 1 "$BIN --version exited non-zero: $(tail -c 300 "$RAW/version.err")"
[ -n "$out" ] || fail 1 "$BIN --version printed nothing"
# The version STRING is the body, so a product that renames itself is a diff on this cell. No clock
# and no path goes into effects: every effects key is compared as MONEY, so anything that moves
# between two runs of the same binary would be a permanent divergence about nothing.
python3 -c '
import json, sys
json.dump({"status": 0, "headers": {}, "body": sys.argv[1] + "\n", "effects": {"stderr": ""}},
          open(sys.argv[2], "w"), sort_keys=True)' "$out" "$RAW/captured.json"
