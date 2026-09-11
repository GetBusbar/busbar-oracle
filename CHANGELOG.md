# Changelog

Released by tag. A consumer pins `tag@sha256` and folds it into its harness revision,
so every entry here is a harness change by definition — a recording made before it and
one made after it are not comparable without saying so out loud.

## 0.3.16

Two shapes of the same defect: a recorder that had MEMORISED a fact about the product
instead of reading it, and a normalizer with no way to take out a figure that is a draw on
one plane and a contract on another — so it took out neither.

- **What a rig can drive is ASKED OF THE RIG, every time.** `plane_scenario()` carried two
  hard-coded answers about the product tree. Both were measured and correct when they were
  written, and both had since stopped being true:

  *`mcp:upstream_down`* said "h2-mock-upstream.mjs honours no fault control". The mcp rig has since
  grown exactly the control its a2a sibling always had — `H2_CONTROL_FILE` set by `h2_boot`, handed
  to the mock, and `h2-upstream-outage.sh` beside it to arm it — and the row went on being a named
  gap anyway, because the tool was reciting a measurement rather than taking one. Measured: v0.3.15's
  `plane_scenario()`, pointed at the tree where the control exists, still answers
  "honours no fault control".

  *`a2a:no-agents-configured`* said "h2_boot always registers and approves the `probe` agent". It no
  longer does — and the configuration its new argument reaches TURNED OUT NOT TO BE THE CELL'S. With
  no `agents:` key the plane mounts no routes at all, so the submission is refused `401` in AUTH,
  upstream of the meter, and the key's usage reads `requests: 0` — while the cell's own `why` states
  `{"requests": 1}`, a caller who drew a slot and bought nothing. Two configurations were wearing one
  name. The row is now the SHARPER one it was always about: a REGISTERED agent whose LANE IS ABSENT,
  fronted, admitted, metered, resolving to nothing. The gap names the argument that would close it,
  `lane-absent`, and `ps_rig_can` reads `h2_boot`'s own argument guard for it.

  `ps_rig_can(plane, capability)` is the only place either question is answered, and it answers by
  reading the tree the tool is POINTED AT. A gap is printed only when it says the rig cannot; when it
  says the rig can, the ordinary gates decide — so a cell that is ALSO blocked by something more
  fundamental (an `issue` obligation, a transport with no client) goes on being refused for that
  reason and not for a capability the rig no longer lacks. The `upstream-fault` probe takes all three
  of `H2_CONTROL_FILE` set at boot, handed to the mock it starts, and a SCENARIO beside the library
  that arms it: "a capability with no caller is a claim" is the product's own sentence for why its
  control shipped together with the scenario, and the probe holds the rig to it. The library is never
  counted as its own caller.

  **This changes what is recorded.** `mcp|…|tools/call|upstream_down` is drivable where it was a gap:
  12 plane cells instead of 11. Measured against 1.6.0 through the product's own rig, that cell
  records `HTTP 200` with `isError: true` and "MCP upstream answered JSON-RPC error -32603: h2
  fixture upstream: down" — the outage, not a healthy 200 wearing its name.

- **Three normalisation rules, each SCOPED to the cells that need it.** Every other rule in
  `normalize.py` applies to every cell, which is right for a nondeterminism busbar emits everywhere.
  These three take out a figure that is a per-run draw on one plane's answers and a real CONTRACT
  somewhere else in the same corpus, so each fires only for a cell whose id matches the rule's own
  regex. The id arrives on `--cell`, which every one of `record.sh`'s call sites and `renormalize.sh`
  now passes; **no cell id, no scoped rule**, so a caller that does not say which cell this is gets
  exactly the behaviour that existed before these rules did.

  * `text.a2a-task-id` — busbar ISSUES ITS OWN a2a task identity, `a2a-<agent>-<16 hex>`
    (`receive.rs`, over a hash of the body, the clock, a process counter and the pid). Sixteen hex
    digits: `ID_RULES`' hex rule needs 32 and its prefix rule needs an underscore, so NOTHING took it.
    Measured through the rig: the `ok` answer carries it twice (`task.id`, `task.contextId`), the
    `upstream_down` refusal a third time (`data[].resourceName`), all different on every run. The
    AGENT NAME is kept; only the digits go.
  * `json.a2a-task-timestamp` — the task's RFC 3339 moment (`"2026-09-11T06:16:32.127Z"` at
    `task.status.timestamp`). `TS_KEYS` already holds `timestamp` but only for an INTEGER, and
    **that bound is not an oversight to fix by widening it**: `ops.scrape|v1models|anthropic-fp`
    records `"created_at": "1970-01-01T00:00:00Z"` sixteen times, a fixed literal busbar emits for
    every model and the one cell that proves it still does. A corpus-wide ISO rule under `TS_KEYS`
    would replace all sixteen. This key, this shape, these cells.
  * `text.retry-after-seconds` — how long until the window rolls, rendered INTO the refusal's own
    prose. Measured on mcp `over_budget`: `… refused by your budget: Limit { … retry_after:
    Some(63690) }`, the seconds left in the UTC day. The same figure is rendered `Retry after {n}s`
    by the breaker-open refusals on both planes. The `Retry-After` HEADER carrying it has been
    blanked by `hdr.retry-after` since that rule was written; this is the same decision one layer
    down, and only the digits are replaced, so a refusal that stopped naming a wait is still red.

  **NO COMMITTED BYTE CAN MOVE, and it is proven rather than argued**: not one of the ids in the
  committed golden's ledger is in scope for any of the three, because no a2a or mcp cell has ever
  been recorded. The scopes are TOOL CODE — a table in `normalize.py`, not a field a corpus can set
  and not a file loaded out of the data directory; the self-test reads that off the code. The
  `--keep` un-strip hook is unchanged: `json_keys` and `text_regex` short-circuit ahead of every
  rule, scoped or not.

  Scope, over the product's 2,332 cells: `text.a2a-task-id` 470, `json.a2a-task-timestamp` 470,
  `text.retry-after-seconds` 460.

## 0.3.15

Three defects measured against the product's pinned harness, all of the same kind: a
recorder that looked confident and recorded something other than what the cell names.

