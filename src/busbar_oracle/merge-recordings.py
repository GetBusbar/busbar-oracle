#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Busbar Inc and contributors
"""Merge several record.sh outputs (recorded with disjoint --filter sets) into one recording.

    merge-recordings.py --out <dir> [--allow-harness-skew] [--allow-host-skew]
                        [--pinned-binaries golden-digests.tsv] [--note TEXT] [--cells cells.json] <part>...
    merge-recordings.py --selftest

A recording is a set of cells: `cells/<id>.json`, `raw/<id>/`, one ledger row each, and a
`meta.json` that names the binary, its digest, the harness revision and the host. Recording in
parts is how a full golden fits under a wall-clock cap, runs on several cores, or has ONE stale
cell replaced by a fresh recording from the same published binary. A cell id present in two parts
is refused: the parts must be disjoint, or the merged ledger would carry two verdicts for one cell.

── WHAT IDENTIFIES A RECORDING'S SOURCE ────────────────────────────────────────────────────────
    IDENTITY = version + a digest that is one of the release's PINNED PER-TRIPLE BINARIES.

`version` is fatal on mismatch, always: two releases are two products.

The digest rule has a default and a general form, and the general one has to be asked for by name:

  * no --pinned-binaries  -> the digests must be EQUAL. With no pin table there is nothing to check
                             a foreign digest against, so the only safe reading of two digests is
                             that they are the same file.
  * --pinned-binaries <golden-digests.tsv>
                          -> each part's digest must be one of that version's pinned per-triple
                             `busbar-<triple>` rows. A release is a SET of binaries, one per triple,
                             and the table is the product's own enumeration of that set; a digest in
                             no row is refused exactly as before.

That general form exists because the strict one makes a real recording impossible rather than
unsafe. busbar's committed golden is `aarch64-apple-darwin`; the three store cells need live
postgres/mysql/valkey and can only be recorded on Linux CI, against `busbar-x86_64-unknown-linux-gnu`.
Same release, different files — so under digest equality those cells could never be merged in, and
would stay permanently unrecorded. What makes the cross-triple merge safe is not that the hosts
differ but that both digests are pinned, in the same table fetch-golden.sh verifies every download
and every cache hit against, and whose --check-golden path already accepts a foreign-triple digest.

The archive rows (`busbar-<triple>.tar.gz`, `.zip`) and the sidecar artifacts (`busbar-openapi-*.json`,
`busbar-*.cdx.json`) share that table and are NOT identity: a tarball's digest proves a download, not
the file record.sh executed.

`host_triple` says which machine did the recording, not which binary was recorded: the SAME
published binary can be legitimately re-recorded on a different host (an A8 store-cell rig runs
`x86_64-unknown-linux-gnu`; the committed golden is `aarch64-apple-darwin`). It is reportable, like
`harness_rev`, not fatal like `binary_sha256`:

  * parts agree                  -> merged as-is
  * parts differ, no flag        -> REFUSED, naming both hosts
  * parts differ, --allow-host-skew + --note
                                 -> merged; the host_triple of the LAST-RECORDED part (max `at`)
                                    becomes the merged `host_triple`, every other distinct triple is
                                    pushed onto `host_triple_history` with the part's path and its
                                    binary_sha256, and --note is recorded in `host_triple_note`. The
                                    binary sha must STILL satisfy the identity rule above either way:
                                    a different host recording the same bytes is fine, and so is a
                                    different host recording that release's OTHER pinned build under
                                    --pinned-binaries — but an unpinned binary never is, whatever the
                                    host said. `--allow-host-skew` is about machines, not about
                                    which build ran; it has never been able to relax the digest.

A PATH IS WHERE A FILE WAS, NOT WHAT IT WAS. meta.json's `binary` is the --bin argument as typed:
`/Users/runner/.cache/busbar-oracle/1.5.5/busbar` on a CI runner, `/Users/<you>/.cache/...` on a
laptop, both the same 11 MB of bytes with the same sha256. This file used to compare that string,
so the ONLY supported way to re-record one stale cell of a CI-recorded golden was to be that CI
runner — and the alternative people reached for instead was hand-editing golden cell files, which
is the one thing the shadow oracle must never do. binary_sha256 answers "is this the same binary?"
exactly, and fetch-golden.sh --check-golden already pins that digest to the published artifact.
The paths are not discarded: every part's own `binary` is recorded in the merged `merged_from`.

`harness_rev` (cells.json, the normalizer, the recorder, the fixtures — see harness-rev.sh) is a
different question: it says what was recorded and how it is normalized, so two parts under two revs
may genuinely disagree about a cell's bytes. It is REPORTED, not silently accepted:

  * parts agree                    -> merged as-is
  * parts differ, no flag          -> REFUSED, naming both revs
  * parts differ, --allow-harness-skew + --note
                                   -> merged; the rev of the LAST-RECORDED part (max `at`) becomes
                                      the merged `harness_rev`, every other distinct rev is pushed
                                      onto `harness_rev_history`, and --note is prepended to
                                      `harness_rev_note`. That note is the only place a reader
                                      learns WHICH cells were re-recorded under which rev and why,
                                      so it is REQUIRED for a skewed merge, not optional.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

# What actually identifies the source of a recording. `binary` (a filesystem path) is deliberately
# NOT here; `harness_rev` and `host_triple` are handled separately because they are reportable
# (skewable with a flag and a note) rather than fatal.
IDENTITY = ("version", "binary_sha256")

# THE TWO LOCATIONS, RESOLVED THE SAME WAY EVERY OTHER DRIVER RESOLVES THEM. Both used to be
# derived from THIS FILE's directory — `--cells` as `<tool>/cells.json`, and the `<repo>` token
# `_tokenize_binary_path` strips as `<tool>/../..`. That derivation was only ever right while the
# tool lived inside the product; an installed tool ships no cells.json, and `<tool>/../..` is
# site-packages. So a merge run through `busbar-oracle merge` wrote a merged ledger it could not
# order (missing cells.json) and left absolute machine paths in a COMMITTED meta.json, which is the
# one thing _tokenize_binary_path exists to prevent.
_HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("BUSBAR_ORACLE_DATA") or _HERE
PRODUCT_ROOT = os.environ.get("BUSBAR_ORACLE_PRODUCT_ROOT") or os.path.abspath(
    os.path.join(_HERE, "..", ".."))

# ── THE PINNED-BINARY IDENTITY ──────────────────────────────────────────────────────────────────
# `binary_sha256` equality is the right rule for parts of one recording made on one machine, and the
# wrong rule for the case that actually arises: a release is published as one binary PER TRIPLE, and
# a cell that needs a live backend can only be recorded on a runner that has one. busbar's committed
# golden is `aarch64-apple-darwin`; the three store cells can only be recorded on Linux CI against
# real postgres/mysql/valkey services. Those two recordings are of the SAME published release and of
# DIFFERENT files, so under strict equality they can never be merged — and the store cells stay
# permanently unrecorded, which is exactly the gap they were run to close.
#
# `--allow-host-skew` is not the answer either: it is deliberately documented as leaving the binary
# digest fatal, because "recorded somewhere else" must not become a licence to merge a recording of
# some other build. What makes the cross-triple case safe is not that the hosts differ but that both
# digests are PINNED — each is a row in golden-digests.tsv, the same table fetch-golden.sh verifies
# every download and every cache hit against, and the same table its --check-golden path already
# accepts a foreign-triple digest from. So the identity generalises without weakening:
#
#     merge identity = version + a digest that is one of the pinned per-triple rows for that version
#
# Equal digests remain the common case and need no table. A caller that wants the general rule names
# the table it means (`--pinned-binaries golden-digests.tsv`); a digest that is in no row is refused
# exactly as before, because an unpinned binary is an unknown binary whatever its host said.
#
# The pin table has two blocks. The ARCHIVE rows (`busbar-<triple>.tar.gz`, `.zip`) prove a
# download; the PER-TRIPLE BINARY rows (asset spelled `busbar-<triple>`, no extension) prove the
# file record.sh actually executes. Only the second block is identity, and the sidecar artifacts
# that live in the same block (`busbar-openapi-*.json`, `busbar-*.cdx.json`) are not binaries and
# are excluded by the same extension test fetch-golden.sh uses.


def _version_key(version) -> str:
    """The golden-digests.tsv version column for a meta.json `version` string.

    meta.json carries what the binary printed (`busbar 1.5.5`); the table is keyed on the bare
    release (`1.5.5`). Splitting on whitespace rather than stripping a `busbar ` prefix keeps this
    working if the product is ever renamed.
    """
    return str(version or "").split()[-1] if str(version or "").strip() else ""


def pinned_binaries(digests_path: str, version) -> dict:
    """{sha256: triple} for every pinned per-triple BINARY row of `version` in the digest table."""
    want = _version_key(version)
    out = {}
    try:
        with open(digests_path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError as e:
        sys.exit(f"merge-recordings: --pinned-binaries {digests_path}: {e.strerror}")
    for ln in lines:
        if ln.startswith("#") or not ln.strip():
            continue
        parts = ln.split("\t")
        if len(parts) < 3:
            continue
        ver, asset, sha = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if ver != want or not asset.startswith("busbar-"):
            continue
        if asset.endswith((".json", ".zip", ".tar.gz", ".tgz", ".txt", ".sig")):
            continue  # an archive or a sidecar artifact, not the binary that was executed
        out[sha] = asset[len("busbar-"):]
    if not out:
        sys.exit(f"merge-recordings: --pinned-binaries {digests_path} has no per-triple binary row "
                 f"for version {want!r}; there is nothing for the identity rule to check against")
    return out


def machine_independent_binary(path: str) -> str:
    """The `binary` bookkeeping path with the operator's name and home layout removed.

    A merged golden's meta.json is COMMITTED, and an absolute path under a personal home directory
    names a person and a machine — scripts/public-hygiene-lint.py's `machine-path` rule refuses
    exactly that in a published file. It is safe to rewrite because this field was never the
    identity: the identity above is the version and the pinned digest, and `binary` is carried only
    so a reader knows WHICH well-known location a part came from. That much is kept:

        under the repo          -> <repo>/target/release/busbar
        under the oracle cache  -> <oracle-cache>/1.5.5/busbar   (BUSBAR_ORACLE_CACHE or ~/.cache/busbar-oracle)
        elsewhere under $HOME   -> <home>/some/path/busbar
        elsewhere               -> unchanged (/usr/local/bin/busbar names no person)

    The SAME rule record.sh applies when it writes a fresh meta.json, so a merge of fresh parts is
    a no-op here and only an OLD part (recorded before that rule existed) is rewritten — and when
    one is, merge() says so on stdout rather than doing it silently.
    """
    if not isinstance(path, str) or not path.startswith("/"):
        return path
    repo = PRODUCT_ROOT
    home = os.environ.get("HOME") or "/nonexistent"
    cache = os.environ.get("BUSBAR_ORACLE_CACHE") or os.path.join(home, ".cache", "busbar-oracle")
    for root, token in ((repo, "<repo>"), (cache, "<oracle-cache>"), (home, "<home>")):
        if root and path.startswith(root.rstrip("/") + "/"):
            return token + "/" + path[len(root.rstrip("/")) + 1:]
    return path


def _load_meta(p: str) -> dict:
    mp = os.path.join(p, "meta.json")
    if not os.path.isfile(mp):
        sys.exit(f"merge-recordings: {p} has no meta.json (an unfinished or failed recording)")
    with open(mp, encoding="utf-8") as f:
        return json.load(f)


def merge(parts, out, allow_harness_skew=False, note=None, cells_json=None,
          allow_host_skew=False, pinned_binaries_tsv=None) -> int:
    metas = [(p, _load_meta(p)) for p in parts]
    base_p, base = metas[0]
    # `version` is fatal on inequality under both rules: two releases are two products, and no pin
    # table makes them one recording.
    for p, m in metas[1:]:
        if m.get("version") != base.get("version"):
            sys.exit(f"merge-recordings: {p} version={m.get('version')!r} but "
                     f"{base_p} version={base.get('version')!r}; "
                     "parts of one recording must come from the same release")
    pins = {}
    if pinned_binaries_tsv:
        # THE GENERAL IDENTITY: every part's digest must be a pinned per-triple row of the shared
        # version. Note this is not a relaxation of the strict rule so much as its statement in the
        # terms the release actually has — a release is a SET of binaries, one per triple, and the
        # table is the product's own enumeration of that set. A digest in no row is refused here
        # just as fetch-golden.sh refuses it on download, and for the same reason.
        pins = pinned_binaries(pinned_binaries_tsv, base.get("version"))
        unpinned = [(p, m.get("binary_sha256")) for p, m in metas if m.get("binary_sha256") not in pins]
        if unpinned:
            detail = "; ".join(f"{p} binary_sha256={s!r}" for p, s in unpinned)
            sys.exit(f"merge-recordings: {detail} — not a pinned {_version_key(base.get('version'))} "
                     f"busbar-<triple> row in {pinned_binaries_tsv}. An unpinned binary is an unknown "
                     "binary whatever host recorded it; pin it (fetch-golden.sh prints the row) or "
                     "re-record against a published release.")
    else:
        # THE STRICT RULE, unchanged, and still the default: with no table named there is nothing to
        # check a foreign digest against, so the only safe reading of two digests is that they must
        # be the same file.
        for p, m in metas[1:]:
            if m.get("binary_sha256") != base.get("binary_sha256"):
                sys.exit(f"merge-recordings: {p} binary_sha256={m.get('binary_sha256')!r} but "
                         f"{base_p} binary_sha256={base.get('binary_sha256')!r}; "
                         "parts of one recording must come from the same binary "
                         "(pass --pinned-binaries <golden-digests.tsv> to merge parts recorded "
                         "against two pinned per-triple builds of one release)")
    # The binary PATH is bookkeeping, not identity: say so out loud rather than refusing on it.
    # The merged meta.json is committed, so every `binary` that reaches it — the base's and each
    # part's in merged_from — is written in the machine-independent form (see the docstring above).
    # Announced, never silent: a reader of the merge output learns the path was rewritten.
    for p, m in metas:
        b = m.get("binary")
        nb = machine_independent_binary(b)
        if nb != b:
            print(f"merge-recordings: {os.path.basename(os.path.normpath(p))} recorded its binary as "
                  f"an absolute path; writing it as {nb} (the digest is the identity, not the path)")
            m["binary"] = nb
    paths = {m.get("binary") for _, m in metas}
    if len(paths) > 1:
        print(f"merge-recordings: parts name {len(paths)} different paths for the same binary "
              f"{base.get('binary_sha256', '?')[:12]} — merging on the digest, recording every path "
              "in merged_from")
    revs = {m.get("harness_rev") for _, m in metas}
    if len(revs) > 1:
        named = ", ".join(f"{os.path.basename(os.path.normpath(p))}={str(m.get('harness_rev'))[:12]}"
                          for p, m in metas)
        if not allow_harness_skew:
            sys.exit(f"merge-recordings: parts were recorded under {len(revs)} different harness "
                     f"revisions ({named}); their cells may differ because the harness changed, not "
                     "because the binary did. Re-record every part under one harness, or pass "
                     "--allow-harness-skew --note '<which cells, which rev, why>'.")
        if not note:
            sys.exit("merge-recordings: --allow-harness-skew needs --note saying which cells were "
                     "re-recorded under which harness_rev and why; a skewed merge with no note is "
                     "an unexplained golden")
        print(f"merge-recordings: HARNESS SKEW ALLOWED — {named}")
    triples = {m.get("host_triple") for _, m in metas}
    if len(triples) > 1:
        named_t = ", ".join(f"{os.path.basename(os.path.normpath(p))}={m.get('host_triple')}"
                            for p, m in metas)
        if not allow_host_skew:
            sys.exit(f"merge-recordings: parts were recorded on {len(triples)} different "
                     f"host_triple values ({named_t}); a cell's bytes may differ because the host "
                     "differed, not because the binary did. Re-record every part on one host, or "
                     "pass --allow-host-skew --note '<which cells, which host, why>'.")
        if not note:
            sys.exit("merge-recordings: --allow-host-skew needs --note saying which cells were "
                     "recorded on which host and why; a skewed merge with no note is an "
                     "unexplained golden")
        print(f"merge-recordings: HOST SKEW ALLOWED — {named_t}")
    if os.path.exists(out):
        sys.exit(f"merge-recordings: {out} exists; refusing to merge over it")
    os.makedirs(os.path.join(out, "cells"))
    os.makedirs(os.path.join(out, "raw"))
    seen, rows, recorded = {}, [], 0
    for p, m in metas:
        with open(os.path.join(p, "ledger.tsv"), encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                cid = line.split("\t", 1)[0]
                if cid in seen:
                    sys.exit(f"merge-recordings: cell {cid!r} is in both {seen[cid]} and {p}; parts must be disjoint")
                seen[cid] = p
                rows.append(line if line.endswith("\n") else line + "\n")
        for sub in ("cells", "raw"):
            src = os.path.join(p, sub)
            if not os.path.isdir(src):
                continue
            for name in os.listdir(src):
                dst = os.path.join(out, sub, name)
                if os.path.exists(dst):
                    sys.exit(f"merge-recordings: {sub}/{name} is in two parts; parts must be disjoint")
                s = os.path.join(src, name)
                shutil.copytree(s, dst) if os.path.isdir(s) else shutil.copy2(s, dst)
        recorded += int(m.get("recorded", 0))
    # IN cells.json ORDER, not part-by-part. record.sh walks cells.json and appends as it goes, so
    # every recording's ledger is in that order — concatenating the parts instead moves a
    # re-recorded cell's row to the end of the file, and the checked-in golden then shows 2N changed
    # ledger lines for N rows whose bytes did not change at all. An id cells.json does not know
    # keeps its encounter order, after the ones it does.
    if cells_json and os.path.isfile(cells_json):
        with open(cells_json, encoding="utf-8") as f:
            order = {c["id"]: i for i, c in enumerate(json.load(f)["cells"])}
        rows.sort(key=lambda ln: order.get(ln.split("\t", 1)[0], len(order)))
    with open(os.path.join(out, "ledger.tsv"), "w", encoding="utf-8") as f:
        f.writelines(rows)
    meta = dict(base)
    meta["recorded"] = recorded
    meta["merged_from"] = [{"part": os.path.basename(os.path.normpath(p)), "recorded": m.get("recorded", 0),
                            "at": m.get("at"), "binary": m.get("binary"), "harness_rev": m.get("harness_rev")}
                           for p, m in metas]
    meta["at"] = max(m.get("at", "") for _, m in metas)
    if pins and len({m.get("binary_sha256") for _, m in metas}) > 1:
        # The merged meta stamps ONE binary_sha256 (the base's), so without this the fact that the
        # cells came from two pinned builds of one release would survive only in the merge note —
        # prose, which nothing can check. Written as data, per part, naming the triple each digest
        # is pinned as, so a reader (and diff-cells' provenance gate) can see which file produced
        # which cells without going back to the table.
        meta["binary_sha256_pins"] = [
            {"part": os.path.basename(os.path.normpath(p)),
             "binary_sha256": m.get("binary_sha256"),
             "pinned_as": f"busbar-{pins.get(m.get('binary_sha256'))}"}
            for p, m in metas
        ]
    if len(revs) > 1:
        # The merged recording is stamped with the rev of the part recorded LAST, and every other
        # rev its cells actually came from is kept in the history rather than dropped on the floor.
        newest = max(metas, key=lambda pm: pm[1].get("at", ""))[1]
        meta["harness_rev"] = newest.get("harness_rev")
        history = list(meta.get("harness_rev_history", []))
        for r in sorted(revs - {newest.get("harness_rev")}):
            if r and r not in history:
                history.append(r)
        meta["harness_rev_history"] = history
    if len(triples) > 1:
        # Same rule as harness_rev above: stamp the host the LAST part was recorded on, keep every
        # other host in history with the part that recorded on it and that part's binary_sha256 (so
        # a reader can see, without opening the part, that the bytes matched even though the host
        # didn't).
        newest_t = max(metas, key=lambda pm: pm[1].get("at", ""))[1].get("host_triple")
        meta["host_triple"] = newest_t
        history_t = list(meta.get("host_triple_history", []))
        for p, m in metas:
            t = m.get("host_triple")
            if t == newest_t:
                continue
            entry = {"host_triple": t, "part": os.path.basename(os.path.normpath(p)),
                     "binary_sha256": m.get("binary_sha256")}
            if entry not in history_t:
                history_t.append(entry)
        meta["host_triple_history"] = history_t
        if note:
            prev_t = meta.get("host_triple_note")
            meta["host_triple_note"] = f"{note} | Earlier note: {prev_t}" if prev_t else note
    if note:
        prev = meta.get("harness_rev_note")
        meta["harness_rev_note"] = f"{note} | Earlier note: {prev}" if prev else note
    with open(os.path.join(out, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"merged {len(metas)} parts, {len(rows)} ledger rows, {recorded} recorded -> {out}")
    return 0


# ── self-test ───────────────────────────────────────────────────────────────────────────────────
# The provenance rule is the whole point of this file, so it proves itself before anyone trusts a
# merged golden: the pair that MUST merge (one binary, two paths) and the pairs that MUST NOT.
def _part(root, name, cell_ids, **meta_over):
    d = os.path.join(root, name)
    os.makedirs(os.path.join(d, "cells"))
    meta = {"binary": "/Users/runner/.cache/busbar-oracle/1.5.5/busbar", "version": "busbar 1.5.5",
            "recorded": len(cell_ids), "binary_sha256": "48e2800c", "harness_rev": "aaaa",
            "host_triple": "aarch64-apple-darwin", "at": "2026-09-06T00:00:00Z"}
    meta.update(meta_over)
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f)
    with open(os.path.join(d, "ledger.tsv"), "w", encoding="utf-8") as f:
        for c in cell_ids:
            f.write(f"{c}\tPASS\tidentical\t\n")
    for c in cell_ids:
        with open(os.path.join(d, "cells", c.replace("|", "__") + ".json"), "w", encoding="utf-8") as f:
            json.dump({"status": 200}, f)
    return d


def selftest() -> int:
    me = os.path.abspath(__file__)
    fails = 0

    def case(name, parts, args, want_rc, want_in=""):
        nonlocal fails
        with tempfile.TemporaryDirectory() as t:
            dirs = [_part(t, n, ids, **over) for n, ids, over in parts]
            r = subprocess.run([sys.executable, me, "--out", os.path.join(t, "merged")] + args + dirs,
                               capture_output=True, text=True)
            blob = r.stdout + r.stderr
            ok = (r.returncode == 0) == (want_rc == 0) and (want_in in blob)
            print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  rc={r.returncode} out={blob.strip()[:200]}"))
            if not ok:
                fails += 1
            return t

    # THE CASE THIS FILE WAS FIXED FOR: same binary bytes, two different filesystem paths.
    # (The second path is deliberately NOT under a home directory: this file is itself public, and a
    # `/Users/<person>/...` literal here is the very thing the machine-path rule keeps out.)
    case("same sha256, different binary PATH -> merges",
         [("a", ["x|1"], {}), ("b", ["x|2"], {"binary": "/opt/busbar-oracle-cache/1.5.5/busbar"})],
         [], 0, "merged 2 parts")
    # A COMMITTED meta.json MAY NOT NAME A PERSON: a part recorded before that rule (or by a caller
    # that passed an absolute --bin) gets its `binary` written machine-independently, out loud.
    case("a part's binary under $HOME -> rewritten, announced",
         [("a", ["x|1"], {}), ("b", ["x|2"], {"binary": os.path.join(os.environ.get("HOME", "/nonexistent"),
                                                                     ".cache/busbar-oracle/1.5.5/busbar")})],
         [], 0, "<oracle-cache>/1.5.5/busbar")
    case("different binary_sha256 -> refused",
         [("a", ["x|1"], {}), ("b", ["x|2"], {"binary_sha256": "deadbeef"})],
         [], 1, "binary_sha256")
    case("different host_triple -> refused",
         [("a", ["x|1"], {}), ("b", ["x|2"], {"host_triple": "x86_64-unknown-linux-gnu"})],
         [], 1, "host_triple")
    case("same sha256, different host_triple, no flag -> refused",
         [("a", ["x|1"], {}), ("b", ["x|2"], {"host_triple": "x86_64-unknown-linux-gnu"})],
         [], 1, "host_triple")
    case("same sha256, different host_triple, --allow-host-skew but no --note -> refused",
         [("a", ["x|1"], {}), ("b", ["x|2"], {"host_triple": "x86_64-unknown-linux-gnu"})],
         ["--allow-host-skew"], 1, "--note")
    case("same sha256, different host_triple, skew allowed and noted -> merges",
         [("a", ["x|1"], {}), ("b", ["x|2"], {"host_triple": "x86_64-unknown-linux-gnu",
                                              "at": "2026-09-06T01:00:00Z"})],
         ["--allow-host-skew", "--note", "x|2 recorded on x86_64-unknown-linux-gnu"], 0, "HOST SKEW ALLOWED")
    case("different binary_sha256 AND different host_triple, --allow-host-skew -> still refused",
         [("a", ["x|1"], {}), ("b", ["x|2"], {"binary_sha256": "deadbeef",
                                              "host_triple": "x86_64-unknown-linux-gnu"})],
         ["--allow-host-skew", "--note", "N"], 1, "binary_sha256")
    case("different version -> refused",
         [("a", ["x|1"], {}), ("b", ["x|2"], {"version": "busbar 1.6.0"})],
         [], 1, "version")
    case("overlapping cell id -> refused",
         [("a", ["x|1"], {}), ("b", ["x|1"], {})],
         [], 1, "disjoint")
    case("different harness_rev, no flag -> refused",
         [("a", ["x|1"], {}), ("b", ["x|2"], {"harness_rev": "bbbb"})],
         [], 1, "harness revisions")
    case("different harness_rev, --allow-harness-skew but no --note -> refused",
         [("a", ["x|1"], {}), ("b", ["x|2"], {"harness_rev": "bbbb"})],
         ["--allow-harness-skew"], 1, "--note")
    case("different harness_rev, skew allowed and noted -> merges",
         [("a", ["x|1"], {}), ("b", ["x|2"], {"harness_rev": "bbbb", "at": "2026-09-06T01:00:00Z"})],
         ["--allow-harness-skew", "--note", "x|2 re-recorded under bbbb"], 0, "HARNESS SKEW ALLOWED")

    # ── THE PINNED-BINARY IDENTITY, BOTH ARMS ───────────────────────────────────────────────────
    # The rule is "version + a digest that is one of the pinned per-triple rows", so it has exactly
    # two things to prove: a digest that IS such a row is accepted even though it differs from the
    # base's, and a digest that is NOT is refused even though the flag is present. A rule with only
    # its permissive arm tested is a rule nobody has checked can still say no.
    def _pins_tsv(t):
        p = os.path.join(t, "golden-digests.tsv")
        with open(p, "w", encoding="utf-8") as f:
            f.write("# version\tasset\tsha256\n")
            f.write("1.5.5\tbusbar-aarch64-apple-darwin.tar.gz\tarchive0\n")  # archive row: NOT identity
            f.write("1.5.5\tbusbar-aarch64-apple-darwin\t48e2800c\n")
            f.write("1.5.5\tbusbar-x86_64-unknown-linux-gnu\t84bde0a0\n")
            f.write("1.5.5\tbusbar-openapi-v1.5.5.json\tsidecar0\n")  # sidecar row: NOT a binary
            f.write("1.6.0\tbusbar-x86_64-unknown-linux-gnu\totherrelease\n")
        return p

    with tempfile.TemporaryDirectory() as t:
        tsv = _pins_tsv(t)
        a = _part(t, "a", ["x|1"])
        b = _part(t, "b", ["x|2"], binary_sha256="84bde0a0", host_triple="x86_64-unknown-linux-gnu",
                  at="2026-09-06T01:00:00Z")
        r = subprocess.run([sys.executable, me, "--out", os.path.join(t, "m"), "--pinned-binaries", tsv,
                            "--allow-host-skew", "--note", "store cells on linux CI", a, b],
                           capture_output=True, text=True)
        ok = r.returncode == 0
        pins_rec = []
        if ok:
            m = json.load(open(os.path.join(t, "m", "meta.json"), encoding="utf-8"))
            pins_rec = m.get("binary_sha256_pins", [])
            ok = ({e["pinned_as"] for e in pins_rec}
                  == {"busbar-aarch64-apple-darwin", "busbar-x86_64-unknown-linux-gnu"}
                  and m["recorded"] == 2)
        print(("PASS  " if ok else "FAIL  ")
              + "two digests that are two pinned per-triple rows of ONE release merge, and the meta records which build made which cells"
              + ("" if ok else f"  rc={r.returncode} pins={pins_rec} out={(r.stdout + r.stderr).strip()[:200]}"))
        fails += 0 if ok else 1

    case("a digest in NO pinned row -> refused even with --pinned-binaries",
         [("a", ["x|1"], {}), ("b", ["x|2"], {"binary_sha256": "deadbeef",
                                              "host_triple": "x86_64-unknown-linux-gnu"})],
         ["--allow-host-skew", "--note", "N"], 1, "binary_sha256")
    with tempfile.TemporaryDirectory() as t:
        tsv = _pins_tsv(t)
        a = _part(t, "a", ["x|1"])
        b = _part(t, "b", ["x|2"], binary_sha256="deadbeef", host_triple="x86_64-unknown-linux-gnu")
        r = subprocess.run([sys.executable, me, "--out", os.path.join(t, "m"), "--pinned-binaries", tsv,
                            "--allow-host-skew", "--note", "N", a, b], capture_output=True, text=True)
        blob = r.stdout + r.stderr
        ok = r.returncode != 0 and "not a pinned" in blob
        print(("PASS  " if ok else "FAIL  ")
              + "an UNPINNED digest is refused even under --pinned-binaries (the rule can still say no)"
              + ("" if ok else f"  rc={r.returncode} out={blob.strip()[:200]}"))
        fails += 0 if ok else 1

    # An ARCHIVE row and a SIDECAR row share the table with the binary rows and are not identity:
    # the tarball's digest proves a download, not the file record.sh executed.
    for label, sha in (("an archive row's digest", "archive0"), ("a sidecar artifact's digest", "sidecar0")):
        with tempfile.TemporaryDirectory() as t:
            tsv = _pins_tsv(t)
            a = _part(t, "a", ["x|1"])
            b = _part(t, "b", ["x|2"], binary_sha256=sha, host_triple="x86_64-unknown-linux-gnu")
            r = subprocess.run([sys.executable, me, "--out", os.path.join(t, "m"), "--pinned-binaries",
                                tsv, "--allow-host-skew", "--note", "N", a, b],
                               capture_output=True, text=True)
            ok = r.returncode != 0
            print(("PASS  " if ok else "FAIL  ") + f"{label} is not a per-triple binary row, so it is refused"
                  + ("" if ok else f"  rc={r.returncode}"))
            fails += 0 if ok else 1

    # A row for a DIFFERENT release is not a row for this one: the table is keyed on both columns.
    with tempfile.TemporaryDirectory() as t:
        tsv = _pins_tsv(t)
        a = _part(t, "a", ["x|1"])
        b = _part(t, "b", ["x|2"], binary_sha256="otherrelease", host_triple="x86_64-unknown-linux-gnu")
        r = subprocess.run([sys.executable, me, "--out", os.path.join(t, "m"), "--pinned-binaries", tsv,
                            "--allow-host-skew", "--note", "N", a, b], capture_output=True, text=True)
        ok = r.returncode != 0
        print(("PASS  " if ok else "FAIL  ")
              + "a pinned row belonging to a DIFFERENT release is not identity for this one"
              + ("" if ok else f"  rc={r.returncode}"))
        fails += 0 if ok else 1

    # Without the table the strict rule is untouched — the general rule is opt-in by naming the pins.
    case("different binary_sha256 and NO --pinned-binaries -> refused as before",
         [("a", ["x|1"], {}), ("b", ["x|2"], {"binary_sha256": "84bde0a0"})],
         [], 1, "--pinned-binaries")

    # the skewed merge's meta must be stamped with the LAST-recorded rev and keep the other in history
    with tempfile.TemporaryDirectory() as t:
        a = _part(t, "a", ["x|1"])
        b = _part(t, "b", ["x|2"], harness_rev="bbbb", at="2026-09-06T01:00:00Z")
        subprocess.run([sys.executable, me, "--out", os.path.join(t, "m"), "--allow-harness-skew",
                        "--note", "N", a, b], capture_output=True, text=True)
        m = json.load(open(os.path.join(t, "m", "meta.json"), encoding="utf-8"))
        ok = (m["harness_rev"] == "bbbb" and "aaaa" in m.get("harness_rev_history", [])
              and m["recorded"] == 2 and m["harness_rev_note"] == "N"
              and {e["binary"] for e in m["merged_from"]} == {"/Users/runner/.cache/busbar-oracle/1.5.5/busbar"})
        print(("PASS  " if ok else "FAIL  ") + "skewed merge stamps the newest rev, keeps the older in history, sums `recorded`, notes the parts"
              + ("" if ok else f"  meta={m}"))
        fails += 0 if ok else 1

    # the host-skewed merge's meta must be stamped with the LAST-recorded host_triple and keep the
    # other in history, with the part's path and its (matching) binary_sha256
    with tempfile.TemporaryDirectory() as t:
        a = _part(t, "a", ["x|1"])
        b = _part(t, "b", ["x|2"], host_triple="x86_64-unknown-linux-gnu", at="2026-09-06T01:00:00Z")
        subprocess.run([sys.executable, me, "--out", os.path.join(t, "m"), "--allow-host-skew",
                        "--note", "N", a, b], capture_output=True, text=True)
        m = json.load(open(os.path.join(t, "m", "meta.json"), encoding="utf-8"))
        history_triples = {e["host_triple"] for e in m.get("host_triple_history", [])}
        ok = (m["host_triple"] == "x86_64-unknown-linux-gnu" and "aarch64-apple-darwin" in history_triples
              and m["recorded"] == 2 and m["host_triple_note"] == "N"
              and all(e["binary_sha256"] == "48e2800c" for e in m.get("host_triple_history", [])))
        print(("PASS  " if ok else "FAIL  ") + "host-skewed merge stamps the newest host_triple, keeps the older in history with part+sha, notes it"
              + ("" if ok else f"  meta={m}"))
        fails += 0 if ok else 1

    # the merged ledger is written in cells.json order, not part-by-part concatenation order
    with tempfile.TemporaryDirectory() as t:
        cj = os.path.join(t, "cells.json")
        with open(cj, "w", encoding="utf-8") as f:
            json.dump({"cells": [{"id": "z|first"}, {"id": "a|second"}]}, f)
        a = _part(t, "a", ["a|second"])
        b = _part(t, "b", ["z|first"])
        subprocess.run([sys.executable, me, "--out", os.path.join(t, "m"), "--cells", cj, a, b],
                       capture_output=True, text=True)
        got = [ln.split("\t", 1)[0] for ln in open(os.path.join(t, "m", "ledger.tsv"), encoding="utf-8")]
        ok = got == ["z|first", "a|second"]
        print(("PASS  " if ok else "FAIL  ") + "merged ledger is in cells.json order, not part order"
              + ("" if ok else f"  got={got}"))
        fails += 0 if ok else 1

    print()
    print("merge-recordings selftest: " + ("GREEN" if fails == 0 else f"RED ({fails})"))
    return 0 if fails == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--allow-harness-skew", action="store_true",
                    help="merge parts recorded under different harness revisions (needs --note)")
    ap.add_argument("--allow-host-skew", action="store_true",
                    help="merge parts recorded on different hosts (needs --note); binary_sha256 must still match")
    ap.add_argument("--pinned-binaries", metavar="TSV", default=None,
                    help="golden-digests.tsv: state the identity as version + a digest that is one of "
                         "the pinned per-triple busbar-<triple> rows, instead of digest equality. A "
                         "digest in no row is still refused.")
    ap.add_argument("--note", default="",
                    help="prepended to the merged meta.json's harness_rev_note/host_triple_note; required for a skewed merge")
    ap.add_argument("--cells", default=os.path.join(DATA, "cells.json"),
                    help="cells.json whose order the merged ledger is written in (record.sh's own "
                         "order). Default: $BUSBAR_ORACLE_DATA/cells.json")
    ap.add_argument("--selftest", action="store_true", help="prove the provenance rule, then exit")
    ap.add_argument("parts", nargs="*")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.out or not a.parts:
        sys.exit("usage: merge-recordings.py --out <dir> [--allow-harness-skew] [--allow-host-skew] "
                 "[--pinned-binaries TSV] [--note TEXT] <part>...")
    return merge(a.parts, a.out, a.allow_harness_skew, a.note, a.cells, a.allow_host_skew,
                 a.pinned_binaries)


if __name__ == "__main__":
    sys.exit(main())
