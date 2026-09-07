# The fixture product

A whole, tiny product for the oracle to judge — a binary, a cell corpus, cell drivers, the two
digest pins, the register, the floor, the gap list, a tool pin and a golden recording — in about
four hundred lines and with no dependency on anything outside this directory.

## Why it exists

The oracle used to live inside busbar. Judging is now a thing it does to a product handed to it as
`--data <dir>` and `--product-root <dir>`, and the claim behind that move is that the tool does not
know what it is judging. A CI that proved the claim by checking out busbar would disprove it: the
tool would once again only be demonstrable against the one product it came from, and every
self-test would be as slow, as networked and as unavailable to an outside contributor as building
busbar is.

So the oracle's CI judges *this* instead. Four jobs read it:

| job | what it runs |
| --- | --- |
| fixture product selftest | `bash tests/fixture-product/selftest.sh` |
| fixture gate, both arms | `busbar-oracle --data tests/fixture-product/data fixture-gate-selftest` |
| the judge judges itself | `busbar-oracle --data tests/fixture-product/data replay-selftest` |
| harness-rev-is-deterministic | `harness-rev` twice, once under `LC_ALL=en_US.UTF-8`, compared |

The last three read the data and never run the binary, so all three would stay green against a
product that cannot answer a request, a `cells.json` naming a driver that is not in the tree, or a
golden recording a version string the binary does not print. `selftest.sh` is the job that goes red
first: it exercises `busbar-stub` on a real socket and then checks the data against what the binary
just said — the golden's recorded bodies against the live answers, the digest pins against the
sha256 of the files they pin, the store map against the driver's own `case` arms.

## What is here

    busbar-stub      the product binary: --version, --validate, and a listener with three routes
    selftest.sh      CI's "fixture product selftest"
    check-data.py    its data half (every check a cross-reference, never a `test -f`)
    data/            the product's oracle data — this is what `--data` points at
      cells.json                 four cells: two recorded, two gated on a backend URL
      scripts/                   the cell drivers the corpus names
      fixtures/                  the two store "plugin" manifests plugin-digests.tsv pins
      golden/1.5.5/              a two-cell golden recording
      accepted-differences.json  empty, and it must stay empty
      accepted-gaps.json         empty, and it must stay empty
      owed-baseline.txt          the two ids the golden owes
      golden-digests.tsv         the stub binary's sha256
      plugin-digests.tsv         the two manifests' sha256
      oracle.pin                 the tool pin, so a self-test has one to bump

## The rule

**This is a FIXTURE. It must never grow into a second copy of busbar's data.**

Every cell here exists because a named self-test case would otherwise be vacuous, and the way to
add one is to name the case first. In particular:

- **No waivers.** `accepted-differences.json` and `accepted-gaps.json` stay empty, and no cell
  carries `compare` or `mock_control`. A waiver is a statement that a real product changed
  behaviour and somebody signed for it; the fixture has no behaviour and nobody to sign.
  `check-data.py` refuses one.
- **No coverage.** Cells are not added here to test busbar. A behaviour of busbar belongs in
  busbar's corpus, where a real binary records it. A cell added here records the stub, which knows
  nothing.
- **Nothing that only the real product can answer.** If a self-test genuinely needs busbar, it
  skips, loudly and by name. It does not get a stub that fakes the answer — a fixture that pretends
  is worse than a case that says it did not run.

If a change to the oracle needs something from this directory that is not here, add the smallest
thing that makes the new case non-vacuous, and say in the commit which case it makes real.