- **A cell's rule set is no longer a stopwatch reading.** `billing|key-usage|after-upstream-down`
  recorded a different `applied` set on 3 of 5 consecutive runs of the same binary — and `applied` is
  COMPARED (diff-cells.py's `norm.rules`), where `metrics.timing` may never be exempted, for the good
  reason stated there. Two independent halves, both closed:

  *The delta could report that time had passed.* `busbar_lane_recovery_hint_ms` counts DOWN the
  milliseconds until a tripped lane may be retried, so two scrapes a second apart read 33000 and
  32000 with busbar having done nothing in between. `normalize.py` already drops that sample from the
  recorded cell — but only AFTER the delta has said it changed, so the `metrics.timing` rule fired on
  the runs where the countdown crossed a boundary and not on the others. `capture.py` now drops a
  wall-clock COUNTDOWN before it can be the reason a rule fired. **No recorded byte moves**: the key
  never survived normalization anyway, so a delta with it and a delta without it produce the same
  `effects.metrics`. The breaker's state is not lost with it — `busbar_lane_state`,
  `busbar_lane_available` and `busbar_lane_available_permits` are the state-transition contract, and
  none of them is a countdown.

  *The settle probe and the snapshot did not see the same metric set.* `settle_then_snapshot()` polls
  until two consecutive reads agree and then snapshots, but the probe digested `/metrics` through a
  blanket `grep -v '_seconds'`: every duration summary AND its sample count was invisible to the
  fixed point while the snapshot a fraction of a second later captured the whole exposition — and the
  countdown, which carries no `_seconds`, was IN the fixed point, where it can never settle, so the
  loop spun out its bound on every cell with a tripped lane and snapshotted at an arbitrary moment.
  `_settle_metrics_view()` keeps what moves when, and only when, a request is OBSERVED
  (`_seconds_count`, `_seconds_bucket`) and drops what moves with the WALL CLOCK (a summary's
  sliding-window quantiles, `busbar_uptime_seconds`, `process_cpu_seconds_total`, the countdown).
  busbar observes a request's duration after it has answered the client, so this is what makes
  `busbar_request_duration_seconds_sum` a deterministic part of the delta rather than a race.

  **Both fixes are recorder-side.** The same flips could be closed in `normalize.py`, but every cell
  of a committed golden was written by the current normalizer, so that would take effect only by
  moving the bytes of cells recorded correctly. Measured after: six consecutive recordings of
  `billing|key-usage|after-upstream-down` are byte-identical, and three of
  `llm|anthropic|anthropic|request|{ok,ok_stream}` are byte-identical with `metrics.timing` firing on
  every one. golden/1.5.5 replayed against itself stays 928/928 PASS.

- **A request streams because the CELL says so, not because of an outcome's name.**
  `build-request.py` decided streaming with `oc in ("ok_stream", "ok_stream_array")` — two outcome
  names. A `stream_upstream_error` cell declares `mock_control: {"stream-error": true}`, and the
  mock gates that fault on `want_stream and stream_error`, so every one of those cells was sent
  BUFFERED, the fault never fired, and the HAPPY PATH was recorded under the name of the failure —
  identically on both binaries, which is the worst shape a green can have. `declares_stream(cell)`
  reads the cell, most specific first: an explicit `stream` field (believed in both directions),
  then a `mock_control` only a stream can reach, then the two outcome names LAST. `stream` is now
  part of the emitted request as well, so no consumer re-derives it from a body that — for gemini
  and bedrock — does not contain it.

  **This changes recordings.** A `stream_upstream_error` cell now records the failure it is named
  for; any earlier recording of one is a recording of the happy path.

- **The mcp/a2a planes are RECORDED, through the product's own conformance rigs.** `record.sh`
  refused them by PLANE, twice — at the argument gate and again per cell, with a row that said
  "never owed" — so 1,382 cells were unowed by category and nothing downstream could notice the
  category swallowing a cell a rig can in fact drive. Both refusals are deleted. `plane-subject.sh`
  sources the product's own rig library (`<repo>/scripts/<plane>-subject/h2-lib.sh`, the path
  derived from the plane name) and drives its own helpers, exactly as the product's gating scenarios
  do; the answer is assembled by the recorder's own `capture.py`. What a rig cannot drive is the
  ORDINARY `needs_fixture` gap row with the rig NAMED in it — never a skip that says a plane is
  proven somewhere else — and a rig that broke is red.

  Measured against busbar 1.6.0: eleven cells that were blanket skips now record, with their real
  usage deltas, audit chains and egress.

- **A cell id is not a path.** Every cell was written to `${id//|/__}`, escaping the one separator
  the llm and core planes happen to use. An mcp method is `tools/call` and an a2a one is
  `GET /.well-known/agent-card.json`, so `cells/<safe>.json` was a path into a directory that does
  not exist. `cell_file_name()` keeps the pipe rule byte-for-byte and maps every other non-portable
  character to `_`; `diff-cells.py` and `merge-recordings.py` state the same rule. No collisions over
  the product's 2,332 ids, and every one of the 928 committed golden cell files is still the name the
  rule gives its id.

- **`text.port` now covers a header value and a JSON string.** The rule's own sentence — a loopback
  address with an ephemeral port is the harness's draw, never busbar's contract — was implemented
  for text bodies and stderr lines alone. Conformance rigs take FREE ports, so a plane refusal's
  `www-authenticate: … http://127.0.0.1:<ephemeral>/…` differed on every run. The `\d{2,5}` bound is
  the original rule's and is load-bearing: `127.0.0.1:1` and `127.0.0.1:9` are config constants a
  cell is about. Re-applying the rule to every string of all 928 committed golden cells moves not
  one byte.

## 0.3.14

- **A cell's own `mock_control` now reaches the mock on EVERY driver, and a `pre` step's own control
  reaches it too.** Three drivers arrange the upstream and only two of them honoured the cell. The
  built-in llm driver wrote `down` when `outcome` was `upstream_down` and read `.mock_control` not
  at all, so a cell that named a verb the outcomes have no word for was recorded against a HEALTHY
  upstream and passed — which is why no consumer has ever been able to record a mid-stream-failure
  cell. And no driver honoured a `mock_control` written on a `pre` SETUP STEP, so a cell that primes
  its state with a request that is supposed to fail primed the opposite state instead, and recorded
  a cell whose id and `why` describe an outage that never happened.

  The rule is now one function, `cell_mock_control(cell, outcome)`, which all three drivers and the
  `pre` runner ask, and which is the only reader of `.mock_control` in the file: the cell's own
  control wins where it names one, because it is the more specific statement and no `outcome` can
  express a per-lane verb; `upstream_down` supplies `down` where the cell names nothing, which is
  what every recording made so far assumed; an empty object is absence. A `pre` step's control is
  written before the step and CLEARED after it, so a setup outage cannot leak into the request the
  cell records. `replay-selftest.sh` drives the real function, extracted by name.

  **THIS CHANGES RECORDINGS, WHICH IS THE POINT.** A cell whose control was previously dropped will
  now record what it is about, and its bytes will differ from any recording made with 0.3.13 or
  earlier. A consumer re-records those cells as parts and says so; no cell that named no control on
  a driver that already honoured it is affected.

## 0.3.13

**A figure that is a measurement of the body, counted twice.** `llm.stream|responses|cut` is the
script-driver cell that pins what busbar tells a caller when the upstream dies mid-stream. 1.6.0's
fabricated `response.failed` terminal carries eight more keys than 1.5.5's — a strict superset,
registered by the owner against the `body` class — and the cell's own driver
(`llm-stream-fault.sh`) *also* records `effects.stream_fault.body_bytes`, which is
`wc -c` over exactly those bytes. So the accepted growth arrived a second time in a class no
register kind may take: `effects.script` is in `MONEY_CLASSES` and `ADDITIVE_CLASSES` is
`{body, headers, effects.stderr, status}`. The cell could not read PASS ACCEPTED although no billed
figure, no status, no header, no frame count and no metric had moved — not a second fact about
busbar, the *same* fact counted twice.

**The fix is a relation the differ PROVES, not a class an owner may claim.** `additive` still
refuses `effects.script` at load, `effects.script` is still rated 10, and no entry anywhere gained a
new power. `derived_from_body()` runs *last*, only when `effects.script` is the one class the
register left unclaimed, and it credits it only when all three hold:

* **(a) the body's verdict is in.** `body` diverged on this cell *and* left `need` — some register
  entry accepted it. A body nothing accepted forgives nothing here either: the whole rule is
  "follows the body's verdict", and with no entry that verdict is red.
