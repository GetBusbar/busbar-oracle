#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Busbar Inc and contributors
#
# Prove the replayer itself: a differ that cannot see a diff is worse than none.
#   (a) golden vs a copy of itself      -> GREEN, zero divergences
#   (b) one cell's status mutated       -> RED, exactly one FAIL, class `status`
#   (c) one candidate cell deleted      -> RED, exactly one FAIL, class `missing.candidate`
#   (d) an empty candidate              -> RED, every owed id FAIL (missing.candidate), never green
#   (e) a normalizer rule on one side   -> RED, class `norm.rules`
#   (f) an effects.usage divergence     -> RED, class `effects.usage`
#   (g) a cell's own `compare` list     -> only the listed classes show, others are dropped
#   (h) an `improvement` acceptance of a `status` class -> the loader refuses it (exit != 0)
#   (i) an accepted `transform`         -> the cell reports ACCEPTED, never a silent PASS
#   (j) a baseline id the golden no longer owes, unnamed -> RED (owed-baseline regression)
#   (k) the same id named in accepted-gaps.json          -> GREEN, with a named-gap line
#   (l) golden/candidate meta.json harness_rev mismatch  -> exit 2
#   (m) --allow-harness-skew on that same mismatch       -> proceeds
#   (n) an accepted `transform` on a cell that carries content-length -> the row's class list is
#       `body` alone, with no phantom `headers` from the length the rewrite itself moved
#   (o) ONE mutation per remaining divergence class -> RED, exactly one FAIL, exactly that class:
#       headers, body, effects.metrics, effects.audit, effects.stderr, effects.egress,
#       effects.readback, effects.files, effects.usage_after_restart, effects.store_errors.
#       Together with (b)/(c)/(e)/(f) and (p) this covers all 15 of CLASS_ORDER: a class the differ
#       computes but no case exercises is a class that could stop working silently.
#   (p) the golden ledger says PASS but the golden cell file is absent -> RED [missing.golden]
#   (q) an `improvement` acceptance of each MONEY class -> the loader refuses it (exit != 0).
#       This is the guard that stops a class being quietly dropped from MONEY_CLASSES: removing
#       effects.usage (or effects.egress / effects.readback / effects.files / ...) from that set
#       makes the matching case here go green-when-it-should-be-red, i.e. RED in this selftest.
#   (w) EVERY script driver's give-up path carries effects.harness_error when it fails with a
#       non-negative status -> record.sh cannot write a PASS row over a harness failure
#   (x) no script driver steps a wall clock into effects (effects.script rates every such key
#       MONEY, so an epoch second there is a permanent divergence about nothing)
#   (y) every golden PASS id is named in owed-baseline.txt -- replay.sh only WARNS about an owed id
#       missing from the baseline, so such a cell can stop being owed later with no red row
#   (r) diff-cells.py --strict --id-filter used directly as a subset gate (land.sh's shape):
#       a filter that selects a diverging cell exits 1, and a filter that selects NOTHING also
#       exits 1 — a subset gate that compared zero cells has proven nothing
#   (w) normalize.py's `eventstream.frames` rule on REAL encoded Bedrock frames: a latencyMs-only
#       difference normalizes EQUAL, a real payload difference stays UNEQUAL, and a corrupted CRC
#       records `eventstream.undecodable` instead of decoding cleanly
# The tracked fixture recording under fixtures/selftest-recording is used read-only: every case
# below works on a `cp -R` of it, never the tracked copy itself.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# The PRODUCT'S oracle data (cells.json, golden/, the registers, the cell drivers).
# Defaults to the tool's own directory, which is the in-tree layout this harness grew up
# in; busbar now passes its own testing/shadow-oracle via BUSBAR_ORACLE_DATA.
data="${BUSBAR_ORACLE_DATA:-$here}"
# The PRODUCT this oracle judges. One check below reads a source file out of it; that check
# skips itself when the product is not present, so the self-tests still run tool-only.
repo="${BUSBAR_ORACLE_PRODUCT_ROOT:-$(cd "${here}/../.." && pwd)}"
FIX="${here}/fixtures/selftest-recording"
CELLS="${FIX}/cells.json"
W="$(mktemp -d "${TMPDIR:-/tmp}/oracle-replay-selftest.XXXXXX")"
trap 'rm -rf "$W"' EXIT
fails=0
say() { printf '%s  %s\n' "$1" "$2"; [ "$1" = PASS ] || fails=$((fails+1)); }
# A CASE THAT COULD NOT RUN IS NOT A CASE THAT PASSED. Two cases below depend on files that are only
# present when the tool is run against a real product tree, and they reported PASS when those files
# were absent — the vacuous-green shape this file refuses everywhere else ("a static case that can
# pass vacuously is worse than no case"). `skip` says so out loud, does not count toward GREEN, and
# is counted in the summary so a run that proved less than it looks is visible from the last line.
skips=0
skipped=""
skip() { printf 'SKIP  %s\n' "$1"; skips=$((skips+1)); skipped="${skipped}
  - $1"; }
# The fixture's meta.json predates harness_rev, so every structural case below (a-g) needs
# --allow-harness-skew just to get past the provenance check; (l)/(m) test that check itself.
# It also names no `binary_sha256` — it was made by no released binary — so the cases below say
# --no-check-golden OUT LOUD. They used to inherit the skip for free, because an absent field and a
# malformed one both read as "no binary named"; case (aa) is what that silence cost.
run_args() { local g="$1" c="$2" o="$3"; shift 3; bash "${here}/replay.sh" --golden "$g" --candidate "$c" --out "$o" --cells "$CELLS" "$@" >"$o.log" 2>&1; echo $?; }
run() { run_args "$1" "$2" "$3" --allow-harness-skew --no-check-golden --accepted "$W/no-accept.json" --baseline "$W/no-baseline.txt"; }
fails_in() { awk -F'\t' '$2=="FAIL"{n++} END{print n+0}' "$1/ledger.tsv"; }
classes_of() { awk -F'\t' -v i="$1" '$1==i{print $3}' "$2/ledger.tsv"; }

printf '{"accepted":[]}' >"$W/no-accept.json"
printf '{"accepted":[]}' >"$W/no-gaps.json"
: >"$W/no-baseline.txt"   # empty: the baseline check is a no-op unless a case opts into a real one

# (a) identical
cp -R "$FIX" "$W/same"
rc="$(run "$FIX" "$W/same" "$W/out-a")"
[ "$rc" = 0 ] && [ "$(fails_in "$W/out-a")" = 0 ] && say PASS "identical recordings -> green, 0 diffs" || say FAIL "identical recordings rc=$rc fails=$(fails_in "$W/out-a")"

# (b) mutate one status
cp -R "$FIX" "$W/mut"
f="$(ls "$W/mut/cells/" | head -1)"
python3 - "$W/mut/cells/$f" <<'EOF'
import json,sys
p=sys.argv[1]; d=json.load(open(p)); d["status"]=418; json.dump(d,open(p,"w"),separators=(",",":"),sort_keys=True)
EOF
id="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['cells'][0]['id'])" "$CELLS")"
rc="$(run "$FIX" "$W/mut" "$W/out-b")"
n="$(fails_in "$W/out-b")"; cls="$(grep -F "$(sed 's/__/|/g' <<<"${f%.json}")" "$W/out-b/ledger.tsv" | cut -f3)"
[ "$rc" != 0 ] && [ "$n" = 1 ] && [ "$cls" = "status" ] && say PASS "one mutated status -> exactly one FAIL [status]" || say FAIL "mutated status rc=$rc fails=$n classes=$cls"

# (c) delete one candidate cell
cp -R "$FIX" "$W/del"; rm "$W/del/cells/$f"
rc="$(run "$FIX" "$W/del" "$W/out-c")"
n="$(fails_in "$W/out-c")"; cls="$(grep -F "$(sed 's/__/|/g' <<<"${f%.json}")" "$W/out-c/ledger.tsv" | cut -f3)"
[ "$rc" != 0 ] && [ "$n" = 1 ] && [ "$cls" = "missing.candidate" ] && say PASS "one deleted cell -> exactly one FAIL [missing.candidate]" || say FAIL "deleted cell rc=$rc fails=$n classes=$cls"

# (d) empty candidate
mkdir -p "$W/empty/cells"; : >"$W/empty/ledger.tsv"
rc="$(run "$FIX" "$W/empty" "$W/out-d")"
owed="$(wc -l <"$W/out-d/owed.txt" | tr -d ' ')"; n="$(fails_in "$W/out-d")"
[ "$rc" != 0 ] && [ "$n" = "$owed" ] && [ "$owed" -gt 0 ] && say PASS "empty candidate -> every owed cell red ($n/$owed)" || say FAIL "empty candidate rc=$rc fails=$n owed=$owed"

# (e) normalizer rule drift
cp -R "$FIX" "$W/norm"
python3 - "$W/norm/cells/$f" <<'EOF'
import json,sys
p=sys.argv[1]; d=json.load(open(p)); d["applied"]=sorted(set(d.get("applied",[]))|{"ts.unix"}); json.dump(d,open(p,"w"),separators=(",",":"),sort_keys=True)
EOF
rc="$(run "$FIX" "$W/norm" "$W/out-e")"
cls="$(grep -F "$(sed 's/__/|/g' <<<"${f%.json}")" "$W/out-e/ledger.tsv" | cut -f3)"
[ "$rc" != 0 ] && [ "$cls" = "norm.rules" ] && say PASS "one-sided normalizer rule -> FAIL [norm.rules]" || say FAIL "norm drift rc=$rc classes=$cls"

# (f) effects.usage divergence — money must be visible even when status/body/headers agree
cp -R "$FIX" "$W/usage"
python3 - "$W/usage/cells/self__a__ok.json" <<'EOF'
import json,sys
p=sys.argv[1]; d=json.load(open(p)); d["effects"]["usage"]["spend_micros"]=19; json.dump(d,open(p,"w"),separators=(",",":"),sort_keys=True)
EOF
rc="$(run "$FIX" "$W/usage" "$W/out-f")"
n="$(fails_in "$W/out-f")"; cls="$(classes_of 'self|a|ok' "$W/out-f")"
[ "$rc" != 0 ] && [ "$n" = 1 ] && [ "$cls" = "effects.usage" ] && say PASS "effects.usage divergence -> exactly one FAIL [effects.usage]" || say FAIL "usage divergence rc=$rc fails=$n classes=$cls"

# (g) a cell's own `compare` list drops every OTHER class it is ALLOWED to drop — a legal narrowing
# keeps status (money) and gives up `headers`; mutate headers and status, only status is owed.
# The narrowing must also be VISIBLE: the row carries `narrowed: [...]` naming what it gave up.
mkdir -p "$W/gcells"
python3 - "$CELLS" "$W/gcells/cells.json" <<'EOF'
import json,sys
d=json.load(open(sys.argv[1]))
for c in d["cells"]:
    if c["id"] == "self|a|ok":
        # everything except `headers`: a legal narrowing gives up a class rated 1, never money
        c["compare"] = ["status", "body", "effects.stderr", "effects.usage", "effects.usage_after_restart",
                        "effects.store_errors", "effects.metrics", "effects.audit", "norm.rules",
                        "effects.egress", "effects.readback", "effects.files", "effects.script"]
        c["why"] = "selftest: the header set is not this cell's contract"
json.dump(d, open(sys.argv[2], "w"))
EOF
cp -R "$FIX" "$W/compare"
python3 - "$W/compare/cells/self__a__ok.json" <<'EOF'
import json,sys
p=sys.argv[1]; d=json.load(open(p)); d["status"]=418; d.setdefault("headers",{})["x-selftest"]="1"; json.dump(d,open(p,"w"),separators=(",",":"),sort_keys=True)
EOF
# run_args hard-codes --cells "$CELLS"; call replay.sh directly here so the compare-list cells.json is used.
bash "${here}/replay.sh" --golden "$FIX" --candidate "$W/compare" --out "$W/out-g" --cells "$W/gcells/cells.json" \
  --allow-harness-skew --no-check-golden --accepted "$W/no-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-g.log" 2>&1
rc=$?
cls="$(classes_of 'self|a|ok' "$W/out-g")"
[ "$rc" != 0 ] && [ "$cls" = "status" ] && say PASS "cell 'compare' list -> only its named classes show (headers dropped)" || say FAIL "compare list rc=$rc classes=$cls (expected status only, headers must not appear)"
grep -q 'narrowed: \[headers\]' "$W/out-g/ledger.tsv" \
  && say PASS "a narrowed cell's row says so ('narrowed: [headers]')" \
  || say FAIL "a narrowed cell reported a bare verdict — the row does not name what it gave up: $(awk -F'\t' '$1=="self|a|ok"' "$W/out-g/ledger.tsv")"

# (h) an `improvement` acceptance of a `status` class must be REFUSED by the loader (exit != 0),
# even when golden and candidate are byte-identical — the register is checked whether or not it
# ever fires.
cat >"$W/bad-accept.json" <<'JSON'
{"accepted":[{"id":"bad status accept","kind":"improvement","by":"selftest","cells":"^self\\|a\\|ok$","classes":["status"],"rationale":"should be refused"}]}
JSON
cp -R "$FIX" "$W/hsame"
bash "${here}/replay.sh" --golden "$FIX" --candidate "$W/hsame" --out "$W/out-h" --cells "$CELLS" \
  --allow-harness-skew --no-check-golden --accepted "$W/bad-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-h.log" 2>&1
rc=$?
grep -q "not kind=breaking" "$W/out-h.log" && msg_ok=1 || msg_ok=0
[ "$rc" != 0 ] && [ "$msg_ok" = 1 ] && say PASS "improvement accepting 'status' -> loader refuses (not kind=breaking)" || say FAIL "bad accept rc=$rc msg_ok=$msg_ok (see $W/out-h.log)"

# (i) an accepted `transform` reports ACCEPTED, never a silent PASS
cp -R "$FIX" "$W/xform"
python3 - "$W/xform/cells/self__b__stream.json" <<'EOF'
import json,sys
p=sys.argv[1]; d=json.load(open(p)); d["body"]["text"]=d["body"]["text"].replace("hi", "hi TOKEN123"); json.dump(d,open(p,"w"),separators=(",",":"),sort_keys=True)
EOF
cat >"$W/xform-accept.json" <<'JSON'
{"accepted":[{"id":"T-1 test token","kind":"improvement","by":"selftest","cells":"^self\\|b\\|stream$","expected_cells":1,"rationale":"selftest transform proof","transform":{"candidate":[[" TOKEN123",""]]}}]}
JSON
bash "${here}/replay.sh" --golden "$FIX" --candidate "$W/xform" --out "$W/out-i" --cells "$CELLS" \
  --allow-harness-skew --no-check-golden --accepted "$W/xform-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-i.log" 2>&1
rc=$?
row="$(awk -F'\t' '$1=="self|b|stream"{print; exit}' "$W/out-i/ledger.tsv")"
status_col="$(cut -f2 <<<"$row")"; title_col="$(cut -f3 <<<"$row")"
[ "$rc" = 0 ] && [ "$status_col" = PASS ] && [[ "$title_col" == ACCEPTED* ]] && say PASS "accepted transform -> row is PASS/ACCEPTED, not a silent identical" || say FAIL "transform accept rc=$rc status=$status_col title=$title_col"

# (j) a baseline id the golden no longer owes, not named anywhere -> RED
cp -R "$FIX" "$W/regress-golden"
python3 - "$W/regress-golden/ledger.tsv" <<'EOF'
import sys
p = sys.argv[1]
lines = open(p, encoding="utf-8").read().splitlines()
out = []
for ln in lines:
    parts = ln.split("\t")
    if parts and parts[0] == "self|a|ok":
        parts[1] = "SKIP"
        parts = parts[:3] + ["golden re-recorded this cell as unsupported"]
    out.append("\t".join(parts))
open(p, "w", encoding="utf-8").write("\n".join(out) + "\n")
EOF
printf 'self|a|ok\nself|b|stream\n' >"$W/baseline-ab.txt"
cp -R "$FIX" "$W/regress-cand"
bash "${here}/replay.sh" --golden "$W/regress-golden" --candidate "$W/regress-cand" --out "$W/out-j" --cells "$CELLS" \
  --allow-harness-skew --no-check-golden --accepted "$W/no-accept.json" --baseline "$W/baseline-ab.txt" --accepted-gaps "$W/no-gaps.json" >"$W/out-j.log" 2>&1
rc=$?
# LAST ROW WINS, the same rule load_ledger() and verdict.sh apply to this file. The candidate still
# carries the cell the golden stopped owing, so it also earns an `extra.candidate` row (the other
# half of the same fact, written first); the baseline verdict is the one that decides.
row="$(awk -F'\t' '$1=="self|a|ok"{r=$0} END{print r}' "$W/out-j/ledger.tsv")"
status_col="$(cut -f2 <<<"$row")"; title_col="$(cut -f3 <<<"$row")"
[ "$rc" != 0 ] && [ "$status_col" = FAIL ] && [[ "$title_col" == *"owed-baseline"* ]] && say PASS "golden dropped a baselined id, unnamed -> RED [owed-baseline regression]" || say FAIL "baseline regression rc=$rc status=$status_col title=$title_col"

# (k) the same regression, named in accepted-gaps.json -> GREEN with a named-gap line
cat >"$W/gaps-ab.json" <<'JSON'
{"accepted":[{"id":"selftest gap self|a|ok","cells":"^self\\|a\\|ok$","owner":"selftest","rationale":"intentionally dropped for this test"}]}
JSON
bash "${here}/replay.sh" --golden "$W/regress-golden" --candidate "$W/regress-cand" --out "$W/out-k" --cells "$CELLS" \
  --allow-harness-skew --no-check-golden --accepted "$W/no-accept.json" --baseline "$W/baseline-ab.txt" --accepted-gaps "$W/gaps-ab.json" >"$W/out-k.log" 2>&1
rc=$?
row="$(awk -F'\t' '$1=="self|a|ok"{r=$0} END{print r}' "$W/out-k/ledger.tsv")"
status_col="$(cut -f2 <<<"$row")"; title_col="$(cut -f3 <<<"$row")"
[ "$rc" = 0 ] && [ "$status_col" = PASS ] && [[ "$title_col" == *"ACCEPTED named gap"* ]] && say PASS "same regression named in accepted-gaps.json -> GREEN, named-gap line" || say FAIL "accepted gap rc=$rc status=$status_col title=$title_col"

# (l) golden/candidate produced by different (or absent) harness revisions -> exit 2, no comparison
cp -R "$FIX" "$W/hrev-g"; cp -R "$FIX" "$W/hrev-c"
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); d['harness_rev']='a'*64; json.dump(d, open(sys.argv[1],'w'))" "$W/hrev-g/meta.json"
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); d['harness_rev']='b'*64; json.dump(d, open(sys.argv[1],'w'))" "$W/hrev-c/meta.json"
# --no-check-golden, so this case reaches the HARNESS guard: the fixture names no binary_sha256 and
# the provenance guard would otherwise (correctly) refuse first, for a different reason.
bash "${here}/replay.sh" --golden "$W/hrev-g" --candidate "$W/hrev-c" --out "$W/out-l" --cells "$CELLS" \
  --no-check-golden --accepted "$W/no-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-l.log" 2>&1
rc=$?
grep -qi "harness" "$W/out-l.log" && msg_ok=1 || msg_ok=0
[ "$rc" = 2 ] && [ "$msg_ok" = 1 ] && say PASS "mismatched harness_rev -> exit 2, named in the message" || say FAIL "harness_rev mismatch rc=$rc msg_ok=$msg_ok"

# (m) --allow-harness-skew on that same mismatched pair -> proceeds to a normal verdict
bash "${here}/replay.sh" --golden "$W/hrev-g" --candidate "$W/hrev-c" --out "$W/out-m" --cells "$CELLS" \
  --allow-harness-skew --no-check-golden --accepted "$W/no-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-m.log" 2>&1
rc=$?
[ "$rc" = 0 ] && [ "$(fails_in "$W/out-m")" = 0 ] && say PASS "--allow-harness-skew proceeds past the same mismatch" || say FAIL "--allow-harness-skew rc=$rc fails=$(fails_in "$W/out-m")"

# (m-2) THE SCALAR AGREES AND THE RECORDINGS STILL DO NOT. `harness_rev` is one field that
# merge-recordings.py, renormalize.sh and a text editor all overwrite: a golden merged from a part
# recorded under A and a part recorded under B is stamped with B alone, so a candidate recorded
# today under B satisfied `grev == crev` and the differ compared the A cells against it with nothing
# said. Each shape below is a real writer's output, and each must refuse on its own.
_hrev_meta() {  # <dir> <python-dict-expression applied to the meta>
  python3 -c "
import json,sys
p=sys.argv[1]; d=json.load(open(p))
d['harness_rev']='b'*64
exec(sys.argv[2])
json.dump(d, open(p,'w'))" "$1/meta.json" "$2"
}
skew_case() {  # <label> <golden-mutation> <want-rc>
  local label="$1" gmut="$2" want="$3" dir_g="$W/skew-g" dir_c="$W/skew-c" rc
  rm -rf "$dir_g" "$dir_c"; cp -R "$FIX" "$dir_g"; cp -R "$FIX" "$dir_c"
  _hrev_meta "$dir_g" "$gmut"
  _hrev_meta "$dir_c" "pass"
  rm -rf "$W/out-skew"
  bash "${here}/replay.sh" --golden "$dir_g" --candidate "$dir_c" --out "$W/out-skew" --cells "$CELLS" \
    --no-check-golden --accepted "$W/no-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-skew.log" 2>&1
  rc=$?
  if [ "$rc" = "$want" ]; then say PASS "$label"; else say FAIL "$label (rc=$rc, want $want): $(tail -2 "$W/out-skew.log" | tr '\n' ' ' | cut -c1-200)"; fi
}
skew_case "a golden whose harness_rev_history names a rev the candidate never saw -> exit 2, even though the scalars agree" \
          "d['harness_rev_history']=['a'*64]" 2
skew_case "a golden merged from a part recorded under another rev (merged_from) -> exit 2" \
          "d['merged_from']=[{'part':'rec','recorded':1,'harness_rev':'a'*64}]" 2
skew_case "a golden whose harness_rev_recorded differs from its stamp -> exit 2 (the field is READ, not decoration)" \
          "d['harness_rev_recorded']='a'*64" 2
skew_case "identical provenance on both sides (the scalar alone) still compares" \
          "pass" 0

# …and the skew is authorisable, exactly like the scalar one, rather than being a wall.
rm -rf "$W/skew2-g" "$W/skew2-c"; cp -R "$FIX" "$W/skew2-g"; cp -R "$FIX" "$W/skew2-c"
_hrev_meta "$W/skew2-g" "d['harness_rev_history']=['a'*64]"
_hrev_meta "$W/skew2-c" "pass"
bash "${here}/replay.sh" --golden "$W/skew2-g" --candidate "$W/skew2-c" --out "$W/out-m2" --cells "$CELLS" \
  --allow-harness-skew --no-check-golden --accepted "$W/no-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-m2.log" 2>&1
rc=$?
m2_flag="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['meta']['harness_skew_allowed'])" "$W/out-m2/report.json" 2>/dev/null)"
[ "$rc" = 0 ] && [ "$m2_flag" = True ] \
  && say PASS "--allow-harness-skew proceeds past a HISTORY skew and report.json records that it was used" \
  || say FAIL "history skew under --allow-harness-skew rc=$rc harness_skew_allowed=$m2_flag"

