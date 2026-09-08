#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Busbar Inc and contributors
#
# The shadow oracle REPLAYER: diff a candidate recording against the golden, cell by cell, and give
# ONE verdict through the same ledger inversion as every other gate in this tree
# (testing/fleet-fixtures/lib.sh + verdict.sh): every owed cell writes exactly one ledger row, and
# verdict.sh is the only place anything is decided. A cell the golden recorded that the candidate
# did not is DID NOT RUN — red in its own column, never green by silence.
#
#   replay.sh --golden <dir> --candidate <dir> --out <dir> [--cells cells.json] [--family <regex>]
#             [--accepted accepted-differences.json] [--allow-harness-skew]
#             [--baseline owed-baseline.txt] [--accepted-gaps accepted-gaps.json] [--rebaseline]
#             [--no-check-golden] [--refuse-extra-candidate]
#
# <out>/  report.json report.md owed.txt owed-gaps.txt diverging.txt extra-candidate.txt
#         corpus-ids.txt selected-ids.txt ledger.tsv
# Exit non-zero on any divergence, any owed cell missing, or zero rows. Exit 2 (before anything is
# compared) if golden and candidate were not proven to come from the same harness revision — see
# harness-rev.sh — unless --allow-harness-skew is given.
#
# Recorder-shrinks-the-gate guard: the golden itself can quietly stop owing a cell it used to pass
# (moved to SKIP/FAIL, or dropped from cells.json entirely) with nobody noticing, because that cell
# simply leaves the owed set — no red anywhere. owed-baseline.txt is the owed ids as of the last
# sign-off; any of those ids the CURRENT golden no longer owes is RED unless accepted-gaps.json
# names it with an owner and a rationale (same discipline as accepted-differences.json: named,
# never silent). A newly-owed id (coverage grew) is fine and just printed. `--rebaseline` accepts
# the current golden's owed set as the new baseline and rewrites the file.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# The PRODUCT'S oracle data (cells.json, golden/, the registers, the cell drivers).
# Defaults to the tool's own directory, which is the in-tree layout this harness grew up
# in; busbar now passes its own testing/shadow-oracle via BUSBAR_ORACLE_DATA.
data="${BUSBAR_ORACLE_DATA:-$here}"
# The PRODUCT this oracle judges. `<tool>/../..` was only ever right while the tool lived
# inside that product; it is now shipped separately, so the root is passed in.
repo="${BUSBAR_ORACLE_PRODUCT_ROOT:-$(cd "${here}/../.." && pwd)}"
# WHERE THE TOOL ITSELF IS — the same contract record.sh states at length. A driver (or a
# re-normalization this replay drives) reaches the tool's own files through this variable, never by
# a path beside itself, and it is exported here as a DEFAULT so the contract does not depend on
# having come in through the product's shim.
export BUSBAR_ORACLE_TOOL_DIR="${BUSBAR_ORACLE_TOOL_DIR:-$here}"

GOLDEN="" CAND="" OUT="" CELLS="${data}/cells.json" FAMILY="" ACCEPTED="${data}/accepted-differences.json"
ALLOW_SKEW=0 BASELINE="${data}/owed-baseline.txt" ACCEPTED_GAPS="${data}/accepted-gaps.json" REBASELINE=0 CHECK_GOLDEN=1
REFUSE_EXTRA=0
while [ $# -gt 0 ]; do
  case "$1" in
    --golden) GOLDEN="$2"; shift 2 ;;
    --candidate) CAND="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --cells) CELLS="$2"; shift 2 ;;
    --family) FAMILY="$2"; shift 2 ;;
    --accepted) ACCEPTED="$2"; shift 2 ;;
    --allow-harness-skew) ALLOW_SKEW=1; shift ;;
    --baseline) BASELINE="$2"; shift 2 ;;
    --accepted-gaps) ACCEPTED_GAPS="$2"; shift 2 ;;
    --rebaseline) REBASELINE=1; shift ;;
    --no-check-golden) CHECK_GOLDEN=0; shift ;;
    --refuse-extra-candidate) REFUSE_EXTRA=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
[ -d "$GOLDEN" ] && [ -d "$CAND" ] && [ -n "$OUT" ] || { echo "usage: $0 --golden <dir> --candidate <dir> --out <dir> [--cells f] [--family re] [--accepted f] [--allow-harness-skew] [--baseline f] [--accepted-gaps f] [--rebaseline] [--no-check-golden] [--refuse-extra-candidate]" >&2; exit 2; }
# absolute paths: verdict.sh runs from the repo root, so a relative --out would make it read an empty
# ledger and (correctly) call the run vacuous
mkdir -p "$OUT"; OUT="$(cd "$OUT" && pwd)"; GOLDEN="$(cd "$GOLDEN" && pwd)"; CAND="$(cd "$CAND" && pwd)"
[ -s "${GOLDEN}/ledger.tsv" ] || { echo "replay: golden has no ledger.tsv — record it first" >&2; exit 2; }
command -v python3 >/dev/null || { echo "replay.sh needs python3" >&2; exit 2; }