* **(b) the figure really is a measurement of the recorded body, on BOTH sides.** A **length** must
  sit the SAME fixed distance above the recorded body on golden and candidate. The distance is not
  zero in general and that is not a loophole: a driver counts the bytes *before* `normalize.py` sees
  them (a count taken after normalization would be a property of the normalizer, which is the whole
  reason `hdr.length` exists), so the bytes an id or timestamp rule replaced sit between the two
  numbers — but they must not *move* between the sides, or something the recording no longer shows
  moved with them. A **digest** must be the sha256 of the recorded body exactly. A `json` or
  `eventstream` body is refused outright: it is recorded as structure, so its byte length in the
  recording is the serializer's, not busbar's.
* **(c) every other `effects.script` member is byte-identical** — proven, not assumed, by putting
  the golden's figure back at each derived leaf and requiring the whole moved subtree to be equal
  again. A neighbour that also moved, a key that appeared on one side only, or a `paths` list
  `json_paths_diff` truncated at its limit all refuse the cell rather than riding along.

Any failure of (a)–(c) leaves `effects.script` a money divergence exactly as before.

**Which fields are derived was measured, not guessed.** Reading the script drivers, `capture.py` and
`capture-exec.py`: `llm-stream-fault.sh` is the only driver in the corpus that measures the body,
and it measures it once — `body_bytes`. `body_len`, `body_length` and `body_sha256` are named beside
it because the *check* is the proof and the name is only the invitation to apply it; a field called
`body_len` whose value is not the length of the body is refused as loudly as one called `survived`.
**`body_frames` is deliberately not derived**: a frame count is a property of the dialect's framing,
not of the body's byte count, and it is one of the facts the ruling required to be unmoved before
the body's growth counted as growth at all.

**It is never a silent pass.** The row leads with the acceptance and the relation that explains it —

```
ACCEPTED derived-from-body (entry <id>): effects.script/stream_fault/body_bytes 482 -> 622 = len(body)
```

— and a *refused* relation leads with exactly where it broke (`… did not move with the body: golden
482 = len(body) 429 + 53, candidate 622 = len(body) 578 + 44`), so a cell that stays red says why in
the same sentence.

## 0.3.12

A whole plane the recorder could not reach. VT-6 measured it against the pinned `v0.3.11`: the
drivers were `llm | http | script | concurrent | exec`, `mock-upstream.py` had **zero** `Upgrade`
handling, and `capture.py`/`normalize.py` had no notion of a frame, a handshake or a close code. So
the streams plane's served sessions — openai-realtime, gemini-live, twilio media streams, the
browser sideband leg — were unrecordable **from anything**, and the golden's zero `^voice` rows
could only say so. This release is the tool half of that gap. The product half (a binary that serves
a socket at all) is not this repository's.

Three things, and none of them is useful without the other two.

