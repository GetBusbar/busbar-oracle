#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Busbar Inc and contributors
"""`busbar-oracle cells` — generate a product's golden-corpus cell list, DERIVED, never hand-listed.

The oracle records a released binary's exact behaviour (bytes + effects) per cell and replays it
against any later binary. Its cell list must be a function of what the PRODUCT claims to support, so
a new method, dialect or transport becomes a new cell automatically (an uncovered cell is RED) and
no one can forget one. That derivation is a fact about the product, so it does not live here.

WHAT THIS FILE IS: the ENGINE, and nothing else. Argument handling, the duplicate-id guard, the doc
assembly, the byte-compare, the per-family floor and the write. It contains no cell, no fixture
name, no inventory name and no vocabulary from any product. It is the same code for every product
the oracle judges, which is the point: two products' corpora are generated, checked and floored by
one implementation, and a change to how a corpus is gated is a change in one place.

WHERE THE PRODUCT HALF LIVES: `$BUSBAR_ORACLE_DATA/cells/__init__.py`, a Python module the product
owns and this engine imports BY PATH. A frozen JSON cell list was the obvious alternative and is the
wrong one: most of a corpus is derived by looping over the product's own inventories, and freezing
the result would destroy the invariant the derivation exists to enforce.

THE CONTRACT that module exports (its own docstring is the normative copy):

  bind(root, data)      bind the module to a checkout: it sets its ROOT/DATA and derives every
                        fixture, inventory and relative path from them. Called before any builder.
  COMMENT: list[str]    the `_comment` lines of the generated document's header (the product's text)
  DERIVED_FROM: dict    the `derived_from` block: what the corpus is derived from, root-relative
  OUTCOME_ROWS: list    the `outcomes` block, already flattened to {"outcome": …, "why": …}
  BUILDERS: list        ordered (name, callable); each callable takes nothing and returns list[dict]
  SHRINK_PROBE: callable() -> (list[dict], set[str])
                        --selftest's planted loss: repoint one family's fixture at a path that
                        cannot exist, call its REAL builder, and hand back what it yielded (which
                        must be []) plus the family names that builder owns. The engine proves the
                        floor discriminates on that, without ever naming a product's builder.

LOCATION COMES FROM THE CLI, NEVER FROM __file__. This file used to sit inside the product and climb
two directories out of itself to find it; that only ever worked while the judge lived in the tree it
judged. Both locations are now arguments:

  BUSBAR_ORACLE_DATA         the product's oracle data dir — where cells.json and cells/ live
  BUSBAR_ORACLE_PRODUCT_ROOT the product repo root — what paths in the document are rendered against

Usage:  busbar-oracle --product-root <dir> --data <dir> cells [--write] [--summary] [--check]
                                                              [--selftest] [--accept-family-shrink F]
        --check regenerates to memory and exits non-zero if the checked-in cells.json has
        drifted from it (a hand edit, or a generator change nobody ran --write for), so the
        oracle's owed set is the set the generator derives and not a list someone edited.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

MODULE_NAME = "busbar_oracle_product_cells"


def location(var: str, flag: str, what: str) -> Path:
    """One of the two locations the CLI provides. Never inferred: an engine that guessed its own
    position in a product tree is exactly what stopped working when the oracle left that tree."""
    raw = os.environ.get(var, "").strip()
    if not raw:
        sys.exit(f"busbar-oracle cells: {var} is not set — name {what} with "
                 f"`busbar-oracle {flag} <dir> cells ...`")
    path = Path(raw).expanduser()
    if not path.is_dir():
        sys.exit(f"busbar-oracle cells: {var}={raw} is not a directory (`busbar-oracle {flag} <dir>`)")
    return path.resolve()


def load_product_cells(root: Path, data: Path):
    """Import the product's cell module by path and bind it to this checkout."""
    path = data / "cells" / "__init__.py"
    if not path.exists():
        sys.exit(f"busbar-oracle cells: {path} does not exist — the data dir named by "
                 f"`busbar-oracle --data <dir>` carries the product's own cell module")
    spec = importlib.util.spec_from_file_location(MODULE_NAME, path)
    if spec is None or spec.loader is None:
        sys.exit(f"busbar-oracle cells: {path} is not importable as a Python module")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    missing = [n for n in ("bind", "COMMENT", "DERIVED_FROM", "OUTCOME_ROWS", "BUILDERS", "SHRINK_PROBE")
               if not hasattr(mod, n)]
    if missing:
        sys.exit(f"busbar-oracle cells: {path} does not export {', '.join(missing)} — see this "
                 f"file's header for the contract")
    mod.bind(root, data)
    return mod