# ── the golden's binary provenance, BEFORE anything is compared ──────────────────────────────────
# diff-cells.py already refuses a pair of recordings that did not come from the same HARNESS
# revision. The other half of "this diff is about busbar" is that the golden came from the binary we
# still believe is the golden binary: meta.json records the sha256 of the file record.sh executed,
# and fetch-golden.sh --check-golden re-hashes the cached release artifact against it. Softened only
# when this host has no cached golden binary at all (exit 5) — replaying two recordings does not
# require the release on disk — and skipped only on an EXPLICIT --no-check-golden. A real
# MISMATCH is fatal: the golden was made by some other build that happens to share a version string.
#
# AN ABSENT OR MALFORMED `binary_sha256` IS FATAL TOO, AND USED NOT TO BE. The two extractions above
# were `python3 -c … 2>/dev/null || true`, so a meta.json that did not parse, or that carried no
# `binary_sha256`, or carried it as null / a number / a truncated digest, produced an EMPTY string —
# and the `[ -n "$gbin" ]` guard then read that emptiness as "this golden names no binary" and
# skipped the whole check. Every failure mode of the field looked exactly like the one case the skip
# was written for. That is the wrong default for a provenance check: the reason to skip has to be
# stated, not inferred from a swallowed error. A golden that does not say which binary made it
# cannot be proven to have come from the pinned release, so the run stops and says which field is
# wrong; a caller that genuinely has no binary to check against (the tracked selftest fixtures, a
# recording pair made by hand) passes `--no-check-golden` and says so out loud.
if [ "$CHECK_GOLDEN" = 1 ]; then
  [ -s "${GOLDEN}/meta.json" ] || {
    echo "replay: refusing to compare — ${GOLDEN}/meta.json is missing or empty, so the golden names no binary and no version. Re-record it, or pass --no-check-golden and say why." >&2
    exit 2; }
  # 2>&1: python's sys.exit(<str>) writes the reason to STDERR, and the reason is the whole point of
  # this check — captured here so the refusal below can name the field rather than print an empty one.
  gprov="$(python3 - "${GOLDEN}/meta.json" 2>&1 <<'PY'
import json, re, sys
p = sys.argv[1]
try:
    with open(p, encoding="utf-8") as f:
        m = json.load(f)
except Exception as e:
    sys.exit(f"{p} is not readable JSON: {e}")
if not isinstance(m, dict):
    sys.exit(f"{p} is not a JSON object")
sha = m.get("binary_sha256")
if sha is None:
    sys.exit(f"{p} carries no `binary_sha256`: this golden does not say which binary produced it")
if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
    sys.exit(f"{p} has a malformed `binary_sha256` ({sha!r}): expected 64 lowercase hex characters")
ver = (m.get("version") or "").replace("busbar ", "").strip()
if not ver:
    sys.exit(f"{p} carries no `version`, so there is no release to check {sha[:12]}… against")
print(ver)
PY
)" || {
    echo "replay: refusing to compare — $gprov" >&2
    echo "replay:       the golden's provenance is the only thing that makes this diff about busbar rather than about two unrelated recordings. Fix meta.json, or pass --no-check-golden if you know why it has none." >&2
    exit 2; }
  gver="$gprov"
  cg_out="$(bash "${here}/fetch-golden.sh" --version "$gver" --check-golden "$GOLDEN" 2>&1)"; cg_rc=$?
  case "$cg_rc" in
    0) echo "$cg_out" ;;
    5) echo "replay: golden binary provenance NOT CHECKED on this host (no cached ${gver} binary); comparing anyway" >&2 ;;
    *) echo "$cg_out" >&2
       echo "replay: refusing to compare — the golden on disk was not proven to come from the pinned ${gver} binary. Re-run testing/shadow-oracle/fetch-golden.sh, or pass --no-check-golden if you know why." >&2
       exit 2 ;;
  esac
fi

