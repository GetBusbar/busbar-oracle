#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Busbar Inc and contributors
"""The data half of tests/fixture-product/selftest.sh.

Every check here is a cross-reference between two files that can drift apart, or between a file and
the binary that produced it. Nothing here asserts that a file EXISTS and stops: an oracle data
directory whose files are all present and mutually contradictory is exactly the state the oracle's
own self-tests cannot see, because they read one file each.

  check-data.py <data-dir> <stub-path> <version-string> <live-chat-body>

Prints one line per check and exits 1 if any failed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys

CLASSES_REQUIRED_IN_CELL = ("status", "headers", "body", "effects")
FAILURES = []


def ok(msg):
    print(f"  ok    {msg}")


def bad(msg):
    print(f"  FAIL  {msg}")
    FAILURES.append(msg)


def load_json(path, label):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except OSError as e:
        bad(f"{label} cannot be read: {e}")
    except ValueError as e:
        bad(f"{label} is not valid JSON: {e}")
    return None


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def rows(path):
    """The non-comment, non-blank rows of a .tsv pin file, split on tabs."""
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            out.append(line.split("\t"))
    return out


def safe_name(cell_id):
    return cell_id.replace("|", "__")


def main(argv):
    data, stub, version, chat_body_path = argv[0], argv[1], argv[2], argv[3]

    # --- the corpus ------------------------------------------------------------------------
    corpus = load_json(os.path.join(data, "cells.json"), "cells.json")
    cells = []
    if corpus is not None:
        cells = corpus["cells"] if isinstance(corpus, dict) and "cells" in corpus else corpus
        ids = [c.get("id") for c in cells]
        if len(ids) != len(set(ids)):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            bad(f"cells.json has duplicate cell id(s) {dupes}; the later row silently wins everywhere")
        elif all(ids):
            ok(f"cells.json holds {len(ids)} cells with unique ids")
        else:
            bad("cells.json has a cell with no id")
        declared = (corpus.get("counts") or {}).get("total") if isinstance(corpus, dict) else None
        if declared is not None and declared != len(cells):
            bad(f"cells.json declares counts.total={declared} but holds {len(cells)} cells")
        elif declared is not None:
            ok(f"cells.json's counts.total agrees with the corpus ({declared})")

        # every script cell names a driver that is actually in scripts/
        for c in cells:
            spec = c.get("script")
            if not spec:
                continue
            p = os.path.join(data, "scripts", spec["name"])
            if not os.path.isfile(p):
                bad(f"cell {c['id']} names driver scripts/{spec['name']}, which is not in the tree")
        named = {c["script"]["name"] for c in cells if c.get("script")}
        present = {f for f in os.listdir(os.path.join(data, "scripts")) if f.endswith(".sh")}
        if named and named == present:
            ok(f"every driver in scripts/ is named by a cell and vice versa ({', '.join(sorted(named))})")
        elif named <= present:
            bad(f"scripts/ holds driver(s) no cell names: {sorted(present - named)}; an orphan driver is "
                f"never exercised and its rules are never proven")
        # a cell that narrows `compare` or orders a `mock_control` is a waiver; the fixture has none
        for c in cells:
            for key in ("compare", "mock_control"):
                if c.get(key) is not None:
                    bad(f"cell {c['id']} carries `{key}`: the fixture product must not teach the shape "
                        f"of a waiver to whoever copies it")

    # --- the store map, both directions ----------------------------------------------------
    script = os.path.join(data, "scripts", "store-persist.sh")
    if not os.path.isfile(script):
        bad("scripts/store-persist.sh is missing; fixture-gate-selftest case (f) hard-errors without it")
    else:
        with open(script, encoding="utf-8") as fh:
            text = fh.read()
        if "case \"$PLUGIN\" in" not in text:
            bad("store-persist.sh has no `case \"$PLUGIN\" in`; fixture-gate case (f) reads its arms")
        arms = dict(re.findall(r'^\s*([A-Za-z0-9_.-]+)\)\s*URL_VAR=([A-Z_][A-Z0-9_]*)\s*;;', text, re.M))
        gated = {c["id"].split("|", 1)[1]: c["needs_fixture"]
                 for c in cells
                 if c.get("id", "").startswith("plugins.store-persist|")
                 and isinstance(c.get("needs_fixture"), str)}
        if not gated:
            bad("cells.json has no env-gated plugins.store-persist cell; fixture-gate case (f) exits "
                "non-zero on an empty set, so the gate would have nothing to prove")
        for plugin, var in sorted(gated.items()):
            if arms.get(plugin) != var:
                bad(f"cell plugins.store-persist|{plugin} is gated on {var} but store-persist.sh reads "
                    f"{arms.get(plugin)!r}")
        for plugin, var in sorted(arms.items()):
            if gated.get(plugin) != var:
                bad(f"store-persist.sh maps {plugin} -> {var} but no cell is gated on it; the arm is dead "
                    f"and the rename it would catch would go uncaught")
        if gated and all(arms.get(p) == v for p, v in gated.items()) and \
           all(gated.get(p) == v for p, v in arms.items()):
            ok(f"the cell corpus and store-persist.sh name the same variable for all {len(gated)} store(s)")

    # --- the registers ---------------------------------------------------------------------
    for name in ("accepted-differences.json", "accepted-gaps.json"):
        reg = load_json(os.path.join(data, name), name)
        if reg is None:
            continue
        if not isinstance(reg, dict) or not isinstance(reg.get("accepted"), list):
            bad(f"{name} has no `accepted` list; the differ's loader reads that key")
        elif reg["accepted"]:
            bad(f"{name} is not empty: the fixture product has no behaviour to forgive and nobody to "
                f"sign for a waiver, so an entry here is a fixture pretending to be a product")
        else:
            ok(f"{name} loads and is empty, as a fixture's register must be")

    # --- the floor and the golden ----------------------------------------------------------
    baseline_path = os.path.join(data, "owed-baseline.txt")
    baseline = []
    if os.path.isfile(baseline_path):
        with open(baseline_path, encoding="utf-8") as fh:
            baseline = [line.strip() for line in fh if line.strip()]
    if not baseline:
        bad("owed-baseline.txt is empty; replay-selftest's owed-ratchet case skips silently without it")
    known = {c["id"] for c in cells}
    stray = [i for i in baseline if i not in known]
    if stray:
        bad(f"owed-baseline.txt names id(s) that are not in cells.json: {stray}")
    elif baseline:
        ok(f"every id in owed-baseline.txt is a cell in cells.json ({len(baseline)})")

    gold = os.path.join(data, "golden", "1.5.5")
    ledger = os.path.join(gold, "ledger.tsv")
    passes = []
    if not os.path.isfile(ledger):
        bad("golden/1.5.5/ledger.tsv is missing; replay-selftest's owed-ratchet case then SKIPS "
            "SILENTLY rather than asserting anything")
    else:
        for row in rows(ledger):
            if len(row) < 2:
                bad(f"golden ledger row is not tab-separated: {row!r}")
            elif row[1] == "PASS":
                passes.append(row[0])
        unowed = [i for i in passes if i not in baseline]
        if unowed:
            bad(f"golden PASS id(s) missing from owed-baseline.txt: {unowed}")
        elif passes:
            ok(f"every golden PASS id is named in owed-baseline.txt ({len(passes)})")
        else:
            bad("the golden ledger has no PASS row, so the ratchet case would prove nothing")

    live = None
    if os.path.isfile(chat_body_path):
        live = load_json(chat_body_path, "the live chat completion the listener just answered")
    for cid in passes:
        p = os.path.join(gold, "cells", safe_name(cid) + ".json")
        cell = load_json(p, f"golden cell {cid}")
        if cell is None:
            continue
        missing = [k for k in CLASSES_REQUIRED_IN_CELL if k not in cell]
        if missing:
            bad(f"golden cell {cid} has no {missing}; the differ compares those keys")
            continue
        # The recorded body must be what the binary SAYS TODAY. This is the check that makes the
        # golden a recording rather than a wish.
        if cid == "cli|--version" and cell["body"] != version + "\n":
            bad(f"golden cell {cid} records body {cell['body']!r} but the binary prints {version!r}")
        elif cid == "http|chat|ok" and live is not None:
            try:
                recorded = json.loads(cell["body"])
            except ValueError:
                recorded = None
            if recorded != live:
                bad(f"golden cell {cid} records a completion the listener does not answer today "
                    f"(recorded {recorded}, live {live})")
    if passes and not FAILURES:
        ok(f"every golden PASS row has a cell file whose recorded answer is the one the binary gives "
           f"today ({len(passes)})")

    # --- the pins --------------------------------------------------------------------------
    pin = os.path.join(data, "oracle.pin")
    if not os.path.isfile(pin):
        bad("oracle.pin is missing; harness-rev.sh hashes it and replay-selftest requires it by name")
    else:
        with open(pin, encoding="utf-8") as fh:
            text = fh.read()
        tag = re.search(r'^tag=(\S+)$', text, re.M)
        sha = re.search(r'^sha256=([0-9a-f]{64})$', text, re.M)
        if tag and sha:
            ok(f"oracle.pin names a tag ({tag.group(1)}) and a 64-hex sha256")
        else:
            bad("oracle.pin has no `tag=` line and/or no `sha256=<64 hex>` line")

    gd = os.path.join(data, "golden-digests.tsv")
    if not os.path.isfile(gd):
        bad("golden-digests.tsv is missing")
    else:
        gd_rows = rows(gd)
        if not gd_rows:
            bad("golden-digests.tsv has no rows, so the digest pin pins nothing")
        for row in gd_rows:
            if len(row) != 3 or not re.fullmatch(r'[0-9a-f]{64}', row[2]):
                bad(f"golden-digests.tsv row is not <version> <asset> <64-hex>: {row!r}")
            elif row[1] == "busbar-stub":
                got = sha256_of(stub)
                if got == row[2]:
                    ok("golden-digests.tsv pins the sha256 the stub binary actually hashes to")
                else:
                    bad(f"golden-digests.tsv pins {row[2][:16]}… for busbar-stub, which hashes to "
                        f"{got[:16]}…; re-pin with `shasum -a 256 tests/fixture-product/busbar-stub`")

    pd = os.path.join(data, "plugin-digests.tsv")
    if not os.path.isfile(pd):
        bad("plugin-digests.tsv is missing")
    else:
        pd_rows = rows(pd)
        if len(pd_rows) < 2:
            bad("plugin-digests.tsv has fewer than two rows, so a walk over it barely walks")
        for row in pd_rows:
            if len(row) != 4 or not re.fullmatch(r'[0-9a-f]{64}', row[3]):
                bad(f"plugin-digests.tsv row is not <plugin> <tag> <asset> <64-hex>: {row!r}")
                continue
            artefact = os.path.join(data, "fixtures", row[2])
            if not os.path.isfile(artefact):
                bad(f"plugin-digests.tsv pins {row[2]}, which is not under fixtures/")
            elif sha256_of(artefact) != row[3]:
                bad(f"plugin-digests.tsv pins the wrong sha256 for {row[2]}; re-pin with "
                    f"`shasum -a 256 tests/fixture-product/data/fixtures/{row[2]}`")
        if not [f for f in FAILURES if "plugin-digests" in f]:
            ok(f"plugin-digests.tsv pins {len(pd_rows)} artefact(s), each hashing to what it claims")

    if FAILURES:
        print(f"  {len(FAILURES)} data check(s) failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
