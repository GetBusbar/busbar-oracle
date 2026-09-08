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
    # NO __pycache__ IN THE JUDGED TREE. `exec_module` on a path inside the PRODUCT writes
    # `<data>/cells/__pycache__/` as a side effect of generating the corpus, so the act of asking
    # what the corpus is dirties the working tree the gate then asserts is clean, and leaves a file
    # the tool/data segregation walk cannot read as source. The product's shim exports
    # PYTHONDONTWRITEBYTECODE for the same reason; this makes it true however the engine was invoked.
    _bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = _bytecode
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


def committed_floor(out: Path) -> tuple:
    """The per-family floor the COMMITTED cells.json declares, or a REASON it cannot be read.

    Returns `(floor, problem)`; exactly one of the two is meaningful. A non-None `problem` is fatal
    at every call site — never a reason to carry on with an empty floor.

    THIS USED TO SWALLOW `json.JSONDecodeError` AND CONTINUE WITH NO FLOOR AT ALL. That is the worst
    available answer to a reference file it cannot read. cells.json IS the owed set: a truncated
    write, a bad merge conflict, a half-finished hand edit made the file unparseable, the floor was
    skipped entirely, every family was free to shrink to nothing, and `--write` then committed the
    shrunken corpus AS THE NEW REFERENCE in the same command — after which the next `--check` had
    nothing to measure against and agreed. The corrupt file was the one thing standing between the
    generator and the baseline, and its corruption was the trigger for ignoring it.
    A reference that cannot be read is not a reference that says "anything goes".

    The COUNTS are validated as well, and for the same reason: a `by_family` entry that is not a
    non-negative integer (a null a hand edit left behind, a string, `true`) used to be skipped
    family-by-family, so exactly the families whose committed count had been damaged were the
    families with no floor.
    """
    try:
        text = out.read_text()
    except OSError as e:
        return {}, f"{out} exists but could not be read ({e})"
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        return {}, (f"{out} is not valid JSON ({e}). It is the committed corpus — the per-family "
                    f"floor is measured against it — so a corpus generated now cannot be proven not "
                    f"to have shrunk. Restore the file (git checkout) before regenerating.")
    if not isinstance(doc, dict):
        return {}, f"{out} is not a JSON object, so it declares no counts.by_family floor"
    counts = doc.get("counts")
    by_family = counts.get("by_family") if isinstance(counts, dict) else None
    if not isinstance(by_family, dict) or not by_family:
        return {}, (f"{out} carries no `counts.by_family` object, so there is no committed floor to "
                    f"hold this generation to")
    bad = sorted(f for f, v in by_family.items()
                 if not isinstance(v, int) or isinstance(v, bool) or v < 0)
    if bad:
        return {}, (f"{out}'s `counts.by_family` gives no non-negative integer count for "
                    f"{', '.join(repr(f) for f in bad)}. A family whose committed count cannot be "
                    f"read has no floor, which is the one family that most needs one.")
    return by_family, None


def unknown_shrinks(accepted: set, floor: dict) -> list:
    """`--accept-family-shrink` names that no committed family answers to.

    The flag exists to put a reviewed loss in the command line of the commit that makes it. A
    MISSPELLED one accepted nothing and refused nothing: it read as a signed-off shrink to the human
    typing it while the family it meant to name went on being floored (or, if the typo happened to
    be the family that was really shrinking, it did not). Either way the operator was told something
    untrue about what they had just accepted."""
    return sorted(a for a in accepted if a not in floor)


def floor_verdict(out: Path, generated: dict, argv) -> tuple:
    """The whole floor decision, as `main` makes it: `(rc, [messages])`, rc 0 meaning "proceed".

    Factored out so the self-test drives THIS function and not a copy of the rule — the reason the
    corrupt-reference hole survived is that the only thing exercising the floor was a helper the
    swallowed exception sat above."""
    if not out.exists():
        return 0, []
    floor, problem = committed_floor(out)
    if problem:
        return 1, ["enumerate-cells: refusing to generate against an unreadable reference:",
                   f"  - {problem}"]
    accepted = accepted_shrinks(argv)
    unknown = unknown_shrinks(accepted, floor)
    if unknown:
        return 1, ["enumerate-cells: --accept-family-shrink names a family the committed corpus "
                   "does not have:",
                   *[f"  - {u!r}" for u in unknown],
                   f"  Known families: {', '.join(sorted(floor))}"]
    problems = family_floor_problems(floor, generated, accepted)
    if problems:
        return 1, ["enumerate-cells: the generated corpus is SMALLER than the committed one:",
                   *[f"  - {p}" for p in problems],
                   "  A missing fixture makes a whole family return [] silently, and cells.json IS",
                   "  the owed set: recorder, replayer and parity report would all agree, over a",
                   "  corpus that lost the family. Restore the fixture, or accept the shrink by name:",
                   "    busbar-oracle cells --write --accept-family-shrink <family>"]
    return 0, []