# (n) an accepted transform on a cell whose headers carry content-length: the length moves BECAUSE
# the accepted rewrite moved the body, so it is the accepted change's shadow, not a second
# divergence. The row must report `body` alone — a phantom `headers` both mis-describes the row and
# would stop a correctly narrow `classes: ["body"]` entry from ever matching.
cp -R "$FIX" "$W/clen-g"; cp -R "$FIX" "$W/clen-c"
python3 - "$W/clen-g/cells/self__b__stream.json" "$W/clen-c/cells/self__b__stream.json" <<'EOF'
import json, sys
gp, cp_ = sys.argv[1], sys.argv[2]
g = json.load(open(gp))
g["headers"]["content-length"] = str(len(g["body"]["text"]))
json.dump(g, open(gp, "w"), separators=(",", ":"), sort_keys=True)
c = json.load(open(cp_))
c["body"]["text"] = c["body"]["text"].replace("hi", "hi TOKEN123")
c["headers"]["content-length"] = str(len(c["body"]["text"]))
json.dump(c, open(cp_, "w"), separators=(",", ":"), sort_keys=True)
EOF
bash "${here}/replay.sh" --golden "$W/clen-g" --candidate "$W/clen-c" --out "$W/out-n" --cells "$CELLS" \
  --allow-harness-skew --no-check-golden --accepted "$W/xform-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-n.log" 2>&1
rc=$?
row="$(awk -F'\t' '$1=="self|b|stream"{print; exit}' "$W/out-n/ledger.tsv")"
status_col="$(cut -f2 <<<"$row")"; title_col="$(cut -f3 <<<"$row")"
[ "$rc" = 0 ] && [ "$status_col" = PASS ] && [[ "$title_col" == *"ACCEPTED"* ]] && [[ "$title_col" == *": body" ]] \
  && say PASS "accepted transform + content-length -> class list is 'body' alone (no phantom headers)" \
  || say FAIL "transform content-length rc=$rc status=$status_col title=$title_col (expected the class list to end ': body')"

# (n1) A TRANSFORM MUST APPLY SYMMETRICALLY, NOT JUST TO THE CANDIDATE. A `transform` entry rewrites
# the candidate on the built-in assumption that the pattern it strips exists ONLY on the candidate
# side (the real case: a diagnostic code or a new series that 1.5.5 never emitted). That assumption
# breaks the moment the golden is ALSO shaped like the candidate — e.g. two recordings of the same
# 1.6.0 binary, or literally a self-diff — and stripping the pattern from the candidate alone then
# manufactures a divergence out of an already-identical pair. Here the pattern is present on BOTH
# sides identically before any transform runs, so the pair is byte-identical; the transform firing
# must not turn that into a FAIL. Before the fix this reported FAIL/body (the candidate lost the
# token, the golden kept it); after the fix it must report 0 diverging rows.
cp -R "$FIX" "$W/sym-g"; cp -R "$FIX" "$W/sym-c"
python3 - "$W/sym-g/cells/self__b__stream.json" "$W/sym-c/cells/self__b__stream.json" <<'EOF'
import json, sys
for p in sys.argv[1:]:
    d = json.load(open(p))
    d["body"]["text"] = d["body"]["text"].replace("hi", "hi TOKEN123")
    json.dump(d, open(p, "w"), separators=(",", ":"), sort_keys=True)
EOF
bash "${here}/replay.sh" --golden "$W/sym-g" --candidate "$W/sym-c" --out "$W/out-n1" --cells "$CELLS" \
  --allow-harness-skew --no-check-golden --accepted "$W/xform-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-n1.log" 2>&1
rc=$?
row="$(awk -F'\t' '$1=="self|b|stream"{print; exit}' "$W/out-n1/ledger.tsv")"
status_col="$(cut -f2 <<<"$row")"
[ "$rc" = 0 ] && [ "$status_col" = PASS ] && [ "$(fails_in "$W/out-n1")" = 0 ] \
  && say PASS "a transform-bearing register applied to a candidate that is ALSO 1.6.0-shaped (self-diff / A-vs-B) -> 0 diverging, not a phantom body FAIL" \
  || say FAIL "symmetric-transform self-diff rc=$rc status=$status_col fails=$(fails_in "$W/out-n1") row='$row' (see $W/out-n1.log)"

# (n2) …and the REAL asymmetric case — a golden line that never carried the pattern, a candidate
# that does — is still forgiven exactly as before: applying the transform to a golden copy that
# lacks the pattern is a no-op on that side, so the fix changes nothing about the intended use.
# This is the same fixture and register as case (i); re-asserted here, side by side with (n1) and
# (n3), so the three properties this fix must hold are proven together rather than scattered.
row="$(awk -F'\t' '$1=="self|b|stream"{print; exit}' "$W/out-i/ledger.tsv")"
status_col="$(cut -f2 <<<"$row")"; title_col="$(cut -f3 <<<"$row")"
[ "$status_col" = PASS ] && [[ "$title_col" == ACCEPTED* ]] \
  && say PASS "the real asymmetric case (golden without the pattern, candidate with it) is still forgiven, unchanged by the symmetry fix" \
  || say FAIL "asymmetric transform case regressed: status=$status_col title=$title_col"

# (n3) A TRANSFORM MUST NEVER LAUNDER A GENUINE DIVERGENCE. The candidate here carries the token
# AND an unrelated body corruption the transform's regex does not touch; stripping the token
# symmetrically must still leave the real difference visible, on both sides of the fix.
cp -R "$FIX" "$W/lie-c"
python3 - "$W/lie-c/cells/self__b__stream.json" <<'EOF'
import json, sys
p = sys.argv[1]; d = json.load(open(p))
d["body"]["text"] = d["body"]["text"].replace("hi", "hi TOKEN123").replace("[DONE]", "[WRONG]")
json.dump(d, open(p, "w"), separators=(",", ":"), sort_keys=True)
EOF
bash "${here}/replay.sh" --golden "$FIX" --candidate "$W/lie-c" --out "$W/out-n3" --cells "$CELLS" \
  --allow-harness-skew --no-check-golden --accepted "$W/xform-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-n3.log" 2>&1
rc=$?
row="$(awk -F'\t' '$1=="self|b|stream"{print; exit}' "$W/out-n3/ledger.tsv")"
status_col="$(cut -f2 <<<"$row")"; title_col="$(cut -f3 <<<"$row")"
[ "$rc" != 0 ] && [ "$status_col" = FAIL ] && [[ "$title_col" == *"body"* ]] \
  && say PASS "a transform never turns a genuine divergence into a pass -> still RED [body] under an otherwise-forgiving entry" \
  || say FAIL "a planted body diff under a transform-bearing entry was NOT red: rc=$rc status=$status_col title=$title_col"

# (pp) A FIRED TRANSFORM MAY TAKE A CLASS IT RENDERED IDENTICAL, WHATEVER THAT CLASS IS RATED ON
# THIS CELL'S FAMILY. The fired-transform branch only runs when compare(g_t, cc_t) found NOTHING
# left to compare — the declared rewrite accounts for the WHOLE of what classes_raw differed in,
# because a transform only ever touches `effects.stderr` and `body.text` (plus the content-length
# shadow of the latter): every other field of g_t/cc_t is a verbatim copy of g/cc, so a class outside
# {headers, body, effects.stderr} that had moved would still show up in `classes` and this branch
# would never run. Subtracting the FAMILY-rated money set on top of the entry's own `allowed` (as
# v0.3.1 did) refused an `improvement` D-1-shaped transform credit for `body` on every
# BODY_IS_CONTRACT family (boot.warning, boot.refusal, cli, config.migrate, admin.ops, ops.scrape)
# even when the rewritten pair was byte-identical: 18 real boot.warning cells in busbar's own corpus
# went from ACCEPTED under v0.2.3/v0.3.0's tool-side conditions to a phantom RED under v0.3.1,
# purely because their family rates `body` 10 and D-1 is `kind: improvement`. Nothing was being
# forgiven — the rewrite already accounted for the entire difference — so nothing should have been
# refused.
pp_cells() {  # <out> — self|b|stream reclassified onto a BODY_IS_CONTRACT family, like busbar's boot.warning
  python3 - "$CELLS" "$1" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
for c in d["cells"]:
    if c["id"] == "self|b|stream":
        c["family"] = "boot.warning"
json.dump(d, open(sys.argv[2], "w"))
EOF
}
pp_cells "$W/pp-cells.json"
cat >"$W/pp-accept.json" <<'JSON'
{"accepted":[{"id":"PP-1 diag suffix","kind":"improvement","by":"selftest","cells":"^self\\|b\\|stream$","expected_cells":1,"rationale":"selftest: a D-1-shaped mid-line diagnostic-code suffix, never a prefix","transform":{"candidate":[[" diag=BUSBAR-\\d{4}",""]]}}]}
JSON

# (pp1) body differs ONLY by the diag suffix (mid-line, not a prefix) -> ACCEPTED, on a family
# whose `body` is rated 10 (money). This is the exact shape of the real regression: RED under the
# v0.3.1 family-money subtraction, GREEN once a transform's own `allowed` is what decides.
cp -R "$FIX" "$W/pp1-c"
python3 - "$W/pp1-c/cells/self__b__stream.json" <<'EOF'
import json, sys
p = sys.argv[1]; d = json.load(open(p))
d["body"]["text"] = d["body"]["text"].replace("hi", "hi diag=BUSBAR-3010")
json.dump(d, open(p, "w"), separators=(",", ":"), sort_keys=True)
EOF
bash "${here}/replay.sh" --golden "$FIX" --candidate "$W/pp1-c" --out "$W/out-pp1" --cells "$W/pp-cells.json" \
  --allow-harness-skew --no-check-golden --accepted "$W/pp-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-pp1.log" 2>&1
rc=$?
row="$(awk -F'\t' '$1=="self|b|stream"{print; exit}' "$W/out-pp1/ledger.tsv")"
status_col="$(cut -f2 <<<"$row")"; title_col="$(cut -f3 <<<"$row")"
[ "$rc" = 0 ] && [ "$status_col" = PASS ] && [[ "$title_col" == *"ACCEPTED"* ]] && [[ "$title_col" == *"PP-1"* ]] \
  && say PASS "an improvement transform takes 'body' on a BODY_IS_CONTRACT family once the rewrite makes the pair byte-identical (the real 18-cell regression, reproduced and fixed)" \
  || say FAIL "PP-1 body-only diag suffix on a BODY_IS_CONTRACT family was not accepted: rc=$rc status=$status_col title=$title_col"

# (pp2) …but if the body ALSO differs somewhere the transform's pattern never touches, the rewritten
# pair is NOT byte-identical, the fired-transform branch never runs, and the cell stays RED — an
# `improvement` transform still may not launder a real difference on a money-rated family.
cp -R "$FIX" "$W/pp2-c"
python3 - "$W/pp2-c/cells/self__b__stream.json" <<'EOF'
import json, sys
p = sys.argv[1]; d = json.load(open(p))
d["body"]["text"] = d["body"]["text"].replace("hi", "hi diag=BUSBAR-3010").replace("[DONE]", "[WRONG]")
json.dump(d, open(p, "w"), separators=(",", ":"), sort_keys=True)
EOF
bash "${here}/replay.sh" --golden "$FIX" --candidate "$W/pp2-c" --out "$W/out-pp2" --cells "$W/pp-cells.json" \
  --allow-harness-skew --no-check-golden --accepted "$W/pp-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-pp2.log" 2>&1
rc=$?
row="$(awk -F'\t' '$1=="self|b|stream"{print; exit}' "$W/out-pp2/ledger.tsv")"
status_col="$(cut -f2 <<<"$row")"; title_col="$(cut -f3 <<<"$row")"
[ "$rc" != 0 ] && [ "$status_col" = FAIL ] && [[ "$title_col" == *"body"* ]] \
  && say PASS "…and a body diff OUTSIDE the diag suffix on the same money-rated family still stays RED [body]" \
  || say FAIL "PP-2 body diff beyond the transform's pattern was not red: rc=$rc status=$status_col title=$title_col"

# (pp3) …and if only STATUS differs (a field no transform ever touches), the fired-transform branch
# never runs either — status is untouched by the rewrite, so compare(g_t, cc_t) still names it and
# the cell stays RED [status], never credited to an `improvement` entry (status is globally MONEY
# and D-1-shaped entries are never `kind: breaking`).
cp -R "$FIX" "$W/pp3-c"
python3 - "$W/pp3-c/cells/self__b__stream.json" <<'EOF'
import json, sys
p = sys.argv[1]; d = json.load(open(p))
d["body"]["text"] = d["body"]["text"].replace("hi", "hi diag=BUSBAR-3010")
d["status"] = 500
json.dump(d, open(p, "w"), separators=(",", ":"), sort_keys=True)
EOF
bash "${here}/replay.sh" --golden "$FIX" --candidate "$W/pp3-c" --out "$W/out-pp3" --cells "$W/pp-cells.json" \
  --allow-harness-skew --no-check-golden --accepted "$W/pp-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-pp3.log" 2>&1
rc=$?
row="$(awk -F'\t' '$1=="self|b|stream"{print; exit}' "$W/out-pp3/ledger.tsv")"
status_col="$(cut -f2 <<<"$row")"; title_col="$(cut -f3 <<<"$row")"
[ "$rc" != 0 ] && [ "$status_col" = FAIL ] && [[ "$title_col" == status* ]] \
  && say PASS "…and a status divergence beside an otherwise-forgivable diag suffix still stays RED [status], never taken by the transform" \
  || say FAIL "PP-3 status divergence was not red: rc=$rc status=$status_col title=$title_col"

# (pp4) A transform pattern that can match the empty string (or names no literal text at all) is
# refused at LOAD. "Byte-identical after the rewrite" only proves the rewrite named the right token
# when the pattern IS a token; `.*`/`\s*`/`(.|\n)*` can swallow arbitrary surrounding content and
# would make PP-1's proof vacuous — it would "explain" any divergence, not just a diagnostic suffix.
for bad_pat in '.*' '\s*' '(.|\n)*' '\d+'; do
  python3 - "$W/pp4-accept.json" "$bad_pat" <<'EOF'
import json, sys
e = {"id": "PP-4 too broad", "kind": "improvement", "by": "selftest", "cells": r"^self\|b\|stream$",
     "expected_cells": 1, "rationale": "selftest: an over-broad transform pattern",
     "transform": {"candidate": [[sys.argv[2], ""]]}}
json.dump({"accepted": [e]}, open(sys.argv[1], "w"))
EOF
  bash "${here}/replay.sh" --golden "$FIX" --candidate "$FIX" --out "$W/out-pp4" --cells "$W/pp-cells.json" \
    --allow-harness-skew --no-check-golden --accepted "$W/pp4-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-pp4.log" 2>&1
  rc=$?
  grep -qi "refused" "$W/out-pp4.log" && msg_ok=1 || msg_ok=0
  if [ "$rc" != 0 ] && [ "$msg_ok" = 1 ]; then
    say PASS "an over-broad transform pattern ($bad_pat) is refused at load"
  else
    say FAIL "an over-broad transform pattern ($bad_pat) was NOT refused: rc=$rc msg_ok=$msg_ok (see $W/out-pp4.log)"
  fi
done

# (qq) `additive` — A THIRD REGISTER KIND FOR GROWTH THE TOOL PROVES, NOT ONE AN OWNER ASSERTS.
# F-011's admin.ops views (GetHooks, PostHooks, GetOpenapiJson, ...) are BODY_IS_CONTRACT, so
# `improvement` can never take `body`/`headers` there (rated 10), and `breaking` would misdescribe a
# response that dropped nothing and changed no existing value. `additive` may take `body`/`headers`
# ONLY when additive_superset()/additive_headers_superset() prove the candidate a superset of the
# golden at every path the golden defines; a failed proof leaves the cell red and names where.
qq_cells() {  # <out> — self|a|ok reclassified onto a BODY_IS_CONTRACT family, like busbar's admin.ops
  python3 - "$CELLS" "$1" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
for c in d["cells"]:
    if c["id"] == "self|a|ok":
        c["family"] = "admin.ops"
json.dump(d, open(sys.argv[2], "w"))
EOF
}
qq_cells "$W/qq-cells.json"
cat >"$W/qq-accept.json" <<'JSON'
{"accepted":[{"id":"QQ-1 additive view","kind":"additive","by":"selftest","cells":"^self\\|a\\|ok$","expected_cells":1,"classes":["body","headers"],"changelog":"selftest: admin.ops views grow keys additively.","rationale":"selftest: F-011-shaped additive proof"}]}
JSON

# (qq1) candidate body adds a key beside every golden key/value unchanged, candidate headers add a
# new header -> ACCEPTED, naming QQ-1, on a family where body/headers are rated 10.
cp -R "$FIX" "$W/qq1-c"
python3 - "$W/qq1-c/cells/self__a__ok.json" <<'EOF'
import json, sys
p = sys.argv[1]; d = json.load(open(p))
d["body"]["json"]["extra"] = True
d["headers"]["x-new"] = "1"
json.dump(d, open(p, "w"), separators=(",", ":"), sort_keys=True)
EOF
bash "${here}/replay.sh" --golden "$FIX" --candidate "$W/qq1-c" --out "$W/out-qq1" --cells "$W/qq-cells.json" \
  --allow-harness-skew --no-check-golden --accepted "$W/qq-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-qq1.log" 2>&1
rc=$?
row="$(awk -F'\t' '$1=="self|a|ok"{print; exit}' "$W/out-qq1/ledger.tsv")"
status_col="$(cut -f2 <<<"$row")"; title_col="$(cut -f3 <<<"$row")"
[ "$rc" = 0 ] && [ "$status_col" = PASS ] && [[ "$title_col" == *"ACCEPTED"* ]] && [[ "$title_col" == *"QQ-1"* ]] \
  && say PASS "additive accepts a superset body + an added header on a BODY_IS_CONTRACT family (the real F-011 shape)" \
  || say FAIL "QQ-1 superset body/added header was not accepted: rc=$rc status=$status_col title=$title_col"

# (qq2) a golden value CHANGED (not merely added-beside) -> still RED, naming the JSON path.
cp -R "$FIX" "$W/qq2-c"
python3 - "$W/qq2-c/cells/self__a__ok.json" <<'EOF'
import json, sys
p = sys.argv[1]; d = json.load(open(p))
d["body"]["json"]["usage"]["in"] = 99
json.dump(d, open(p, "w"), separators=(",", ":"), sort_keys=True)
EOF
bash "${here}/replay.sh" --golden "$FIX" --candidate "$W/qq2-c" --out "$W/out-qq2" --cells "$W/qq-cells.json" \
  --allow-harness-skew --no-check-golden --accepted "$W/qq-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-qq2.log" 2>&1
rc=$?
row="$(awk -F'\t' '$1=="self|a|ok"{print; exit}' "$W/out-qq2/ledger.tsv")"
status_col="$(cut -f2 <<<"$row")"; diff_col="$(cut -f4 <<<"$row")"
[ "$rc" != 0 ] && [ "$status_col" = FAIL ] && [[ "$diff_col" == *"additive: not a superset at"* ]] && [[ "$diff_col" == *"/usage/in"* ]] \
  && say PASS "additive refuses a cell where a golden value CHANGED, naming the path" \
  || say FAIL "QQ-2 changed value was not red-with-path: rc=$rc status=$status_col diff='$diff_col'"

# (qq3) an ADDED header is additive growth; a CHANGED header value is not -> RED, naming the header.
cp -R "$FIX" "$W/qq3-c"
python3 - "$W/qq3-c/cells/self__a__ok.json" <<'EOF'
import json, sys
p = sys.argv[1]; d = json.load(open(p))
d["headers"]["content-type"] = "text/plain"
json.dump(d, open(p, "w"), separators=(",", ":"), sort_keys=True)
EOF
bash "${here}/replay.sh" --golden "$FIX" --candidate "$W/qq3-c" --out "$W/out-qq3" --cells "$W/qq-cells.json" \
  --allow-harness-skew --no-check-golden --accepted "$W/qq-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-qq3.log" 2>&1
rc=$?
row="$(awk -F'\t' '$1=="self|a|ok"{print; exit}' "$W/out-qq3/ledger.tsv")"
status_col="$(cut -f2 <<<"$row")"; diff_col="$(cut -f4 <<<"$row")"
[ "$rc" != 0 ] && [ "$status_col" = FAIL ] && [[ "$diff_col" == *"additive: not a superset at header"* ]] && [[ "$diff_col" == *"content-type"* ]] \
  && say PASS "additive refuses a cell where a golden HEADER's value changed, naming the header" \
  || say FAIL "QQ-3 changed header was not red-with-name: rc=$rc status=$status_col diff='$diff_col'"

# (qq4) a status change beside an otherwise-superset body -> still RED [status]; additive never
# touches status (it is not in ADDITIVE_CLASSES), so the row reports the real status divergence,
# not an additive rejection.
cp -R "$FIX" "$W/qq4-c"
python3 - "$W/qq4-c/cells/self__a__ok.json" <<'EOF'
import json, sys
p = sys.argv[1]; d = json.load(open(p))
d["body"]["json"]["extra"] = True
d["status"] = 201
json.dump(d, open(p, "w"), separators=(",", ":"), sort_keys=True)
EOF
bash "${here}/replay.sh" --golden "$FIX" --candidate "$W/qq4-c" --out "$W/out-qq4" --cells "$W/qq-cells.json" \
  --allow-harness-skew --no-check-golden --accepted "$W/qq-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-qq4.log" 2>&1
rc=$?
row="$(awk -F'\t' '$1=="self|a|ok"{print; exit}' "$W/out-qq4/ledger.tsv")"
status_col="$(cut -f2 <<<"$row")"; title_col="$(cut -f3 <<<"$row")"
[ "$rc" != 0 ] && [ "$status_col" = FAIL ] && [[ "$title_col" == status* ]] \
  && say PASS "a status change beside a superset body still stays RED [status], never taken by additive" \
  || say FAIL "QQ-4 status-beside-superset-body was not red: rc=$rc status=$status_col title=$title_col"

# (qq5) an additive entry naming `status` is refused at LOAD — additive is defined for body/headers
# only, and there is no superset relation for a status code.
cat >"$W/qq5-accept.json" <<'JSON'
{"accepted":[{"id":"QQ-5 bad status","kind":"additive","by":"selftest","cells":"^self\\|a\\|ok$","expected_cells":1,"classes":["status"],"changelog":"x","rationale":"y"}]}
JSON
bash "${here}/replay.sh" --golden "$FIX" --candidate "$FIX" --out "$W/out-qq5" --cells "$W/qq-cells.json" \
  --allow-harness-skew --no-check-golden --accepted "$W/qq5-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-qq5.log" 2>&1
rc=$?
grep -qi "kind=breaking\|additive" "$W/out-qq5.log" && msg_ok=1 || msg_ok=0
[ "$rc" != 0 ] && [ "$msg_ok" = 1 ] \
  && say PASS "an additive entry naming 'status' is refused at load" \
  || say FAIL "QQ-5 additive-naming-status was NOT refused: rc=$rc msg_ok=$msg_ok (see $W/out-qq5.log)"

# (rr) `text_list_growth` — THE SAME PROOF FOR A LIST NAMED IN PROSE, NOT JSON. admin.ops's
# DeleteOverlaySection|not-found and the boot-refusal config cells (P20/P29/P30) name their enum as
# a backtick-quoted comma list inside a sentence, on `body`/`effects.stderr` — there is no JSON
# structure for additive_superset() to walk. `text_list_growth: true` proves the SAME growth by
# splicing golden's own list text back into the candidate: if what remains is not byte-identical to
# golden, the difference was never just the list.
rr_cells() {  # <out> — self|b|stream reclassified onto a BODY_IS_CONTRACT family, like boot.refusal
  python3 - "$CELLS" "$1" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
for c in d["cells"]:
    if c["id"] == "self|b|stream":
        c["family"] = "boot.refusal"
json.dump(d, open(sys.argv[2], "w"))
EOF
}
rr_cells "$W/rr-cells.json"
cat >"$W/rr-accept.json" <<'JSON'
{"accepted":[{"id":"RR-1 refusal list growth","kind":"additive","by":"selftest","cells":"^self\\|b\\|stream$","expected_cells":1,"classes":["body"],"changelog":"selftest: config refusal enum grows additively","rationale":"selftest: text_list_growth proof","text_list_growth":true}]}
JSON
rr_body() {  # <out-cell-json> <text>
  python3 - "$1" "$2" <<'EOF'
import json, sys
p, t = sys.argv[1], sys.argv[2]
d = json.load(open(p))
d["body"] = {"text": t}
json.dump(d, open(p, "w"), separators=(",", ":"), sort_keys=True)
EOF
}
RR_GOLDEN='unknown overlay section `limits`: expected `groups`, `hooks`, `root`, or `plugin_versions`'
cp -R "$FIX" "$W/rr-golden"
rr_body "$W/rr-golden/cells/self__b__stream.json" "$RR_GOLDEN"