* **A `ws` recorder driver** (`capture-ws.py`, `wsframe.py`; `record.sh`'s `record_ws_cell`).
  It opens a WebSocket against the door, drives a scripted client, and records the handshake
  (status and headers — or, on a refused upgrade, the whole ordinary HTTP response, body and all),
  every frame in wire order **in both directions**, the close code, reason and who closed first,
  whether `Sec-WebSocket-Accept` was the value RFC 6455 derives from the key, and the same usage /
  metrics / audit / egress deltas every other driver records — through `capture.py`'s own helpers,
  so a ws cell's `effects` cannot drift from an http cell's.

  Everything the client does is **fixed**: RFC 6455 §1.3's own sample nonce for the key, a constant
  mask, one fixed header order, a script that is data. The bytes the door receives are a pure
  function of the cell, which is the only thing that lets a second recording be a diff rather than
  a nonce. The framing is `wsframe.py` — about two hundred lines against the RFC — and not a
  package: `pyproject`'s `dependencies = []` is what lets the oracle judge a workspace it shares
  nothing with, and a WebSocket library is exactly the wrong thing to break it for, because one
  that coalesces fragments, answers pings for you, or picks a random key per connection has already
  decided what a frame is before the recorder sees it.

  A script step is `send` / `send_text` / `send_binary_base64` / `ping` / `await` / `await_opcode` /
  `close`. `await` addresses frames by **RFC 6901 pointer** — this repository's existing addressing
  idiom, the same one 0.3.10's pointer builder and resolver are held to — so a cell says
  `{"/type": "response.done"}` or `{"/serverContent/turnComplete": true}` and no dialect name
  appears in the driver. An `await` the door never satisfies inside `timeout_secs` is a **harness
  failure**, never a recorded outcome: a transcript cut off by the recorder's own clock is not what
  the door did, and freezing it into a golden would make every later binary reproduce this
  harness's timeout and call it a pass.

* **A WebSocket mock upstream.** A GET carrying `Upgrade: websocket` on a dialect's realtime path
  completes the handshake and plays a fixed, scripted duplex session. A session is not a
  request/response pair, so it cannot be a pure function of one request; it is a pure function of
  the **client's frame sequence** instead — fixed ids, the same 11-in/7-out usage every other
  dialect draws, event ids counted from 1 per session, no clocks.

  The dialect is a **table**, never a branch: how a path selects it, which key names the event, how
  a server frame is stamped, what is sent on open, what each client event is answered with, and how
  that dialect says "error" are all one row, and the session loop knows nothing about OpenAI or
  Google.

  The verbs are the existing ones, plus two. A handshake refusal is the ordinary `down` / `401` /
  `5xx` — an upgrade is a GET and gets that status. `cut` kills the socket after the open frames
  with no close frame at all. And the **dispute case** — the upstream that dies *after* the door has
  accepted the session and started billing — needs the upstream to say so *in band*, which no status
  code can: `ws-error` answers the first client event, then sends the dialect's own error shape and
  closes 1011; `ws-close` closes 1011 with no error frame first. Both are chosen through the
  existing control file, out of band, so busbar's own frames stay byte-identical to the healthy
  session the recording is compared against.

* **Per-dialect frame canonicalisation** (`normalize.py`'s `WS_DIALECTS`; rows for
  `openai-realtime`, `gemini-live`, `twilio-media` and `echo`). Without it the first two are
  unusable: every real dialect stamps its frames with values that are new on every run — a fresh
  `event_id` per Realtime event, `response_id`/`item_id` minted per turn, a `streamSid` and a
  millisecond `timestamp` on every Twilio media message — so a session recorded raw can never be
  reproduced, and therefore can never be a diff.

  The canonicaliser is **data, keyed by dialect name**. There is no `if dialect == "openai"` in
  neutral code; adding a dialect is adding a row, the same shape the mock uses for the other end of
  the same socket. A dialect the table does not know fires `ws.dialect-unknown` into `applied`,
  which is itself the `norm.rules` diff class — loud, rather than quietly a nonce.

  Ids are **interned, not blanked**. One `<ID>` for everything would throw away what a transcript is
  for: `response.text.delta` and `response.done` naming the *same* response is how a reader knows
  they are one turn, and two turns interleaved on one socket is a real bug a single placeholder
  would hide. Each distinct value takes the next `<ID:n>` in wire order, across every id key and
  pointer at once — so the correlation survives, the nonce does not, and a door that started
  *reusing* an id is itself a diff.

  Audio becomes **length and digest**. Tens of kilobytes of base64 make a golden unreviewable, and
  dropping it would let a door that sent silence, or truncated a turn, record identically to one
  that did not; `{"bytes": N, "sha256": …}` keeps the two facts that are a contract. Neutral for
  binary frames — a binary frame on a realtime session is audio by construction — while *which text
  keys* carry it is the dialect's business, which is why `delta` is listed per event: it is a
  transcript on `response.text.delta` and base64 PCM on `response.audio.delta`.

**And the differ had to learn the block.** `compare` walks `status`, `headers`, `body` and the
`effects` keys; a ws cell's whole contract is a top-level `ws` block, so without a class of its own
a candidate could drop half its frames, reorder the transcript, flip a frame's direction, close 1011
where the golden closed 1000, or answer in a different dialect entirely, and the row would still
print `PASS  identical` — the same hole the `effects.script` sweep was added to close, one level up.
The new class is **`ws`**, rated 10 and in `MONEY_CLASSES`, for the reason `effects.egress` is: a
frame that stopped arriving is a turn the caller paid for and did not get, and neither that nor a
moved close code shows up in `status`, because the handshake was a 101 either way. `ws_diff` leads
with the session-level facts, then the first index at which the ordered frames part company, with
the frame **count** first when it moved — a frame missing near the front shifts every frame after
it, and a raw list diff would print the whole tail of the session.

`streams` joins `llm`/`core`/`all` as a plane `record.sh` records natively.

Four things found by the new tests, all of them in this release rather than after it:

* the driver **never read the close echo**. `send_close` set `closed`, and the wait for the echo was
  gated on `while not closed`, so it returned immediately and the door's answering close was dropped
  from every clean transcript — the recorder was losing the last frame of the shutdown it exists to
  record. `ws.close.echo` now says which of `close`/`eof`/`none` happened, so a door that *stops*
  echoing is a diff on a key rather than a silently shorter frame list.
* `await` matches by RFC 6901 pointer and so can only ever match a **text** frame; a cell whose
  subject is the door streaming audio *back* had nothing to point at. New step: `await_opcode`.
* normalizing a ws cell **twice** corrupted the audio digest — fed its own output, the walk saw
  `{"bytes": N, "sha256": "<64 hex>"}` as an ordinary dict and the generic `<HASH>` rule ate the
  digest. `renormalize.sh` re-runs from `raw/captured.json` and so does not hit it today, but
  "faithful only as long as nobody feeds it a cell" is not a property worth having.
* `renormalize.sh` **refused every ws cell**: its table names the normalize.py call sites
  `record.sh` has, and there are now five. The ws row is `--key-id` alone — and deliberately no
  `--driver` flag anywhere, because the frame canonicaliser is chosen by the recording's own
  `ws.dialect`, which travels inside `captured.json`. So a ws cell is re-derived years later without
  a driver table that could have drifted, and can never be re-normalized under a dialect other than
  the one it was recorded in.

`tests/` gains **130 unit tests** (`pytest tests`), per driver, driving the real shipped mock in a
subprocess with the real driver rather than two test doubles agreeing with each other. Every server
they start binds an **ephemeral** port. The ten `ws`-class differ tests were run against the
pre-change `diff-cells.py` and all ten fail there. `pytest` is pinned in `requirements-dev.txt` by
version and digest, with its transitive pins; there is still **no runtime dependency**, and
deliberately no `websockets`.

## 0.3.11

Two seams the 0.3.10 release closed in behaviour but did not STATE, and one door that could not
record the fault it is named for.

- **The pointer builder and the resolver are held to RFC 6901 directly, not to each other's habits.**
  0.3.10 made `additive_superset` escape and `resolve_json_pointer` unescape, and proved it through a
  whole replay — the right level for "can the register name this leaf", but not a statement of the
  property that makes the pointer trustworthy, and a property nobody states is one the next edit can
  break in a single half. That is precisely how the original defect arose: a `/`-join on one side and
  a `/`-split on the other, two halves of one convention that agreed on every key without a slash and
  disagreed on every key that IS a URL. `replay-selftest` now asserts the seam itself — the real
  `paths` key `/api/v1/admin/overlay/{section}` (slashes AND a `{}` template segment) round-trips;
  the adversarial keys whose meaning the substitution ORDER decides (`~`, a literal `~1`, `~0`)
  round-trip; the pointer the BUILDER emits for that key is the one the RESOLVER resolves, to the
  same leaf, with nothing passed between them but the standard; the pre-0.3.10 `/`-join spelling
  resolves NOWHERE, so the two conventions are not quietly both live; and a reference token with
  neither `/` nor `~` escapes to ITSELF, asserted over every key in the shipped fixture recording
  rather than over a sample. That last case is the one a consuming product's existing register rests
  on: no pointer already written changes meaning, because no segment of any of them holds either
  character.

- **`/v1/responses` streams on the FAULT verbs, so a `cut` on that door has a stream to cut.** The
  mock answered this path BUFFERED whatever `stream` said, so a `cut` sliced a JSON object in half
  (`_send`'s cut arm splits on the SSE frame boundary and falls back to half the bytes when there is
  none) — a shape no upstream produces, and one that cannot record what a door does when a real
  stream dies after its first frame. There is now a `response.*` stream builder
  (`response.created` → one `output_text.delta` → `response.completed` carrying the usage), wired to
  `cut` only. NOT to `stream` itself, and that restraint is the point: six cells are already recorded
  against this door with `stream: true` in their egress body
  (`llm|{anthropic,bedrock,cohere,gemini,openai,responses}|responses|request|ok_stream`), every one
  of them recorded from a PUBLISHED binary against a buffered upstream — a golden is re-made by
  re-recording that binary, never by the release that changed the judge. So the healthy answer stays
  byte-identical (asserted) and only the fault verbs, which no existing golden on this door uses,
  gain frames. Widening it to the ordinary path is a separate change with a re-record attached.
  `mock-upstream --selftest` drives all of it through a real server on a real socket: the cut answers
  `text/event-stream`, delivers exactly ONE complete `response.created` frame and never the terminal
  `response.completed` the usage is read off; the healthy `stream: true` answer is still the buffered
  object byte for byte; `stream-error` is unchanged.

No verdict moves for any cell that does not order a `cut` on `/v1/responses`.

## 0.3.10

Two gaps 0.3.9 named as blockers and left open. Both were the TOOL's, not the product's, and both
stood between `admin.ops|GetOpenapiJson|ok` and a verdict the tool proves rather than one the
register asserts — under the owner's standing rule that 1.5.5's recorded descriptions are VERBATIM,
the judge has to read what the product actually wrote.

- **`text_list_growth` reads a THIRD spelling: backtick-quoted items joined by pipes.** 1.5.5's own
  400 line on the DELETE `/api/v1/admin/overlay/{section}` route is
  `` (expected `groups`|`hooks`|`root`|`plugin_versions`) ``, and its `OverlayResetView.reset`
  description is the spaced form `` (`groups` | `hooks` | `root` | `plugin_versions`) ``. Neither was
  read by either existing rule — the backtick rule wants `, ` / ` or ` / `, or ` between its items,
  and the pipe rule's item is a BARE word (backticks are excluded from it so an unparenthesised run
  cannot swallow the sentence around it) — so a candidate that ONLY GREW that list, in the golden's
  own spelling, with nothing else on the line touched, was refused for the punctuation rather than
  for anything the message stopped saying. That left the register only one way to accept real growth:
  declare it, which is exactly the say-so `additive` exists to replace. The run is now its own kind
  (`backtick-pipe`), under the SAME set relation, the SAME splice proof and the SAME
  every-other-list-byte-identical requirement as the other two. Because it is its own kind, a list
  that moved BETWEEN spellings is still a template change refused by name — which is what 1.6.0 did
  to that very line (`` `a`|`b` `` became "expected one of `a`, `b`"), and it stays red. Runs are
  read longest-first from the earliest start, so `` `a`|`b`, `c` `` is one backtick-pipe run from
  `a`, never a bare backtick run starting at `b`.

