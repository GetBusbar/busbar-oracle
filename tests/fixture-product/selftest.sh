#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Busbar Inc and contributors
#
# THE FIXTURE PRODUCT'S OWN SELF-TEST: prove this directory is a working product, not a directory
# of files with the right names.
#
# The oracle's CI runs three jobs against tests/fixture-product/data -- the fixture gate, the
# replayer's self-tests and the harness-revision determinism check. All three read the data and none
# of them ever runs the binary, so all three would stay green against a product that cannot answer a
# request, a cells.json that names a driver which is not there, or a golden that records a version
# string the binary does not print. This script is the job that would go red first.
#
# It has two halves:
#   (1) THE BINARY IS EXERCISED. `--version` is asserted against the exact string, `--validate` is
#       asserted to accept a real config AND to refuse a shapeless one (a validator that always
#       exits 0 proves nothing about the recorder that calls it), and the listener is started and
#       talked to on a real socket.
#   (2) THE DATA IS CHECKED AGAINST THE BINARY AND AGAINST ITSELF. The golden's recorded bodies are
#       compared with what the listener JUST ANSWERED, the digest pins are verified by hashing the
#       files they pin, the cell corpus is required to name drivers that exist, and the two static
#       driver rules the oracle enforces over any product's scripts/ are enforced here too, so a
#       fixture that would fail replay-selftest fails here first with a message about the driver.
#
# Exit non-zero on the first failure, naming what failed.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
data="${here}/data"
stub="${here}/busbar-stub"

fails=0
ok()  { printf '  ok    %s\n' "$1"; }
bad() { printf '  FAIL  %s\n' "$1"; fails=$((fails + 1)); }

W="$(mktemp -d "${TMPDIR:-/tmp}/fixture-product-selftest.XXXXXX")"
STUB_PID=""
cleanup() { [ -n "$STUB_PID" ] && kill "$STUB_PID" 2>/dev/null; rm -rf "$W"; }
trap cleanup EXIT

echo "fixture product: the binary answers"

[ -x "$stub" ] || { echo "FAIL  ${stub} is not executable — chmod +x it" >&2; exit 1; }

# (1a) --version, asserted against the exact bytes. The golden recording carries this string as a
# cell body, so "something stable" is not enough: it has to be THIS.
ver="$("$stub" --version 2>"$W/version.err")"
if [ "$ver" = "busbar 0.0.0-fixture" ]; then
  ok "--version prints 'busbar 0.0.0-fixture'"
else
  bad "--version printed '${ver}' (wanted 'busbar 0.0.0-fixture'); the golden's cli|--version cell records the old string and every replay against it is now a diff about the fixture"
fi

# (1b) a real config is accepted…
port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
printf 'listen: "127.0.0.1:%s"\n' "$port" >"$W/config.yaml"
if "$stub" --validate "$W/config.yaml" >"$W/validate.log" 2>&1; then
  ok "--validate accepts a well-formed config (exit 0)"
else
  bad "--validate refused a well-formed config: $(tail -2 "$W/validate.log")"
fi

# (1c) …and a shapeless one is refused. Without this the whole of (1b) is satisfied by `exit 0`.
printf 'nothing: here\n' >"$W/bad.yaml"
if "$stub" --validate "$W/bad.yaml" >"$W/validate-bad.log" 2>&1; then
  bad "--validate ACCEPTED a config that names no listen address, so it accepts everything and proves nothing"
else
  ok "--validate refuses a config with no listen address"
fi
if "$stub" --validate "$W/does-not-exist.yaml" >/dev/null 2>&1; then
  bad "--validate ACCEPTED a config path that does not exist"
else
  ok "--validate refuses a config that is not there"
fi

# (1d) the listener. Started for real, polled until it binds, and asked the three things a cheap
# cell asks. Answers are captured to files so the data half below can compare the golden against
# what this binary ACTUALLY said, rather than against what the golden says it said.
"$stub" --serve "$W/config.yaml" >"$W/serve.log" 2>&1 &
STUB_PID=$!
up=0
for _ in $(seq 1 100); do
  if curl -sS -m 2 -o "$W/healthz.body" -w '%{http_code}' "http://127.0.0.1:${port}/healthz" >"$W/healthz.code" 2>/dev/null; then
    [ "$(cat "$W/healthz.code")" = 200 ] && { up=1; break; }
  fi
  kill -0 "$STUB_PID" 2>/dev/null || break
  sleep 0.1