rr_run() {  # <label> <candidate-text> <want: accept|reject> <needle>
  local label="$1" ctext="$2" want="$3" needle="$4"
  rm -rf "$W/rr-cand"; cp -R "$FIX" "$W/rr-cand"
  rr_body "$W/rr-cand/cells/self__b__stream.json" "$ctext"
  rm -rf "$W/out-rr"
  bash "${here}/replay.sh" --golden "$W/rr-golden" --candidate "$W/rr-cand" --out "$W/out-rr" --cells "$W/rr-cells.json" \
    --allow-harness-skew --no-check-golden --accepted "$W/rr-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-rr.log" 2>&1
  rc=$?
  row="$(awk -F'\t' '$1=="self|b|stream"{print; exit}' "$W/out-rr/ledger.tsv")"
  status_col="$(cut -f2 <<<"$row")"; title_col="$(cut -f3 <<<"$row")"; diff_col="$(cut -f4 <<<"$row")"
  if [ "$want" = accept ]; then
    if [ "$rc" = 0 ] && [ "$status_col" = PASS ] && [[ "$title_col" == *"ACCEPTED"* ]] && [[ "$diff_col" == *"$needle"* ]]; then
      say PASS "$label"
    else
      say FAIL "$label (rc=$rc status=$status_col title=$title_col diff='$diff_col')"
    fi
  else
    if [ "$rc" != 0 ] && [ "$status_col" = FAIL ] && [[ "$diff_col" == *"$needle"* ]]; then
      say PASS "$label"
    else
      say FAIL "$label (rc=$rc status=$status_col diff='$diff_col')"
    fi
  fi
}

# (rr1) pure appended items -> ACCEPTED, naming the items.
rr_run "text_list_growth accepts pure appended items, naming them" \
  'unknown overlay section `limits`: expected `groups`, `hooks`, `root`, `plugin_versions`, `identity-providers`, `export`, `tools`, `agents`' \
  accept "additive: added identity-providers, export, tools, agents"

# (rr2) the template word changed ("expected" -> "expected one of") ALONGSIDE the growth -> RED,
# naming the first differing byte -- the real admin.ops|DeleteOverlaySection|not-found shape.
rr_run "text_list_growth refuses a reworded template beside real growth, naming the first differing byte" \
  'unknown overlay section `limits`: expected one of `groups`, `hooks`, `root`, `plugin_versions`, `identity-providers`, `export`, `tools`, `agents`' \
  reject "additive: not a superset at text byte"

# (rr3) an item REMOVED from the golden's list -> RED.
rr_run "text_list_growth refuses a removed item" \
  'unknown overlay section `limits`: expected `groups`, `root`, or `plugin_versions`' \
  reject "additive: not a superset at list item"

# (rr4) an item INSERTED mid-list (not appended at the end) -> RED.
rr_run "text_list_growth refuses an item inserted mid-list" \
  'unknown overlay section `limits`: expected `groups`, `hooks`, `NEW`, `root`, or `plugin_versions`' \
  reject "additive: not a superset at list item"

# (rr5) growth in TWO separate backtick lists in the same text -> RED, saying why: one declared
# list per entry keeps the check narrow.
rr_run_two_lists() {
  rm -rf "$W/rr-golden2" "$W/rr-cand2"
  cp -R "$FIX" "$W/rr-golden2"; cp -R "$FIX" "$W/rr-cand2"
  rr_body "$W/rr-golden2/cells/self__b__stream.json" \
    'unknown overlay section `limits`: expected `groups`, `hooks`, `root`, or `plugin_versions` in file `a`, `b`, or `c`'
  rr_body "$W/rr-cand2/cells/self__b__stream.json" \
    'unknown overlay section `limits`: expected `groups`, `hooks`, `root`, `plugin_versions`, `identity-providers` in file `a`, `b`, `c`, `d`'
  bash "${here}/replay.sh" --golden "$W/rr-golden2" --candidate "$W/rr-cand2" --out "$W/out-rr5" --cells "$W/rr-cells.json" \
    --allow-harness-skew --no-check-golden --accepted "$W/rr-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-rr5.log" 2>&1
  rc=$?
  row="$(awk -F'\t' '$1=="self|b|stream"{print; exit}' "$W/out-rr5/ledger.tsv")"
  status_col="$(cut -f2 <<<"$row")"; diff_col="$(cut -f4 <<<"$row")"
  [ "$rc" != 0 ] && [ "$status_col" = FAIL ] && [[ "$diff_col" == *"more than one backtick list"* ]] \
    && say PASS "text_list_growth refuses growth in TWO lists at once (one declared list per entry keeps the check narrow)" \
    || say FAIL "growth-in-two-lists was not red-with-reason: rc=$rc status=$status_col diff='$diff_col'"
}
rr_run_two_lists

# (rr6) `effects.stderr` is refused at load without `text_list_growth: true` -- there is no
# JSON-superset relation for a raw string, so naming it bare would be a silent no-op acceptance.
cat >"$W/rr6-accept.json" <<'JSON'
{"accepted":[{"id":"RR-6 bad stderr additive","kind":"additive","by":"selftest","cells":"^self\\|b\\|stream$","expected_cells":1,"classes":["effects.stderr"],"changelog":"x","rationale":"y"}]}
JSON
bash "${here}/replay.sh" --golden "$FIX" --candidate "$FIX" --out "$W/out-rr6" --cells "$W/rr-cells.json" \
  --allow-harness-skew --no-check-golden --accepted "$W/rr6-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-rr6.log" 2>&1
rc=$?
grep -qi "text_list_growth" "$W/out-rr6.log" && msg_ok=1 || msg_ok=0
[ "$rc" != 0 ] && [ "$msg_ok" = 1 ] \
  && say PASS "an additive entry naming 'effects.stderr' without text_list_growth is refused at load" \
  || say FAIL "RR-6 bare-stderr-additive was NOT refused: rc=$rc msg_ok=$msg_ok (see $W/out-rr6.log)"

# (ss) `text_list_growth` REACHES A STRING LEAF INSIDE AN OTHERWISE-SUPERSET JSON BODY, not just a
# plain-text body. admin.ops|DeleteOverlaySection|not-found's real shape is JSON — the enum lives at
# `/error/message` — so the proof has to run additive_superset()'s ordinary walk (extra keys allowed,
# every other value equal) and apply text_list_growth_check() ONLY at the one string leaf that
# differs, deferring rather than failing on a string mismatch mid-walk. The real target text uses
# busbar's actual Oxford "or" before the LAST item on both sides (golden: "...`root`, or
# `plugin_versions`"; grown candidate: "...`plugin_versions`, ..., or `agents`") — proving the splice
# still holds however this list is punctuated, wherever "or" was in the run.
ss_cells() {  # <out> — self|a|ok reclassified onto a BODY_IS_CONTRACT family, like admin.ops
  python3 - "$CELLS" "$1" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
for c in d["cells"]:
    if c["id"] == "self|a|ok":
        c["family"] = "admin.ops"
json.dump(d, open(sys.argv[2], "w"))
EOF
}
ss_cells "$W/ss-cells.json"
cat >"$W/ss-accept.json" <<'JSON'
{"accepted":[{"id":"SS-1 leaf list growth","kind":"additive","by":"selftest","cells":"^self\\|a\\|ok$","expected_cells":1,"classes":["body"],"changelog":"selftest: overlay section enum grows additively","rationale":"selftest: text_list_growth on a JSON leaf","text_list_growth":true}]}
JSON
ss_msg() {  # <out-cell-json> <message> [<hint>]
  python3 - "$1" "$2" "${3:-}" <<'EOF'
import json, sys
p, msg, hint = sys.argv[1], sys.argv[2], sys.argv[3]
d = json.load(open(p))
d["body"]["json"]["error"] = {"message": msg}
if hint:
    d["body"]["json"]["hint"] = hint
json.dump(d, open(p, "w"), separators=(",", ":"), sort_keys=True)
EOF
}
SS_GOLDEN='unknown overlay section `limits`: expected `groups`, `hooks`, `root`, or `plugin_versions`'
cp -R "$FIX" "$W/ss-golden"
ss_msg "$W/ss-golden/cells/self__a__ok.json" "$SS_GOLDEN" "see docs"

ss_run() {  # <label> <candidate-message> <candidate-hint> <want: accept|reject> <needle>
  local label="$1" cmsg="$2" chint="$3" want="$4" needle="$5"
  rm -rf "$W/ss-cand"; cp -R "$FIX" "$W/ss-cand"
  ss_msg "$W/ss-cand/cells/self__a__ok.json" "$cmsg" "$chint"
  rm -rf "$W/out-ss"
  bash "${here}/replay.sh" --golden "$W/ss-golden" --candidate "$W/ss-cand" --out "$W/out-ss" --cells "$W/ss-cells.json" \
    --allow-harness-skew --no-check-golden --accepted "$W/ss-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-ss.log" 2>&1
  rc=$?
  row="$(awk -F'\t' '$1=="self|a|ok"{print; exit}' "$W/out-ss/ledger.tsv")"
  status_col="$(cut -f2 <<<"$row")"; title_col="$(cut -f3 <<<"$row")"; diff_col="$(cut -f4 <<<"$row")"
  if [ "$want" = accept ]; then
    if [ "$rc" = 0 ] && [ "$status_col" = PASS ] && [[ "$title_col" == *"ACCEPTED"* ]] && [[ "$diff_col" == *"$needle"* ]]; then
      say PASS "$label"
    else
      say FAIL "$label (rc=$rc status=$status_col title=$title_col diff='$diff_col')"
    fi
  else
    if [ "$rc" != 0 ] && [ "$status_col" = FAIL ] && [[ "$diff_col" == *"$needle"* ]]; then
      say PASS "$label"
    else
      say FAIL "$label (rc=$rc status=$status_col diff='$diff_col')"
    fi
  fi
}

# (ss1) pure appended items, on the real Oxford-"or" shape -> ACCEPTED, naming the JSON pointer and
# the items.
ss_run "text_list_growth reaches a JSON leaf: pure appended items (Oxford 'or'), naming path + items" \
  'unknown overlay section `limits`: expected `groups`, `hooks`, `root`, `plugin_versions`, `identity-providers`, `export`, `tools`, or `agents`' \
  "see docs" accept "added identity-providers, export, tools, agents at /error/message"

# (ss2) the template word changed ALONGSIDE the growth -> RED, naming the leaf's path AND the first
# differing byte.
ss_run "leaf text_list_growth refuses a reworded template beside real growth, naming path + byte" \
  'unknown overlay section `limits`: expected one of `groups`, `hooks`, `root`, `plugin_versions`, `identity-providers`, `export`, `tools`, or `agents`' \
  "see docs" reject "additive: not a superset at /error/message (not a superset at text byte"

# (ss3) an item REMOVED from golden's list, inside the leaf -> RED, naming the path.
ss_run "leaf text_list_growth refuses a removed item, naming the path" \
  'unknown overlay section `limits`: expected `groups`, `root`, or `plugin_versions`' \
  "see docs" reject "additive: not a superset at /error/message"

# (ss4) an item INSERTED mid-list inside the leaf -> RED, naming the path.
ss_run "leaf text_list_growth refuses an item inserted mid-list, naming the path" \
  'unknown overlay section `limits`: expected `groups`, `hooks`, `NEW`, `root`, or `plugin_versions`' \
  "see docs" reject "additive: not a superset at /error/message"

# (ss5) growth in TWO backtick lists inside the SAME leaf's text -> RED, same reason as (rr5). Needs
# its own golden (also carrying two lists) so the counts still pair — a golden with ONE list and a
# candidate with two is the "could not pair" case, a different refusal from "touched more than one".
rm -rf "$W/ss-golden5"; cp -R "$FIX" "$W/ss-golden5"
ss_msg "$W/ss-golden5/cells/self__a__ok.json" \
  'unknown overlay section `limits`: expected `groups`, `hooks`, `root`, or `plugin_versions` in file `a`, `b`, or `c`' "see docs"
rm -rf "$W/ss-cand5"; cp -R "$FIX" "$W/ss-cand5"
ss_msg "$W/ss-cand5/cells/self__a__ok.json" \
  'unknown overlay section `limits`: expected `groups`, `hooks`, `root`, `plugin_versions`, `identity-providers` in file `a`, `b`, `c`, `d`' "see docs"
rm -rf "$W/out-ss5"
bash "${here}/replay.sh" --golden "$W/ss-golden5" --candidate "$W/ss-cand5" --out "$W/out-ss5" --cells "$W/ss-cells.json" \
  --allow-harness-skew --no-check-golden --accepted "$W/ss-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-ss5.log" 2>&1
rc=$?
row="$(awk -F'\t' '$1=="self|a|ok"{print; exit}' "$W/out-ss5/ledger.tsv")"
status_col="$(cut -f2 <<<"$row")"; diff_col="$(cut -f4 <<<"$row")"
[ "$rc" != 0 ] && [ "$status_col" = FAIL ] && [[ "$diff_col" == *"more than one backtick list"* ]] \
  && say PASS "leaf text_list_growth refuses growth in two lists inside one leaf" \
  || say FAIL "leaf two-lists-in-one-leaf was not red-with-reason: rc=$rc status=$status_col diff='$diff_col'"

# (ss6) TWO DIFFERENT LEAVES differ (message grew its list AND hint changed) -> RED, naming BOTH
# JSON pointers -- one leaf's own story is never enough to explain a second leaf moving too.
ss_run "two differing string leaves are refused together, naming both paths" \
  'unknown overlay section `limits`: expected `groups`, `hooks`, `root`, `plugin_versions`, or `identity-providers`' \
  "see the docs" reject "more than one differing string leaf: /error/message, /hint"

# (tt) `description_corrections` — A NAMED LEAF MAY DIFFER OUTRIGHT, NO GROWTH PROOF NEEDED, BECAUSE
# THE REGISTER SAYS SO EXPLICITLY. Unlike `text_list_growth` (which proves growth mechanically),
# this is a declared factual correction to 1.5.5's prose: the entry names the exact JSON pointer,
# and ONLY that leaf may differ — every other leaf still follows the ordinary superset rules, and a
# pointer that never resolves to a string in the golden is refused before any cell is even compared.
tt_cells() {  # <out> — self|a|ok reclassified onto a BODY_IS_CONTRACT family, like admin.ops
  python3 - "$CELLS" "$1" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
for c in d["cells"]:
    if c["id"] == "self|a|ok":
        c["family"] = "admin.ops"
json.dump(d, open(sys.argv[2], "w"))
EOF
}
tt_cells "$W/tt-cells.json"
cat >"$W/tt-accept.json" <<'JSON'
{"accepted":[{"id":"TT-1 corrected description","kind":"additive","by":"selftest","cells":"^self\\|a\\|ok$","expected_cells":1,"classes":["body"],"changelog":"selftest: corrected a factually wrong description","rationale":"selftest: description_corrections proof","description_corrections":["/description"]}]}
JSON
tt_body() {  # <out-cell-json> <description>
  python3 - "$1" "$2" <<'EOF'
import json, sys
p, desc = sys.argv[1], sys.argv[2]
d = json.load(open(p))
d["body"]["json"]["description"] = desc
json.dump(d, open(p, "w"), separators=(",", ":"), sort_keys=True)
EOF
}
cp -R "$FIX" "$W/tt-golden"
tt_body "$W/tt-golden/cells/self__a__ok.json" "the OLD wrong text"

# (tt1) the LISTED leaf differs -> ACCEPTED, naming it with BOTH texts.
cp -R "$FIX" "$W/tt1-c"
tt_body "$W/tt1-c/cells/self__a__ok.json" "the corrected text"
bash "${here}/replay.sh" --golden "$W/tt-golden" --candidate "$W/tt1-c" --out "$W/out-tt1" --cells "$W/tt-cells.json" \
  --allow-harness-skew --no-check-golden --accepted "$W/tt-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-tt1.log" 2>&1
rc=$?
row="$(awk -F'\t' '$1=="self|a|ok"{print; exit}' "$W/out-tt1/ledger.tsv")"
status_col="$(cut -f2 <<<"$row")"; title_col="$(cut -f3 <<<"$row")"; diff_col="$(cut -f4 <<<"$row")"
[ "$rc" = 0 ] && [ "$status_col" = PASS ] && [[ "$title_col" == *"ACCEPTED"* ]] && [[ "$title_col" == *"TT-1"* ]] \
  && [[ "$diff_col" == *"corrected /description"* ]] && [[ "$diff_col" == *"the OLD wrong text"* ]] && [[ "$diff_col" == *"the corrected text"* ]] \
  && say PASS "description_corrections accepts a listed leaf that differs, naming it with both texts" \
  || say FAIL "TT-1 listed-leaf correction was not accepted-with-both-texts: rc=$rc status=$status_col title=$title_col diff='$diff_col'"

# (tt2) an UNLISTED leaf differs -> still RED — description_corrections covers exactly the paths it
# names, nothing wider.
cp -R "$FIX" "$W/tt2-c"
tt_body "$W/tt2-c/cells/self__a__ok.json" "the OLD wrong text"
python3 - "$W/tt2-c/cells/self__a__ok.json" <<'EOF'
import json, sys
p = sys.argv[1]; d = json.load(open(p))
d["body"]["json"]["usage"]["in"] = 99
json.dump(d, open(p, "w"), separators=(",", ":"), sort_keys=True)
EOF
bash "${here}/replay.sh" --golden "$W/tt-golden" --candidate "$W/tt2-c" --out "$W/out-tt2" --cells "$W/tt-cells.json" \
  --allow-harness-skew --no-check-golden --accepted "$W/tt-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-tt2.log" 2>&1
rc=$?
row="$(awk -F'\t' '$1=="self|a|ok"{print; exit}' "$W/out-tt2/ledger.tsv")"
status_col="$(cut -f2 <<<"$row")"; diff_col="$(cut -f4 <<<"$row")"
[ "$rc" != 0 ] && [ "$status_col" = FAIL ] && [[ "$diff_col" == *"not a superset at /usage/in"* ]] \
  && say PASS "description_corrections does not cover an unlisted leaf -- still RED" \
  || say FAIL "TT-2 unlisted-leaf divergence was not red: rc=$rc status=$status_col diff='$diff_col'"

# (tt3) a listed pointer that is NOT a string leaf in the golden -> refused at LOAD.
cat >"$W/tt3-accept.json" <<'JSON'
{"accepted":[{"id":"TT-3 bad pointer","kind":"additive","by":"selftest","cells":"^self\\|a\\|ok$","expected_cells":1,"classes":["body"],"changelog":"x","rationale":"y","description_corrections":["/usage"]}]}
JSON
bash "${here}/replay.sh" --golden "$W/tt-golden" --candidate "$W/tt-golden" --out "$W/out-tt3" --cells "$W/tt-cells.json" \
  --allow-harness-skew --no-check-golden --accepted "$W/tt3-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-tt3.log" 2>&1
rc=$?
grep -qi "not a string leaf" "$W/out-tt3.log" && msg_ok=1 || msg_ok=0
[ "$rc" != 0 ] && [ "$msg_ok" = 1 ] \
  && say PASS "description_corrections naming a pointer that is not a string leaf in the golden is refused at load" \
  || say FAIL "TT-3 non-string-leaf pointer was NOT refused: rc=$rc msg_ok=$msg_ok (see $W/out-tt3.log)"

# (o) one mutation per remaining class. Each fragment edits ONLY the candidate's self|a|ok cell, so
# the expected outcome is always: exactly one FAIL, whose class column is exactly the named class.
# A class that shows up alongside another (or not at all) is a differ that cannot name what moved.
one_class_case() {  # <tag> <expected-class> <python fragment over the candidate cell dict `d`>
  local tag="$1" want="$2" frag="$3" rc n cls
  cp -R "$FIX" "$W/m-${tag}"
  python3 -c 'import json,sys
p=sys.argv[1]; d=json.load(open(p))
exec(sys.argv[2])
json.dump(d,open(p,"w"),separators=(",",":"),sort_keys=True)' "$W/m-${tag}/cells/self__a__ok.json" "$frag" || {
    say FAIL "${want}: could not apply the mutation"; return; }
  rc="$(run "$FIX" "$W/m-${tag}" "$W/out-${tag}")"
  n="$(fails_in "$W/out-${tag}")"; cls="$(classes_of 'self|a|ok' "$W/out-${tag}")"
  [ "$rc" != 0 ] && [ "$n" = 1 ] && [ "$cls" = "$want" ] \
    && say PASS "${want} divergence -> exactly one FAIL [${want}]" \
    || say FAIL "${want} divergence rc=$rc fails=$n classes=$cls (expected 1 FAIL [${want}])"
}

one_class_case hdr    headers        'd["headers"]["content-type"]="text/plain"'
one_class_case body   body           'd["body"]["json"]["usage"]["out"]=8'
one_class_case met    effects.metrics 'd["effects"]["metrics"]["busbar_requests_total{outcome=\"ok\"}"]=2'
one_class_case aud    effects.audit  'd["effects"]["audit"]={"added":1,"items":[{"actor":"admin","action":"keys.create","resource":"vk_<KEY>","outcome":"ok","chain_ok":True}]}'
one_class_case err    effects.stderr 'd["effects"]["stderr"]="[error] store error: disk full"'
one_class_case egr    effects.egress 'd["effects"]["egress"]=[{"path":"/v1/messages","method":"POST","headers":{},"body":{}}]'
one_class_case rbk    effects.readback 'd["effects"]["readback"]=[{"path":"/api/v1/admin/hooks/h","status":200,"body":{"json":{"name":"h"}}}]'
one_class_case fls    effects.files  'd["effects"]["files"]=["busbar.wal"]'
one_class_case uar    effects.usage_after_restart 'd["effects"]["usage_after_restart"]={"spend_micros":18,"requests":1}'
one_class_case ste    effects.store_errors 'd["effects"]["store_errors"]=2'
# A SCRIPT CELL'S EVIDENCE IS NOT IN THE NAMED CLASSES, AND IT USED TO BE COMPARED BY NOBODY. The
# script driver writes whatever keys its cell is about straight into `effects` — `survived`,
# `key_after_restart`, `validate_exit`, `hazard_lines`, `usage_before`/`usage_after` — and the differ
# only ever looked at nine hard-coded ones. 14 golden cells carry such a key that no other class
# mirrors (plugins.store-persist|store-sqlite's `survived`/`key_after_restart`, hazard|no-data-dir's
# `hazard_lines`, teller|admit-refusal's `usage_before`/`usage_after`), so a build that lost the
# store across a restart, or leaked a hazard line, diverged on nothing the differ computed and the
# row printed `PASS ... identical`. Every effects key is compared now; this case is red without that.
one_class_case scr    effects.script 'd["effects"]["survived"]="no"'