- **JSON pointers are RFC 6901, in both directions.** The differ's path convention was a `/`-join,
  not a pointer, and a document whose KEYS contain slashes broke it at both ends at once: an OpenAPI
  `paths` key IS a URL, so `additive_superset` reported a leaf as
  `/paths//api/v1/admin/overlay/{section}/delete/summary` and `resolve_json_pointer` split that back
  into segments (`paths`, ``, `api`, …) that named nothing. The register could not address a single
  leaf of the largest document busbar records; the 0.3.6 guard refused such an entry at load, which
  was the polite failure and still a dead end. Paths are now BUILT with `~0`/`~1` escaping
  (`ptr_escape`, `~` first then `/`, so an escaped literal `~1` cannot become a slash) and RESOLVED
  unescaped (`ptr_unescape`, `~1` first then `~0`), so `description_corrections` can name
  `/paths/~1api~1v1~1admin~1overlay~1{section}/delete/responses/409/description`. THE ESCAPING WIDENS
  WHAT CAN BE ADDRESSED, NEVER WHAT MAY BE MISSING: a pointer that still resolves nowhere after
  unescaping is refused at load exactly as before. A key containing neither `/` nor `~` escapes to
  itself, so every path this file has ever printed for every other cell in the corpus is
  byte-identical. `""`/`"/"` remains this file's root convention (RFC 6901 would read `/` as the
  empty-string key); nothing addresses an empty key here, and moving it would move existing verdicts.

Nothing else moves. Every verdict in the self-test outside the eight cases these two changes are for
is unchanged, and the only paths whose TEXT changes are those under a slash-bearing key, which no
cell but the openapi document has.

## 0.3.9

- **`text_list_growth` proves EVERY grown string leaf of a body, not one slot.** One
  release-note-worthy fact is often written down in several leaves of the SAME document, and through
  0.3.8 the second one ended the matter: `text_list_growth found more than one differing string
  leaf: …`, a refusal about the COUNT of leaves that moved rather than about anything any one of them
  said. busbar's `admin.ops|GetOpenapiJson|ok` is that document — 1.5.5 states the overlay-section
  enum in THREE prose leaves of the DELETE `/api/v1/admin/overlay/{section}` operation and its view
  schema (the operation `summary`, its 400 `description`, and `OverlayResetView.reset`'s
  `description`), and 1.6.0 grows that enum by the four sections it added (`identity-providers`,
  `export`, `tools`, `agents`). An `additive` entry may now see N differing string leaves; EACH is
  put through the same 0.3.8 check on its own terms (every golden item found in the candidate's list
  as a SET, every other list byte-identical, and golden's own raw list text spliced back must
  reproduce golden byte for byte), the cell is forgiven only when every one of them is growth, and
  the row names each with its own JSON pointer and its own added items. NOTHING IS LOOSER PER LEAF: a
  leaf that changed in ANY other way still refuses the cell, and now names ITSELF and its own reason
  — which is the message a reader can act on — instead of a leaf count that reads the same whether
  one document grew a fact in three places or a second field was quietly reworded. Every hard failure
  of the JSON walk (a missing key, a short array, a non-string scalar, a `null` outside
  `null_to_value`) is untouched and still stops at the first one. THE RUN KIND IS DECIDED PER LEAF,
  not once per body: the openapi operation's three leaves are not spelled alike (the `summary`'s run
  is BARE PIPE, `section ∈ groups|hooks|root|plugin_versions`, the others are backtick-quoted), so a
  generalisation that widened only the count while assuming one spelling per document would pass a
  single-spelling body and still refuse the one this release is for. Each leaf is read by
  `find_text_lists` on its own text, and the same-spelling-on-both-sides rule stays a per-leaf rule
  too — a leaf that RESPELLED its list is still refused, naming that leaf and the two kinds it
  paired, however honestly its neighbours grew. In the self-test the only verdict
  that could have moved is `ss6`'s, and it does not: two leaves where one grew and one was reworded
  is still RED — it now says `not a superset at /hint (… no backtick or pipe list found)` instead of
  naming both paths and neither reason. That is the single existing case whose MESSAGE changes.

