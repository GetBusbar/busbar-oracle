#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Busbar Inc and contributors
#
# THE PLANE DRIVER: record ONE mcp/a2a cell by driving the PRODUCT'S OWN CONFORMANCE RIG SUBJECT.
#
#   plane-subject.sh <plane> <cell-json> <raw-dir>
#
# WHAT THIS REPLACES, AND WHY IT HAD TO GO. record.sh used to refuse these two planes twice: at the
# argument gate (`case "$PLANE" in llm|core|streams|all`) and again per cell
# (`mcp|a2a) record "$id" SKIP "UNSUPPORTED: ${plane} is proven by its conformance rig, not recorded
# here" "named gap on the golden, never owed"`). Both were decisions about the PLANE, taken before
# anything looked at the cell — so 1,382 cells were unowed by category, and the phrase "never owed"
# meant nothing downstream could ever notice that the category had swallowed a cell the rig can in
# fact drive. That is the shape of skip the ledger's kind rule refuses: a gap must be NAMED, owed,
# and attached to the thing that would close it.
#
# THE RIG IS THE PRODUCT'S, AND IT IS INVOKED THE WAY THE PRODUCT'S OWN TESTS INVOKE IT. Each plane's
# subject ships a library, `<repo>/scripts/<plane>-subject/h2-lib.sh`, and every one of that plane's
# gating scenarios (scripts/a2a-subject/h2-*.sh, scripts/mcp-subject/h2-*.sh) sources it and drives
# the same eight helpers: h2_boot, h2_stop, h2_mint, h2_bind, h2_call, h2_usage, h2_egress_count,
# h2_audit_max_seq. This driver sources exactly that library and drives exactly those helpers. It
# does NOT reimplement a plane: it has no config template, no agent/tool registration, no card
# signing, no audience-token minting of its own. If the rig's boot changes, this changes with it,
# which is the whole reason to go through the rig rather than beside it.
#
# THE PATH IS DERIVED FROM THE PLANE NAME, never from a table of planes this file knows about:
# `<repo>/scripts/<plane>-subject/h2-lib.sh`. A plane whose rig is not in the tree is reported as
# exactly that, by path, and the recorder turns it into a needs_fixture gap NAMING the rig — never a
# skip that says the plane is proven somewhere else.
#
# WHAT THE RIG CAN DRIVE IS A FACT ABOUT THE RIG, AND IT IS STATED, NOT ASSUMED. `h2_call` speaks one
# request shape per plane (a2a: a `jsonrpc` `message/send` at the fronted agent; mcp: a
# `streamable-http` `tools/call` at the fronted server), and the rig's boot arranges one upstream. A
# cell on another transport, another method, or an outcome the rig has no mechanism for is NOT
# driven and NOT skipped: it exits PLANE_NO_SCENARIO with the reason, and the recorder records a
# needs_fixture gap naming the rig entry point that would have to grow the scenario. See
# plane_scenario() for the whole table, which is the one place that decides it.
#
# EXIT STATUS
#   0                       <raw-dir>/captured.json written — the recorder's own capture shape, by
#                           the recorder's own capture.py, from the same five artifacts every other
#                           driver hands it (headers, status, body, before/, after/) plus the rig's
#                           egress records.
#   3  PLANE_RIG_MISSING    no rig subject for this plane. stdout: the path that was looked for.
#   4  PLANE_NO_SCENARIO    the rig is here but drives no scenario for THIS cell. stdout: why,
#                           naming the rig entry point.
#   1                       the rig broke. stdout: what went wrong. Never a recorded cell.
set -uo pipefail
PLANE_RIG_MISSING=3
PLANE_NO_SCENARIO=4

ps_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ps_repo="${BUSBAR_ORACLE_PRODUCT_ROOT:-$(cd "${ps_here}/../.." && pwd)}"

