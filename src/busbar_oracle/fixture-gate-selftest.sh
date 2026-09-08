#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Busbar Inc and contributors
#
# Prove the FIXTURE GATE — the rule that decides whether a cell whose fixture is not in the tree is
# recorded or written down as a named gap. Both arms, because only one of them is ever exercised on
# any given machine: a box with no live backend never runs the recording arm, and CI with the
# service containers up never runs the gap arm. A gate tested on one arm is a gate that can start
# recording nothing (or recording against a backend it was never pointed at) and stay green.
#
#   (a) needs_fixture absent / false / null   -> RECORD  (the ordinary cell)
#   (b) needs_fixture true                    -> GAP     (flat: no environment can supply it)
#   (c) needs_fixture <VAR>, VAR unset        -> GAP
#   (d) needs_fixture <VAR>, VAR empty        -> GAP     (an empty URL is not a backend)
#   (e) needs_fixture <VAR>, VAR set          -> RECORD  (the arm the live services turn on)
#   (g) needs_fixture <anything that is not a shell name> -> REFUSED (never recorded, never a gap)
#   (h) every needs_fixture value in the PRODUCT'S OWN corpus is one the gate can read
#   (f) the three network-backed store cells name a var that store-persist.sh actually reads, and
#       the two maps agree id-for-id. This is the one that catches a rename on one side only:
#       the cell would be gated on a variable the script ignores, so the cell records against
#       whatever the plugin's own defaults point at and calls that the 1.5.5 contract.
#
# Needs no busbar binary, no plugin and no backend: it is the cheap gate, meant to run beside
# replay-selftest.sh before anything expensive starts.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# The PRODUCT'S oracle data (cells.json, golden/, the registers, the cell drivers).
# Defaults to the tool's own directory, which is the in-tree layout this harness grew up
# in; busbar now passes its own testing/shadow-oracle via BUSBAR_ORACLE_DATA.
data="${BUSBAR_ORACLE_DATA:-$here}"
# shellcheck source=oracle-config.sh
source "${here}/oracle-config.sh"

FAILED=0
ok()   { printf '  ok    %s\n' "$1"; }
bad()  { printf '  FAIL  %s\n' "$1"; FAILED=1; }

# `oracle_fixture_missing` returns 0 for "gap", 1 for "record", 2 for "this value is not a fixture
# gate at all". Assert against the word, not the number, so a reversed return value reads as the
# wrong verdict rather than as a passing test.
#
# NOT `if oracle_fixture_missing …`. That is the shape the recorder had, and it is the shape the
# defect exploits: on a value that is not a shell identifier the indirect expansion inside the
# function aborts the whole compound command, so NEITHER branch runs and control falls through —
# in record.sh, straight past the `continue` and into recording the cell without its fixture. The
# status is captured and dispatched on instead, so "the gate could not answer" is a value this test
# can see rather than a branch it silently skips.
verdict() {
  oracle_fixture_missing "$1"; local rc=$?
  case "$rc" in 0) echo GAP ;; 1) echo RECORD ;; *) echo REFUSED ;; esac
}

case_is() {  # <want> <needs_fixture> <label>
  local want="$1" nf="$2" label="$3" got
  got="$(verdict "$nf")"
  [ "$got" = "$want" ] && ok "$label -> $want" || bad "$label -> got $got, want $want"
}

echo "fixture gate: the verdict for each needs_fixture shape"
unset ORACLE_SELFTEST_FIXTURE_URL 2>/dev/null || :
case_is RECORD ""     "(a) absent"
case_is RECORD false  "(a) false"
case_is RECORD null   "(a) null"
case_is GAP    true   "(b) flat true"
case_is GAP    ORACLE_SELFTEST_FIXTURE_URL "(c) named var, unset"
ORACLE_SELFTEST_FIXTURE_URL="" case_is GAP    ORACLE_SELFTEST_FIXTURE_URL "(d) named var, empty"
ORACLE_SELFTEST_FIXTURE_URL="postgres://u@h/db" case_is RECORD ORACLE_SELFTEST_FIXTURE_URL "(e) named var, set"