- **An `additive` entry may name a `transform`, and the rewrite is applied BEFORE the growth proof.**
  Through 0.3.8 this was refused at load — "additive proves growth by inspecting the recorded pair,
  never by rewriting it first; use one register kind or the other" — and busbar's
  `boot.refusal|BOOT-P20|validate` (and `|BOOT-P29|`, `|BOOT-P30|`) is the cell that ruling had no
  answer for. 1.6.0 stamps a diagnostic code onto the refusal line (`[error] BUSBAR-3015: `, which
  the register's D-1 transform has covered for these families since codes were introduced) AND the
  limit-metric enum on that SAME line grew by the four token metrics. Neither kind could take it:
  `transform` is credited for a class only when the REWRITTEN PAIR IS BYTE-IDENTICAL, and it is not,
  because the list grew; `additive` saw the raw pair, where the prefix is one more thing that moved,
  and refused it as a template change. Splitting it across two entries cannot express it either —
  both changes are on the same line, so each entry would have to forgive the other's difference to be
  credited for its own. The transform is now applied to BOTH sides first (the same symmetric
  normalization the transform branch already performs) and the growth proof runs on the rewritten
  pair, on `effects.stderr`, on a plain-text `body`, and per string leaf inside a JSON body. NOTHING
  IS WEAKENED BY THE ORDER: the transform is still held to `transform_pattern_too_broad()` (it must
  name a specific token, never a shape wide enough to swallow arbitrary content) and to a declared
  `cells` scope with an `expected_cells` count; the growth proof is still the whole of what forgives
  the class; and — the guard that matters — an `additive` entry carrying a transform is NEVER added
  to the `transforms` list, so the fired-transform path that hands a cell's WHOLE raw class list to
  the accepted column never runs for it. It claims exactly the classes it proves, and a pair the
  rewrite happens to reconcile completely is just `text_list_growth_check`'s `golden == cand` case.

- Not changed, and named out loud because it is what still stands between
  `admin.ops|GetOpenapiJson|ok` and a green proved on growth alone: **there is no third list
  spelling.** 1.5.5's real 400 line joins BACKTICK-QUOTED items with PIPES —
  `` (expected `groups`|`hooks`|`root`|`plugin_versions`) `` — and that run is read by neither rule
  (the backtick rule wants `, ` / ` or ` / `, or ` between items; the pipe rule's item is a bare
  word, backticks excluded, so an unparenthesised run cannot swallow the sentence around it). A
  candidate that ONLY grows that list, in the golden's own spelling, with nothing else on the line
  touched, is still red — for the spelling, not for anything the message stopped saying. This release
  generalises the leaf COUNT and the transform ORDER and adds no spelling; the self-test pins the
  case so that closing it either way (busbar spelling that line the way it spells the other two, or a
  later release learning the backtick-pipe run) shows up as a case moving.

- Not changed, and worth saying because the other obvious workaround for the openapi cell runs into it:
  `description_corrections` still cannot name a leaf under an OpenAPI `paths` key. `additive_superset`
  builds a leaf's path by joining keys with `/`, and a `paths` key IS a URL, so the pointer comes out
  `/paths//api/v1/admin/overlay/{section}/…` and `resolve_json_pointer` splits it back into segments
  that name nothing. The 0.3.6 load-time guard refuses such an entry rather than letting it sit there
  covering nothing, which is the right outcome of the two rules meeting; the self-test now pins it,
  so it is a documented blocker rather than a surprise. It is why the openapi cell has to be made
  green by GROWTH in each leaf rather than by declaration.

## 0.3.8

- **`text_list_growth` reads a PIPE-SEPARATED list, and proves growth as a SET rather than a
  prefix.** Two changes to one check, both found the same way: busbar's three limit-validation
  cells (`boot.refusal|BOOT-P20|validate`, `|BOOT-P29|`, `|BOOT-P30|`) all say the same thing — a
  limit's metric enum gained the four token metrics 1.6.0 added (`tokens_input`, `tokens_output`,
  `tokens_cache_read`, `tokens_cache_write`) — and all three stayed red under 0.3.7 for reasons
  about PUNCTUATION and POSITION rather than about anything the message stopped saying.
  (1) A list spelled the way a grammar is written out — `(requests | tokens | budget | concurrent)`,
  or unparenthesised mid-sentence, `<metric> is one of requests|tokens|budget|concurrent and
  <window> one of minute|hour|day|month|total` — is now found beside the backtick-quoted spelling,
  by one rule: an item is a bare word, at least two of them joined by `|`, so a run ends at the
  first token no pipe follows and cannot swallow the sentence around it. The parentheses are PROSE
  AROUND the run, not part of it, and stay under the byte-identical-surroundings requirement like
  every other character of the template. Runs of the two spellings never overlap, and a list that
  changed WHICH spelling it uses is a template change, refused by name.
  (2) The changed list's relation is now membership, not position: every golden item must appear
  somewhere in the candidate's list (multiplicity respected), and whatever is left over is the
  growth. Through 0.3.7 golden's items had to be an ordered PREFIX, so growth was only ever
  forgiven at the END of a list — and real enums grow next to the item they refine. An item DROPPED
  is still the first thing refused, named by its position in the golden's list.
  DELIBERATE WIDENING, stated out loud: a candidate that merely REORDERS golden's items, adding
  nothing, is a set superset and is now ACCEPTED, where 0.3.7 refused it as "added no new items".
  A reordered operator-visible list is still a change nobody announced (busbar's own register says
  so: F-013's BOOT-020 narrowing ruled exactly that "neither additive nor better" and fixed it
  rather than forgiving it) — but catching it is the register's job, not this check's: an entry
  naming the cell, with a changelog line and a declared width, still has to be written by a person
  before any of this runs. Such a row says `additive: the declared list was REORDERED, no items
  added or dropped` rather than falling through to a raw before/after that reads like a forgiven
  rewrite. Everything else is byte-identical in behaviour: the splice proof (golden's own raw list
  text put back into the candidate must reproduce golden BYTE FOR BYTE), the one-changed-list
  limit, the every-other-list-byte-identical rule, the JSON-leaf path and its one-differing-leaf
  slot, and every load-time refusal are untouched. In the self-test, the ONLY verdicts that move
  are the two mid-list-insertion cases (`rr4`, `ss4`), which were red for the new item's position
  alone and are now accepted naming it.

## 0.3.7

- **`additive` gains `new_route`, for a route that did not exist in 1.5.5 now answering.** Every
  additive relation so far (superset, `text_list_growth`, `description_corrections`) compares two
  REAL responses; there is no relation between 1.5.5's not-found stub and whatever a new route
  answers with now, so none of them apply. An entry may declare `new_route: true`; on a cell where
  the golden's status is 404 AND the golden body is shaped like 1.5.5's not-found envelope (a JSON
  object whose only top-level key is `error`, itself carrying a string `message`) AND the
  candidate's status is not 5xx, the entry may take `status`, `headers` and `body` for that cell
  wholesale, no relation checked between the two bodies at all — reported on the accepted row as
  `new route: golden 404 -> candidate <status>`. Any other golden status, or a candidate 5xx,
  leaves the flag inert and the ordinary additive rules decide exactly as if it were absent.
  `status` joins the classes `additive` may take, refused at load unless `new_route: true` is set;
  the money guard gains a narrow exception for `new_route` + `kind: additive` + a changelog line +
  only `status` among the money classes named — there is no previous behavior to declare `breaking`
  against, but it is still money and still needs its own changelog line.

## 0.3.6