# (u) A LEDGER ID THAT APPEARS TWICE RESOLVES TO ITS LAST ROW, exactly as verdict.sh resolves one.
# `record` APPENDS, so an id can legitimately carry more than one row — a driver that retried, a
# later step that revised its own verdict. The differ read the FIRST row per id while verdict.sh
# (which adjudicates the very same file) reads the LAST, so the two halves of one gate disagreed
# about what the golden said. The dangerous direction is a golden whose first row is SKIP and whose
# corrected row is PASS: the cell then falls out of the OWED set entirely, is never compared, never
# appears in diverging.txt, and a divergence on it can never be seen at all. Here the corrected row
# says PASS for a cell whose candidate is mutated, so a first-row read leaves 0 FAILs (green) and a
# last-row read leaves exactly the one.
cp -R "$FIX" "$W/dup-golden"
printf 'self|a|ok\tSKIP\tUNSUPPORTED: a stale first attempt\tsuperseded below\n' >"$W/dup-golden/ledger.tsv.new"
cat "$FIX/ledger.tsv" >>"$W/dup-golden/ledger.tsv.new"
mv "$W/dup-golden/ledger.tsv.new" "$W/dup-golden/ledger.tsv"
cp -R "$FIX" "$W/dup-cand"
python3 -c 'import json,sys
p=sys.argv[1]; d=json.load(open(p)); d["status"]=418
json.dump(d,open(p,"w"),separators=(",",":"),sort_keys=True)' "$W/dup-cand/cells/self__a__ok.json"
rc="$(run "$W/dup-golden" "$W/dup-cand" "$W/out-u")"
n="$(fails_in "$W/out-u")"; cls="$(classes_of 'self|a|ok' "$W/out-u")"
[ "$rc" != 0 ] && [ "$n" = 1 ] && [ "$cls" = "status" ] \
  && say PASS "a golden id with a superseded first row -> the LAST row decides (owed, and red)" \
  || say FAIL "duplicate golden ledger row rc=$rc fails=$n classes=$cls (a first-row read makes this cell unowed and invisible)"

# (p) missing.golden: the golden's OWN ledger says PASS but its cell file is not there. That is a
# recorder bug, and it must be red rather than quietly dropping the cell out of the comparison.
cp -R "$FIX" "$W/mg-golden"; cp -R "$FIX" "$W/mg-cand"
rm "$W/mg-golden/cells/self__a__ok.json"
rc="$(run "$W/mg-golden" "$W/mg-cand" "$W/out-p")"
n="$(fails_in "$W/out-p")"; cls="$(classes_of 'self|a|ok' "$W/out-p")"
[ "$rc" != 0 ] && [ "$n" = 1 ] && [ "$cls" = "missing.golden" ] \
  && say PASS "golden ledger PASS with no golden cell file -> exactly one FAIL [missing.golden]" \
  || say FAIL "missing.golden rc=$rc fails=$n classes=$cls"

# (q) the MONEY guard, per class. An `improvement` entry naming a money class must be refused by the
# LOADER, whether or not it ever fires — so this runs against byte-identical recordings. If a class
# is ever dropped from diff-cells.py's MONEY_CLASSES, its case here stops refusing and goes red.
cp -R "$FIX" "$W/money-same"
for mc in status effects.usage effects.usage_after_restart effects.store_errors missing.candidate \
          effects.egress effects.readback effects.files effects.script; do
  python3 -c 'import json,sys
json.dump({"accepted":[{"id":"bad money accept","kind":"improvement","by":"selftest",
  "cells":"^self\\|a\\|ok$","classes":[sys.argv[2]],"rationale":"should be refused"}]},
  open(sys.argv[1],"w"))' "$W/money-accept.json" "$mc"
  bash "${here}/replay.sh" --golden "$FIX" --candidate "$W/money-same" --out "$W/out-q" --cells "$CELLS" \
    --allow-harness-skew --no-check-golden --accepted "$W/money-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-q.log" 2>&1
  rc=$?
  grep -q "not kind=breaking" "$W/out-q.log" && msg_ok=1 || msg_ok=0
  [ "$rc" != 0 ] && [ "$msg_ok" = 1 ] \
    && say PASS "improvement accepting '${mc}' -> loader refuses (money class)" \
    || say FAIL "improvement accepting '${mc}' was NOT refused (rc=$rc msg_ok=$msg_ok) — is ${mc} still in MONEY_CLASSES?"
done

# (s) THE MONEY GUARD MUST TEST WHAT AN ENTRY *DOES*, NOT WHAT IT SAYS. An entry that omits
# `classes` forgives a whole default set, and for kind=breaking that default used to be every class
# there is — while the loader's guard looked only at the (empty) `classes` literal and so found no
# money class to refuse and demanded no changelog line. Four lines of JSON with `cells: "."` then
# forgave a 200 -> 418 and a changed bill on every cell and the oracle reported GREEN.
cp -R "$FIX" "$W/blanket"
python3 -c 'import json,sys
p=sys.argv[1]; d=json.load(open(p)); d["status"]=418; d["effects"]["usage"]["spend_micros"]=999999
json.dump(d,open(p,"w"),separators=(",",":"),sort_keys=True)' "$W/blanket/cells/self__a__ok.json"
cat >"$W/blanket-accept.json" <<'JSON'
{"accepted":[{"id":"classless breaking","kind":"breaking","by":"selftest","cells":"^self\\|a\\|ok$","rationale":"no classes, no changelog"}]}
JSON
bash "${here}/replay.sh" --golden "$FIX" --candidate "$W/blanket" --out "$W/out-s" --cells "$CELLS" \
  --allow-harness-skew --no-check-golden --accepted "$W/blanket-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-s.log" 2>&1
rc=$?
grep -q "not kind=breaking with a changelog" "$W/out-s.log" && msg_ok=1 || msg_ok=0
[ "$rc" != 0 ] && [ "$msg_ok" = 1 ] \
  && say PASS "classless 'breaking' entry -> loader refuses (it would forgive every money class)" \
  || say FAIL "classless 'breaking' entry was NOT refused (rc=$rc msg_ok=$msg_ok) — it forgives status/effects.usage with no changelog line (see $W/out-s.log)"

# (t) `missing.golden` — the golden's ledger saying PASS for a cell it never wrote — is a RECORDER
# bug, and CLASS_WEIGHT's own assertion calls it never acceptable at all. Naming it in the register
# must be refused by the loader rather than waiving a golden that recorded nothing.
cat >"$W/mg-accept.json" <<'JSON'
{"accepted":[{"id":"waive a recorder bug","kind":"improvement","by":"selftest","cells":".","classes":["missing.golden"],"rationale":"should be refused"}]}
JSON
bash "${here}/replay.sh" --golden "$W/mg-golden" --candidate "$W/mg-cand" --out "$W/out-t" --cells "$CELLS" \
  --allow-harness-skew --no-check-golden --accepted "$W/mg-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-t.log" 2>&1
rc=$?
grep -q "recorder bug" "$W/out-t.log" && msg_ok=1 || msg_ok=0
[ "$rc" != 0 ] && [ "$msg_ok" = 1 ] \
  && say PASS "an entry naming 'missing.golden' -> loader refuses (recorder bug, never acceptable)" \
  || say FAIL "'missing.golden' acceptance was NOT refused (rc=$rc msg_ok=$msg_ok) — a golden that recorded nothing can be waived (see $W/out-t.log)"

# (u) --strict on a filtered subset: the differ is a gate on its own when a caller (land.sh) uses it
# that way. A filter that selects the mutated cell must exit 1 …
DC="python3 ${here}/diff-cells.py"
$DC --golden "$FIX" --candidate "$W/mut" --out "$W/out-u" --cells "$CELLS" --accepted "$W/no-accept.json" \
  --allow-harness-skew --strict --id-filter '^self\|a\|ok$' >"$W/out-u.log" 2>&1
rc=$?
owed_u="$(wc -l <"$W/out-u/owed.txt" | tr -d ' ')"
[ "$rc" = 1 ] && [ "$owed_u" = 1 ] && grep -q "unaccepted divergence" "$W/out-u.log" && say PASS "--strict with a filter selecting the mutated cell -> exit 1" || say FAIL "strict filtered rc=$rc owed=$owed_u (see $W/out-u.log)"

# (v) … and a filter that selects NOTHING is red too: a subset gate that compared zero cells proved
# nothing, and must never be mistaken for a clean run.
$DC --golden "$FIX" --candidate "$W/mut" --out "$W/out-v" --cells "$CELLS" --accepted "$W/no-accept.json" \
  --allow-harness-skew --strict --id-filter 'no-such-cell-anywhere' >"$W/out-v.log" 2>&1
rc=$?
grep -q "no owed cells matched" "$W/out-v.log" && msg_ok=1 || msg_ok=0
[ "$rc" = 1 ] && [ "$msg_ok" = 1 ] && say PASS "--strict with a filter matching nothing -> exit 1 (nothing was compared)" || say FAIL "strict empty filter rc=$rc msg_ok=$msg_ok (see $W/out-v.log)"

# (w) AN ENTRY MAY NOT MATCH MORE CELLS THAN IT SAYS IT DOES. The money guard asks WHICH classes an
# entry forgives and never over HOW MANY cells, so a live entry could be widened by gluing one more
# alternation onto its `cells` regex — the reviewable prose (id, rationale, changelog line) stays
# word-for-word what it was and the scope quietly doubles. That is exactly what M-3 did: an entry
# about Cohere `billed_units` carried effects.usage over every `billing|*` cell in the corpus.
# `expected_cells` is the count the author saw; matching more is refused.
cp -R "$FIX" "$W/width"
python3 -c 'import json,sys
json.dump({"accepted":[{"id":"widened waiver","kind":"improvement","by":"selftest",
  "cells":"^self\\|a\\|ok$|^self\\|b\\|","classes":["body"],"expected_cells":1,
  "rationale":"declares one cell, the regex takes two"}]}, open(sys.argv[1],"w"))' "$W/width-accept.json"
bash "${here}/replay.sh" --golden "$FIX" --candidate "$W/width" --out "$W/out-w" --cells "$CELLS" \
  --allow-harness-skew --no-check-golden --accepted "$W/width-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-w.log" 2>&1
rc=$?
grep -q "matches 2 cells" "$W/out-w.log" && msg_ok=1 || msg_ok=0
[ "$rc" != 0 ] && [ "$msg_ok" = 1 ] \
  && say PASS "an entry matching more cells than expected_cells -> loader refuses (silent widening)" \
  || say FAIL "widened entry was NOT refused (rc=$rc msg_ok=$msg_ok) — a waiver can grow without its prose changing (see $W/out-w.log)"

# …and the same entry, declaring the truth, loads and runs. Without this arm the guard could be a
# blanket refusal and case (w) would still pass.
python3 -c 'import json,sys
json.dump({"accepted":[{"id":"honest waiver","kind":"improvement","by":"selftest",
  "cells":"^self\\|a\\|ok$|^self\\|b\\|","classes":["body"],"expected_cells":2,
  "rationale":"declares what it takes"}]}, open(sys.argv[1],"w"))' "$W/width-ok.json"
bash "${here}/replay.sh" --golden "$FIX" --candidate "$W/width" --out "$W/out-w2" --cells "$CELLS" \
  --allow-harness-skew --no-check-golden --accepted "$W/width-ok.json" --baseline "$W/no-baseline.txt" >"$W/out-w2.log" 2>&1
rc=$?
[ "$rc" = 0 ] && [ "$(fails_in "$W/out-w2")" = 0 ] \
  && say PASS "an entry declaring its true width loads (the guard refuses widening, not entries)" \
  || say FAIL "an honest expected_cells was refused (rc=$rc fails=$(fails_in "$W/out-w2")) (see $W/out-w2.log)"

# …and an entry that declares NO width at all is refused: an optional field is one nobody fills in.
python3 -c 'import json,sys
json.dump({"accepted":[{"id":"undeclared width","kind":"improvement","by":"selftest",
  "cells":"^self\\|a\\|ok$","classes":["body"],"rationale":"no expected_cells"}]}, open(sys.argv[1],"w"))' "$W/width-none.json"
bash "${here}/replay.sh" --golden "$FIX" --candidate "$W/width" --out "$W/out-w3" --cells "$CELLS" \
  --allow-harness-skew --no-check-golden --accepted "$W/width-none.json" --baseline "$W/no-baseline.txt" >"$W/out-w3.log" 2>&1
rc=$?
grep -q "declares no \`expected_cells\`" "$W/out-w3.log" && msg_ok=1 || msg_ok=0
[ "$rc" != 0 ] && [ "$msg_ok" = 1 ] \
  && say PASS "an entry with no expected_cells -> loader refuses (the width must be declared)" \
  || say FAIL "an entry with no expected_cells was accepted (rc=$rc msg_ok=$msg_ok) (see $W/out-w3.log)"

# …and the SHIPPED register is loaded against the SHIPPED corpus, so the guard is proven against the
# real file rather than only against fixtures: a widened live entry fails HERE, not in CI.
if python3 "${here}/diff-cells.py" --golden "$FIX" --candidate "$W/width" --out "$W/out-w4" \
     --cells "${data}/cells.json" --accepted "${data}/accepted-differences.json" \
     --allow-harness-skew >"$W/out-w4.log" 2>&1; then
  say PASS "the shipped accepted-differences.json loads against the shipped cells.json"
else
  say FAIL "the shipped accepted-differences.json was REFUSED against the shipped cells.json: $(tail -3 "$W/out-w4.log")"
fi

# NO READINESS WAIT IN THE RECORDER MAY CARRY ITS OWN HARD-CODED CEILING. record.sh documents ONE
# boot bound, ORACLE_BOOT_BOUND_SECS, "so a host slow enough to blow one bound blows both
# consistently instead of the harness inventing a second, independently-drifting hard-code" — and
# then the exec `boot` cell's own /healthz poll counted to a literal 100 at 0.1 s, a private 10-second
# ceiling nothing could raise. That one matters more than the others, because blowing it does not
# read as a failure to record: the cell is captured with rc=124 and a PASS row, so a warning-boot
# whose golden is `exit 0` records `exit 124` on a loaded machine — the harness's stopwatch frozen
# into the cell as if it were the binary's answer. STATIC, over the file, so the next wait somebody
# adds is held to the same rule. `${BOOT_BOUND_RE}` is deliberately loose: any arithmetic naming the
# knob counts.
rec_sh="${here}/record.sh"
bad_wait=""
while IFS= read -r ln; do
  # a `while [ $x -lt <literal> ]` guarding a /healthz poll is the shape being refused
  bad_wait="${bad_wait} ${ln}"