mkdir -p "$OUT"
export LEDGER="${OUT}/ledger.tsv"; : >"$LEDGER"
# THE LEDGER MACHINERY IS THE TOOL'S, AND ONLY THE PRODUCT'S WHEN THE PRODUCT HAS ONE. `record` and
# the verdict were sourced out of `${repo}/testing/fleet-fixtures/` unconditionally, which was the
# only true statement while the judge lived inside busbar. Shipped separately, `$repo` is whatever
# the caller named with --product-root — and a run that names no product at all (the tool judging a
# fixture, which is the whole point of the extraction) resolved it to the TOOL'S OWN grandparent,
# where no such directory exists. `source` then failed, `record` was not defined, every owed row was
# written by a command that did not exist, and this script exited 127 with an empty ledger. The
# package already ships fleet_fixtures/lib.sh and verdict.sh for exactly this reason — harness-rev.sh
# has looked in both places since the extraction — so resolve them the same way here.
# The PRODUCT'S copy still wins where there is one: an in-tree layout must keep sourcing the file
# its recordings were made under, not a shipped copy that may have moved on.
_fleet() {  # _fleet <basename> -> the path to source/run, product's copy first
  if [ -f "${repo}/testing/fleet-fixtures/$1" ]; then printf '%s\n' "${repo}/testing/fleet-fixtures/$1"
  else printf '%s\n' "${here}/fleet_fixtures/$1"; fi
}
# shellcheck source=fleet_fixtures/lib.sh
source "$(_fleet lib.sh)"

diff_args=(--golden "$GOLDEN" --candidate "$CAND" --out "$OUT" --cells "$CELLS" --accepted "$ACCEPTED")
[ -z "$FAMILY" ] || diff_args+=(--family "$FAMILY")
[ "$ALLOW_SKEW" != 1 ] || diff_args+=(--allow-harness-skew)
[ "$REFUSE_EXTRA" != 1 ] || diff_args+=(--refuse-extra-candidate)
rows="$(python3 "${here}/diff-cells.py" "${diff_args[@]}")"
rc=$?
if [ "$rc" -ne 0 ]; then
  if [ "$rc" -eq 2 ]; then exit 2; fi   # diff-cells.py already printed the reason to stderr
  echo "replay: diff-cells.py failed" >&2
  exit 1
fi

extra_ids=""
while IFS=$'\t' read -r id status classes first; do
  [ -n "$id" ] || continue
  record "$id" "$status" "$classes" "$first" >/dev/null
  # An `extra.candidate` row is about a cell NOTHING compared (see diff-cells.py). It is owed to the
  # verdict all the same: a row nobody expects is a row verdict.sh's resolver never reads, so
  # --refuse-extra-candidate would have written FAIL rows that decided nothing.
  case "$classes" in extra.candidate) extra_ids="${extra_ids}${id}
" ;; esac
done <<<"$rows"

# ── owed-baseline: the golden must not silently stop owing a cell it used to ─────────────────────
if [ "$REBASELINE" = 1 ]; then
  sort -u "${OUT}/owed.txt" >"$BASELINE"
  echo "rebaselined ${BASELINE}: $(wc -l <"$BASELINE" | tr -d ' ') owed ids"
  baseline_rows=""
elif [ -s "$BASELINE" ]; then
  baseline_rows="$(python3 - "$OUT" "$BASELINE" "$ACCEPTED_GAPS" <<'PY'
import json, os, re, sys

out, baseline_path, gaps_path = sys.argv[1], sys.argv[2], sys.argv[3]


def read_ids(p):
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip()]


owed = set(read_ids(os.path.join(out, "owed.txt")))
# The whole corpus, and the part of it THIS run selected — written by diff-cells.py on every run.
# Without them "the golden no longer owes this id" and "this run never looked at this id" are the
# same silence, which is the hole below.
corpus = set(read_ids(os.path.join(out, "corpus-ids.txt")))
selected = set(read_ids(os.path.join(out, "selected-ids.txt")))
gap_reason = {}
gpath = os.path.join(out, "owed-gaps.txt")
if os.path.exists(gpath):
    with open(gpath, encoding="utf-8") as f:
        for ln in f:
            parts = ln.rstrip("\n").split("\t")
            if parts and parts[0]:
                gap_reason[parts[0]] = "\t".join(parts[1:])
