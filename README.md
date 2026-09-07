# busbar-oracle

A **shadow oracle**: it records exactly what a released binary does — bytes and side
effects, per *cell* — and replays that recording against any later binary, so a
difference in the report is a difference in the product.

It was built to keep [busbar](https://github.com/GetBusbar/busbar) honest across a
major version. Nothing in this repository knows anything about busbar.

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

Also available: `replay-selftest`, `fetch-plugin`, `rigs-ledger`, `apply-mutation`,
`build-request`, `capture`, `fixture-gate-selftest`.

## Ported to Python, or shipped as a script?

**Nothing was ported. Every bash driver ships as the file that produced the existing
goldens**, and `busbar-oracle` dispatches to it.

That is a deliberate line, and it is drawn at a specific place: *a driver that
records a cell, judges a cell, or verifies a pinned digest may not be rewritten
during a move.* Every bash file here does one of those three things:

| file | lines | why it is not a port |
|---|---|---|
| `record.sh` | ~1080 | records |
| `rigs-ledger.sh` | ~840 | records, and writes the ledger a verdict is read from |
| `replay-selftest.sh` | ~430 | judges the judge |
| `oracle-config.sh` | ~290 | decides what the recorded binary is configured with — a config change moves 30+ cells |
| `fetch-golden.sh` | ~230 | verifies a pinned digest |
| `replay.sh` | ~200 | judges |
| `selftest.sh` | ~90 | judges |
| `harness-rev.sh` | ~76 | *is* the definition of "the harness changed"; its output is committed into every golden |
| `renormalize.sh` | ~69 | rewrites recordings |
| `fetch-plugin.sh` | ~45 | verifies a pinned digest |

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
```

These run against the tiny fixture product in `tests/fixture-product/` and need no
real binary, so the tool is testable in this repository alone.

`apply-mutation --selftest` is the one that needs **PyYAML** — a boot mutation is
applied to the parsed config document. It is pinned in `requirements-dev.txt` and
deliberately *not* a runtime dependency: `pyproject.toml`'s `dependencies` stays
empty so the oracle can be installed into an environment that shares nothing with
the workspace it judges. A product with `mutation:` cells provides PyYAML in the
interpreter that runs the recorder.

## Versioning

Released by tag. Consumers should pin a tag *and* the sha256 of the release archive,
and treat a change to either as a harness change — because it is one.

## License

Apache-2.0. See [LICENSE](LICENSE).