def family_floor_problems(was: dict, now: dict, accepted: set) -> list:
    """Every family whose generated cell count fell below the committed one, unless accepted.

    `was` has already been validated by committed_floor(): a count that is not a non-negative
    integer is refused there rather than skipped here, because skipping it dropped the floor for
    precisely the family whose committed number was damaged."""
    problems = []
    for fam in sorted(was):
        if fam in accepted:
            continue
        want = was[fam]
        if not isinstance(want, int) or isinstance(want, bool) or want < 0:
            problems.append(f"family {fam!r}: the committed count {want!r} is not a cell count")
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

    # ── THE REFERENCE ITSELF ────────────────────────────────────────────────────────────────────
    # Every case above measures a generation against a floor that was read successfully. These
    # measure what happens when the floor CANNOT be read, which was the hole: the decode error was
    # swallowed, the floor was skipped, and `--write` committed whatever the generator produced.
    # They drive floor_verdict() — the function main() calls — against real files in a temp dir, so
    # the case is a test of the shipped decision and not of a restatement of it.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="enumerate-cells-selftest.") as td:
        tdp = Path(td)
        full = {"counts": {"by_family": dict(was)}, "cells": []}
        # the same generation that passed above, but against a reference that will not parse
        corrupt = tdp / "corrupt.json"
        corrupt.write_text(json.dumps(full)[:-3])
        rc, msgs = floor_verdict(corrupt, dict(was), [])
        say(rc != 0 and any("not valid JSON" in m for m in msgs),
            "a CORRUPT cells.json is refused, not silently treated as 'no floor'")
        # and the shrink it was hiding: with the reference unreadable, the loss below used to pass
        rc_shrunk, _ = floor_verdict(corrupt, {f: 0 for f in was}, [])
        say(rc_shrunk != 0,
            "a corpus that lost EVERY family is still refused when the reference is corrupt "
            "(the corrupt reference cannot launder the loss)")

        # a committed count that is not a cell count leaves that family with no floor
        damaged = tdp / "damaged.json"
        one = sorted(was)[0]
        by_fam = dict(was); by_fam[one] = None
        damaged.write_text(json.dumps({"counts": {"by_family": by_fam}, "cells": []}))
        rc, msgs = floor_verdict(damaged, dict(was), [])
        say(rc != 0 and any(repr(one) in m for m in msgs),
            f"a committed count that is not a number is refused by name ({one!r}), never skipped")

        # a floor with no counts at all is a floor that proves nothing
        empty = tdp / "empty-counts.json"
        empty.write_text(json.dumps({"cells": []}))
        rc, _ = floor_verdict(empty, dict(was), [])
        say(rc != 0, "a cells.json with no counts.by_family is refused as a reference")

        good = tdp / "good.json"
        good.write_text(json.dumps(full))
        rc, _ = floor_verdict(good, dict(was), [])
        say(rc == 0, "a readable reference and an unchanged corpus still proceed")

        # --accept-family-shrink is a claim about a family that exists
        rc, msgs = floor_verdict(good, {f: 0 for f in was}, ["--accept-family-shrink", one + "-typo"])
        say(rc != 0 and any("does not have" in m for m in msgs),
            "--accept-family-shrink naming a family the corpus does not have is refused")
        rc, _ = floor_verdict(good, {**{f: was[f] for f in was}, one: 0}, ["--accept-family-shrink", one])
        say(rc == 0, "--accept-family-shrink naming a REAL family still accepts its loss")

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
    floor_rc, floor_msgs = floor_verdict(out, doc["counts"]["by_family"], sys.argv)
    for m in floor_msgs:
        sys.stderr.write(m + "\n")
    if floor_rc != 0:
        return floor_rc
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
        # ATOMIC, because the file being written IS the reference the next run's floor is measured
        # against. `out.write_text` truncates first: a write interrupted (^C, a full disk, an OOM)
        # left a TRUNCATED cells.json behind — unparseable, or worse, parseable with fewer cells —
        # and the corpus the whole gate owes is then whatever survived the interruption.
        tmp = out.with_name(out.name + ".tmp")
        try:
            tmp.write_text(rendered)
            os.replace(tmp, out)
        finally:
            if tmp.exists():
                tmp.unlink()
        print(f"wrote {rel} ({len(cells)} cells)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