def accepted_shrinks(argv) -> set:
    """`--accept-family-shrink NAME` / `--accept-family-shrink=NAME`, repeatable."""
    out = {a.split("=", 1)[1] for a in argv if a.startswith("--accept-family-shrink=")}
    for i, a in enumerate(argv):
        if a == "--accept-family-shrink" and i + 1 < len(argv):
            out.add(argv[i + 1])
    return out


def family_floor_problems(was: dict, now: dict, accepted: set) -> list:
    """Every family whose generated cell count fell below the committed one, unless accepted."""
    problems = []
    for fam in sorted(was):
        if fam in accepted:
            continue
        want = was[fam]
        if not isinstance(want, int):
            continue
        got = now.get(fam, 0)
        if got < want:
            problems.append(
                f"family {fam!r}: the generator now yields {got} cell(s), {want} are committed."
                + (" The family is GONE — its fixture is missing, and every builder here returns []"
                   " for a missing fixture." if got == 0 else ""))
    return problems


def selftest(mod, out: Path) -> int:
    """Prove the per-family floor discriminates, by CONSTRUCTING the loss rather than hoping.

    The case that matters is the real one: point a family's fixture at a path that does not exist,
    exactly as a rename does, and watch its builder return [] without complaint. Under the old code
    that loss went straight into cells.json on the next `--write` and nothing anywhere objected;
    here the floor must refuse it.

    WHICH family is the product's business, not the engine's: SHRINK_PROBE plants the loss in the
    product's own module and reports which families it owns, so this stays a test of the floor
    rather than a second place that knows a product's fixture names.
    """
    bad = 0

    def say(ok, msg):
        nonlocal bad
        print(f"  [{'ok' if ok else 'FAILED'}] {msg}")
        if not ok:
            bad += 1

    if not out.exists():
        print(f"  [FAILED] {out} does not exist — there is no committed floor to prove")
        return 1
    was = (json.loads(out.read_text()).get("counts") or {}).get("by_family") or {}
    say(bool(was), f"the committed corpus records {len(was)} family/families as the floor")

    say(not family_floor_problems(was, dict(was), set()),
        "an unchanged corpus passes the floor")

    # A MISSING FIXTURE, PLANTED. The product repoints one family's fixture at a path that cannot
    # exist and calls the REAL builder: it must return [] (that is the hazard), and the floor must
    # refuse the result.
    lost, probed = mod.SHRINK_PROBE()
    say(lost == [],
        "a missing fixture makes its builder return [] silently — the hazard, reproduced")

    fams = {f for f in was if f in probed}
    say(bool(fams), f"the committed corpus owes {len(fams)} probed family/families: {sorted(fams)}")
    shrunk = {f: (0 if f in fams else was[f]) for f in was}
    problems = family_floor_problems(was, shrunk, set())
    say(len(problems) == len(fams),
        f"losing every probed family is REFUSED ({len(problems)} problem(s) reported)")

    say(bool(family_floor_problems(was, {f: max(0, v - 1) for f, v in was.items()}, set())),
        "losing even ONE cell from a family is REFUSED")

    say(not family_floor_problems(was, shrunk, fams),
        "--accept-family-shrink names the loss and lets it through, one family at a time")

    if bad:
        print(f"\nSELFTEST FAILED: {bad} check(s) did not hold")
        return 1
    print("\nenumerate-cells selftest: the per-family floor holds")
    return 0