done < <(awk '
  /_max=\$\(\( *[a-zA-Z_]+ *\* *[0-9]+ *\)\)/ { next }
  /while \[ \$[a-z]+ -lt [0-9]+ \]/ { line=NR; buf=$0; getline nxt; getline nxt2;
    if (buf ~ /healthz/ || nxt ~ /healthz/ || nxt2 ~ /healthz/) print line": "buf }
' "$rec_sh")
[ -z "$(printf '%s' "$bad_wait" | tr -d ' ')" ] \
  && say PASS "no /healthz readiness wait in record.sh carries a hard-coded ceiling (all derive from ORACLE_BOOT_BOUND_SECS)" \
  || say FAIL "record.sh has a /healthz readiness wait with a private hard-coded ceiling, so a slow host records its own stopwatch as the cell's exit status:${bad_wait}"

# EVERY `mock_control` A CELL DECLARES MUST RESOLVE TO A VERB THE MOCK IMPLEMENTS. The control file
# is the ONLY way a cell orders an outage, and the mock's fallback for a control it cannot resolve is
# a healthy 200 — so a cell whose control the mock does not understand records the SUCCESS path, with
# a PASS row, on both binaries, agreeing. That is not a hypothetical: `{"stream-error": true}` (six
# cells) and `{"citation": true}` (one) are the shape the corpus actually ships, and the resolver only
# ever looked up the cell's MODEL name or "*" in that object — both miss, so all seven resolved to no
# verb at all. They are `needs_fixture` today, which is the only reason no golden froze the healthy
# stream in place of the mid-stream failure it claims to record. The guard is STATIC and runs over the
# SHIPPED cells.json, so it also catches the next control shape somebody invents.
mock_ctl_bad="$(python3 "${here}/mock-upstream.py" --check-controls "${data}/cells.json" 2>&1)" && mock_ctl_rc=0 || mock_ctl_rc=$?
[ "$mock_ctl_rc" = 0 ] \
  && say PASS "every mock_control in cells.json resolves to a verb mock-upstream.py implements" \
  || say FAIL "cell(s) declare a mock_control the mock resolves to NO verb, so the outage they order is served as a healthy 200: ${mock_ctl_bad}"

# …and the resolver's own unit cases, which no corpus file can prove: the flag form, the per-model
# form, the "*" fallback, and the refusal of a control that names neither a model nor a verb.
mock_unit="$(python3 "${here}/mock-upstream.py" --selftest 2>&1)" && mock_unit_rc=0 || mock_unit_rc=$?
[ "$mock_unit_rc" = 0 ] \
  && say PASS "mock-upstream.py's control-verb resolver selftest is green" \
  || say FAIL "mock-upstream.py --selftest is RED: $(printf '%s' "$mock_unit" | tr '\n' ' ' | tail -c 400)"

# (w) EVERY script driver's give-up path must be distinguishable from a recorded outcome. record.sh
# reads a script cell's `status` alone: -1 is a named gap (SKIP), and EVERY other status is recorded
# PASS unless the capture carries `effects.harness_error` (record.sh:835). So a driver whose fail()
# writes a NON-NEGATIVE status without that marker freezes its own infrastructure failure into the
# golden — "this cell is exit 1" with a PASS row behind it — and the candidate, failing the same way
# for the same reason, matches it exactly. hazard-no-data-dir.sh was the one driver in scripts/ that
# did this (`fail 1 "busbar did not come up"`, `fail 2 "could not mint a key"`). This is a STATIC
# guard, not a mutation case, because it has to hold for the next driver somebody writes too.
# THE CELL DRIVERS ARE THE PRODUCT'S, NOT THE TOOL'S. This read `${here}/scripts` — the oracle's own
# directory — which held them back when the oracle lived inside busbar. Under the shim the tool ships
# no scripts/ at all, so the glob matched nothing, `grep` printed "No such file or directory" for the
# unexpanded pattern, `continue` swallowed it, and both this case and (x) below reported PASS over an
# empty set. A guard on the count now, because a static case that can pass vacuously is worse than
# no case: it reads green while proving that nothing was looked at.
sd="${data}/scripts"
sd_n=0
for f in "$sd"/*.sh; do [ -f "$f" ] && sd_n=$((sd_n + 1)); done
[ "$sd_n" -gt 0 ] \
  || say FAIL "the data directory has no scripts/*.sh, so the two static driver guards below are looking at nothing and would report PASS over an empty set"
# THE TRIGGER WAS A LITERAL DIGIT, AND THE CHECK WAS A WHOLE-FILE GREP. Both let a give-up through:
#   * `fail "$rc" "busbar did not come up"` does not match `fail[[:space:]]+[0-9]`, so the driver was
#     skipped by `continue` and never asked for the marker at all.
#   * a driver whose OWN fail() writes harness_error (most of them) satisfied a file-wide grep no
#     matter what any OTHER give-up path in the same file wrote — e.g. an inline
#     `printf '{"status":2,…,"effects":{}}' >"$RAW/captured.json"; exit 0` on a boot timeout, which
#     record.sh read as "this cell is exit 2" with a PASS row behind it.
# So: the trigger accepts a variable status too, and every WRITE of captured.json that is not the
# cell's own result must be reachable from a give-up that marks itself. That last part cannot be
# decided by grep in general, so what is checked is the shape that is decidable and that covers the
# real hazard: a driver that writes captured.json with a non-negative literal `status` inline (not
# through its fail()/gap() helpers) and no harness_error on that line.
missing_he=""
inline_giveup=""
for f in "$sd"/*.sh; do
  [ -f "$f" ] || continue
  # does this driver ever call fail with a status other than -1? (`fail -1 …` is the named-gap
  # shape). A literal digit, "$var", ${var} or $var all count: the give-up is the same either way.
  if grep -Eq '(^|[^-[:alnum:]_])fail[[:space:]]+([0-9]|"?\$)' "$f"; then
    grep -q 'harness_error' "$f" || missing_he="${missing_he} $(basename "$f")"
  fi
  # an inline write of a non-negative status into captured.json, on a line that does not mark itself
  while IFS= read -r ln; do
    case "$ln" in *harness_error*) continue ;; esac
    inline_giveup="${inline_giveup} $(basename "$f")"
    break
  done < <(grep -nE '"status"[[:space:]]*:[[:space:]]*[0-9]' "$f" | grep -F 'captured.json')
done
[ -z "$missing_he" ] \
  && say PASS "every script driver that fails with a non-negative status — literal or variable — marks it harness_error" \
  || say FAIL "script driver(s) fail with a non-negative status and NO harness_error, so record.sh writes a PASS row over a harness failure:${missing_he}"
[ -z "$inline_giveup" ] \
  && say PASS "no script driver writes a non-negative status into captured.json inline without marking it" \
  || say FAIL "script driver(s) write a captured.json with a literal non-negative status on a line that does not mark harness_error, so a give-up written outside fail() is recorded as the contract:${inline_giveup}"

# (x) A DRIVER MUST NOT PUT A WALL CLOCK IN `effects`. Since diff-cells.py grew `effects.script`,
# EVERY effects key a driver writes is compared, and rated MONEY. A raw `date +%s` (or a store column
# holding one) stepped into effects therefore differs on every single replay by construction — the
# golden was recorded at one instant and the candidate runs at another — and normalize.py cannot save
# it: its TS_KEYS rewrite is an EXACT key-name match on an int/float, and `step` writes strings under
# names of the driver's own choosing (`now_at_edit`, `expires_at_after_edit`). The result is a
# permanent, unforgivable-except-as-`breaking` divergence on a cell about nothing.
clock_in_eff=""
for f in "$sd"/*.sh; do
  [ -f "$f" ] || continue
  grep -Eq '^[[:space:]]*step[[:space:]]+[A-Za-z_][A-Za-z0-9_]*[[:space:]]+"\$\((date|python3 -c .import time)' "$f" \
    && clock_in_eff="${clock_in_eff} $(basename "$f")"
done
[ -z "$clock_in_eff" ] \
  && say PASS "no script driver steps a wall clock straight into effects (money-rated by effects.script)" \
  || say FAIL "script driver(s) step a raw wall clock into effects, which effects.script now compares as MONEY on every replay:${clock_in_eff}"

# (y) THE OWED RATCHET MUST COVER EVERY GOLDEN PASS. replay.sh only prints "new coverage" to stderr
# for an owed id missing from owed-baseline.txt — it is not a red row. So a golden PASS absent from
# the baseline is a cell that can silently stop being owed later with nothing to catch it: exactly
# the regression owed-baseline.txt exists to make impossible. (`http.crosscut|413|gemini-path` was
# such a cell.) Only checked when the real golden is present in the tree.
#
# AND AN EMPTY BASELINE IS THE FINDING, NOT A REASON TO SKIP. This `if` had no `else`: with
# owed-baseline.txt truncated to zero bytes — a bad merge, a botched regeneration, or a deliberate
# `: > owed-baseline.txt` to quiet a red row — replay.sh owes nothing from it, no cell can produce
# an owed-baseline regression row, case (j)'s red arm can never fire in production, and THIS case
# said nothing at all while the file printed GREEN. Every ratchet the floor file provides would be
# gone and both the gate and its own selftest silent about it. The file's own rule, three cases
# above: "a static case that can pass vacuously is worse than no case".
GOLD="${data}/golden/1.5.5"
if [ ! -s "${GOLD}/ledger.tsv" ]; then
  skip "the owed ratchet: ${data}/golden/1.5.5/ledger.tsv is not in this tree (a tool-only run has no golden to measure the baseline against)"
elif [ ! -s "${data}/owed-baseline.txt" ]; then
  say FAIL "the golden owes $(awk -F'\t' '$2=="PASS"{n++} END{print n+0}' "${GOLD}/ledger.tsv") PASS cell(s) and ${data}/owed-baseline.txt is absent or EMPTY — the ratchet that stops the golden quietly ceasing to owe a cell has nothing in it, so no cell can ever produce an owed-baseline regression row"
else
  awk -F'\t' '$2=="PASS"{print $1}' "${GOLD}/ledger.tsv" | LC_ALL=C sort -u >"$W/gold-pass.txt"
  grep -v '^[[:space:]]*$' "${data}/owed-baseline.txt" | LC_ALL=C sort -u >"$W/base.txt"
  unowed="$(comm -23 "$W/gold-pass.txt" "$W/base.txt" | tr '\n' ' ')"
  [ -z "$(printf '%s' "$unowed" | tr -d ' ')" ] \
    && say PASS "every golden PASS id is named in owed-baseline.txt (the ratchet covers all of them)" \
    || say FAIL "golden PASS id(s) missing from owed-baseline.txt, so they can stop being owed with no red row: ${unowed}"
fi

# (z) `compare` IS POLICED LIKE THE REGISTER IS. It is a whitelist with no owner, no kind and no
# changelog line, so it is the widest waiver in the tree and the cheapest to write; the one shipped
# use (`cli|--generate-signing-key`, `compare: ["status"]`) discarded effects.files on the one verb
# that mints a secret — a build writing that key to disk would have been invisible. `compare` may
# now only drop classes outside MONEY_CLASSES, never effects.files/effects.script, and must say why.
mkdir -p "$W/xcells"
compare_case() {  # <name> <json-fragment-for-the-cell> <expected-message> <label>
  python3 - "$CELLS" "$W/xcells/$1.json" "$2" <<'EOF'
import json,sys
d=json.load(open(sys.argv[1]))
patch=json.loads(sys.argv[3])
for c in d["cells"]:
    if c["id"] == "self|a|ok":
        c.pop("why", None); c.update(patch)
json.dump(d, open(sys.argv[2], "w"))
EOF
  bash "${here}/replay.sh" --golden "$FIX" --candidate "$W/same" --out "$W/out-x-$1" --cells "$W/xcells/$1.json" \
    --allow-harness-skew --no-check-golden --accepted "$W/no-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-x-$1.log" 2>&1
  local rc=$?
  grep -q "$3" "$W/out-x-$1.log" && local msg_ok=1 || local msg_ok=0
  [ "$rc" != 0 ] && [ "$msg_ok" = 1 ] \
    && say PASS "$4" \
    || say FAIL "$4 — NOT refused (rc=$rc msg_ok=$msg_ok, see $W/out-x-$1.log)"
}
compare_case money '{"compare":["body"],"why":"selftest"}' 'DROPS' \
  "a 'compare' dropping a money class -> loader refuses"
compare_case files '{"compare":["status","body","headers","effects.stderr","effects.usage","effects.usage_after_restart","effects.store_errors","effects.metrics","effects.audit","norm.rules","effects.egress","effects.readback","effects.script"],"why":"selftest"}' 'effects.files' \
  "a 'compare' dropping effects.files -> loader refuses (a written keyset is invisible elsewhere)"
compare_case nowhy '{"compare":["status","body","effects.stderr","effects.usage","effects.usage_after_restart","effects.store_errors","effects.metrics","effects.audit","norm.rules","effects.egress","effects.readback","effects.files","effects.script"]}' 'carries no .why.' \
  "a 'compare' with no 'why' -> loader refuses"
compare_case typo '{"compare":["stauts"],"why":"selftest"}' 'unknown compare class' \
  "a 'compare' naming a misspelt class -> loader refuses (the typo would drop what it meant to keep)"

# …and the SHIPPED corpus obeys the policy, so a future `compare: [status]` on a real cell is red
# here rather than green forever.
if python3 "${here}/diff-cells.py" --golden "$FIX" --candidate "$W/same" --out "$W/out-x-ship" \
     --cells "${data}/cells.json" --accepted "${data}/accepted-differences.json" \
     --allow-harness-skew >"$W/out-x-ship.log" 2>&1; then
  say PASS "every 'compare' in the shipped cells.json obeys the policy"
else
  say FAIL "a shipped cell's 'compare' was refused: $(tail -3 "$W/out-x-ship.log")"
fi

# (aa) THE `norm.rules` EXEMPTION IS THE ONLY WAY A NORMALIZER RULE ESCAPES BEING COMPARED, so it is
# held to normalize.py rather than trusted to have been copied correctly. diff-cells.ORDER_RULES
# drops a rule name out of the one-sided-firing check; the ONLY rules that may be in it are pure
# RE-SORTS, which change no content. Two ways it can be wrong, and this case catches both:
#   * a re-sort rule normalize.py emits is MISSING from the set -> that rule counts as one-sided and
#     the differ reports a divergence about nothing (this had already happened: normalize.py grew a
#     third sort_runs call, `boot.exhaustion-order`, and the set was never told);
#   * a rule that DROPS or BLANKS content is ADDED to the set -> its one-sided firing goes
#     invisible, which is how content leaves a cell with no class saying so. diff-cells.py asserts
#     that direction at import (ORDER_RULES vs CONTENT_RULES); this case proves the assert bites.
# normalize.py's re-sort rules are read out of the source: the three `sort_runs(...)` calls plus the
# two in-place sorts (`boot.pair-order`, `keys.order`), so a fourth one added tomorrow is red here
# the day it lands rather than the day someone remembers this set exists.
n_sort="$(grep -oE 'sort_runs\([^,]+, [^,]+, "[^"]+"' "${here}/normalize.py" | sed 's/.*"\(.*\)"/\1/' | LC_ALL=C sort -u)"
n_inplace="$(grep -oE 'applied\.add\("(boot\.pair-order|keys\.order)"\)' "${here}/normalize.py" | sed 's/.*"\(.*\)".*/\1/' | LC_ALL=C sort -u)"
printf '%s\n%s\n' "$n_sort" "$n_inplace" | grep -v '^$' | LC_ALL=C sort -u >"$W/resort-rules.txt"
python3 - "$W/order-rules.txt" <<PY
import sys
sys.path.insert(0, "${here}")
import importlib.util
spec = importlib.util.spec_from_file_location("dc", "${here}/diff-cells.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
open(sys.argv[1], "w").write("\n".join(sorted(m.ORDER_RULES)) + "\n")
PY
if diff -u "$W/resort-rules.txt" "$W/order-rules.txt" >"$W/order-rules.diff" 2>&1; then
  say PASS "diff-cells' norm.rules exemption is exactly normalize.py's re-sort rules ($(wc -l <"$W/resort-rules.txt" | tr -d ' ') of them)"
else
  say FAIL "diff-cells.ORDER_RULES has drifted from normalize.py's re-sort rules (- normalize.py, + diff-cells): $(tail -n +4 "$W/order-rules.diff" | tr '\n' ' ')"
fi
# …and a CONTENT rule may never be smuggled into the exemption: the assert must fire.
if python3 - <<PY >"$W/order-assert.log" 2>&1
import importlib.util, sys
spec = importlib.util.spec_from_file_location("dc", "${here}/diff-cells.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
# the guard is the disjointness the module asserts at import; prove it is not vacuous
assert m.CONTENT_RULES, "CONTENT_RULES is empty, so the disjointness assert forbids nothing"
bad = m.ORDER_RULES | {"metrics.timing"}
if bad & m.CONTENT_RULES:
    sys.exit(0)
sys.exit("metrics.timing is not in CONTENT_RULES, so adding it to the exemption would be allowed")
PY
then
  say PASS "a content-dropping rule cannot be exempted from norm.rules (the disjointness guard is not vacuous)"
else
  say FAIL "the ORDER_RULES/CONTENT_RULES guard does not forbid exempting a content-dropping rule: $(tail -2 "$W/order-assert.log")"
fi

# (ab) THE HARNESS REVISION IS A PINNED JUDGE PLUS THE PRODUCT'S OWN EVIDENCE — AND NOTHING ELSE.
#
# These cases used to assert the opposite, and were right to, under a layout that no longer exists.
# When the oracle lived inside busbar, the files that decide a recording and the files that decide a
# verdict sat in one directory, so hashing the directory hashed both. The rule was "the rev covers
# every file that decides a recording OR a verdict", and the proof was: edit replay.sh, edit
# renormalize.sh, edit fleet-fixtures/lib.sh — the rev must move.
#
# The oracle is a separate product now. The judge ships on its own release cadence and the product
# PINS it: testing/shadow-oracle/oracle.pin names a tag and the sha256 of that tag's archive, the
# product's shim refuses a tool whose bytes do not hash to the pin, and harness-rev.sh folds the pin
# into the revision instead of hashing the judge's files. So the model the old cases proved is not
# merely stale — asserting it now would be asserting a bug:
#
#   * The judge's files are NOT in the set as bytes. If they were, a tool upgrade would move the rev
#     twice by two different routes, and — worse — a judge running from a tree that does not match
#     the product's committed pin would still produce a rev that looked settled. One statement of
#     which judge ran, or none.
#   * The pin IS in the set, as a file with a name (oracle.pin) and as the digest of the tool that
#     actually ran ($BUSBAR_ORACLE_TOOL_DIGEST). A pin bump is therefore a data change: visible to
#     `--files`, to a `git diff`, and to the hash.
#   * The product's DATA is still in the set exactly as before. accepted-differences.json and
#     owed-baseline.txt are neither executed while recording nor shipped by the tool, and both change
#     the verdict on identical bytes; that argument never depended on where the judge lived.
#
# What follows proves the new model in four parts: the data is covered by name, the judge is not,
# the pin moves the rev, and editing the judge does not.
#
# rigs-baseline.json is deliberately NOT required here: it is the sign-off floor of the SEPARATE
# plane-rigs gate (rigs-ledger.sh), which has its own verdict and never touches an LLM-plane
# recording or this replay.
hr_dp="$(cd "${data}/.." && pwd)"
hr_pin="v0.0.0-selftest@0000000000000000000000000000000000000000000000000000000000000000"
hr_files_of() {  # hr_files_of <data-dir> <product-root> -> the set, repo-relative, under a pin
  BUSBAR_ORACLE_DATA="$1" BUSBAR_ORACLE_PRODUCT_ROOT="$2" BUSBAR_ORACLE_TOOL_DIGEST="${3:-$hr_pin}" \
    bash "${4:-$here}/harness-rev.sh" --files
}
hr_rev_of() {  # hr_rev_of <data-dir> <product-root> [pin] [tool-dir] -> the revision
  BUSBAR_ORACLE_DATA="$1" BUSBAR_ORACLE_PRODUCT_ROOT="$2" BUSBAR_ORACLE_TOOL_DIGEST="${3:-$hr_pin}" \
    bash "${4:-$here}/harness-rev.sh" | awk '{print $2}'
}
# The cell drivers under scripts/ are data and are in the set; they are excluded from the name scans
# below only so that a driver called replay.sh could never be mistaken for the judge's replay.sh.
hr_top="$(hr_files_of "$data" "$hr_dp" | grep -v '/scripts/' | while IFS= read -r f; do basename "$f"; done)"

# (ab-1) THE PRODUCT'S EVIDENCE IS COVERED BY NAME. Checked against `harness-rev.sh --files` rather
# than against the hash, so this is red the day a file is dropped from the list rather than the day
# a verdict is doubted. oracle.pin is in this list for the reason the other six are: it decides the
# verdict on identical bytes, by deciding who reads them.
hr_missing=""
for want in cells.json accepted-differences.json accepted-gaps.json owed-baseline.txt \
            golden-digests.tsv plugin-digests.tsv oracle.pin; do
  printf '%s\n' "$hr_top" | grep -Fqx "$want" || hr_missing="${hr_missing} ${want}"
done
[ -z "$hr_missing" ] \
  && say PASS "the harness revision covers the register, the floor, the gap list, the digest pins and the tool pin" \
  || say FAIL "file(s) that decide a verdict are OUTSIDE the harness revision, so they can change while the rev sits still and the skew guard stays quiet:${hr_missing}"

# (ab-2) …AND THE JUDGE IS NOT IN IT AS BYTES. The pin stands in for every one of these. A file
# here would mean the product hashes its judge twice — once as the tool it pinned and once as the
# tree it happened to be running from — and those two can disagree.
hr_intruder=""
for nope in replay.sh harness-rev.sh renormalize.sh record.sh oracle-config.sh \
            verdict.sh lib.sh normalize.py diff-cells.py merge-recordings.py; do
  printf '%s\n' "$hr_top" | grep -Fqx "$nope" && hr_intruder="${hr_intruder} ${nope}"
done
[ -z "$hr_intruder" ] \
  && say PASS "the judge's own files are outside the harness revision — the pin is how the tool enters it" \
  || say FAIL "the judge is hashed as BYTES as well as pinned as a DIGEST, so a tool running from a tree that does not match the product's pin still stamps a settled-looking revision:${hr_intruder}"

# (ab-3) THE PIN MOVES THE REVISION. This is the whole of the tool half now: if a different pinned
# judge produced the same rev, the skew guard would happily compare a recording made by v0.1.0
# against one made by v0.9.0 and find nothing to say.
hr_p1="$(hr_rev_of "$data" "$hr_dp" "v0.1.0@1111111111111111111111111111111111111111111111111111111111111111")"
hr_p2="$(hr_rev_of "$data" "$hr_dp" "v0.2.0@2222222222222222222222222222222222222222222222222222222222222222")"
hr_p3="$(hr_rev_of "$data" "$hr_dp" "v0.1.0@1111111111111111111111111111111111111111111111111111111111111111")"
if [ -n "$hr_p1" ] && [ "$hr_p1" != "$hr_p2" ] && [ "$hr_p1" = "$hr_p3" ]; then
  say PASS "a different pinned judge is a different harness revision (and the same pin is the same one)"
else
  say FAIL "the pinned tool digest does not move the harness revision (v0.1.0=${hr_p1:0:12} v0.2.0=${hr_p2:0:12} again=${hr_p3:0:12}) — two judges would stamp one revision"
fi

# (ab-4) …AND EDITING THE JUDGE DOES NOT. The inverse of the old case, and the reason it is safe to
# state it: the pin is a digest of the tool's ARCHIVE, so any edit to any of these files is already
# a different tool that the product's shim would refuse to run under the old pin. Hashing them here
# as well would be a second, weaker copy of that fact.
#
# Mutated over a COPY of the tool, never the installed tree. The case this replaces appended to
# ${here}/replay.sh in place and restored it from a backup with no trap — an interrupted run left
# the installation corrupt, and a read-only install (system site-packages, a container layer, a Nix
# store path) failed outright.
hrtool="$W/hrtool"
cp -R "${here}" "$hrtool"
hr_t0="$(hr_rev_of "$data" "$hr_dp" "$hr_pin" "$hrtool")"
hr_tool_moved=""
hr_tool_seen=0
for tf in replay.sh harness-rev.sh renormalize.sh record.sh fleet_fixtures/verdict.sh fleet_fixtures/lib.sh; do
  [ -f "$hrtool/$tf" ] || continue
  hr_tool_seen=$((hr_tool_seen + 1))
  printf '\n# harness-rev selftest probe\n' >>"$hrtool/$tf"
  [ "$(hr_rev_of "$data" "$hr_dp" "$hr_pin" "$hrtool")" = "$hr_t0" ] || hr_tool_moved="${hr_tool_moved} ${tf}"
  cp "${here}/$tf" "$hrtool/$tf"
done
if [ -z "$hr_t0" ] || [ "$hr_tool_seen" -lt 5 ]; then
  say FAIL "the judge-edit case found only ${hr_tool_seen} of the tool's own files to edit (rev=${hr_t0:0:12}) — it is proving nothing"
elif [ -z "$hr_tool_moved" ]; then
  say PASS "editing the judge's own files does not move the harness revision — the pin does (${hr_tool_seen} files)"
else
  say FAIL "editing the judge moved the harness revision by bytes as well as by pin, so one tool upgrade moves it twice and a tree that disagrees with the pin is still stamped:${hr_tool_moved}"
fi

# (ab-5) THE REVISION FOLLOWS THE DATA'S NAMES AND BYTES, NOT ITS LOCATION ON DISK. This is the case
# that licenses every mutation case below it, and its absence is exactly why two of them were red.
#
# The old mutation cases copied ${here} — the ORACLE'S OWN DIRECTORY — and edited
# accepted-differences.json and owed-baseline.txt inside the copy, because under the in-tree layout
# the judge and the evidence shared a directory and copying one copied the other. Under the shim
# they do not: ${here} is the installed tool, the data is the product's testing/shadow-oracle, and
# the `cp` of a data file out of the tool's directory failed with "No such file or directory". The
# mutation then landed on a file that did not exist, the rev was computed over a data set that was
# never there, and the case reported that the harness revision had not moved.
#
# The fix is to copy the DATA and re-root the tool at it — which is only possible because the
# product's shim exports BUSBAR_ORACLE_DATA and BUSBAR_ORACLE_PRODUCT_ROOT as DEFAULTS (`${VAR:-…}`)
# rather than overrides. A shim that forced every run at busbar's real directory would leave this
# suite unable to mutate anything it judges, and the harness-rev half of it could not exist.
#
# So: the same bytes under the same repo-relative names, at a different absolute path, must be the
# same revision. If they are not, a mutation over a throwaway copy is measuring $TMPDIR.
hr_alt="$W/alt/$(basename "$data")"
mkdir -p "$hr_alt/scripts" "$hr_alt/fixtures"
for f in cells.json golden-digests.tsv plugin-digests.tsv accepted-differences.json \
         accepted-gaps.json owed-baseline.txt oracle.pin; do
  [ -f "$data/$f" ] && cp "$data/$f" "$hr_alt/$f"
done
[ -d "$data/scripts" ] && cp -R "$data/scripts/." "$hr_alt/scripts/"
for f in "$data"/fixtures/*.json; do [ -f "$f" ] && cp "$f" "$hr_alt/fixtures/"; done
hr_here_rev="$(hr_rev_of "$data" "$hr_dp")"
hr_alt_rev="$(hr_rev_of "$hr_alt" "$W/alt")"
if [ -n "$hr_here_rev" ] && [ "$hr_here_rev" = "$hr_alt_rev" ]; then
  say PASS "a byte-identical copy of the data at another path has the same harness revision (so a mutation over a copy proves the real thing)"
else
  say FAIL "the harness revision depends on where the data sits, not only on its names and bytes (real=${hr_here_rev:0:12} copy=${hr_alt_rev:0:12}) — every mutation case below is measuring the temp directory"
fi

# (ac) renormalize.sh IS FAITHFUL TO THE RECORDER'S CALL SITE, OR IT REFUSES. It rewrites a
# recording's normalized cells IN PLACE — over the tracked golden — and its own header claims the
# cell's `keep`/`body_lines` spec is "passed exactly as record.sh passes it". record.sh has FOUR
# normalize.py invocations, not one, and they differ: an http cell gets --key-id and both keep
# flags, an exec cell gets the keep flags and no --key-id, a concurrent cell gets --key-id alone,
# and a script cell gets nothing at all. Passing all three uniformly means a `keep` on a script cell
# is applied here and never was by the recorder — the golden silently becomes a cell no recording
# ever produced, and the harness_rev re-stamp makes the rewrite look like an honest re-derivation.
# No script or concurrent cell declares a spec TODAY, so that was faithful by luck; this case is the
# rule. It drives the REAL renormalize.sh against a fixture corpus that gives a script cell a
# `keep`, and requires a refusal that names the cell.
rn="$W/renorm"; mkdir -p "$rn/raw/self__s__script" "$rn/cells"
printf '{"status":0,"headers":{},"body":"{\\"ok\\":true}","effects":{"survived":"yes"}}' \
  >"$rn/raw/self__s__script/captured.json"
printf '{"status":0}' >"$rn/cells/self__s__script.json"
printf '{"harness_rev":"x","version":"busbar 1.5.5"}' >"$rn/meta.json"
cat >"$W/renorm-cells.json" <<'JSON'
{"cells":[{"id":"self|s|script","driver":"script","keep":{"headers":["date"]}}]}
JSON
cp -R "$rn" "$rn-before"
if bash "${here}/renormalize.sh" --cells "$W/renorm-cells.json" "$rn" >"$W/renorm.log" 2>&1; then
  say FAIL "renormalize.sh applied a script cell's keep spec that record.sh never passes, and exited 0"
else
  # The refusal must NAME the recorder call site whose behaviour it is reproducing. Asserted on the
  # call site's NAME, not on a line number: this case used to demand the literal `record.sh:825`, so
  # every edit to record.sh above that line made the citation wrong and CORRECTING it turned this
  # case red for the wrong reason.
  if grep -q 'self|s|script' "$W/renorm.log" && grep -q "script call site" "$W/renorm.log" \
     && cmp -s "$rn-before/cells/self__s__script.json" "$rn/cells/self__s__script.json"; then
    say PASS "renormalize.sh refuses a cell whose spec record.sh's own call site would not have passed, and leaves the cell untouched"
  else
    say FAIL "renormalize.sh refused but did not name the cell/call site, or rewrote the cell anyway: $(tail -2 "$W/renorm.log")"
  fi
fi
# …and the faithful case still re-normalizes: the refusal must not be a blanket.
rn2="$W/renorm-ok"; mkdir -p "$rn2/raw/self__h__http" "$rn2/cells"
printf '{"status":200,"headers":{"Date":"x"},"body":"{\\"ok\\":true}","effects":{}}' \
  >"$rn2/raw/self__h__http/captured.json"
printf '{"status":200}' >"$rn2/cells/self__h__http.json"
printf '{"harness_rev":"x","version":"busbar 1.5.5"}' >"$rn2/meta.json"
printf '{"cells":[{"id":"self|h|http","driver":"http"}]}' >"$W/renorm-cells-ok.json"
if bash "${here}/renormalize.sh" --cells "$W/renorm-cells-ok.json" "$rn2" >"$W/renorm-ok.log" 2>&1 \
   && grep -q '"status":200' "$rn2/cells/self__h__http.json"; then
  say PASS "renormalize.sh still re-normalizes a cell whose driver matches its spec (the refusal is not a blanket)"
else
  say FAIL "renormalize.sh refused a faithful http cell: $(tail -2 "$W/renorm-ok.log")"
fi

# (y) A DATA-FILE CHANGE STILL MOVES THE HARNESS REVISION. This is the half of the old model that
# survives the extraction unchanged, and it is the half that matters most day to day: the judge is
# upgraded a few times a year, but the register, the floor and the corpus move every week.
#
# harness_rev is the ONE fact that lets diff-cells refuse a golden and a candidate made under
# different rules. accepted-differences.json (the register of divergences the differ FORGIVES) and
# owed-baseline.txt (the floor of ids the golden must not stop owing) are the two clearest cases:
# neither is executed while recording, both change the verdict on identical bytes, and each could
# change while every recording on disk went on claiming the same revision. cells.json decides what
# is recorded at all; the two digest pins decide which binary and which plugins produced it;
# accepted-gaps.json forgives an owed regression; oracle.pin decides who reads the result.
#
# Proven by MUTATION over the DATA copy built above — whose fidelity (ab-5) has already established.
# The old version of this loop copied the ORACLE'S directory and mutated inside it, which is why the
# accepted-differences.json and owed-baseline.txt arms were red: under the shim those files are not
# in the oracle's directory at all, so the `cp` failed, the mutation landed nowhere, and the case
# reported a rev that had — correctly, given a data set that was never assembled — not moved.
hr_of() { hr_rev_of "$hr_alt" "$W/alt"; }
hr0="$(hr_of)"
[ -n "$hr0" ] || say FAIL "harness-rev.sh printed no revision over the copied data"
for hf in accepted-differences.json owed-baseline.txt cells.json accepted-gaps.json \
          golden-digests.tsv plugin-digests.tsv oracle.pin; do
  if [ ! -f "$hr_alt/$hf" ]; then
    say FAIL "harness_rev cannot be proven to move when ${hf} changes — the data directory has no ${hf}, so the file that decides a verdict is not there to be hashed"
    continue
  fi
  printf '\n# harness-rev selftest mutation\n' >>"$hr_alt/$hf"
  hrN="$(hr_of)"
  if [ -n "$hrN" ] && [ "$hrN" != "$hr0" ]; then
    say PASS "harness_rev moves when ${hf} changes"
  else
    say FAIL "harness_rev did NOT move when ${hf} changed — a recording made under a different ${hf} claims the same revision, and the skew guard has nothing to see"
  fi
  # put it back, so each file is proven on its own and the loop compares against the same hr0
  cp "$data/$hf" "$hr_alt/$hf"
  [ "$(hr_of)" = "$hr0" ] || say FAIL "restoring ${hf} did not restore the revision (the selftest's own fixture drifted)"
done
# …and a file APPEARING or DISAPPEARING moves it too. Concatenated contents alone cannot see an
# empty new fixture or a deleted driver, and both change what gets recorded — which is why the set
# is hashed with its names, not only its bytes.
if [ -f "$hr_alt/owed-baseline.txt" ]; then
  mv "$hr_alt/owed-baseline.txt" "$W/owed-baseline.gone"
  hr_gone="$(hr_of)"
  mv "$W/owed-baseline.gone" "$hr_alt/owed-baseline.txt"
  if [ -n "$hr_gone" ] && [ "$hr_gone" != "$hr0" ] && [ "$(hr_of)" = "$hr0" ]; then
    say PASS "harness_rev moves when a file leaves the set entirely (names are hashed, not only bytes)"
  else
    say FAIL "deleting owed-baseline.txt did not move the harness revision (with=${hr0:0:12} without=${hr_gone:0:12}) — a dropped file is invisible to the skew guard"
  fi
fi

# (z) A RE-NORMALIZED RECORDING SAYS SO. renormalize.sh rewrites the normalized cells of a recording
# that is already on disk, and it is itself in the harness-rev set — so after it runs, the recording
# holds cells THIS harness wrote while meta.json still named the harness that recorded them. The skew
# guard then compares two stale stamps, finds them equal, and permits a comparison across a
# normalizer change. The rewrite has to move the stamp.
# ITS OWN DIRECTORY. This case used `$W/renorm`, which the script-cell refusal case above has
# already filled with a raw/self__s__script/ tree — and renormalize.sh iterates `raw/*/`, so this
# case re-normalized that cell too, under a different --cells, and its verdict was a function of a
# fixture belonging to another case.
rn="$W/renorm-restamp"; mkdir -p "$rn/cells" "$rn/raw/cli__--version"
printf '%s\n' '{"status":0,"headers":{},"body":"busbar 1.5.5\\n","effects":{"stderr":""}}' >"$rn/raw/cli__--version/captured.json"
printf '%s\n' '{"binary":"fixture","version":"busbar fixture","recorded":1,"harness_rev":"deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"}' >"$rn/meta.json"
if bash "${here}/renormalize.sh" "$rn" >"$W/renorm.log" 2>&1; then
  now="$(bash "${here}/harness-rev.sh" | awk '{print $2}')"
  got="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("harness_rev",""))' "$rn/meta.json")"
  hist="$(python3 -c 'import json,sys; print(",".join(json.load(open(sys.argv[1])).get("harness_rev_history",[])))' "$rn/meta.json")"
  if [ "$got" = "$now" ]; then
    say PASS "renormalize.sh re-stamps harness_rev after rewriting a recording's cells"
  else
    say FAIL "renormalize.sh rewrote cells but left harness_rev at '${got:0:12}' (this harness is ${now:0:12}) — the recording claims a harness that did not write its cells"
  fi
  case "$hist" in *deadbeefdeadbeef*) say PASS "the superseded harness_rev is kept in harness_rev_history" ;;
    *) say FAIL "renormalize.sh dropped the superseded harness_rev instead of recording it (history: ${hist:-<empty>})" ;;
  esac
  # AND THE FIELD THE SKEW GUARD READS IS WRITTEN. `harness_rev_recorded` sat in the shipped golden
  # while no file in this tool wrote it and no file read it — a claim nothing could check. It is the
  # rev the cells were RECORDED under, which a re-stamp is precisely what destroys, and diff-cells.py
  # now folds it into a recording's provenance.
  hrec="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("harness_rev_recorded",""))' "$rn/meta.json")"
  [ "$hrec" = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef" ] \
    && say PASS "renormalize.sh records the rev the cells were RECORDED under, which the skew guard reads" \
    || say FAIL "renormalize.sh left harness_rev_recorded='${hrec:-<absent>}' — the field the provenance guard reads is written by nobody"
else
  say FAIL "renormalize.sh failed on a minimal recording: $(tail -3 "$W/renorm.log")"
fi

# (aa) A GOLDEN THAT DOES NOT SAY WHICH BINARY MADE IT IS NOT COMPARED. The binary-provenance check
# extracted `binary_sha256` with `python3 -c … 2>/dev/null || true` and then ran only `if [ -n
# "$gbin" ]`, so EVERY failure of the field — absent, null, a number, a truncated digest, a
# meta.json that did not parse at all — collapsed to the empty string and took the same branch as
# the one legitimate case (a fixture recording that names no binary). The check that proves the
# golden came from the pinned 1.5.5 release was skipped by anything that broke it. Each shape is
# driven separately, because they failed for different reasons and must now each be named.
prov_case() {  # <name> <meta.json bytes> <expected-message-fragment> <label>
  local pg="$W/prov-$1"
  rm -rf "$pg"; cp -R "$FIX" "$pg"
  printf '%s' "$2" >"$pg/meta.json"
  bash "${here}/replay.sh" --golden "$pg" --candidate "$W/same" --out "$W/out-prov-$1" --cells "$CELLS" \
    --allow-harness-skew --accepted "$W/no-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-prov-$1.log" 2>&1
  local rc=$?
  grep -q "$3" "$W/out-prov-$1.log" && local msg_ok=1 || local msg_ok=0
  [ "$rc" = 2 ] && [ "$msg_ok" = 1 ] \
    && say PASS "$4" \
    || say FAIL "$4 — NOT refused (rc=$rc msg_ok=$msg_ok, see $W/out-prov-$1.log)"
}
prov_case absent   '{"binary":"fixture","version":"busbar 1.5.5"}' 'carries no .binary_sha256.' \
  "a golden meta.json with no binary_sha256 -> refuses to compare"
prov_case null     '{"binary":"fixture","version":"busbar 1.5.5","binary_sha256":null}' 'carries no .binary_sha256.' \
  "a golden meta.json with a null binary_sha256 -> refuses to compare"
prov_case short    '{"binary":"fixture","version":"busbar 1.5.5","binary_sha256":"48e2800c"}' 'malformed .binary_sha256.' \
  "a golden meta.json with a truncated binary_sha256 -> refuses to compare"
prov_case notjson  '{"binary": "fixture", "version":' 'not readable JSON' \
  "a golden meta.json that does not parse -> refuses to compare (it used to be swallowed)"
prov_case noversion '{"binary":"fixture","binary_sha256":"48e2800cc1fbf229104d73c23039ba4d4a703c0a8db9e7872e22fec33f9b1e48"}' 'carries no .version.' \
  "a golden meta.json with no version -> refuses to compare (nothing to check the digest against)"

# …and --no-check-golden is still the way to say "I know this pair has no released binary": the
# refusal must be escapable DELIBERATELY, or every fixture-based case above would be unreachable.
rm -rf "$W/prov-esc"; cp -R "$FIX" "$W/prov-esc"
printf '%s' '{"binary":"fixture","version":"busbar 1.5.5"}' >"$W/prov-esc/meta.json"
bash "${here}/replay.sh" --golden "$W/prov-esc" --candidate "$W/same" --out "$W/out-prov-esc" --cells "$CELLS" \
  --allow-harness-skew --no-check-golden --accepted "$W/no-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-prov-esc.log" 2>&1
rc=$?
[ "$rc" = 0 ] && say PASS "--no-check-golden still skips the provenance check, explicitly" \
  || say FAIL "--no-check-golden did not skip the provenance check (rc=$rc, see $W/out-prov-esc.log)"

# (bb) A TRANSFORM MUST HAVE A SCOPE, AND FIRING IS NOT ACCEPTING. `cells` defaulted to "." for every
# entry, and the two shipped transforms (D-1, D-2) carried none — so their rewrites ran over all 2299
# cells. That was called harmless because a transform is line-precise, but the match is not the
# acceptance: once ANY of a cell's text was rewritten, the branch below handed the cell's WHOLE raw
# class list to the accepted column without ever testing it against the entry's `allowed` set. A cell
# where D-1's `[error] BUSBAR-NNNN: ` rewrite fired and whose STATUS had also moved reported
# `PASS ACCEPTED improvement (D-1 …): status` — a money class, forgiven by an `improvement`.
cp -R "$FIX" "$W/tscope"
python3 - "$W/tscope/cells/self__b__stream.json" <<'EOF'
import json,sys
p=sys.argv[1]; d=json.load(open(p))
d["body"]["text"]=d["body"]["text"].replace("hi", "hi TOKEN123")
json.dump(d,open(p,"w"),separators=(",",":"),sort_keys=True)
EOF
# T-2 fires (its rewrite erases the body divergence) but claims only `headers`. `body` is outside
# its allowed set, so the cell stays RED — the rewrite is not a licence over the cell.
cat >"$W/tscope-accept.json" <<'JSON'
{"accepted":[{"id":"T-2 fires and overreaches","kind":"improvement","by":"selftest","cells":"^self\\|b\\|stream$","expected_cells":1,"classes":["headers"],"rationale":"fires on the body, but only claims headers","transform":{"candidate":[[" TOKEN123",""]]}}]}
JSON
bash "${here}/replay.sh" --golden "$FIX" --candidate "$W/tscope" --out "$W/out-bb" --cells "$CELLS" \
  --allow-harness-skew --no-check-golden --accepted "$W/tscope-accept.json" --baseline "$W/no-baseline.txt" >"$W/out-bb.log" 2>&1
rc=$?
row="$(awk -F'\t' '$1=="self|b|stream"{print; exit}' "$W/out-bb/ledger.tsv")"
[ "$rc" != 0 ] && [ "$(cut -f2 <<<"$row")" = FAIL ] \
  && say PASS "a fired transform does not forgive a class outside its 'allowed' set" \
  || say FAIL "a transform that merely FIRED carried a class it does not claim into the accepted column (rc=$rc row=$row)"

# …and a transform that DOES claim the class it erased still accepts, or the guard above would just
# be a blanket refusal of every transform.
cat >"$W/tscope-ok.json" <<'JSON'
{"accepted":[{"id":"T-2b claims what it erases","kind":"improvement","by":"selftest","cells":"^self\\|b\\|stream$","expected_cells":1,"classes":["body"],"rationale":"claims the class its rewrite erases","transform":{"candidate":[[" TOKEN123",""]]}}]}
JSON
bash "${here}/replay.sh" --golden "$FIX" --candidate "$W/tscope" --out "$W/out-bb1b" --cells "$CELLS" \
  --allow-harness-skew --no-check-golden --accepted "$W/tscope-ok.json" --baseline "$W/no-baseline.txt" >"$W/out-bb1b.log" 2>&1
rc=$?
row="$(awk -F'\t' '$1=="self|b|stream"{print; exit}' "$W/out-bb1b/ledger.tsv")"
[ "$rc" = 0 ] && [[ "$(cut -f3 <<<"$row")" == ACCEPTED* ]] \
  && say PASS "a transform that claims the class it erases still reports ACCEPTED" \
  || say FAIL "a correctly-scoped transform was refused (rc=$rc row=$row)"

# …and a transform with no `cells` at all is refused: an unscoped rewrite is the whole corpus.
cat >"$W/tnoscope.json" <<'JSON'
{"accepted":[{"id":"T-3 unscoped","kind":"improvement","by":"selftest","rationale":"no cells","transform":{"candidate":[[" TOKEN123",""]]}}]}
JSON
bash "${here}/replay.sh" --golden "$FIX" --candidate "$W/tscope" --out "$W/out-bb2" --cells "$CELLS" \
  --allow-harness-skew --no-check-golden --accepted "$W/tnoscope.json" --baseline "$W/no-baseline.txt" >"$W/out-bb2.log" 2>&1
rc=$?
grep -q "declares no \`cells\`" "$W/out-bb2.log" && msg_ok=1 || msg_ok=0
[ "$rc" != 0 ] && [ "$msg_ok" = 1 ] \
  && say PASS "a transform entry with no 'cells' -> loader refuses (an unscoped rewrite is the whole corpus)" \
  || say FAIL "an unscoped transform was accepted (rc=$rc msg_ok=$msg_ok, see $W/out-bb2.log)"

# …and a transform's `cells` is held to its declared width, exactly like every other entry's.
cat >"$W/twide.json" <<'JSON'
{"accepted":[{"id":"T-4 widened","kind":"improvement","by":"selftest","cells":"^self\\|","expected_cells":1,"rationale":"declares one, takes three","transform":{"candidate":[[" TOKEN123",""]]}}]}
JSON
bash "${here}/replay.sh" --golden "$FIX" --candidate "$W/tscope" --out "$W/out-bb3" --cells "$CELLS" \
  --allow-harness-skew --no-check-golden --accepted "$W/twide.json" --baseline "$W/no-baseline.txt" >"$W/out-bb3.log" 2>&1
rc=$?
grep -q "expected_cells=1" "$W/out-bb3.log" && msg_ok=1 || msg_ok=0
[ "$rc" != 0 ] && [ "$msg_ok" = 1 ] \
  && say PASS "a transform entry is held to its expected_cells too" \
  || say FAIL "a widened transform was accepted (rc=$rc msg_ok=$msg_ok, see $W/out-bb3.log)"
# (cc) normalize.py's `eventstream.frames` rule, on REAL `application/vnd.amazon.eventstream` bytes.
# The frames below are built by a real encoder — big-endian prelude, a header block in AWS's own
# name/type/length encoding, and both CRC32s computed over the actual bytes — so this exercises the
# decoder against the wire format, not against a mock of itself. Three things are proved:
#   1. a per-run MEASUREMENT inside a frame is normalized away: two streams identical but for the
#      metadata frame's `metrics.latencyMs` (0 vs 2) are BYTE-DIFFERENT before and EQUAL after.
#      Before this rule the body was `.decode("utf-8","replace")`d, so the literal latency sat in
#      the golden and the cell diverged on how fast the machine was — the reason S-1 existed.
#   2. a real CONTENT difference inside a frame is still caught: changing the contentBlockDelta's
#      text is UNEQUAL after. A rule that made (1) pass by flattening the body would fail here.
#   3. a corrupted stream does not decode into a clean-looking list: flipping one bit of the last
#      message CRC yields `eventstream.undecodable`, never `eventstream.frames`. Because the applied
#      set is the `norm.rules` diff class, that swap is red on its own.
esgen="$W/es-frames.py"
cat >"$esgen" <<'PYES'
import base64, json, struct, sys, zlib
def frame(event_type, payload):
    hb = b""
    for n, v in ((":event-type", event_type), (":content-type", "application/json"), (":message-type", "event")):
        nb, vb = n.encode(), v.encode()
        hb += bytes([len(nb)]) + nb + b"\x07" + struct.pack(">H", len(vb)) + vb   # 0x07 = STRING
    pb = json.dumps(payload, separators=(",", ":")).encode()
    pre = struct.pack(">II", 16 + len(hb) + len(pb), len(hb))
    pre += struct.pack(">I", zlib.crc32(pre) & 0xFFFFFFFF)          # prelude CRC32
    msg = pre + hb + pb
    return msg + struct.pack(">I", zlib.crc32(msg) & 0xFFFFFFFF)    # message CRC32
def stream(latency_ms, text):
    return (frame("messageStart", {"role": "assistant"})
            + frame("contentBlockStart", {"contentBlockIndex": 0, "start": {}})
            + frame("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": text}})
            + frame("contentBlockStop", {"contentBlockIndex": 0})
            + frame("messageStop", {"stopReason": "end_turn"})
            + frame("metadata", {"metrics": {"latencyMs": latency_ms},
                                 "usage": {"inputTokens": 11, "outputTokens": 7, "totalTokens": 18}}))
which = sys.argv[1]
body = stream(0, "oracle-marker") if which in ("lat0", "corrupt") else \
       stream(2, "oracle-marker") if which == "lat2" else stream(0, "DIFFERENT-TEXT")
if which == "corrupt":
    b = bytearray(body); b[-1] ^= 0xFF; body = bytes(b)
print(json.dumps({"status": 200,
                  "headers": {"content-type": "application/vnd.amazon.eventstream",
                              "content-length": str(len(body))},
                  "body": "base64:" + base64.b64encode(body).decode(),
                  "effects": {}}))
PYES
es_ok=1
for w in lat0 lat2 difftext corrupt; do
  python3 "$esgen" "$w" >"$W/es-$w.cap.json" 2>"$W/es-$w.err" || { es_ok=0; break; }
  python3 "${here}/normalize.py" "$W/es-$w.cap.json" >"$W/es-$w.norm.json" 2>>"$W/es-$w.err" || { es_ok=0; break; }
done
if [ "$es_ok" != 1 ]; then
  say FAIL "eventstream.frames: the fixture or the normalizer errored (see $W/es-*.err)"
else
  raw_differ=0; cmp -s "$W/es-lat0.cap.json" "$W/es-lat2.cap.json" || raw_differ=1
  norm_equal=0; cmp -s "$W/es-lat0.norm.json" "$W/es-lat2.norm.json" && norm_equal=1
  norm_differ=0; cmp -s "$W/es-lat0.norm.json" "$W/es-difftext.norm.json" || norm_differ=1
  rules="$(python3 -c 'import json,sys;print(",".join(json.load(open(sys.argv[1]))["applied"]))' "$W/es-lat0.norm.json")"
  bad_rules="$(python3 -c 'import json,sys;print(",".join(json.load(open(sys.argv[1]))["applied"]))' "$W/es-corrupt.norm.json")"
  [ "$raw_differ" = 1 ] && [ "$norm_equal" = 1 ] \
    && say PASS "eventstream.frames: latencyMs 0 vs 2 differ on the wire, equal after normalization" \
    || say FAIL "eventstream.frames: latencyMs 0 vs 2 raw_differ=$raw_differ norm_equal=$norm_equal"
  [ "$norm_differ" = 1 ] \
    && say PASS "eventstream.frames: a real payload difference inside a frame is still UNEQUAL after" \
    || say FAIL "eventstream.frames: a changed contentBlockDelta normalized to the same bytes — the rule is hiding content"
  case ",$rules," in *,eventstream.frames,*) f1=1 ;; *) f1=0 ;; esac
  case ",$rules," in *,metrics.timing,*) f2=1 ;; *) f2=0 ;; esac
  [ "$f1" = 1 ] && [ "$f2" = 1 ] \
    && say PASS "eventstream.frames: the rule and metrics.timing both fire (the payload went through the JSON path)" \
    || say FAIL "eventstream.frames: applied=$rules (wanted eventstream.frames AND metrics.timing)"
  case ",$bad_rules," in *,eventstream.undecodable,*) c1=1 ;; *) c1=0 ;; esac
  case ",$bad_rules," in *,eventstream.frames,*) c2=1 ;; *) c2=0 ;; esac
  [ "$c1" = 1 ] && [ "$c2" = 0 ] \
    && say PASS "eventstream.frames: a flipped message CRC -> eventstream.undecodable, never a clean decode" \
    || say FAIL "eventstream.frames: corrupt stream applied=$bad_rules (wanted undecodable, not frames)"
