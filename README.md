# busbar-oracle

A **shadow oracle**: it records exactly what a released binary does — bytes and side
effects, per *cell* — and replays that recording against any later binary, so a
difference in the report is a difference in the product.

It was built to keep [busbar](https://github.com/GetBusbar/busbar) honest across a
major version, and it is still a busbar-shaped instrument that has been *parameterized*
rather than a product-agnostic one: the release URL it fetches a golden binary from, the
directory layout it prefers to source a product's ledger helpers out of, the plane and
dialect vocabulary, and several behaviours transcribed from busbar's own source all name
that product. What **is** true, is enforced, and is what the seam below is about:

> **A product file is never resolved against the tool's own directory.**

Everything a product owns — its cells, its golden, its registers, its drivers, its
fixtures, its pins — is named by `--data` / `--product-root`, and the self-tests check
that as a class over every shipped file rather than as a list of known offenders.

## What a cell is

A cell is one addressable behaviour: a method on a transport under a role, an admin
operation, a boot refusal, a CLI invocation, a metrics scrape. Recording a cell
captures its response bytes *and* its effects — what it wrote, what it billed, what
it logged, what a follow-up read returns — then normalizes away the things that are
allowed to differ between two runs of the *same* binary (timestamps, ports, generated
keys, paths) using rules that are themselves part of the record.

Replaying compares a candidate's cells against the golden's, class by class. A
difference is RED unless the product's own register forgives it, and a forgiveness
must say which cells it covers and how many.

## Why Python, and why it ships separately

The oracle judges a workspace, so it must not share anything with it. A separate
interpreter, a separate dependency set (there are no third-party dependencies at
all), a separate release cadence, and a pinned digest on the consuming side: none of
the product's toolchain can move the oracle's verdict without that move being
visible as a changed pin.

## Install

```
pip install busbar-oracle            # from a release artifact or a checkout
busbar-oracle --help
```

## Use

The product being judged, and that product's oracle data, are arguments:

```
busbar-oracle --product-root /path/to/product --data /path/to/product/oracle-data \
  record --bin /path/to/product/target/release/candidate --plane all --out ./recordings/candidate

busbar-oracle --product-root /path/to/product --data /path/to/product/oracle-data \
  replay --golden ./oracle-data/golden/1.5.5 --candidate ./recordings/candidate --out ./reports/run
```

- `--data` holds `cells.json`, `golden/`, the accepted-difference and accepted-gap
  registers, the owed baseline, the digest pins, the fixtures and the per-cell
  drivers under `scripts/`. All of it describes the *product*.
- `--product-root` is the product itself — its binary, its sources.

Both default to the old in-tree derivation (`<tool>/..` and `<tool>/../..`), so a
project that still keeps the oracle inside its own tree needs neither flag.

Every other flag is passed through to the underlying driver unchanged.

## The tool/data seam

There are exactly two kinds of file in play, and every path in this tool resolves
against one of them:

| kind | lives in | examples |
|---|---|---|
| **tool** | the installed package | `record.sh`, `replay.sh`, `capture.py`, `capture-exec.py`, `normalize.py`, `diff-cells.py`, `mock-upstream.py`, `oracle-config.sh`, `fetch-plugin.sh`, `fetch-golden.sh` |
| **data** | `--data` / `$BUSBAR_ORACLE_DATA` | `cells.json`, `cells/`, `golden/`, `scripts/`, `fixtures/*.json`, `accepted-differences.json`, `accepted-gaps.json`, `owed-baseline.txt`, `golden-digests.tsv`, `plugin-digests.tsv`, `oracle.pin` |

**A product file is never resolved against the tool's own directory.** That
derivation was right only while the oracle lived inside the product it judged; in
an installed tool it names a path that does not exist, and the failure is not loud
— it arrives as a plane of red cells that look like a product regression. Three
defaults had it (`apply-mutation.py --fixture`, `diff-cells.py --cells/--accepted`,
`merge-recordings.py --cells`); all three now default to `$BUSBAR_ORACLE_DATA/…`
and fall back to the tool's directory only when that variable is unset, which is
the in-tree layout every existing golden was recorded under. `replay-selftest`
scans the shipped files for the shape and is red if it comes back.

### The driver contract

A product's cell drivers (`$BUSBAR_ORACLE_DATA/scripts/*.sh`) are **data** — they
belong to the product and the tool never ships them. They do, however, call tool
files: `capture.py`, `capture-exec.py`, `mock-upstream.py`, `oracle-config.sh`,
`fetch-plugin.sh`, `fetch-golden.sh`. Those files are not beside the driver any
more and must not be looked for there — a stale in-tree copy that happened to
survive would silently decide what a cell records.

So a driver reaches them through **`BUSBAR_ORACLE_TOOL_DIR`**, the absolute path of
the installed package directory:

```sh
here="$(cd "$(dirname "$0")/.." && pwd)"          # the DATA dir (this driver's product)
python3 "${BUSBAR_ORACLE_TOOL_DIR:-$here}/capture.py"  … # a TOOL file, through the variable
tarball="$(bash "${BUSBAR_ORACLE_TOOL_DIR:-$here}/fetch-plugin.sh" store-sqlite)"
```

`record.sh` and `replay.sh` export the variable themselves — **as a default, never an
override** — so a driver behaves identically whether it was reached through a
product's own shim or by running `busbar-oracle record` directly. A shim that
installed the tool and already knows where it put it keeps the last word; a caller
that says nothing gets the running driver's own directory. The `:-$here` fallback
inside a driver is for the in-tree layout only.

The contract is about **the value the driver ends up with**, not about which layer
set it, and `replay-selftest` asserts both arms: nothing pre-set, and pre-set by the
caller.

### What the harness revision covers, and how

`harness-rev` is the answer to "was this golden made and judged by the same thing
that is making and judging now?", and it covers **everything that decides a
verdict** — but the two kinds are covered by two different mechanisms, on purpose:

- the **tool** by its *digest*: `$BUSBAR_ORACLE_TOOL_DIGEST` (`tag@sha256`, which the
  consuming product's shim sets from its pin) is folded into the revision as one
  line. A tool upgrade therefore moves the rev exactly once, by a route the product
  cannot change without also changing which tool it runs.
- the **data** by *hashing*: every file in the table above is hashed, names as well
  as bytes, including the product's committed `oracle.pin` — so a pin bump is a data
  change with a name, visible to `busbar-oracle harness-rev --files`, to a `git
  diff`, and to the hash.

Hashing the tool's files *as well* would state which judge ran twice, by two routes
that can disagree; hashing them *instead* would miss a tool upgrade that edited
nothing in the data directory. One statement, one route. When no digest is supplied
the tool is running from inside the tree it judges, and the old file glob is hashed
exactly as before, so recordings made under that layout stay verifiable.

## Subcommands

| command | driver | does |
|---|---|---|
| `record` | `record.sh` | run a product's cells, write a recording |
| `replay` | `replay.sh` | judge a candidate recording against a golden |
| `diff` | `diff-cells.py` | the verdict itself — replay's judging half |
| `normalize` | `normalize.py` | apply the norm rules to a captured cell |
| `merge` | `merge-recordings.py` | fold recordings into one golden |
| `renormalize` | `renormalize.sh` | re-apply the norm rules to an existing recording |
| `harness-rev` | `harness-rev.sh` | the revision of what records and judges |
| `selftest` | `selftest.sh` | the tool's own self-tests |
| `fetch-golden` | `fetch-golden.sh` | fetch and digest-verify a pinned golden binary |
| `mock` | `mock-upstream.py` | the multi-dialect mock upstream |
| `capture-ws` | `capture-ws.py` | the `ws` driver — drive and record one duplex session |

Also available: `replay-selftest`, `fetch-plugin`, `rigs-ledger`, `apply-mutation`,
`build-request`, `capture`, `fixture-gate-selftest`.

### The `ws` driver

A cell whose `driver` is `ws` records a **session**, not a request. `record.sh` opens a
WebSocket against the door, drives a scripted client between its before- and
after-snapshots, and records:

| key | is |
|---|---|
| `status`, `headers` | the **handshake** — or, when the door refuses the upgrade, the whole ordinary HTTP response, body and all |
| `ws.frames` | every frame in wire order, **both directions**: `{"dir": "out"\|"in", "opcode", "text"\|"json"\|"base64_audio"\|("code","reason")}` |
| `ws.close` | who closed first (`server`\|`client`\|`eof`), the code and reason, and — when the client closed — whether the door echoed |
| `ws.accept_ok` | whether `Sec-WebSocket-Accept` was the value RFC 6455 derives from the key |
| `effects` | usage Δ, metrics Δ, audit Δ, egress — the same closed loop every other driver records |

Everything the client does is **fixed**: RFC 6455 §1.3's own sample nonce for the
`Sec-WebSocket-Key`, a constant mask, one fixed header order, and a script that is data.
So the bytes the door receives are a pure function of the cell, and a recording made
twice from one binary is byte-identical.

The cell:

```json
{
  "id": "streams|ws|openai-realtime|/v1/realtime|session-open",
  "plane": "streams",
  "driver": "ws",
  "ws": {
    "dialect": "openai-realtime",
    "path": "/v1/realtime?model=m-openai-realtime",
    "auth": "ok",
    "headers": {},
    "timeout_secs": 15,
    "script": [
      {"await": {"/type": "session.created"}},
      {"send": {"type": "response.create"}},
      {"await": {"/type": "response.done"}}
    ],
    "close": {"code": 1000, "reason": ""}
  },
  "mock_control": "ws-error"
}
```

Script steps: `send` (canonical JSON), `send_text`, `send_binary_base64`, `ping`,
`await` (an **RFC 6901 pointer** map, this repo's addressing idiom — or the literal
`"close"`), `await_opcode`, `close`. An `await` the door never satisfies inside
`timeout_secs` is a **harness failure**, never a recorded outcome: a transcript cut off
by the recorder's own clock is not what the door did.

**Frame canonicalisation is data, keyed by dialect name** (`normalize.py`'s
`WS_DIALECTS`, rows for `openai-realtime`, `gemini-live`, `twilio-media`, `echo`).
Volatile ids are **interned**, not blanked — each distinct value takes the next `<ID:n>`
in wire order, so `response.text.delta` and `response.done` naming one turn still say so,
and a door that started *reusing* an id is itself a diff. Audio becomes
`{"bytes": N, "sha256": …}`. A dialect the table does not know fires `ws.dialect-unknown`
into `applied`, which is the `norm.rules` diff class — loud, not silently a nonce.

The differ compares the block under its own class, **`ws`**, rated 10 and in
`MONEY_CLASSES`: a frame that stopped arriving is a turn the caller paid for and did not
get, and neither that nor a close code moving 1000 → 1011 shows up in `status`.

The mock upstream answers `Upgrade: websocket` on each dialect's realtime path and plays
a scripted duplex session. Handshake refusals are the ordinary `down`/`401`/`5xx` verbs;
`cut` kills the socket with no close frame; and the in-session dispute case — the upstream
that fails *after* the door started billing — is `ws-error` (the dialect's own error
frame, then 1011) and `ws-close` (1011 with no error frame first).

### A script effect that is a *measurement of the body*

`effects.script` — every key a driver writes into `effects` that has no class of its own
— is rated 10 and is in `MONEY_CLASSES`, because a script cell's evidence is the whole of
what that cell pins. One shape of it is not an independent signal at all, though: a figure
the driver computed **by measuring the response body**. `llm-stream-fault.sh` records
`stream_fault.body_bytes`, which is `wc -c` over the very bytes the `body` class already
compares, so when the register accepts the body's change that same change arrives a second
time as a money divergence — not a second fact about busbar, the *same* fact counted twice.
Since 0.3.13 the differ can prove that and let the figure **follow the body's verdict**,
and it is a relation the tool proves rather than a class an owner may claim: `additive`
still refuses `effects.script` at load, and the relation only fires when (a) `body`
diverged *and* a register entry on that cell accepted it, (b) every differing
`effects.script` leaf is a named body-derived measurement (`body_bytes`, `body_len`,
`body_length`, `body_sha256` — never `body_frames`, which counts framing and not bytes)
whose value really is that measurement of the recorded body **on both sides**, at the same
fixed distance above it (the driver counts pre-normalization bytes, and the bytes an
id/timestamp rule replaced must not move between the two sides), and (c) every *other*
`effects.script` member is byte-identical, proven by putting the golden's figure back at
each derived leaf and requiring the moved subtree to be equal again. Any failure of
(a)–(c) leaves the class money exactly as before. It is never a silent pass: the row reads
`ACCEPTED derived-from-body (entry <id>): effects.script/stream_fault/body_bytes 482 -> 622
= len(body)`, and a refused relation leads with exactly where it broke.

### Flags a gate should know about

| flag | on | means |
|---|---|---|
| `--allow-harness-skew` | `replay`, `diff` | compare two recordings whose **whole provenance** does not match. Since 0.3.0 that is the set of every revision a recording's cells came from (`harness_rev`, `harness_rev_history`, `merged_from[].harness_rev`, `harness_rev_recorded`) — a merged or re-normalized golden is mixed-revision by construction and a fresh candidate is not, so such a pair is refused until this is passed deliberately or the golden is re-normalized to one revision |
| `--refuse-extra-candidate` | `replay`, `diff` | make `extra.candidate` rows RED. A cell the candidate recorded that the golden does not owe is *reported* by default and red only on request: nothing was compared, so it is not a divergence — but silence about it is how a rename reads as a deletion |
| `--accept-family-shrink <family>` | `cells` | a reviewed, named loss of cells from one family. Anything else that shrinks a family — including a `cells.json` too corrupt to read as the floor — is refused |
| `--accept-baseline-loss <row-id>` | `rigs-ledger` | a reviewed, named row that was PASS at the last sign-off and is deliberately not expected any more |
| `--check` | `rigs-ledger` | demand the baseline comparison actually happened: refuses a run with no baseline, and refuses to be combined with `--rebaseline` |

## Ported to Python, or shipped as a script?

**Nothing was ported. Every bash driver ships as the file that produced the existing
goldens**, and `busbar-oracle` dispatches to it.

That is a deliberate line, and it is drawn at a specific place: *a driver that
records a cell, judges a cell, or verifies a pinned digest may not be rewritten
during a move.* Every bash file here does one of those three things:

| file | lines | why it is not a port |
|---|---|---|
| `record.sh` | ~1290 | records |
| `rigs-ledger.sh` | ~940 | records, and writes the ledger a verdict is read from |
| `replay-selftest.sh` | ~1720 | judges the judge |
| `oracle-config.sh` | ~440 | decides what the recorded binary is configured with — a config change moves 30+ cells |
| `fetch-golden.sh` | ~240 | verifies a pinned digest |
| `replay.sh` | ~290 | judges |
| `selftest.sh` | ~91 | judges |
| `harness-rev.sh` | ~195 | *is* the definition of "the harness changed"; its output is committed into every golden |
| `renormalize.sh` | ~190 | rewrites recordings |
| `fetch-plugin.sh` | ~51 | verifies a pinned digest |

`harness-rev.sh` and `fetch-plugin.sh` are thin enough to port, and were left alone
anyway: one emits a hash that is already committed inside every existing golden's
`meta.json`, and a port that changed glob order, locale or newline handling would
silently invalidate every one of them; the other is the digest check that decides
whether a downloaded binary is the one that was pinned. Neither is worth the risk
for the size of the win.

If a driver is ever ported, the bar is the same bar this repository's own release
used: the ported tool must produce **byte-identical report output** on the same
golden/candidate pair before the script is deleted.

## Self-tests

```
busbar-oracle selftest                 # the recorder/normalizer/differ's own arms
busbar-oracle fixture-gate-selftest    # both arms of the fixture gate
busbar-oracle replay-selftest          # the judge, against a fixture recording
busbar-oracle apply-mutation --selftest # the mutation applier, incl. the data-dir fixture seam
busbar-oracle merge --selftest         # the merge provenance rule
busbar-oracle cells --selftest         # the per-family corpus floor, and the reference it is read from
busbar-oracle rigs-ledger --selftest   # the rig ledger's baseline, floors and refusals
busbar-oracle capture --selftest       # the effect-delta guards
busbar-oracle capture-ws --selftest    # the ws driver, against a scripted local server
python3 "$(python3 -c 'import busbar_oracle,os;print(os.path.dirname(busbar_oracle.__file__))')/wsframe.py"  # the RFC 6455 framing
bash "$(python3 -c 'import busbar_oracle,os;print(os.path.dirname(busbar_oracle.__file__))')/oracle-config.sh" --selftest
```

A case that **cannot run** in the layout it was invoked in reports `SKIP` and is counted
on the last line — never `PASS`. A static case that can pass vacuously is worse than no
case, so several of them assert on a non-empty input set before they assert anything else.

These run against the tiny fixture product in `tests/fixture-product/` and need no
real binary, so the tool is testable in this repository alone.

Beside them, `pytest tests` runs the **driver unit tests** — one named assertion per
behaviour, so a regression names which behaviour broke rather than a count. They need
`pytest` (pinned in `requirements-dev.txt`) and nothing else: the WebSocket peers they
drive are built out of `wsframe.py` itself and stdlib sockets, on ephemeral ports. There
is deliberately **no `websockets` dependency** — a library that coalesces fragments or
answers pings on your behalf cannot be used to prove that this recorder does not.

`apply-mutation --selftest` is the one that needs **PyYAML** — a boot mutation is
applied to the parsed config document. It is pinned in `requirements-dev.txt` and
deliberately *not* a runtime dependency: `pyproject.toml`'s `dependencies` stays
empty so the oracle can be installed into an environment that shares nothing with
the workspace it judges. A product with `mutation:` cells provides PyYAML in the
interpreter that runs the recorder.

## Versioning

See [CHANGELOG.md](CHANGELOG.md). Released by tag. Consumers should pin a tag *and* the sha256 of the release archive,
and treat a change to either as a harness change — because it is one.

## License

Apache-2.0. See [LICENSE](LICENSE).