def main() -> int:
    root = location("BUSBAR_ORACLE_PRODUCT_ROOT", "--product-root", "the product being judged")
    data = location("BUSBAR_ORACLE_DATA", "--data", "the product's oracle data dir")
    mod = load_product_cells(root, data)
    out = data / "cells.json"
    rel = out.relative_to(root) if out.is_relative_to(root) else out

    if "--selftest" in sys.argv:
        return selftest(mod, out)

    cells = sorted((c for _, build in mod.BUILDERS for c in build()), key=lambda c: c["id"])
    ids = [c["id"] for c in cells]
    # A REAL CHECK, NOT AN `assert`. This ran as a bare `assert`, which `python3 -O` removes
    # outright — so the one statement standing between a duplicate cell id and a silently
    # half-recorded corpus was optional at runtime. Duplicate ids are not a programming slip to
    # catch in development: the recorder and the replayer both key on the id, so a duplicate means
    # one of the two cells is never recorded and never compared, and the counts still add up.
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        sys.stderr.write("enumerate-cells: duplicate cell id(s) — the recorder and the replayer key "
                         "on the id, so one of each pair would never be recorded or compared:\n")
        for d in dupes:
            sys.stderr.write(f"  {d}\n")
        return 2
    doc = {
        "_comment": list(mod.COMMENT),
        "derived_from": dict(mod.DERIVED_FROM),
        "outcomes": list(mod.OUTCOME_ROWS),
        "counts": {
            "total": len(cells),
            "by_plane": {p: sum(1 for c in cells if c["plane"] == p) for p in sorted({c["plane"] for c in cells})},
            "by_family": {f: sum(1 for c in cells if c.get("family", c["plane"]) == f)
                          for f in sorted({c.get("family", c["plane"]) for c in cells})},
        },
        "cells": cells,
    }
    rendered = json.dumps(doc, indent=2) + "\n"

    # ── THE PER-FAMILY FLOOR ──────────────────────────────────────────────────────────────────────
    # EVERY builder on the product side opens with `if not <FIXTURE>.exists(): return []`. That is a
    # sane guard against a crash and a terrible one against a mistake: a fixture that is renamed,
    # moved, or simply not present in the checkout contributes ZERO cells, the generator exits 0, and
    # the family vanishes from the corpus. Nothing downstream can notice — cells.json IS the owed
    # set, so the recorder records one family fewer, the replayer compares one family fewer, and the
    # parity report says "0 divergences" over a corpus that quietly lost, say, every admin cell.
    # `--write` then commits the shrunken corpus as the new truth in the same command.
    #
    # So the COMMITTED cells.json's own `counts.by_family` is the floor. A family that shrinks, or
    # disappears, is refused — on `--check` and on `--write` alike, because `--write` is what would
    # otherwise launder the loss into the baseline the next `--check` measures against. A real,
    # reviewed shrink is `--accept-family-shrink <family>`, once per family, which puts the loss in
    # the command line of the commit that makes it.
    floor_problems = []
    if out.exists():
        try:
            committed = json.loads(out.read_text())
        except json.JSONDecodeError:
            committed = None
        if committed is not None:
            floor_problems = family_floor_problems(
                (committed.get("counts") or {}).get("by_family") or {},
                doc["counts"]["by_family"],
                accepted_shrinks(sys.argv),
            )
    if floor_problems:
        sys.stderr.write("enumerate-cells: the generated corpus is SMALLER than the committed one:\n")
        for p in floor_problems:
            sys.stderr.write(f"  - {p}\n")
        sys.stderr.write("  A missing fixture makes a whole family return [] silently, and cells.json IS\n")
        sys.stderr.write("  the owed set: recorder, replayer and parity report would all agree, over a\n")
        sys.stderr.write("  corpus that lost the family. Restore the fixture, or accept the shrink by name:\n")
        sys.stderr.write("    busbar-oracle cells --write --accept-family-shrink <family>\n")
        return 1
    # --check: regenerate to MEMORY and compare against the checked-in cells.json. cells.json is the
    # oracle's owed set — the recorder and the replayer both iterate it — so a tree whose generator
    # and whose committed cell list disagree is a gate measuring a cell set nobody reviewed. A hand
    # edit (the file's own header says "do not edit by hand") and a generator change someone forgot
    # to --write both land here, and both are red.
    if "--check" in sys.argv:
        if not out.exists():
            sys.stderr.write(f"enumerate-cells --check: {out} does not exist — run "
                             f"busbar-oracle cells --write\n")
            return 1
        have = out.read_text()
        if have == rendered:
            print(f"ok  {rel} matches the generator ({len(cells)} cells)")
            return 0
        have_doc = json.loads(have) if have.strip() else {"cells": []}
        have_ids = {c["id"] for c in have_doc.get("cells", [])}
        want_ids = {c["id"] for c in cells}
        sys.stderr.write(f"enumerate-cells --check: DRIFT — {rel} is not what the generator "
                         f"produces ({len(have_ids)} committed cells vs "
                         f"{len(want_ids)} generated)\n")
        for cid in sorted(want_ids - have_ids)[:20]:
            sys.stderr.write(f"  generated but NOT committed: {cid}\n")
        for cid in sorted(have_ids - want_ids)[:20]:
            sys.stderr.write(f"  committed but NOT generated: {cid}\n")
        if have_ids == want_ids:
            sys.stderr.write("  the id sets agree: a cell DEFINITION (or the counts/outcomes header) changed\n")
        sys.stderr.write("  regenerate with: busbar-oracle cells --write\n")
        return 1
    if "--summary" in sys.argv or "--write" not in sys.argv:
        print(json.dumps(doc["counts"], indent=2))
    if "--write" in sys.argv:
        out.write_text(rendered)
        print(f"wrote {rel} ({len(cells)} cells)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