# "scope" is every id this run has an opinion about (owed now, or a named golden gap now). A
# baseline id outside scope (e.g. this run used --family to cover only part of cells.json) is
# neither confirmed nor regressed here — silently skipped, not a false alarm.
#
# THAT SKIP USED TO SWALLOW THE CASE THIS GUARD NAMES FIRST. `owed` and `gap_reason` are both
# derived from cells.json, so a cell DELETED FROM cells.json is in neither — and `cid not in scope`
# then dropped it, silently, which is the one outcome the header above says must never happen ("or
# dropped from cells.json entirely"). The default reason string on the branch below was provably
# unreachable. Reproduced against this very script: remove a PASSing, baselined cell from the
# corpus and the run reports GREEN with no row, no stderr line and no mention of the loss anywhere.
#
# So the two silences are separated, using the corpus/selection files diff-cells.py writes:
#   in the corpus but not selected  -> genuinely out of this run's scope (--family), skip
#   NOT IN THE CORPUS AT ALL        -> the id left cells.json. RED, unless accepted-gaps names it.
# An id RENAMED rather than removed is now visible from both ends: the old id is red here, and the
# new one lands in the differ's `extra.candidate` channel instead of only being praised as "new
# coverage" on stderr.
scope = owed | set(gap_reason)


def out_of_scope_reason(cid):
    """Why this baselined id produced no verdict: a real gap in coverage, or a filtered run.
    Returns None when the run legitimately has no opinion."""
    # No corpus file (an older --out, a caller driving diff-cells.py by hand): fall back to the old
    # behaviour rather than turning every baseline id red on a missing artifact.
    if not corpus:
        return None
    if cid not in corpus:
        return "no longer present in cells.json"
    if cid not in selected:
        return None                      # --family / --id-filter: not this run's question
    return ("present in cells.json and selected by this run, but the run produced neither an owed "
            "row nor a named gap for it")

accepted = []
if os.path.exists(gaps_path):
    try:
        doc = json.load(open(gaps_path, encoding="utf-8"))
    except Exception as e:
        sys.exit(f"accepted-gaps: {gaps_path} is not valid JSON: {e}")
    for e in doc.get("accepted", []):
        if "owner" not in e or "rationale" not in e or not e.get("cells"):
            sys.exit(f"accepted-gaps: entry {e.get('id', '?')!r} needs cells, owner and rationale — named, never silent")
        accepted.append({"id": e.get("id", e["cells"]), "rx": re.compile(e["cells"]), "owner": e["owner"], "rationale": e["rationale"]})


def find_accept(cid):
    for e in accepted:
        if e["rx"].search(cid):
            return e
    return None


baseline = read_ids(baseline_path)
for cid in sorted(owed - set(baseline)):
    sys.stderr.write(f"owed-baseline: new coverage (not yet in the baseline): {cid}\n")

for cid in baseline:
    if cid in owed:
        continue
    if cid not in scope:
        reason = out_of_scope_reason(cid)
        if reason is None:
            continue
    else:
        reason = gap_reason.get(cid, "no longer present in cells.json")
    e = find_accept(cid)
    if e:
        print(f"{cid}\tPASS\tACCEPTED named gap ({e['id']}, owner {e['owner']}): {e['rationale']}\t{reason}")
    else:
        print(f"{cid}\tFAIL\towed-baseline regression\tgolden no longer owes this id (was PASS at baseline; now: {reason}) — name it in accepted-gaps.json with an owner and rationale, or if intentional run replay.sh --rebaseline")
PY
)"
  brc=$?
  [ "$brc" -eq 0 ] || { echo "replay: owed-baseline check failed" >&2; exit 1; }
else
  baseline_rows=""
fi

baseline_ids=""
if [ -n "$baseline_rows" ]; then
  while IFS=$'\t' read -r id status classes first; do
    [ -n "$id" ] || continue
    record "$id" "$status" "$classes" "$first" >/dev/null
    baseline_ids="${baseline_ids}${id}
"
  done <<<"$baseline_rows"
fi

# ONE ID PER LINE, and the newlines are the point. These ids are cells.json ids, and 84 of them
# contain a space (`…|GET /.well-known/agent-card.json|ok`). Flattened to a space-separated string
# they came apart in the verdict's owed loop into fragments that owe nothing, so the oracle's own
# gate reported hundreds of phantom `did not run` rows. verdict.sh reads a newline-separated owed
# set line by line; keep it that way.
OWED="$(<"${OUT}/owed.txt")"
echo
echo "golden gaps (recorded SKIP/FAIL on the golden, not owed): $(wc -l <"${OUT}/owed-gaps.txt" | tr -d ' ')"
echo "candidate cells nothing compared (extra.candidate, not red by default): $(wc -l <"${OUT}/extra-candidate.txt" | tr -d ' ')"
GATE_NAME="shadow oracle vs golden" EXPECTED_IDS="${OWED}
${baseline_ids}${extra_ids}" LEDGER="$LEDGER" bash "$(_fleet verdict.sh)"
rc=$?
echo "report: ${OUT}/report.md"
exit $rc