# ── WHICH CELLS EACH RIG SUBJECT CAN ACTUALLY DRIVE ─────────────────────────────────────────────
# One table, one decision. Everything it does not name is a named gap, so growing the rig is the
# only way to grow the recorded set — a cell can never become "recorded" by a driver quietly
# guessing at a shape the rig does not serve.
#
#   transport/method   what h2_call sends. Anything else is a request the rig has no client for.
#   obligation         `handle` is busbar ANSWERING a caller, which is what h2_call makes it do. The
#                      `issue` half of the same exchange is busbar as the upstream's client; the rig
#                      observes it as egress (and this driver folds that egress into the cell), but
#                      it is not a request anything here can send, so those rows stay gaps.
#   outcome            the mechanism each refusal needs, all of them the rig's own:
#                        ok               h2_mint + h2_bind + h2_call
#                        unauthenticated  the same call with no bearer at all
#                        out_of_scope     `allowed_pools: []` — the documented C6 cross-kind rule an
#                                         explicit empty list triggers (h2-verify-refusal.sh's own
#                                         mechanism, quoted there at length)
#                        over_budget      a `requests: 1` group and a second call
#                                         (h2-admit-refusal.sh's mechanism)
#                        malformed        a body the plane cannot decode
#                        upstream_down    the rig's mock honours a `down` control file. WHETHER IT
#                                         DOES IS ASKED, NOT REMEMBERED — see ps_rig_can below.
#                        undecodable-body a2a's own name for the decode refusal, one cell of the
#                                         reachability pair
#
# ── ASK THE RIG. DO NOT REMEMBER WHAT IT COULD NOT DO LAST TIME. ────────────────────────────────
#
# Two arms of this table used to be HARD-CODED facts about the product tree, decided here and true
# only until somebody changed the rig:
#
#   * `mcp:upstream_down` — "h2-mock-upstream.mjs honours no fault control". That was measured and
#     correct when it was written, and it stopped being true the day the mcp rig grew the control its
#     a2a sibling always had (h2_boot's H2_CONTROL_FILE, handed to the mock, and
#     h2-upstream-outage.sh, which is the scenario that keeps it honest). The product half closed and
#     the row stayed a gap, because the tool was still reciting a measurement instead of taking one.
#   * `a2a:no-agents-configured` — "h2_boot always registers and approves the `probe` agent". h2_boot
#     has since taken a third argument, and the configuration it reaches TURNED OUT NOT TO BE THE
#     CELL'S: booted with no `agents:` key the plane mounts no routes at all, so a submission is
#     refused 401 in AUTH, upstream of the meter, and the key's usage reads `requests: 0` — while the
#     cell's own `why` states `{"requests": 1}`, a caller who drew a slot and bought nothing. Two
#     configurations wore one name. The sharper one, and the one this row is actually about, is a
#     REGISTERED agent whose LANE IS ABSENT: fronted, admitted, metered, and resolving to nothing.
#
# A capability is a fact about the tree this tool is POINTED AT, so it is read off that tree every
# time the question is asked. `ps_rig_can` is the only place that asks, a gap is printed only when it
# answers no, and when it answers yes the ordinary gates below decide — which is how a cell that is
# ALSO blocked by something more fundamental (an `issue` obligation, a transport with no client) goes
# on being refused for that reason and not for a rig capability it no longer lacks.
ps_rig_can() {  # <plane> <capability> -> 0 the rig in this tree exposes it, 1 it does not
  # DECLARED THEN ASSIGNED, not both on the `local` line: `local a="$1" b="${a}x"` reads `a` before
  # bash has finished the declaration under `set -u`, and this function's first act would abort the
  # recorder on an unbound variable rather than answer a question about the rig.
  local plane="$1" cap="$2" dir lib boot
  dir="${ps_repo}/scripts/${plane}-subject"
  lib="${dir}/h2-lib.sh"
  [ -f "$lib" ] || return 1
  boot="$(sed -n '/^h2_boot()/,/^}/p' "$lib")"
  [ -n "$boot" ] || return 1
  case "$cap" in
    upstream-fault)
      # THE MECHANISM IS THE CONTROL FILE, and it takes all three of these to be a mechanism rather
      # than a variable: h2_boot must SET H2_CONTROL_FILE, must HAND it to the mock it starts (a
      # control no mock reads is a path), and the plane must ship a scenario that ARMS it — "a
      # capability with no caller is a claim", which is the product's own sentence for why the mcp
      # control shipped together with h2-upstream-outage.sh. All three are read off the tree.
      grep -q 'H2_CONTROL_FILE=' <<<"$boot" || return 1
      grep -qE 'node .*"\$H2_CONTROL_FILE"' <<<"$boot" || return 1
      # …and a scenario that ARMS it. The library itself is excluded by name: it is where the
      # variable is DEFINED, so counting it would make every rig that has the variable look like a
      # rig that uses it.
      grep -l 'H2_CONTROL_FILE' "${dir}"/h2-*.sh 2>/dev/null | grep -qv '/h2-lib\.sh$' || return 1
      return 0 ;;
    lane-absent)
      # h2_boot's own argument guard is the contract: it names the configurations it can boot and
      # refuses every other word. So the question "can this rig boot a registered agent whose lane is
      # absent?" is asked of the guard, by the name the row needs — never by counting arguments or by
      # assuming that any third argument must be this one. `none` is NOT this: it boots a deployment
      # that fronts nothing, which is the OTHER configuration that was wearing this row's name.
      sed -n '/case "\$agents" in/,/esac/p' <<<"$boot" | grep -qE '(^|[[:space:]|(])lane-absent([[:space:]|)])' || return 1
      return 0 ;;
    *) return 1 ;;
  esac
}