fi

# (dd) `stderr.platform-capability` DROPS THE HOST LINE ON BOTH SIDES, AND SAYS SO WHEN IT FIRED ON
# ONE. The golden is recorded on darwin (jemalloc cannot start its purge thread) and CI's candidate
# runs on linux, where the same binary never prints the line — so this rule is asymmetric BY
# CONSTRUCTION and the whole question is what the differ does about that. Two halves:
#   * normalize.py: two captures identical but for the host line normalize to the SAME stderr, and
#     the applied sets differ by exactly `stderr.platform-capability` — the asymmetry is recorded,
#     not erased;
#   * diff-cells.py: that pair is GREEN (the rule is exempt from norm.rules), AND the row carries a
#     `platform` field naming the side that fired. A green cell's `detail` is emptied, so a rule
#     reported only through `detail` would be invisible in exactly this case, which is the one that
#     matters. If `platform` ever stops being written, this case goes red.
export HERE="$here"   # the python heredocs below are quoted, so they read the path from the env
jem='[warn] could not enable jemalloc background purge thread (`name` or `mib` specifies an unknown/invalid value.); enabling busbar'"'"'s idle purge fallback so RSS still returns to idle after a load burst'
python3 - "$W" "$jem" <<'PY'
import json, os, subprocess, sys
w, jem = sys.argv[1], sys.argv[2]
here = os.environ["HERE"]
base = ["[info] busbar starting", "[info] listening on 127.0.0.1:8080"]
def norm(lines):
    cap = {"status": 200, "headers": {"content-type": "application/json"}, "body": "{}",
           "effects": {"stderr": "\n".join(lines)}}
    p = os.path.join(w, "cap.json")
    json.dump(cap, open(p, "w"))
    return json.loads(subprocess.run([sys.executable, os.path.join(here, "normalize.py"), p],
                                     capture_output=True, text=True, check=True).stdout)
with_host = norm(base[:1] + [jem] + base[1:])
without    = norm(base)
# The 1.6.0 spelling of the same host fact: an [info] line naming the target. Both spellings are
# the recording host's capability, and both must vanish, or a darwin candidate diverges from a
# linux golden on every boot cell by a sentence about the machine.
jem_info = "[info] jemalloc background purge thread unavailable on this target (`name` or `mib` specifies an unknown/invalid value.) — EXPECTED on macOS; busbar's idle-purge fallback keeps RSS returning to idle after a load burst"
with_info = norm(base[:1] + [jem_info] + base[1:])
json.dump({"same_stderr": with_host["effects"]["stderr"] == without["effects"]["stderr"]
                          and with_info["effects"]["stderr"] == without["effects"]["stderr"]
                          and "stderr.platform-capability" in with_info["applied"],
           "rule_on_golden": "stderr.platform-capability" in with_host["applied"],
           "rule_on_candidate": "stderr.platform-capability" in without["applied"]},
          open(os.path.join(w, "dd.json"), "w"))
PY
dd_same="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["same_stderr"])' "$W/dd.json")"
dd_g="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["rule_on_golden"])' "$W/dd.json")"
dd_c="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["rule_on_candidate"])' "$W/dd.json")"
[ "$dd_same" = True ] && [ "$dd_g" = True ] && [ "$dd_c" = False ] \
  && say PASS "stderr.platform-capability: the host line is dropped on both sides, one-sided firing recorded" \
  || say FAIL "stderr.platform-capability normalize: same=$dd_same golden=$dd_g candidate=$dd_c"

cp -R "$FIX" "$W/plat-g"; cp -R "$FIX" "$W/plat-c"
python3 - "$W/plat-g/cells/self__a__ok.json" "$W/plat-c/cells/self__a__ok.json" <<'PY'
import json, sys
g, c = sys.argv[1], sys.argv[2]
for p, applied in ((g, True), (c, False)):
    d = json.load(open(p))
    # Identical stderr on BOTH sides — the host line is already gone, which is what the rule does.
    d["effects"]["stderr"] = "[info] busbar starting\n[info] listening"
    if applied:
        d["applied"] = sorted(set(d.get("applied", [])) | {"stderr.platform-capability"})
    json.dump(d, open(p, "w"), separators=(",", ":"), sort_keys=True)
