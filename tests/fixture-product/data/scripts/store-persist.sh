#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Busbar Inc and contributors
#
# Script-driver cell for the FIXTURE PRODUCT: boot busbar-stub against a named store backend and
# read back what the store kept. It has the SHAPE of busbar's own store-persist.sh -- the same
# plugin-name -> backend-URL-variable `case`, the same -1 UNSUPPORTED refusal when the backend is
# not in the environment, the same `fail` that marks a give-up as harness_error -- and none of its
# substance. It boots no real backend and loads no real plugin.
#
# The shape is the point. fixture-gate-selftest.sh case (f) reads the `case` arms below out of this
# file and the `needs_fixture` values out of cells.json and requires the two maps to agree id for
# id; that check catches a rename on one side only, and it can only catch it against a file that
# really is the driver the cells name.
#
# Env from the recorder: BUSBAR_BIN RAW WORK; args: <plugin-name> [<settings-json>]
set -uo pipefail
PLUGIN="${1:?plugin name}"; SETTINGS="${2:-}"
RAW="${RAW:?RAW is the directory the capture is written to}"
BIN="${BUSBAR_BIN:?BUSBAR_BIN is the product binary under test}"

eff='{}'
# `step` records one intermediate fact in the cell's effects. NEVER a wall clock: diff-cells rates
# every effects key a driver writes as MONEY, so an epoch second here would be a permanent
# divergence about nothing on every replay.
step() { eff="$(printf '%s' "$eff" | python3 -c '
import json, sys
d = json.load(sys.stdin); d[sys.argv[1]] = sys.argv[2]; print(json.dumps(d, sort_keys=True))' "$1" "$2")"; }
# A -1 status is a NAMED GAP: the environment could not supply this cell, so nothing was recorded.
gap() { printf '{"status":-1,"headers":{},"body":"","effects":{"error":%s}}\n' \
  "$(printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')" >"$RAW/captured.json"; exit 0; }
# A `fail` IS THE HARNESS GIVING UP, NOT AN OUTCOME OF THE BINARY. record.sh reads a script cell's
# status alone and writes PASS for anything that is not -1, so a non-negative give-up would freeze
# this driver's own infrastructure failure into the golden -- and the candidate, failing the same
# way for the same reason, would match it exactly. `harness_error` is what tells the two apart.
fail() { local st="$1" msg="$2"; printf '%s' "$eff" | python3 -c '
import json, sys
eff = json.load(sys.stdin); msg = sys.argv[2]; eff["harness_error"] = msg
json.dump({"status": int(sys.argv[1]), "headers": {}, "body": msg, "effects": eff},
          open(sys.argv[3], "w"), sort_keys=True)' "$st" "$msg" "$RAW/captured.json"; exit 0; }

# THE MAP. One line per plugin, `<plugin>) URL_VAR=<VAR> ;;`, because that is the line
# fixture-gate-selftest.sh reads. A store with no arm here is a store whose fixture lives in the
# tree rather than on the network, and it boots on its own defaults.
case "$PLUGIN" in
  store-fixture-a) URL_VAR=FIXTURE_STORE_A_URL ;;
  store-fixture-b) URL_VAR=FIXTURE_STORE_B_URL ;;
  *)               URL_VAR="" ;;
esac
if [ -z "$SETTINGS" ] && [ -n "$URL_VAR" ]; then
  url="$(eval "printf '%s' \"\${${URL_VAR}:-}\"")"
  # The recorder gates this cell on the same variable, so an unset one should never reach here. If
  # it does -- a direct call -- refuse in the -1 shape rather than booting on nothing and recording
  # whatever a store with no backend answers as the product's contract.
  [ -n "$url" ] || gap "$URL_VAR is unset: no live backend for $PLUGIN"
  SETTINGS="{\"url\":\"${url}\"}"
fi
[ -n "$SETTINGS" ] || SETTINGS='{}'
step plugin "$PLUGIN"
step settings "$SETTINGS"

"$BIN" --version >"$RAW/version.txt" 2>&1 || fail 1 "$BIN could not name its own version"
step version "$(head -1 "$RAW/version.txt")"
printf '%s\n' 'listen: "127.0.0.1:1"' >"$RAW/store-config.yaml"
"$BIN" --validate "$RAW/store-config.yaml" >"$RAW/store-validate.log" 2>&1
rc=$?
step validate_exit "$rc"
[ "$rc" = 0 ] || fail 2 "$(tail -c 300 "$RAW/store-validate.log")"
# The persistence verdict a real driver reaches by killing the process and reading the key back.
# Here there is nothing to lose, and the cell says so in the same key the real one uses.
step survived yes
printf '%s' "$eff" | python3 -c '
import json, sys
json.dump({"status": 0, "headers": {}, "body": "{}", "effects": json.load(sys.stdin)},
          open(sys.argv[1], "w"), sort_keys=True)' "$RAW/captured.json"