- **`additive` gains `description_corrections`, for a named JSON string leaf that may differ
  outright.** Not every body difference is growth — sometimes 1.5.5's prose was simply wrong, and
  1.6.0's replacement is a registered factual correction rather than an addition.
  `description_corrections: ["<json pointer>", ...]` forgives a STRING leaf's mismatch at exactly
  the listed pointer(s), no growth relation checked, reported on the accepted row with both texts;
  every other leaf still follows the ordinary superset rules unchanged, and an entry may correct
  one leaf while separately proving `text_list_growth` on a different leaf in the same body (a
  corrected leaf never competes for `text_list_growth`'s one-differing-leaf slot — checked first).
  Refused at load: a pointer that does not resolve to a string in the golden body of any cell the
  entry matches — a typo'd path would otherwise silently forgive nothing while the cell stays red
  for an unrelated-looking reason.

## 0.3.5

- **`text_list_growth` reaches a string LEAF inside a JSON body, not only a plain-text body.**
  `admin.ops|DeleteOverlaySection|not-found`'s enum lives at `/error/message` inside a JSON
  envelope, not in a plain-text body. `additive_superset()`'s walk now defers a STRING leaf
  mismatch (collecting the JSON pointer, golden value and candidate value) instead of failing on
  it immediately; every other mismatch (a missing key, a short array, a non-string scalar, an
  uncovered `null`) still fails the walk immediately, unchanged. After the walk: zero deferred
  leaves is a plain superset as before; exactly one is fed to the existing
  `text_list_growth_check()` proof, naming the leaf's JSON pointer on both the accepted row and a
  refusal; more than one differing leaf is refused by name, naming every path — a second leaf
  moving is not "one list grew" under either leaf's own story. Verified against busbar's real
  Oxford-comma shape on both sides (golden "...`root`, or `plugin_versions`"; a grown candidate
  "...`plugin_versions`, ..., or `agents`"): list membership is defined by extracting
  backtick-quoted tokens via a run regex whose separator alternation treats "or" purely as prose
  glue wherever it falls, never as an item of its own, so the Oxford "or" moving to before the new
  last item as the list grows needs no special case.

## 0.3.4

- **`additive` gains `text_list_growth: true`, the same superset proof for a key-list named in
  prose instead of JSON.** A body/`effects.stderr` string can grow an enum inside a sentence
  ("expected `groups`, `hooks`, `root`, or `plugin_versions`") the same way a JSON array grows a
  key — but there is no JSON structure to walk, and the surrounding template is exactly the part
  that must not move for free. The check finds every backtick-quoted comma list in the golden and
  candidate text (paired by order; a mismatched count, or growth touching more than one list, is
  refused — one declared list per entry keeps the check narrow); the one list that changed must
  hold golden's items as a PREFIX; every other list and everything outside the lists must be
  byte-identical; and the actual proof is a splice — golden's own raw list text spliced back into
  the candidate at the changed list's position must reproduce golden byte for byte. That splice is
  what catches a reworded template beside real growth: `admin.ops|DeleteOverlaySection|not-found`
  changed "expected" to "expected one of" alongside adding four items, and the splice fails at the
  first byte the wording differs, so the cell stays red. `effects.stderr` is now a valid `additive`
  class, but refused at load unless `text_list_growth: true` is also set — a raw string has no
  other growth proof this file knows. Accepted rows report the items proved new via
  `detail["additive.removed"]`.

## 0.3.3

- **A third register kind, `additive`, for growth the tool proves rather than an owner asserts.**
  `improvement` can never take `body`/`headers` on a BODY_IS_CONTRACT family (rated 10 there), and
  `breaking` would misdescribe a response that dropped nothing and changed no existing value —
  neither fits a body that grew a key. `additive` may take `body` and `headers` ONLY when the tool
  itself proves the candidate a superset of the golden at every path the golden defines: for
  `body`, both sides must parse as JSON, every golden key/value must be present in the candidate
  (recursively; extra keys allowed; arrays are a golden-prefix-of-candidate under the same rule
  per element; a golden `null` growing into a value needs the path listed under `null_to_value`);
  for `headers`, every golden header must be present with an equal value (extra headers allowed;
  `content-length` exempt only when this same entry's body check passed). `status`, usage,
  `effects.readback` and every `missing.*` class are never taken by `additive` — refused at load,
  same as any class outside `{body, headers}` — and an `additive` entry must carry a `changelog`
  line, exactly as `breaking` does. A failed proof leaves the cell red and the row names where:
  `additive: not a superset at <json path / header>`. Fixes the second half of the rated-weight
  problem: F-011's admin.ops views (`GetHooks`, `PostHooks`, `GetOpenapiJson`, ...) are
  BODY_IS_CONTRACT and were owner-ruled additive growth, not breaking, with no register kind able
  to say so without either being refused (`improvement`) or overclaiming (`breaking`).

## 0.3.2

- **A fired transform may take a class it rendered identical, whatever that class is rated on
  the cell's family.** 0.3.1's fired-transform branch subtracted the FAMILY-rated money set
  (`money_at_cell`) from an entry's own `allowed` set even when the rewritten pair was
  byte-identical, so an `improvement`-kind transform (e.g. stripping a `diag=BUSBAR-NNNN`
  diagnostic suffix) could no longer be credited for `body` on any BODY_IS_CONTRACT family
  (`boot.warning`, `boot.refusal`, `cli`, `config.migrate`, `admin.ops`, `ops.scrape`) — 18 real
  `boot.warning` cells in busbar's own corpus went from accepted to a phantom divergence purely
  because their family rates `body` 10 and the entry is not `kind: breaking`. The fired-transform
  branch only ever runs once the rewritten pair has been PROVEN byte-identical (a transform
  touches only `effects.stderr` and `body.text`, so every other field is a verbatim copy and
  would still show up in the comparison if it moved); nothing is being forgiven at that point,
  so the entry's own `allowed` set — already held to the GLOBAL money guard at load — is what
  decides, not a second family-specific subtraction. A class the rewrite does not touch (a
  changed `status`, a real body difference outside the rewritten pattern) still fails to reach
  this branch at all and stays red, exactly as before.

- **A transform pattern that can match the empty string, or names no literal text at all, is
  refused at load** (`.*`, `\s*`, `(.|\n)*`, `\d+`, ...). "Byte-identical after the rewrite" only
  proves the rewrite named the right token when the pattern IS a token; a pattern this broad can
  swallow arbitrary surrounding content and would make that proof vacuous.

## 0.3.1

- **A `transform` in `accepted-differences.json` is applied symmetrically, not just to the
  candidate.** `diff-cells.py`'s transform branch rewrote only the candidate side before
  comparing, on the assumption that the pattern a transform strips (a diagnostic code, a
  new metering series) exists on the candidate and never on the golden. That assumption
  fails the moment the golden is *also* shaped like the candidate — two recordings of the
  same binary taken to prove determinism, or a candidate-vs-candidate A/B — and stripping
  the pattern from one side only manufactured a phantom divergence out of an
  already-identical pair. The golden is now rewritten by the same rules before either side
  is compared: when the golden lacks the pattern (the real golden-vs-candidate case) this
  is a no-op, so the transform still only ever removes a difference, never invents one.
  `replay-selftest.sh` proves all three properties this fix must hold: a self-diff (or any
  candidate-vs-candidate pair) under a transform-bearing register now reports 0 diverging
  (RED before this fix), the real asymmetric case is forgiven exactly as before, and a
  genuine body divergence planted under a transform-bearing entry still reports RED.

## 0.3.0

Four external audits of the tool at `v0.2.3` are the source of this release. Every
change below closes a path by which the oracle could report **GREEN over something it
had not compared**, and every one ships with a self-test case that was red before it
and is green after — in `busbar-oracle replay-selftest`, `cells --selftest`,
`fixture-gate-selftest`, `rigs-ledger --selftest` or `oracle-config.sh --selftest`,
never a new test runner.

### The corpus can no longer shrink unseen

- **A corrupt `cells.json` is refused as a floor.** `enumerate-cells.py` swallowed
  `json.JSONDecodeError` on the committed corpus, skipped the per-family floor
  entirely, and `--write` then committed the shrunken corpus **as the new reference**
  in the same command. A reference that cannot be read is not a reference that says
  "anything goes". Also refused: a `counts.by_family` that is missing, or that gives a
  count which is not a non-negative integer (that family had silently had no floor),
  and an `--accept-family-shrink` naming a family the corpus does not have. `--write`
  is now atomic, and importing the product's cell module no longer writes
  `__pycache__` into the tree the gate asserts is clean.

- **A baselined cell deleted from `cells.json` is RED.** `replay.sh`'s owed-baseline
  ratchet names that case first in its own header and could not see it: `owed` and the
  gap list are both derived *from* `cells.json`, so an id removed from the corpus was
  in neither, and `if cid not in scope: continue` dropped it in silence. The default
  reason string on that branch was provably unreachable. `diff-cells.py` now writes
  `corpus-ids.txt` and `selected-ids.txt`, and the ratchet distinguishes "this id left
  the corpus" (red, unless `accepted-gaps.json` names it) from "this run used
  `--family` and never looked at it" (still skipped).

- **`extra.candidate`: a cell the candidate recorded that nothing compared.** Every
  loop in the differ walks the OWED set, which comes from the golden, so a cell file
  the candidate wrote that the golden does not owe was invisible — no row, no class,
  no line. That covers a gap the candidate has closed, the *other half of a rename*,
  and a stale file from an earlier recording into the same `--out`. Reported on its
  own ledger row, in `extra-candidate.txt`, in `report.json` and in `report.md`. Not
  red by default (nothing was compared); `--refuse-extra-candidate` makes it red, and
  the ids reach `EXPECTED_IDS` so the verdict actually reads them.

### The provenance guard covers the whole recording

- **Harness skew is a set, not a scalar.** The guard was one equality on
  `meta.json.harness_rev` — a field `merge-recordings.py`, `renormalize.sh` and a text
  editor all overwrite. A golden merged from a part recorded under revision A and a
  part recorded under B is stamped `B`, so a candidate recorded today under B satisfied
  it exactly and the differ compared A's cells against B's with nothing said. The
  provenance is now `harness_rev` ∪ `harness_rev_history` ∪ `merged_from[].harness_rev`
  ∪ `harness_rev_recorded`, and the two sides must name the same set.
  `harness_rev_recorded` — a field no file in this repository wrote or read, while
  sitting in a shipped golden carrying a real digest — is now written by both
  re-stampers (once, so the original recording revision survives every later re-stamp)
  and read here. `report.json` gains `golden_harness_revs` / `candidate_harness_revs`.

  **Consuming products should expect this to bite.** A golden that was merged or
  re-normalized is mixed-revision by construction, and a fresh candidate is not: such
  a pair is now refused unless `--allow-harness-skew` is passed deliberately, or the
  golden is re-normalized to a single revision.

### Money classes are armed where they were not

- **Concurrent cells capture their real egress.** `capture-concurrent.py` hardcoded
  `"effects": {… "egress": []}` — which is `capture.py`'s own spelling of "this cell
  must never reach upstream" — on cells that bill for eight upstream requests.
  `effects.egress` is rated 10 and is a MONEY class, so on every concurrency/queue cell
  a candidate that sent a mangled system prompt, dropped the tool list, leaked a client
  header upstream or doubled its upstream attempts diverged on nothing: `[] == []`,
  `PASS identical`. The one other witness (`busbar_upstream_attempts_total`) is masked
  on this very driver. The recorder now snapshots egress around the burst, settles it,
  and names the files, exactly as the single-request path does.

- **The "an `improvement` may not forgive money" rule is keyed on the RATED weight.**
  `MONEY_CLASSES` is keyed on the class and asserted against `CLASS_WEIGHT`; the weight
  a divergence is actually scored with is keyed on the FAMILY. On the six
  `BODY_IS_CONTRACT` families the `body` class is rated 10 while `CLASS_WEIGHT` rates
  it 3 and `MONEY_CLASSES` does not name it — so a four-line `improvement` entry with
  no `kind: breaking` and no changelog line could waive the entire stdout and stderr of
  a boot refusal, the only thing such a cell records, weighted 10 in the D/W ratio. The
  file's own assertion could not catch it because it never looked at the family path.
  One function (`rated_weight`) now answers both questions; entries that lose reach are
  named on stderr when the register loads. Also fixed here: a declared `"weight"` could
  price a money divergence as a cosmetic one, and `"weight": 0` silently became 10.

- **Egress ordering no longer depends on the recording host's locale.** The order of
  the egress filenames *is* the recorded order of the upstream requests; `sort` and
  `comm` are pinned `LC_ALL=C`, as `harness-rev.sh` already pins its own glob.

### A recording is what a driver that succeeded produced

- **A script cell passes when its driver exited 0, into a directory this run emptied.**
  PASS meant "a non-empty `captured.json` exists, its status is not -1, and it carries
  no `effects.harness_error`" — the exit status of the process that wrote the file was
  discarded at the invocation, and three drivers in the consuming product define no
  `fail()` and no `harness_error` at all, so for those the file test was the whole gate.
  Nothing cleared the output directory either, so on a re-record into an existing
  `--out` that file could be *the previous run's*. The give-up ordering was also wrong:
  `status == -1` was tested before `harness_error` and `continue`d, so a driver that
  gave up **and marked it correctly** was filed as a named gap — and a SKIP row leaves
  the owed set entirely. One rule now, in one order, in `script_cell_verdict()`, which
  the self-test drives directly.

- **The fixture gate refuses a `needs_fixture` it cannot read.** `${!1}` requires a
  valid shell identifier; on anything else bash prints `invalid variable name` and
  **aborts the enclosing compound command**, so the recorder's
  `if oracle_fixture_missing …; then SKIP; continue; fi` ran neither branch, fell
  through, and **recorded the cell with its fixture absent** — freezing whatever a
  product with no backend answers into the golden, with a PASS row, reproduced by every
  candidate. (`needs_fixture: 1` was the same hole by indirecting onto `$1`.) The gate
  now answers three ways — gap, record, or *this is not a fixture gate* — and the third
  is a red row. `fixture-gate-selftest` checks every value in the product's own corpus
  against it.

- **`oracle_write_config` and `oracle_env` cannot name different directories.** One
  wrote to `$1`, the other booted from `$WORK`; the pair was correct only because every
  shipped driver happens to export the same value. It is now bound, and a caller whose
  `WORK` names somewhere else is refused rather than served a config it did not write.

### The rig ledger fails closed

`rigs-ledger.sh` had four ways to report green having compared nothing, and all four
are now rows:

- a **corrupt baseline** made the fold throw, produced no rows, and `[ -n "$rows" ]`
  read that as "no regressions" — every signed-off row unchecked. The exit status is
  captured (as the file's four other folds already did) and a fold that threw is
  `baseline|_fold_failed`, red and owed;
- a **missing baseline** was an advisory line. It is `baseline|_missing`, red and owed
  — except under `--rebaseline`, which is the run that creates the first one;
- **`--rebaseline` skipped the diff**, so it refused to sign off a FAIL and happily
  signed off an *absence*: a scenario that stopped executing became the new floor.
  The comparison now runs under it too;
- **`--check` was parsed and never read**, so the one caller that asked for the
  baseline comparison out loud got the same run as one that did not. It now refuses a
  run with no baseline, and refuses to be combined with `--rebaseline`.

New: `--accept-baseline-loss <row-id>` (repeatable), so a reviewed removal is named in
the command line of the commit that makes it rather than accepted by the absence of a
check.

### The self-tests can no longer pass vacuously

- case (y), the owed ratchet, was **skipped in silence** when `owed-baseline.txt` was
  empty or absent — the one state in which every ratchet the file provides is gone. An
  empty baseline beside a golden that owes cells is now the finding;
- the `harness_error` guard triggered only on a **literal digit** after `fail`, so
  `fail "$rc" …` was skipped entirely, and it asked the whole FILE for the string
  rather than the give-up path. Both widened, plus a new check for a give-up written
  inline rather than through `fail()`;
- a case that **could not run** now reports `SKIP` and is counted in the summary
  instead of reporting `PASS` (the `SUPERVISOR_MARKERS` check and the owed ratchet);
- the driver-contract case's "beside itself" arm resolved against an empty temp
  directory, so it would have passed with the extraction reverted. It resolves against
  the product's own `scripts/` directory now, and says so when the layout cannot
  discriminate;
- two independent cases shared `$W/renorm`, so one measured the other's leftovers;
- `renormalize.sh`'s faithfulness table cited `record.sh` **line numbers** that had all
  rotted, and a self-test asserted on one of them — so correcting the comment turned
  the case red for the wrong reason. Call sites are named by function now.

### Documentation

- the README claimed "Nothing in this repository knows anything about busbar", which is
  not true of this tool and never was: the release URL, the directory layout it sources
  from, the plane vocabulary, the dialect table and several transcribed source
  behaviours are all the product's. Corrected to the claim that is true and that the
  code actually enforces — a *product* file is never resolved against the tool's own
  directory — which the self-tests check as a class.

## 0.2.3 and earlier

See the tag history. `0.2.x` was the extraction of the oracle out of the product's tree:
the tool/data seam, the driver contract (`BUSBAR_ORACLE_TOOL_DIR`), the harness revision
covering the tool by digest and the data by hash, and the self-tests for each.