PY
rc="$(run "$W/plat-g" "$W/plat-c" "$W/out-dd")"
plat="$(python3 -c '
import json,sys
r=json.load(open(sys.argv[1]))
row=[x for x in r["cells"] if x["id"]=="self|a|ok"][0]
print(json.dumps(row.get("platform")))' "$W/out-dd/report.json" 2>/dev/null || echo null)"
[ "$rc" = 0 ] && [ "$plat" != "null" ] \
  && say PASS "stderr.platform-capability: one-sided firing is GREEN and REPORTED on the row ($plat)" \
  || say FAIL "platform skew rc=$rc platform=$plat (green? reported?)"

# (ee) THE RULE MAY NEVER TOUCH A REFUSAL. A refusal message is the single thing in stderr the oracle
# exists to compare, so the patterns are anchored whole-line and name a specific capability. This
# case feeds the rule the two shapes that would fall to a lazier pattern — a refusal that MENTIONS
# jemalloc, and a `[warn]` line that is not the capability line — and proves both survive byte-exact
# with the rule NOT recorded. Then it proves a cell differing only in such a refusal is still RED: if
# someone widens the pattern to a keyword or a severity, this goes green and the case fails.
python3 - "$W" <<'PY'
import json, os, subprocess, sys
w = sys.argv[1]; here = os.environ["HERE"]
decoys = [
    "[error] refusing to start: jemalloc background purge thread could not be enabled and strict mode is on",
    "[warn] could not enable the plugin staging sweep; continuing",
    "[warn] could not enable jemalloc background purge thread",  # no trailing detail: still the line
]
out = []
for text in decoys:
    p = os.path.join(w, "cap2.json")
    json.dump({"status": 200, "headers": {}, "body": "{}", "effects": {"stderr": text}}, open(p, "w"))
    r = json.loads(subprocess.run([sys.executable, os.path.join(here, "normalize.py"), p],
                                  capture_output=True, text=True, check=True).stdout)
    out.append({"text": text, "kept": r["effects"]["stderr"] == text,
                "fired": "stderr.platform-capability" in r["applied"]})
json.dump(out, open(os.path.join(w, "ee.json"), "w"))
PY
ee="$(python3 -c '
import json,sys
d=json.load(open(sys.argv[1]))
# the two decoys must be untouched; the third IS the capability line and must fire
ok = d[0]["kept"] and not d[0]["fired"] and d[1]["kept"] and not d[1]["fired"] and d[2]["fired"]
print("OK" if ok else "BAD "+json.dumps(d))' "$W/ee.json")"
[ "$ee" = OK ] \
  && say PASS "stderr.platform-capability: a refusal naming jemalloc, and a sibling [warn], are untouched" \
  || say FAIL "stderr.platform-capability touched something it must not: $ee"

cp -R "$FIX" "$W/refuse"
python3 - "$W/refuse/cells/self__a__ok.json" <<'PY'
import json, sys
p = sys.argv[1]; d = json.load(open(p))
d["effects"]["stderr"] = "[error] refusing to start: jemalloc background purge thread could not be enabled and strict mode is on"
json.dump(d, open(p, "w"), separators=(",", ":"), sort_keys=True)
PY
cp -R "$FIX" "$W/refuse-g"
python3 - "$W/refuse-g/cells/self__a__ok.json" <<'PY'
import json, sys
p = sys.argv[1]; d = json.load(open(p))
d["effects"]["stderr"] = "[info] busbar starting"
json.dump(d, open(p, "w"), separators=(",", ":"), sort_keys=True)
PY
rc="$(run "$W/refuse-g" "$W/refuse" "$W/out-ee")"
cls="$(classes_of 'self|a|ok' "$W/out-ee")"
[ "$rc" != 0 ] && [ "$cls" = "effects.stderr" ] \
  && say PASS "a refusal that differs is RED [effects.stderr], not swallowed by the host-line rule" \
  || say FAIL "refusal difference rc=$rc classes=$cls"

# (ff) A DROPPED Retry-After IS RED. `route.failover|fo|primary-429` is NAMED for a 429 that carries
# one; before the response half of effects.egress existed, the mock could have served the 429 with no
# Retry-After at all and every recorded byte would have been identical. effects.egress is a MONEY
# class (weight 10), so this is not a cosmetic row.
cp -R "$FIX" "$W/eg-g"; cp -R "$FIX" "$W/eg-c"
python3 - "$W/eg-g/cells/self__a__ok.json" "$W/eg-c/cells/self__a__ok.json" <<'PY'
import json, sys
g, c = sys.argv[1], sys.argv[2]
def put(p, retry_after):
    d = json.load(open(p))
    d["effects"]["egress"] = [{"path": "/v1/chat/completions", "method": "POST",
                               "headers": {"host": "127.0.0.1:<PORT>"}, "body": {"model": "m"},
                               "response": {"status": 429, "retry_after": retry_after, "elapsed": "<1s"}}]
    d["applied"] = sorted(set(d.get("applied", [])) | {"egress.elapsed"})
    json.dump(d, open(p, "w"), separators=(",", ":"), sort_keys=True)
put(g, "7")     # the upstream sent one, as the cell's name says
put(c, None)    # it stopped sending one: the cell keeps its name and loses its meaning
PY
rc="$(run "$W/eg-g" "$W/eg-c" "$W/out-ff")"
cls="$(classes_of 'self|a|ok' "$W/out-ff")"
[ "$rc" != 0 ] && [ "$cls" = "effects.egress" ] \
  && say PASS "a dropped upstream Retry-After -> RED [effects.egress]" \
  || say FAIL "dropped Retry-After rc=$rc classes=$cls"

# (gg) A REMOVED SLEEP IS RED. `route.failover|fo|primary-slow` exists because the attempt ran PAST
# busbar's cap. `egress.elapsed` buckets the raw millisecond count precisely so that this fact can be
# compared without pinning a stopwatch: an attempt that stops being slow moves >5s -> <1s and the
# cell moves with it. If the bucket were dropped from the golden instead of normalized, or the
# boundaries were widened until everything landed in one bucket, this case goes green.
cp -R "$FIX" "$W/slow-g"; cp -R "$FIX" "$W/slow-c"
python3 - "$W/slow-g/cells/self__a__ok.json" "$W/slow-c/cells/self__a__ok.json" <<'PY'
import json, sys
def put(p, bucket):
    d = json.load(open(p))
    d["effects"]["egress"] = [{"path": "/v1/chat/completions", "method": "POST",
                               "headers": {"host": "127.0.0.1:<PORT>"}, "body": {"model": "m"},
                               "response": {"status": 200, "retry_after": None, "elapsed": bucket}}]
    d["applied"] = sorted(set(d.get("applied", [])) | {"egress.elapsed"})
    json.dump(d, open(p, "w"), separators=(",", ":"), sort_keys=True)
put(sys.argv[1], ">5s")   # the mock slept past busbar's attempt cap
put(sys.argv[2], "<1s")   # the sleep is gone: the cell is still called primary-slow
PY
rc="$(run "$W/slow-g" "$W/slow-c" "$W/out-gg")"
cls="$(classes_of 'self|a|ok' "$W/out-gg")"
[ "$rc" != 0 ] && [ "$cls" = "effects.egress" ] \
  && say PASS "a removed upstream sleep (>5s -> <1s) -> RED [effects.egress]" \
  || say FAIL "removed sleep rc=$rc classes=$cls"