done
if [ "$up" = 1 ] && [ "$(cat "$W/healthz.body")" = "ok" ]; then
  ok "the listener came up on 127.0.0.1:${port} and GET /healthz answers 200 ok"
else
  bad "the listener never answered GET /healthz (code=$(cat "$W/healthz.code" 2>/dev/null), body=$(cat "$W/healthz.body" 2>/dev/null)): $(tail -3 "$W/serve.log" 2>/dev/null)"
fi

if [ "$up" = 1 ]; then
  code="$(curl -sS -m 5 -o "$W/models.body" -w '%{http_code}' "http://127.0.0.1:${port}/v1/models")"
  if [ "$code" = 200 ] && grep -q 'm-fixture' "$W/models.body"; then
    ok "GET /v1/models answers 200 and names m-fixture"
  else
    bad "GET /v1/models answered ${code}: $(head -c 200 "$W/models.body")"
  fi

  code="$(curl -sS -m 5 -o "$W/chat.body" -w '%{http_code}' -X POST "http://127.0.0.1:${port}/v1/chat/completions" \
    -H 'Content-Type: application/json' -d '{"model":"m-fixture","messages":[{"role":"user","content":"ping"}]}')"
  if [ "$code" = 200 ]; then
    ok "POST /v1/chat/completions answers 200"
  else
    bad "POST /v1/chat/completions answered ${code}: $(head -c 200 "$W/chat.body")"
  fi

  # A listener that answers 200 to everything is a listener that has not been asked anything.
  code="$(curl -sS -m 5 -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:${port}/v1/chat/completions" \
    -H 'Content-Type: application/json' -d '{"model":"m-fixture"}')"
  [ "$code" = 400 ] && ok "a chat request with no messages is refused 400" \
    || bad "a chat request with no messages answered ${code}, not 400 — the listener answers everything"
  code="$(curl -sS -m 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1:${port}/no/such/route")"
  [ "$code" = 404 ] && ok "an unknown route is 404" \
    || bad "an unknown route answered ${code}, not 404"
fi

kill "$STUB_PID" 2>/dev/null
wait "$STUB_PID" 2>/dev/null
STUB_PID=""

echo "fixture product: the data is consistent with the binary and with itself"
python3 "${here}/check-data.py" "$data" "$stub" "$ver" "$W/chat.body" || fails=$((fails + 1))

echo "fixture product: every cell driver obeys the rules the oracle enforces over any product's scripts/"
sd_n=0
for f in "$data"/scripts/*.sh; do
  [ -f "$f" ] || continue
  sd_n=$((sd_n + 1))
  bash -n "$f" 2>"$W/syntax.err" || bad "$(basename "$f") is not valid bash: $(head -2 "$W/syntax.err")"
  # replay-selftest case (w): a driver that gives up with a non-negative status must say so, or
  # record.sh writes a PASS row over a harness failure.
  if grep -Eq '(^|[^-[:alnum:]_])fail[[:space:]]+[0-9]' "$f" && ! grep -q 'harness_error' "$f"; then
    bad "$(basename "$f") fails with a non-negative status and never marks harness_error"
  fi
  # replay-selftest case (x): a wall clock stepped into effects is money-rated and diverges forever.
  if grep -Eq '^[[:space:]]*step[[:space:]]+[A-Za-z_][A-Za-z0-9_]*[[:space:]]+"\$\((date|python3 -c .import time)' "$f"; then
    bad "$(basename "$f") steps a raw wall clock into effects"
  fi
done
[ "$sd_n" -gt 0 ] && ok "${sd_n} cell driver(s) checked" \
  || bad "data/scripts holds no *.sh driver, so the oracle's two static driver guards would look at nothing and report PASS over an empty set"

if [ "$fails" = 0 ]; then
  echo "fixture product selftest: GREEN"
  exit 0
fi
echo "fixture product selftest: RED (${fails} failure(s))"
exit 1