plane_scenario() {  # <plane> <cell-json> -> "<scenario>" | "-<TAB><why>"
  local plane="$1" cell="$2" transport method outcome obligation want_transport want_method entry
  transport="$(jq -r '.transport // ""' <<<"$cell")"
  method="$(jq -r '.method // ""' <<<"$cell")"
  outcome="$(jq -r '.outcome // ""' <<<"$cell")"
  obligation="$(jq -r '.obligation // ""' <<<"$cell")"
  case "$plane" in
    a2a) want_transport="jsonrpc"; want_method="SendMessage"
         entry="h2_call in scripts/a2a-subject/h2-lib.sh (a jsonrpc message/send at the fronted agent)" ;;
    mcp) want_transport="streamable-http"; want_method="tools/call"
         entry="h2_call in scripts/mcp-subject/h2-lib.sh (a streamable-http tools/call at the fronted server)" ;;
    *)   printf -- '-\tscripts/%s-subject/h2-lib.sh may be in the tree, but plane-subject.sh states no request shape for plane %s — the rig it would be driven through is scripts/%s-subject/\n' "$plane" "$plane" "$plane"; return 0 ;;
  esac
  if [ "$transport" != "$want_transport" ]; then
    printf -- '-\t%s speaks %s only; this cell is %s\n' "$entry" "$want_transport" "${transport:-<none>}"; return 0
  fi
  if [ "$method" != "$want_method" ]; then
    printf -- '-\t%s sends %s only; this cell is %s\n' "$entry" "$want_method" "${method:-<none>}"; return 0
  fi
  # ── WHAT THE RIG CAN DO, ASKED OF THE RIG. A gap here ONLY when the probe answers no. ─────────
  case "$plane:$outcome" in
    *:upstream_down)
      if ! ps_rig_can "$plane" upstream-fault; then
        printf -- '-\th2_boot in scripts/%s-subject/h2-lib.sh exposes no fault control (H2_CONTROL_FILE, set at boot and handed to the mock it starts, with a scenario beside it that arms it), so this plane has no way to make its upstream refuse — and a cell driven against a healthy upstream would freeze a success under the name of an outage\n' "$plane"
        return 0
      fi ;;
    a2a:no-agents-configured)
      if ! ps_rig_can a2a lane-absent; then
        printf -- '-\th2_boot in scripts/a2a-subject/h2-lib.sh boots no configuration with a REGISTERED agent whose LANE IS ABSENT — the one this row is about, where the submission is fronted, admitted and METERED and draws a requests-only row. Its `none` argument boots a deployment that fronts nothing, which is a different configuration wearing the same name: measured on 1.6.0 the submission is refused 401 in auth, upstream of the meter, and usage reads `requests: 0`. The argument this row needs is `lane-absent`\n'
        return 0
      fi ;;
    a2a:undecodable-body) printf 'malformed\n'; return 0 ;;
  esac
  case "$obligation" in
    handle) ;;
    *) printf -- '-\t%s makes busbar HANDLE a call; this cell is the `%s` half of the exchange, which the rig observes as egress but cannot send\n' "$entry" "${obligation:-<none>}"; return 0 ;;
  esac
  case "$outcome" in
    ok|unauthenticated|out_of_scope|over_budget|malformed) printf '%s\n' "$outcome" ;;
    upstream_down) printf 'upstream_down\n' ;;   # both planes: the control was PROBED above, per plane
    # Reachable ONLY because the arm above returned when the probe said the rig cannot boot it: this
    # is the configuration with a REGISTERED agent whose lane is absent, and `no_lane` is the name
    # main() boots it under. No second probe, and no code path without a caller.
    no-agents-configured) printf 'no_lane\n' ;;
    *) printf -- '-\t%s has no mechanism for outcome %s\n' "$entry" "${outcome:-<none>}" ;;
  esac
}