# (g) A VALUE THAT IS NOT A VARIABLE NAME IS REFUSED, NOT GUESSED AT. `${!1}` requires a valid shell
# identifier: on anything else bash prints "invalid variable name" and aborts the enclosing compound
# command, so the recorder's `if oracle_fixture_missing …; then SKIP; continue; fi` ran NEITHER
# branch and fell through to recording the cell WITH ITS FIXTURE ABSENT — freezing whatever a busbar
# with no backend answers into the golden, with a PASS row, reproduced by every candidate. The prose
# shape is the one to fear: this file's own header describes the env-gated arm in exactly those words.
case_is REFUSED "a Postgres URL in the environment" "(g) prose, not an identifier"
case_is REFUSED "postgres://u@h/db"                 "(g) a URL, not an identifier"
case_is REFUSED "1"                                 "(g) a number (which would indirect onto the positional \$1 and read as SET)"
case_is REFUSED "BUSBAR TEST URL"                   "(g) an identifier with a space in it"
# …and the gate still answers for every shape that IS a name, so the refusal is not a blanket. Both
# of these are valid identifiers naming variables nobody set, which is the ordinary env-gated GAP —
# `True` in particular is a name and NOT the boolean, so it gates on a variable called True.
case_is GAP "True"                          "(g) a capitalised true is a NAME, not the boolean (gap while unset)"
case_is GAP _ORACLE_UNSET_UNDERSCORE_LEAD   "(g) a leading-underscore identifier is a name"
_ORACLE_UNSET_UNDERSCORE_LEAD="postgres://u@h/db" case_is RECORD _ORACLE_UNSET_UNDERSCORE_LEAD "(g) …and it records once that variable is set"

# (f) The cell corpus and the script must name the SAME variable per store. Both are read out of the
# files that actually run, not restated here — a copy of the map in this test would be a third
# thing to keep in step, and it would pass while the two real ones disagreed.
echo "fixture gate: the cells and store-persist.sh name the same variable"
CELLS="${data}/cells.json"
SCRIPT="${data}/scripts/store-persist.sh"
if [ ! -s "$CELLS" ]; then
  bad "(f) no cells.json — run enumerate-cells.py --write"
else
  while IFS=$'\t' read -r id var; do
    plugin="${id#plugins.store-persist|}"
    # The script's map is a plain `case` arm: `store-postgres) URL_VAR=BUSBAR_TEST_POSTGRES_URL ;;`
    script_var="$(sed -n "s/^[[:space:]]*${plugin})[[:space:]]*URL_VAR=\([A-Z_][A-Z0-9_]*\).*/\1/p" "$SCRIPT" | head -1)"
    if [ -z "$script_var" ]; then
      bad "(f) $id is gated on $var but store-persist.sh has no arm for $plugin"
    elif [ "$script_var" != "$var" ]; then
      bad "(f) $id is gated on $var but store-persist.sh reads $script_var"
    else
      ok "(f) $plugin: both sides read $var"
    fi
  done < <(python3 -c '
import json, sys
cells = json.load(open(sys.argv[1]))
cells = cells["cells"] if isinstance(cells, dict) and "cells" in cells else cells
n = 0
for c in cells:
    nf = c.get("needs_fixture")
    if isinstance(nf, str) and c["id"].startswith("plugins.store-persist|"):
        print(c["id"], nf, sep="\t"); n += 1
# Zero rows would make this whole case vacuously green — the exact shape that hid the gap before.
if n == 0:
    sys.exit("no env-gated store cells in cells.json: the gate has nothing to prove")
' "$CELLS") || bad "(f) could not read the env-gated store cells out of cells.json"
fi

# (h) AND THE PRODUCT'S OWN CORPUS IS HELD TO THE GATE. The cases above prove what the gate does
# with each shape; this proves no cell in the tree carries a shape it refuses. Nothing else
# constrains `needs_fixture` — there is no schema — so a corpus author writing prose (which the
# gate's own header describes the env-gated arm in) is caught here rather than by a cell that
# recorded without its fixture.
echo "fixture gate: every needs_fixture value in the corpus is one the gate can read"
if [ ! -s "$CELLS" ]; then
  bad "(h) no cells.json to check"
else
  while IFS= read -r nf; do
    [ -n "$nf" ] || continue
    got="$(verdict "$nf")"
    [ "$got" = REFUSED ] && bad "(h) a cell declares needs_fixture '${nf}', which the gate cannot read" || :
  done < <(python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
cells = d["cells"] if isinstance(d, dict) and "cells" in d else d
seen = set()
for c in cells:
    nf = c.get("needs_fixture")
    if nf is not None and not isinstance(nf, bool) and str(nf) not in seen:
        seen.add(str(nf)); print(nf)
' "$CELLS")
  ok "(h) $(python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
cells = d["cells"] if isinstance(d, dict) and "cells" in d else d
print(len({str(c.get("needs_fixture")) for c in cells if c.get("needs_fixture") is not None}))' "$CELLS") distinct needs_fixture value(s) in the corpus, all readable by the gate"
fi

[ "$FAILED" = 0 ] && echo "fixture-gate selftest: both arms hold" || echo "fixture-gate selftest: RED"
exit "$FAILED"