# And the bucket boundaries themselves, since the two cases above depend on them separating the
# harness's real timings: a healthy mock answer (single-digit ms) and the slow verb's sleep
# (ORACLE_MOCK_SLOW_SECS, default 8s) must never share a bucket.
bk="$(python3 -c '
import importlib.util, sys
spec = importlib.util.spec_from_file_location("n", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print(",".join(m.elapsed_bucket(x) for x in (4, 999, 1000, 4999, 5000, 8000)))' "${here}/normalize.py")"
[ "$bk" = "<1s,<1s,1-5s,1-5s,>5s,>5s" ] \
  && say PASS "egress.elapsed buckets: healthy ms and an 8s sleep can never share one ($bk)" \
  || say FAIL "egress.elapsed bucket boundaries moved: $bk"

# (hh) `metrics.concurrent-attempts` MASKS ONE VALUE ON ONE DRIVER, AND NOTHING ELSE. The recorder
# fires N requests at once; the scrape that closes the window does not see N workers' increments at
# one instant, and the published 1.5.5 binary records 3, 1, 1, 1 for the same cell across four runs.
# Three things are proved, and the last two are what stop this becoming a blanket:
#   * with --driver concurrent the attempts VALUE is masked and the rule is recorded;
#   * the KEY survives, and busbar_requests_total (and every other series) keeps its real value —
#     a masked attempts series must not take the request count or the money with it;
#   * WITHOUT the flag the same capture is untouched, so on the other three drivers the attempts
#     value is still compared byte for byte.
python3 - "$W" <<'PY'
import json, os, subprocess, sys
w = sys.argv[1]; here = os.environ["HERE"]
cap = {"status": 200, "headers": {}, "body": "{}", "effects": {"metrics": {
    'busbar_upstream_attempts_total{lane="1"}': 3,
    'busbar_requests_total{outcome="ok"}': 4,
}, "usage": {"requests": 4, "spend_micros": 72}}}
p = os.path.join(w, "cap3.json"); json.dump(cap, open(p, "w"))
def run(*flags):
    return json.loads(subprocess.run([sys.executable, os.path.join(here, "normalize.py"), p, *flags],
                                     capture_output=True, text=True, check=True).stdout)
json.dump({"conc": run("--driver", "concurrent"), "plain": run()}, open(os.path.join(w, "hh.json"), "w"))
PY
hh="$(python3 -c '
import json,sys
d=json.load(open(sys.argv[1])); conc, plain = d["conc"], d["plain"]
cm, pm = conc["effects"]["metrics"], plain["effects"]["metrics"]
ok = (cm["busbar_upstream_attempts_total{lane=\"1\"}"] == "<ATTEMPTS>"
      and "metrics.concurrent-attempts" in conc["applied"]
      and cm["busbar_requests_total{outcome=\"ok\"}"] == 4
      and conc["effects"]["usage"] == {"requests": 4, "spend_micros": 72}
      and pm["busbar_upstream_attempts_total{lane=\"1\"}"] == 3
      and "metrics.concurrent-attempts" not in plain["applied"])
print("OK" if ok else "BAD "+json.dumps(d))' "$W/hh.json")"
[ "$hh" = OK ] \
  && say PASS "metrics.concurrent-attempts: the attempts VALUE is masked on the concurrent driver alone" \
  || say FAIL "metrics.concurrent-attempts: $hh"

# And the mask must not hide a real regression NEXT to it: two concurrent cells whose request count
# differs are still RED, even though both had their attempts value masked.
cp -R "$FIX" "$W/conc-g"; cp -R "$FIX" "$W/conc-c"
python3 - "$W/conc-g/cells/self__a__ok.json" "$W/conc-c/cells/self__a__ok.json" <<'PY'
import json, sys
def put(p, requests):
    d = json.load(open(p))
    d["effects"]["metrics"] = {'busbar_upstream_attempts_total{lane="1"}': "<ATTEMPTS>",
                               'busbar_requests_total{outcome="ok"}': requests}
    d["applied"] = sorted(set(d.get("applied", [])) | {"metrics.concurrent-attempts"})
    json.dump(d, open(p, "w"), separators=(",", ":"), sort_keys=True)
put(sys.argv[1], 4)
put(sys.argv[2], 3)   # one request stopped being counted, on a cell whose attempts are masked
PY
rc="$(run "$W/conc-g" "$W/conc-c" "$W/out-hh")"
cls="$(classes_of 'self|a|ok' "$W/out-hh")"
[ "$rc" != 0 ] && [ "$cls" = "effects.metrics" ] \
  && say PASS "a masked attempts value does not hide a changed request count -> RED [effects.metrics]" \
  || say FAIL "masked attempts hid a metrics regression rc=$rc classes=$cls"

# (ii) THE RECORDER UNSETS THE SUPERVISOR MARKERS. busbar-core decides `supervisor_detected` from
# INVOCATION_ID / KUBERNETES_SERVICE_HOST and answers the restart verb differently on each arm, down
# to the 202 body length. systemd sets INVOCATION_ID for every unit it starts — i.e. every job on a
# Linux runner — and nothing sets it on a mac, so the golden and a re-recording took different arms
# for a reason that is not busbar. This case reads the marker list out of the RUST SOURCE and holds
# record.sh to it, so a third marker added to SUPERVISOR_MARKERS tomorrow is red here rather than
# silently re-opening the hole.
src="${repo}/crates/busbar-core/src/admin/restart.rs"
if [ -f "$src" ]; then
  want="$(grep -oE 'SUPERVISOR_MARKERS[^=]*= *\[[^]]*\]' "$src" | grep -oE '"[A-Z_]+"' | tr -d '"' | LC_ALL=C sort -u)"
  got="$(grep -oE '^unset [A-Z_ ]+' "${here}/record.sh" | sed 's/^unset //' | tr ' ' '\n' | grep -v '^$' | LC_ALL=C sort -u)"
  missing="$(comm -23 <(printf '%s\n' "$want") <(printf '%s\n' "$got"))"
  [ -n "$want" ] && [ -z "$missing" ] \
    && say PASS "record.sh unsets every SUPERVISOR_MARKER the binary reads ($(tr '\n' ' ' <<<"$want"))" \
    || say FAIL "record.sh does not unset: $(tr '\n' ' ' <<<"$missing") (host would pick the restart arm)"
else
  skip "record.sh vs SUPERVISOR_MARKERS: the Rust source naming them is not in this tree, so the one case that would notice record.sh no longer unsetting INVOCATION_ID cannot run"
fi

# ── (jj) THE TOOL/DATA SEAM ──────────────────────────────────────────────────────────────────────
# Everything below is about ONE bug class, found by a full linux re-record against v0.2.0 and
# reproduced locally: a file that belongs to the PRODUCT being resolved against the TOOL, or the
# other way round. It is a silent class. Nothing about it is visible in this repository, because
# in-tree the two directories are the same directory and every wrong derivation is accidentally
# right; it only appears once the tool is installed somewhere else, which is the only way anyone
# actually runs it. Its cost was 232 FAIL rows reported as busbar regressions.
#
# There are three seams and each gets a case that would be red without the fix.

# (jj-1) THE MUTATION FIXTURE IS READ OUT OF THE DATA DIRECTORY. apply-mutation.py's `--fixture`
# defaulted to `<tool>/fixtures/boot-mutations.json`; the package ships only
# fixtures/selftest-recording/**, and record.sh passed no --fixture at all, so every `mutation:`
# cell died with FileNotFoundError. The planted id below exists in NO packaged fixture, so this
# arm cannot be satisfied by a copy sitting beside the tool.
#
# Driven through the file's own --selftest, which owns the case (it needs PyYAML, exactly as a real
# mutation cell does). PyYAML is not a runtime dependency of this tool and must not become one — the
# oracle judges a workspace and shares nothing with it — so a host without it says so by name here
# instead of reporting a green it did not earn.
if python3 -c 'import yaml' >/dev/null 2>&1; then
  am_out="$(python3 "${here}/apply-mutation.py" --selftest 2>&1)"; am_rc=$?
  if [ "$am_rc" = 0 ] && printf '%s' "$am_out" | grep -q "read out of the DATA dir"; then
    say PASS "apply-mutation.py resolves its mutation fixture against \$BUSBAR_ORACLE_DATA, not against itself"
  else
    say FAIL "apply-mutation.py --selftest rc=${am_rc}: $(printf '%s' "$am_out" | grep '^FAIL' | tr '\n' ' ' | cut -c1-300)"
  fi
else
  say FAIL "PyYAML is not importable, so the mutation-fixture case could not run — install it (it is a DEV dependency of this tool and a RUNTIME requirement of any product with mutation cells) rather than letting this read green"
fi

# (jj-2) AND record.sh NAMES IT RATHER THAN RELYING ON THAT DEFAULT. Two ends that agree by
# accident agree until one of them moves. Static, because the alternative is recording a cell.
if grep -q -- '--fixture "\${data}/fixtures/boot-mutations.json"' "${here}/record.sh"; then
  say PASS "record.sh passes the DATA dir's boot-mutations.json explicitly (the two ends cannot disagree)"
else
  say FAIL "record.sh does not pass --fixture, so which mutation inventory a cell is recorded against is decided by a default in another file"
fi

# (jj-3) THE DRIVER CONTRACT: BUSBAR_ORACLE_TOOL_DIR. The product's cell drivers call TOOL files
# (capture.py, capture-exec.py, mock-upstream.py, oracle-config.sh, fetch-plugin.sh, fetch-golden.sh)
# that left the product's tree with the tool. They reach them through this variable.
#
# THE CLAIM IS ABOUT THE VALUE THE DRIVER ENDS UP WITH, NOT ABOUT WHO SET IT. Two callers exist and
# both are legitimate:
#   * the PRODUCT'S SHIM, which exports the variable itself (it knows where it installed the tool)
#     and then execs `busbar-oracle`. The driver must LEAVE THAT ALONE — the export below is
#     `${VAR:-…}`, a default and not an override, for the same reason BUSBAR_ORACLE_DATA is: the
#     harness-rev cases further up re-root the tool at a throwaway copy of the data, which an
#     overriding driver would make impossible.
#   * `busbar-oracle record` run DIRECTLY, with nothing pre-set. Then the driver must supply its own
#     directory, or the cell driver it runs falls back to `:-$here` — a path beside itself, which is
#     either absent (a broken cell) or a stale in-tree copy (a cell recorded by code nobody pinned).
# Both arms are asserted below. Asserting only the second described a tool that is correct and a
# shim that is not.
#
# HOW IT IS OBSERVED, AND WHY NOT WITH AN EXIT TRAP ALONE. This has been wrong twice, both times by
# measuring the probe instead of the driver, so the mechanism is spelled out:
#
#   1. THE DRIVER'S OWN $0. Every driver derives `here` from `dirname "$0"`, so a
#      `bash -c '. "$1"' _ <driver>` reads $0 as `_` and the driver locates itself at the CURRENT
#      DIRECTORY. `bash -c <cmd> <name>` sets $0 to <name>, which is the hook needed: the driver
#      sees its real path, sets `here`, exports, and then exits 2 on its own usage check.
#   2. NOT AN `EXIT` TRAP ALONE. record.sh sources the PRODUCT's ledger machinery
#      (`$BUSBAR_ORACLE_PRODUCT_ROOT/testing/fleet-fixtures/lib.sh`) three lines after the export,
#      and a product's copy installs its own cleanup trap — busbar's line 81 is
#      `trap _reap_fixtures EXIT`, which REPLACES the probe's. The probe then produced no output at
#      all, the empty string was read as the exported value, and `./bin/oracle replay-selftest`
#      inside busbar reported RED against a tool that was exporting the right path. Note the shape:
#      the probe worked in this repository, where no product root exists and the `source` fails
#      harmlessly, and broke in the only place the case is about.
#      So `exit` is overridden as a FUNCTION as well. A trap can be replaced by the product; a
#      function of that name cannot be, short of the product defining one too, and the two
#      mechanisms fail independently. Whichever fires first is the answer.
#   3. AN OBSERVATION THAT DID NOT HAPPEN IS NOT A VALUE. `<no-observation>` is distinct from
#      `<unset>` and from a wrong path, and it reads as "this case did not run" rather than as a
#      verdict about the driver — which is precisely the confusion that cost the round above.
#
# The probe runs against a STAND-IN PRODUCT ROOT that installs an EXIT trap of its own, so the
# clobber is reproduced here, in this repository, on every run — rather than only in a product
# nobody has checked out.
td_root="$W/tdroot"
mkdir -p "$td_root/testing/fleet-fixtures"
cat >"$td_root/testing/fleet-fixtures/lib.sh" <<'LIB'
# Stand-in for a product's ledger machinery, in the ONE respect that broke the probe: it installs an
# EXIT trap, exactly as busbar's testing/fleet-fixtures/lib.sh does (`trap _reap_fixtures EXIT`).
# record.sh sources this three lines after the export under test.
_probe_product_reap() { :; }
trap _probe_product_reap EXIT
LIB
td_probe='
_probe_say() { printf "%s\n" "${BUSBAR_ORACLE_TOOL_DIR:-<unset>}" >&9; }
exit() { _probe_say; builtin exit "$@"; }
trap _probe_say EXIT
. "$0"
'
tool_dir_after() {  # <driver.sh> [preset] -> the value the driver ends up with
  local drv="$1" out
  if [ "$#" -ge 2 ]; then
    out="$(BUSBAR_ORACLE_TOOL_DIR="$2" BUSBAR_ORACLE_PRODUCT_ROOT="$td_root" \
      bash -c "$td_probe" "$drv" 9>&1 >/dev/null 2>&1 | head -1)"
  else
    out="$(env -u BUSBAR_ORACLE_TOOL_DIR BUSBAR_ORACLE_PRODUCT_ROOT="$td_root" \
      bash -c "$td_probe" "$drv" 9>&1 >/dev/null 2>&1 | head -1)"
  fi
  printf '%s\n' "${out:-<no-observation>}"
}
td_preset="$W/a-shim-said-so"
for drv in record.sh replay.sh; do
  got_td="$(tool_dir_after "${here}/${drv}")"
  [ "$got_td" = "$here" ] \
    && say PASS "${drv}: with nothing pre-set, a driver it runs sees BUSBAR_ORACLE_TOOL_DIR=<the installed tool>" \
    || say FAIL "${drv}: with nothing pre-set, a driver it runs sees BUSBAR_ORACLE_TOOL_DIR=${got_td} (wanted ${here}) — it cannot resolve capture.py/mock-upstream.py/fetch-plugin.sh"

  got_td="$(tool_dir_after "${here}/${drv}" "$td_preset")"
  [ "$got_td" = "$td_preset" ] \
    && say PASS "${drv}: a value the caller pre-exported (what the product's shim does) survives untouched" \
    || say FAIL "${drv}: a pre-exported BUSBAR_ORACLE_TOOL_DIR became ${got_td} (wanted ${td_preset}) — the driver is overriding its caller, and a shim that installed the tool cannot say where"
done

# …and the two halves of the contract are not the same half. A probe that reads a tool file THROUGH
# the variable must work; the same probe reading it BESIDE ITSELF (the pre-extraction shape, and the
# `:-$here` fallback every driver still carries) must fail under the shipped layout. Without the
# second arm the first proves only that some path exists somewhere.
#
# THE SECOND ARM HAS TO RESOLVE SOMEWHERE REAL. It used to run out of `$W/tdprobe`, a directory this
# selftest creates and into which no mock-upstream.py is ever written, so `td_beside_rc` was 7
# unconditionally — it would have "passed" with the extraction reverted and the drivers sitting
# beside the tool again. The probe now resolves beside the PRODUCT'S OWN drivers ($data/scripts),
# which is where a real driver's `$here/..` lands, so the arm measures the shipped layout instead of
# an empty temp dir. In an in-tree layout (data dir and tool dir are one) both arms resolve and
# there is nothing to prove — said out loud rather than reported as a pass.
mkdir -p "$W/tdprobe/scripts"
cat >"$W/tdprobe/scripts/probe-var.sh" <<'PROBE'
here="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "${BUSBAR_ORACLE_TOOL_DIR:-$here}/mock-upstream.py" ] || exit 7
PROBE
BUSBAR_ORACLE_TOOL_DIR="$here" bash "$W/tdprobe/scripts/probe-var.sh"; td_var_rc=$?
# beside-itself, against the real data dir: `$here/..` from $data/scripts/<driver> is $data
if [ -f "${data}/mock-upstream.py" ]; then
  td_beside_rc=0    # in-tree: the tool IS the data dir, so both arms resolve (see below)
else
  td_beside_rc=7
fi
if [ "${data}" = "${here}" ] || [ -f "${data}/mock-upstream.py" ]; then
  skip "the driver contract's beside-itself arm: this run's data dir (${data}) also holds the tool's files, so both arms resolve and only the shipped layout could tell them apart"
fi
if [ "$td_var_rc" = 0 ] && [ "$td_beside_rc" != 0 ]; then
  say PASS "a driver that names a tool file through BUSBAR_ORACLE_TOOL_DIR resolves it, and one that names it beside itself (the product's own scripts/ dir) does not — so the variable is load-bearing, not decorative"
elif [ "$td_var_rc" = 0 ]; then
  say PASS "a driver that names a tool file through BUSBAR_ORACLE_TOOL_DIR resolves it (the beside-itself arm cannot discriminate in this layout — see the SKIP above)"
else
  say FAIL "a driver cannot resolve a tool file through BUSBAR_ORACLE_TOOL_DIR at all (rc=${td_var_rc}), so every driver that names capture.py/mock-upstream.py/fetch-plugin.sh through it is broken"
fi

# (jj-4) THE VERDICT HALF READS THE PRODUCT'S CELL LIST BY DEFAULT. `busbar-oracle diff` without
# --cells defaulted to `<tool>/cells.json`, a file an installed tool does not ship — so the one
# subcommand that IS the verdict crashed when run directly. replay.sh always passes --cells, which
# is precisely why nobody saw it. Run with only BUSBAR_ORACLE_DATA set, and required to agree with
# the same run that names the file.
dd_out="$W/dd-default"; dd_exp="$W/dd-explicit"
BUSBAR_ORACLE_DATA="$data" python3 "${here}/diff-cells.py" --golden "$FIX" --candidate "$W/same" \
  --out "$dd_out" --accepted "$W/no-accept.json" --allow-harness-skew >"$W/dd-default.log" 2>&1
dd_rc=$?
python3 "${here}/diff-cells.py" --golden "$FIX" --candidate "$W/same" --out "$dd_exp" \
  --cells "${data}/cells.json" --accepted "$W/no-accept.json" --allow-harness-skew >"$W/dd-explicit.log" 2>&1
dd_rc2=$?
if [ "$dd_rc" = "$dd_rc2" ] && [ -f "$dd_out/report.json" ] && [ -f "$dd_exp/report.json" ] \
   && cmp -s "$dd_out/report.json" "$dd_exp/report.json"; then
  say PASS "diff without --cells reads \$BUSBAR_ORACLE_DATA/cells.json and produces the same report as naming it"
else
  say FAIL "diff without --cells does not read the data dir's cell list (rc=${dd_rc} vs ${dd_rc2}): $(tail -3 "$W/dd-default.log" | tr '\n' ' ' | cut -c1-300)"
fi

# (jj-5) AND NO SHIPPED FILE GOES BACK TO DERIVING A DATA PATH FROM ITSELF. The four cases above are
# each about one file; this is about the class. Every name below is a PRODUCT file — none of them is
# shipped by this package — so a default that joins one to the tool's own directory is the bug,
# whichever file grows it next. Written as a scan rather than a list of known offenders because the
# whole lesson of this round is that the offender is always the file nobody thought to check.
data_names='cells\.json|accepted-differences\.json|accepted-gaps\.json|owed-baseline\.txt|golden-digests\.tsv|plugin-digests\.tsv|boot-mutations\.json|rigs-baseline\.json'
seam_bad=""
for f in "${here}"/*.py; do
  [ -f "$f" ] || continue
  # a data file joined to __file__'s directory, on one line — the exact shape all three defects had
  if grep -nE "(dirname\(os\.path\.abspath\(__file__\)\)|\bHERE\b|\b_HERE\b)[^#]*(${data_names})" "$f" \
       | grep -v '^\s*#' | grep -q .; then
    seam_bad="${seam_bad} $(basename "$f")"
  fi
done
for f in "${here}"/*.sh; do
  [ -f "$f" ] || continue
  if grep -nE "\\\$\{?here\}?/(${data_names})" "$f" | grep -q .; then
    seam_bad="${seam_bad} $(basename "$f")"
  fi
done
[ -z "$seam_bad" ] \
  && say PASS "no shipped file resolves a PRODUCT data file against the tool's own directory" \
  || say FAIL "shipped file(s) resolve a product data file against the tool's own directory, which is only ever right in-tree:${seam_bad}"

# (kk-1) A CELL THE CANDIDATE RECORDED AND NOTHING COMPARED. Every loop in diff-cells.py walks the
# OWED set, which comes from the GOLDEN — so a cell file in the candidate that the golden does not
# owe was invisible: no row, no class, no line. Two shapes, both real: an id cells.json HAS but the
# golden could not record (a gap the candidate has closed), and an id cells.json does not have at
# all (a rename, or a file left behind by an earlier recording into the same --out).
cp -R "$FIX" "$W/extra"
cp "$FIX/cells/self__a__ok.json" "$W/extra/cells/self__c__gap.json"
cp "$FIX/cells/self__a__ok.json" "$W/extra/cells/self__zz__renamed.json"
rc="$(run "$FIX" "$W/extra" "$W/out-kk1")"
n_extra="$(wc -l <"$W/out-kk1/extra-candidate.txt" | tr -d ' ')"
kk_rows="$(awk -F'\t' '$3=="extra.candidate"{n++} END{print n+0}' "$W/out-kk1/ledger.tsv")"
kk_fail="$(fails_in "$W/out-kk1")"
if [ "$rc" = 0 ] && [ "$n_extra" = 2 ] && [ "$kk_rows" = 2 ] && [ "$kk_fail" = 0 ] \
   && grep -q 'self|c|gap' "$W/out-kk1/extra-candidate.txt" \
   && grep -q 'self__zz__renamed' "$W/out-kk1/extra-candidate.txt"; then
  say PASS "a cell the candidate recorded that the golden does not owe is reported as extra.candidate on its own row, and is not red"
else
  say FAIL "extra.candidate rc=$rc extras=$n_extra rows=$kk_rows fails=$kk_fail: $(cat "$W/out-kk1/extra-candidate.txt" | tr '\n' ' ' | cut -c1-200)"
fi

# (kk-2) …and RED when the caller asks for it. A row nobody expects is a row verdict.sh never reads,
# so this also proves the ids reach EXPECTED_IDS rather than sitting in the ledger deciding nothing.
rc="$(run_args "$FIX" "$W/extra" "$W/out-kk2" --allow-harness-skew --no-check-golden \
        --accepted "$W/no-accept.json" --baseline "$W/no-baseline.txt" --refuse-extra-candidate)"
kk2_fail="$(fails_in "$W/out-kk2")"
[ "$rc" != 0 ] && [ "$kk2_fail" = 2 ] \
  && say PASS "--refuse-extra-candidate turns those rows RED and the verdict sees them (2 FAIL)" \
  || say FAIL "--refuse-extra-candidate rc=$rc fails=$kk2_fail (want non-zero, 2)"

# (kk-3) THE CASE THE OWED-BASELINE RATCHET NAMES FIRST AND COULD NOT SEE: a baselined, PASSING cell
# DELETED FROM cells.json. Its id leaves the owed set and the gaps list together, so the old
# `if cid not in scope: continue` skipped it in silence and the run reported GREEN over a corpus one
# cell smaller than the one signed off. Reproduced here against the real replay.sh.
python3 - "$CELLS" "$W/cells-minus-a.json" <<'EOF'
import json,sys
d=json.load(open(sys.argv[1]))
d["cells"]=[c for c in d["cells"] if c["id"]!="self|a|ok"]
json.dump(d,open(sys.argv[2],"w"))
EOF
printf 'self|a|ok\nself|b|stream\n' >"$W/baseline-kk.txt"
cp -R "$FIX" "$W/kk3"
rc="$(bash "${here}/replay.sh" --golden "$FIX" --candidate "$W/kk3" --out "$W/out-kk3" \
        --cells "$W/cells-minus-a.json" --allow-harness-skew --no-check-golden \
        --accepted "$W/no-accept.json" --baseline "$W/baseline-kk.txt" \
        --accepted-gaps "$W/no-gaps.json" >"$W/out-kk3.log" 2>&1; echo $?)"
kk3_cls="$(awk -F'\t' '$1=="self|a|ok"{print $3}' "$W/out-kk3/ledger.tsv")"
if [ "$rc" != 0 ] && [ "$(fails_in "$W/out-kk3")" = 1 ] && [ "$kk3_cls" = "owed-baseline regression" ] \
   && grep -q 'no longer present in cells.json' "$W/out-kk3/ledger.tsv"; then
  say PASS "a baselined cell DELETED from cells.json is an owed-baseline regression, not a silence"
else
  say FAIL "deleted-from-cells.json baseline id rc=$rc fails=$(fails_in "$W/out-kk3") class='$kk3_cls'"
fi

# (kk-4) …and a FILTERED run still says nothing about the ids it never looked at. The fix above must
# not turn --family into a machine for false regressions: an id that is still in cells.json and
# simply outside this run's selection is neither confirmed nor regressed.
rc="$(bash "${here}/replay.sh" --golden "$FIX" --candidate "$W/kk3" --out "$W/out-kk4" \
        --cells "$CELLS" --family 'nomatch' --allow-harness-skew --no-check-golden \
        --accepted "$W/no-accept.json" --baseline "$W/baseline-kk.txt" \
        --accepted-gaps "$W/no-gaps.json" >"$W/out-kk4.log" 2>&1; echo $?)"
kk4_baseline_rows="$(awk -F'\t' '$3=="owed-baseline regression"{n++} END{print n+0}' "$W/out-kk4/ledger.tsv")"
[ "$kk4_baseline_rows" = 0 ] \
  && say PASS "a --family run that selects none of the baselined ids reports no regression for them" \
  || say FAIL "a filtered run invented ${kk4_baseline_rows} owed-baseline regression(s) for ids it never selected"

# (mm) A CONCURRENT CELL'S EGRESS IS MEASURED, NOT ASSERTED. capture-concurrent.py hardcoded
# `"egress": []`, which is capture.py's own spelling of "this cell never reached upstream" — on
# cells that bill for eight upstream requests. effects.egress is MONEY (weight 10), so the class was
# structurally unarmed on exactly the five cells where concurrency makes egress hardest to reason
# about, and the only other witness (busbar_upstream_attempts_total) is masked on this very driver.
# Drives the REAL capture-concurrent.py, the REAL normalize.py --driver concurrent and the REAL
# differ, end to end.
mm="$W/mm"; mkdir -p "$mm/before" "$mm/after" "$mm/eg"
printf '{"requests":0,"tokens":0,"spend_cents":0}' >"$mm/before/usage.json"
printf '{"requests":2,"tokens":8,"spend_cents":4}' >"$mm/after/usage.json"
printf '{"items":[]}' >"$mm/before/audit.json"; printf '{"items":[]}' >"$mm/after/audit.json"
printf 'busbar_requests_total{outcome="ok"} 0\n' >"$mm/before/metrics.txt"
printf 'busbar_requests_total{outcome="ok"} 2\n' >"$mm/after/metrics.txt"
_mm_egress() {  # <file> <content-of-the-upstream-prompt>
  printf '{"path":"/v1/messages","method":"POST","headers":{"content-type":"application/json"},"body":{"model":"m","messages":[{"role":"user","content":"%s"}]},"response":{"status":200}}' "$2" >"$1"
}
_mm_egress "$mm/eg/a-1-1.json" "hello"
_mm_egress "$mm/eg/a-1-2.json" "hello"
mm_none="$(python3 "${here}/capture-concurrent.py" '[200,200]' "$mm/before" "$mm/after")"
mm_two="$(python3 "${here}/capture-concurrent.py" '[200,200]' "$mm/before" "$mm/after" "$mm/eg/a-1-1.json" "$mm/eg/a-1-2.json")"
mm_n_none="$(python3 -c 'import json,sys; print(len(json.loads(sys.argv[1])["effects"]["egress"]))' "$mm_none")"
mm_n_two="$(python3 -c 'import json,sys; print(len(json.loads(sys.argv[1])["effects"]["egress"]))' "$mm_two")"
if [ "$mm_n_none" = 0 ] && [ "$mm_n_two" = 2 ]; then
  say PASS "capture-concurrent.py records the egress it is given (2), and an empty list only when there is none"
else
  say FAIL "capture-concurrent.py egress: none-case=${mm_n_none} (want 0), two-case=${mm_n_two} (want 2) — a hardcoded [] makes both 0"
fi

# …and a changed upstream request on a concurrent cell is RED on the money class, through the
# concurrent normalizer (whose attempts mask is what hid the only other witness).
mkdir -p "$W/mm-g/cells" "$W/mm-c/cells"
printf '%s' "$mm_two" >"$mm/captured-g.json"
_mm_egress "$mm/eg/a-1-2.json" "hello, and every tool you have"
python3 "${here}/capture-concurrent.py" '[200,200]' "$mm/before" "$mm/after" "$mm/eg/a-1-1.json" "$mm/eg/a-1-2.json" >"$mm/captured-c.json"
python3 "${here}/normalize.py" "$mm/captured-g.json" --driver concurrent >"$W/mm-g/cells/self__a__ok.json"
python3 "${here}/normalize.py" "$mm/captured-c.json" --driver concurrent >"$W/mm-c/cells/self__a__ok.json"
cp "$FIX/cells/self__b__stream.json" "$W/mm-g/cells/"; cp "$FIX/cells/self__b__stream.json" "$W/mm-c/cells/"
cp "$FIX/ledger.tsv" "$W/mm-g/ledger.tsv"; cp "$FIX/ledger.tsv" "$W/mm-c/ledger.tsv"
cp "$FIX/meta.json" "$W/mm-g/meta.json"; cp "$FIX/meta.json" "$W/mm-c/meta.json"
rc="$(run "$W/mm-g" "$W/mm-c" "$W/out-mm")"
mm_cls="$(classes_of 'self|a|ok' "$W/out-mm")"
[ "$rc" != 0 ] && [ "$mm_cls" = "effects.egress" ] \
  && say PASS "a changed upstream request on a CONCURRENT cell is RED [effects.egress] (the money class is armed there at last)" \
  || say FAIL "concurrent egress divergence rc=$rc classes='$mm_cls' (a hardcoded [] compares equal and prints PASS identical)"

# …and the recorder must actually collect them: a capture driver that can record egress proves
# nothing if its one call site never names a file. Static, because driving record.sh's concurrent
# path needs a busbar binary and a live mock; the shape is what regressed and the shape is checked.
mm_src="$(sed -n '/^record_concurrent_cell()/,/^}/p' "${here}/record.sh")"
if grep -q 'egress.before' <<<"$mm_src" && grep -q 'egress.after' <<<"$mm_src" \
   && grep -q 'egress_settle' <<<"$mm_src" \
   && grep -qE 'capture-concurrent\.py.*egress_files\[@\]' <<<"$mm_src"; then
  say PASS "record.sh's concurrent path snapshots egress around the burst, settles it, and names the files to capture-concurrent.py"
else
  say FAIL "record.sh's concurrent path does not collect egress, so capture-concurrent.py can only ever record an empty list"
fi

# (nn) A SCRIPT CELL'S PASS MEANS THE DRIVER SUCCEEDED. It used to mean "a non-empty captured.json
# exists, its status is not -1, and it carries no harness_error" — the exit status of the process
# that wrote the file was thrown away at the invocation, and three of busbar's own drivers define no
# fail() and no harness_error at all, so for those the file test WAS the whole gate. Drives the REAL
# script_cell_verdict() out of record.sh (extracted by name, so a change to the rule changes this
# case with it), never a restatement of it.
eval "$(sed -n '/^script_cell_verdict()/,/^}/p' "${here}/record.sh")"
nn="$W/nn"; mkdir -p "$nn"
nn_case() {  # <want> <driver-exit-status> <captured-json> <label>
  local want="$1" rc="$2" body="$3" label="$4" got
  printf '%s' "$body" >"$nn/captured.json"
  got="$(script_cell_verdict "$nn/captured.json" "$rc" | cut -f1)"
  [ "$got" = "$want" ] && say PASS "script cell: $label -> $want" \
                       || say FAIL "script cell: $label -> got $got, want $want"
}
nn_case PASS 0 '{"status":0,"headers":{},"body":"ok","effects":{}}' \
  "a driver that exited 0 and wrote a clean capture"
nn_case FAIL 1 '{"status":0,"headers":{},"body":"ok","effects":{}}' \
  "a driver that wrote a plausible capture and then EXITED 1 (no harness_error anywhere)"
nn_case FAIL 2 '{"status":0,"headers":{},"body":"ok","effects":{}}' \
  "a driver that exited 2 with a status of 0 in its capture"
nn_case FAIL 0 '{"status":1,"headers":{},"body":"","effects":{"harness_error":"openssl produced no cert"}}' \
  "a driver that marked a harness_error (still red, whatever it exited)"
nn_case SKIP 0 '{"status":-1,"headers":{},"body":"","effects":{"error":"no backend in the environment"}}' \
  "a -1 named gap from a driver that exited 0"
nn_case FAIL 0 '{"status":-1,"headers":{},"body":"","effects":{"harness_error":"the boot never answered","error":"x"}}' \
  "a driver that gave up AND marked it on a -1 path is a FAILURE, not a named gap (a SKIP row leaves the owed set and is never compared)"
: >"$nn/captured.json"
[ "$(script_cell_verdict "$nn/captured.json" 0 | cut -f1)" = FAIL ] \
  && say PASS "script cell: an empty captured.json -> FAIL" \
  || say FAIL "script cell: an empty captured.json was not refused"

# …and the recorder must ASK, which means capturing the status it used to throw away, and must give
# every selected cell a fresh directory — the stale-file path that made the file test satisfiable by
# the previous run's output.
nn_src="$(cat "${here}/record.sh")"
nn_bad=""
grep -qE 'bash "\$\{data\}/scripts/\$\{sname\}".*\n?' <<<"$nn_src" >/dev/null
grep -q 'script_rc=\$?' <<<"$nn_src" || nn_bad="${nn_bad} the driver's exit status is never captured;"
grep -q 'script_cell_verdict "\$raw/captured.json" "\$script_rc"' <<<"$nn_src" || nn_bad="${nn_bad} the verdict is not asked for that status;"
grep -q 'raw="\$OUT/raw/\$safe"; rm -rf "\$raw"; mkdir -p "\$raw"' <<<"$nn_src" || nn_bad="${nn_bad} the per-cell raw dir is not emptied before the cell runs;"
grep -q 'rm -f "\$OUT/cells/\$safe.json"' <<<"$nn_src" || nn_bad="${nn_bad} a previous run's normalized cell survives into this one;"
[ -z "$nn_bad" ] \
  && say PASS "record.sh captures the driver's exit status, asks script_cell_verdict for the verdict, and gives every selected cell a fresh raw dir and no stale cell file" \
  || say FAIL "record.sh's script path:${nn_bad}"

# (oo) AN `improvement` MAY NOT FORGIVE A CLASS THE DIFFER ITSELF RATES 10. MONEY_CLASSES is keyed
# on the CLASS; the weight a divergence is scored with is keyed on the FAMILY. On the six
# BODY_IS_CONTRACT families (admin.ops, boot.refusal, boot.warning, config.migrate, cli, ops.scrape
# — 604 cells of busbar's corpus) `body` is rated 10 while CLASS_WEIGHT rates it 3 and
# MONEY_CLASSES does not name it, so a four-line `improvement` entry with no changelog line could
# waive the entire stdout of a boot refusal — the only thing such a cell records, weighted 10 in the
# D/W ratio, forgiven by an entry the money guard never inspected. The file's own assertion could
# not catch it because it never looks at the family path.
oo_cells() {  # <out> <family>
  python3 - "$CELLS" "$1" "$2" <<'EOF'
import json,sys
d=json.load(open(sys.argv[1]))
for c in d["cells"]:
    if c["id"] == "self|a|ok":
        c["family"] = sys.argv[3]
json.dump(d, open(sys.argv[2], "w"))
EOF
}
oo_reg() {  # <out> <kind> <changelog-or-empty>
  python3 - "$1" "$2" "$3" <<'EOF'
import json,sys
e = {"id": "OO-1 body wording", "cells": r"^self\|a\|ok$", "classes": ["body"],
     "kind": sys.argv[2], "expected_cells": 1, "owner": "selftest",
     "rationale": "selftest: a body-only acceptance"}
if sys.argv[3]:
    e["changelog"] = sys.argv[3]
json.dump({"accepted": [e]}, open(sys.argv[1], "w"))
EOF
}
cp -R "$FIX" "$W/oo-c"
python3 - "$W/oo-c/cells/self__a__ok.json" <<'EOF'
import json,sys
p=sys.argv[1]; d=json.load(open(p))
d["body"]["json"]["ok"] = "refused: BOOT-141, the download could not be verified"
json.dump(d, open(p, "w"), separators=(",",":"), sort_keys=True)
EOF
oo_run() {  # <cells.json> <register> <out> -> ledger class column for self|a|ok
  bash "${here}/replay.sh" --golden "$FIX" --candidate "$W/oo-c" --out "$3" --cells "$1" \
    --allow-harness-skew --no-check-golden --accepted "$2" --baseline "$W/no-baseline.txt" \
    >"$3.log" 2>&1
  awk -F'\t' '$1=="self|a|ok"{r=$2"/"$3} END{print r}' "$3/ledger.tsv"
}
oo_cells "$W/oo-cells-refusal.json" "boot.refusal"
oo_cells "$W/oo-cells-plain.json"   "self"
oo_reg "$W/oo-reg-improvement.json" improvement ""
oo_reg "$W/oo-reg-breaking.json"    breaking    "Boot refusals name the artifact they could not verify."

oo_a="$(oo_run "$W/oo-cells-refusal.json" "$W/oo-reg-improvement.json" "$W/out-oo-a")"
oo_b="$(oo_run "$W/oo-cells-plain.json"   "$W/oo-reg-improvement.json" "$W/out-oo-b")"
oo_c="$(oo_run "$W/oo-cells-refusal.json" "$W/oo-reg-breaking.json"    "$W/out-oo-c")"

case "$oo_a" in
  FAIL/body) say PASS "an improvement may not forgive \`body\` on a family where the body IS the contract (rated 10) — the cell stays RED" ;;
  *) say FAIL "a body-10 boot.refusal cell was forgiven by an improvement entry: row='${oo_a}'" ;;
esac
case "$oo_b" in
  "PASS/ACCEPTED improvement (OO-1 body wording): body") say PASS "…and the same entry still forgives \`body\` on a family the differ rates it 3 (the rule is the RATING, not a ban on the class)" ;;
  *) say FAIL "a body-3 cell was not forgiven by an improvement entry naming body: row='${oo_b}'" ;;
esac
case "$oo_c" in
  "PASS/ACCEPTED breaking (OO-1 body wording): body") say PASS "…and a \`breaking\` entry with a changelog line forgives it, exactly as it may for every other money class" ;;
  *) say FAIL "a breaking+changelog entry could not forgive a body-10 cell: row='${oo_c}'" ;;
esac
# and the register's owners are told at LOAD which entries just lost reach, rather than finding out
# when a cell goes red months later
grep -q "OO-1 body wording" "$W/out-oo-a.log" \
  && say PASS "the entry that lost reach is named on stderr when the register loads" \
  || say FAIL "no entry was named when the register loaded, so the narrowing is silent"

echo
[ "$skips" -eq 0 ] || printf 'replay selftest: %s case(s) SKIPPED — not proven by this run:%s\n\n' "$skips" "$skipped"
[ "$fails" -eq 0 ] && echo "replay selftest: GREEN${skips:+ (with $skips skipped)}" || { echo "replay selftest: RED ($fails)"; exit 1; }