# ── the per-plane request h2_call sends ─────────────────────────────────────────────────────────
# The rig's h2_call prints "<status> <body>" and nothing else; a recorded cell needs the response
# HEADERS and the raw body bytes as files, so the request is issued here. It is the SAME endpoint
# variable, the same protocol headers and the same JSON-RPC envelope the rig's own h2_call builds —
# read them side by side, they are one request — with two things h2_call has no reason to offer: the
# bearer may be absent (the `unauthenticated` scenario) and the body may be undecodable (the
# `malformed` one).
ps_endpoint() { case "$1" in a2a) printf '%s' "$H2_PLANE_URL" ;; mcp) printf '%s' "$H2_CANON" ;; esac; }
ps_protocol_headers() {  # <plane> -> curl -H args
  case "$1" in
    mcp) printf '%s\n' "mcp-method: tools/call" "mcp-protocol-version: 2026-07-28" "Mcp-Name: probe_ping" ;;
  esac
}
ps_body() {  # <plane> <label>
  case "$1" in
    a2a) printf '{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":{"role":"user","parts":[{"text":"%s"}]}}}' "$2" ;;
    mcp) printf '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"probe_ping","arguments":{"label":"%s"},"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}' "$2" ;;
  esac
}
# The undecodable body, in the one shape every plane agrees is not a document.
PS_MALFORMED_BODY='{this is not json'

# ── the effect snapshots, in the recorder's own shape ───────────────────────────────────────────
# capture.py reads usage.json / audit.json / metrics.txt out of a before/ and an after/ directory and
# takes the delta. A file it cannot read becomes {"unavailable": true} in the golden — visible, never
# a silent zero — so these writes follow record.sh's own `-o … || rm -f` discipline exactly: a failed
# read leaves the file ABSENT rather than empty.
ps_snapshot() {  # <dir> <key-id> <plane-token-or-empty>
  local d="$1" kid="$2" tok="$3"; mkdir -p "$d"
  curl -fsS -m 5 -H "Authorization: Bearer ${H2_ADMIN_TOKEN}" \
    "http://127.0.0.1:${H2_ADMIN_PORT}/api/v1/admin/keys/${kid}/usage" -o "$d/usage.json" 2>/dev/null || rm -f "$d/usage.json"
  curl -fsS -m 5 -H "Authorization: Bearer ${H2_ADMIN_TOKEN}" \
    "http://127.0.0.1:${H2_ADMIN_PORT}/api/v1/admin/audit?limit=1000" -o "$d/audit.json" 2>/dev/null || rm -f "$d/audit.json"
  if [ -n "$tok" ]; then
    curl -fsS -m 5 -H "Authorization: Bearer ${tok}" \
      "http://127.0.0.1:${H2_DATA_PORT}/metrics" -o "$d/metrics.txt" 2>/dev/null || rm -f "$d/metrics.txt"
  fi
}

ps_fail() { printf '%s\n' "$1"; exit 1; }

main() {
  [ $# -eq 3 ] || { echo "usage: $0 <plane> <cell-json> <raw-dir>" >&2; exit 2; }
  local plane="$1" cell="$2" raw="$3"
  command -v jq >/dev/null || { echo "plane-subject.sh needs jq" >&2; exit 2; }
  mkdir -p "$raw"

  local lib="${ps_repo}/scripts/${plane}-subject/h2-lib.sh"
  [ -f "$lib" ] || { printf '%s\n' "$lib"; exit "$PLANE_RIG_MISSING"; }

  local scenario why
  scenario="$(plane_scenario "$plane" "$cell")"
  case "$scenario" in
    -*) why="${scenario#-$(printf '\t')}"; printf '%s\n' "$why"; exit "$PLANE_NO_SCENARIO" ;;
  esac

  # The rig picks its binary out of its own plane-named variable, exactly as the product's scenarios
  # let it: point BOTH at the binary this recording is of, so a rig can never quietly record a
  # different build than the one the ledger names.
  export A2A_SUBJECT_BUSBAR_BIN="${BUSBAR_BIN:?plane-subject.sh needs BUSBAR_BIN}"
  export MCP_SUBJECT_BUSBAR_BIN="$BUSBAR_BIN"
  # shellcheck source=/dev/null
  source "$lib" || ps_fail "the ${plane} rig library at ${lib} could not be sourced"
  trap 'h2_stop' EXIT

  # over_budget needs a spent bucket; everything else needs a budget big enough not to be the subject.
  local groups
  case "$scenario" in
    over_budget) groups="groups:
  h2-oracle:
    limits:
      - { requests: 1, per: day }" ;;
    *) groups="groups:
  h2-oracle:
    limits:
      - { budget: 1000000, per: day }" ;;
  esac
  # THE BOOT ARGUMENT IS THE SCENARIO'S, and only `no_lane` has one: it is the a2a configuration
  # with a registered agent whose LANE IS ABSENT, which plane_scenario only names after ps_rig_can
  # has read that argument out of this rig's own h2_boot. Every other scenario boots exactly as it
  # always did, with no third argument at all.
  local -a boot_args=("${raw}/rig" "$groups")
  [ "$scenario" != no_lane ] || boot_args+=(lane-absent)
  h2_boot "${boot_args[@]}" >"${raw}/rig-boot.log" 2>&1 \
    || ps_fail "the ${plane} rig's h2_boot failed: $(tail -c 400 "${raw}/rig-boot.log" | tr '\n' ' ')"

  # The principal. `out_of_scope` mints through the admin API with an EXPLICIT EMPTY allowed_pools —
  # the same zero-entitlement key h2-verify-refusal.sh mints, and for the reason it documents.
  local kid tok bound=""
  if [ "$scenario" = out_of_scope ]; then
    local mint_resp
    mint_resp="$(curl -sS -m 10 -X POST "http://127.0.0.1:${H2_ADMIN_PORT}/api/v1/admin/keys" \
      -H "Authorization: Bearer $H2_ADMIN_TOKEN" -H 'Content-Type: application/json' \
      -d '{"name":"oracle-noscope","group":"h2-oracle","allowed_pools":[]}')"
    kid="$(jq -r '.id // ""' <<<"$mint_resp")"; tok="$(jq -r '.token // ""' <<<"$mint_resp")"
    [ -n "$tok" ] || ps_fail "the ${plane} rig's admin API would not mint a zero-entitlement key: ${mint_resp}"
  else
    read -r kid tok <<<"$(h2_mint h2-oracle)"
    [ -n "${tok:-}" ] || ps_fail "the ${plane} rig's h2_mint returned no token"
  fi
  # A key is minted even for `unauthenticated`: the cell's contract includes that NOTHING was drawn
  # against a real principal, which cannot be recorded without one to read.
  [ "$scenario" = unauthenticated ] || bound="$(h2_bind "$tok")"
  [ -n "$bound" ] || [ "$scenario" = unauthenticated ] || ps_fail "the ${plane} rig's h2_bind produced no audience-bound token"

  # over_budget's first call is the SETUP that spends the bucket, and it is not the cell: it runs
  # before the `before` snapshot, so nothing it charged appears in this cell's delta.
  if [ "$scenario" = over_budget ]; then
    h2_call "$bound" "oracle-prime" >"${raw}/prime.log" 2>&1 \
      || ps_fail "the ${plane} rig's priming call failed: $(tail -c 200 "${raw}/prime.log" | tr '\n' ' ')"
  fi
  # upstream_down: the a2a rig's mock agent refuses while its control file reads `down`
  # (h2-mock-agent.mjs). Written before the snapshot and cleared after, so the outage belongs to this
  # cell and to nothing after it.
  if [ "$scenario" = upstream_down ]; then
    printf 'down' >"$H2_CONTROL_FILE" || ps_fail "could not arm the ${plane} rig's agent control file"
  fi

  ps_snapshot "${raw}/before" "$kid" "${bound:-}"
  ls "$H2_EGRESS_DIR" 2>/dev/null | LC_ALL=C sort >"${raw}/egress.before"

  local body_file="${raw}/request.body"
  if [ "$scenario" = malformed ]; then printf '%s' "$PS_MALFORMED_BODY" >"$body_file"
  else ps_body "$plane" "oracle" >"$body_file"; fi
  local -a hdr=(-H 'content-type: application/json')
  while IFS= read -r h; do [ -n "$h" ] && hdr+=(-H "$h"); done < <(ps_protocol_headers "$plane")
  [ -z "$bound" ] || hdr+=(-H "authorization: Bearer ${bound}")
  local status curl_rc
  status="$(curl -sS -m 30 -X POST "$(ps_endpoint "$plane")" "${hdr[@]}" \
    --data-binary "@$body_file" -D "${raw}/headers" -o "${raw}/body" -w '%{http_code}' 2>"${raw}/curl.err")"; curl_rc=$?
  case "$curl_rc:$status" in 0:*|18:[1-5]??|56:[1-5]??) ;; *) status="000" ;; esac
  [ "$status" != "000" ] || ps_fail "no HTTP response from the ${plane} rig's subject: $(tr '\n' ' ' <"${raw}/curl.err" | tail -c 200)"

  ps_snapshot "${raw}/after" "$kid" "${bound:-}"
  [ "$scenario" != upstream_down ] || rm -f "$H2_CONTROL_FILE"
  ls "$H2_EGRESS_DIR" 2>/dev/null | LC_ALL=C sort >"${raw}/egress.after"
  local -a egress=()
  while IFS= read -r f; do [ -n "$f" ] && egress+=("$H2_EGRESS_DIR/$f"); done \
    < <(LC_ALL=C comm -13 "${raw}/egress.before" "${raw}/egress.after")

  python3 "${BUSBAR_ORACLE_TOOL_DIR:-$ps_here}/capture.py" "${raw}/headers" "$status" "${raw}/body" \
    "${raw}/before" "${raw}/after" "${egress[@]}" >"${raw}/captured.json" 2>"${raw}/capture.err" \
    || ps_fail "capture.py failed on the ${plane} rig's answer: $(tail -c 300 "${raw}/capture.err")"
  printf '%s\n' "$status" >"${raw}/status"
  printf '%s\n' "$kid" >"${raw}/key-id"
  printf '%s\n' "$scenario"
}

main "$@"
