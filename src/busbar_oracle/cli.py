#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Busbar Inc and contributors
"""The `busbar-oracle` entrypoint.

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT

This is a DISPATCHER, not a reimplementation. Every subcommand hands off to the
recorder, normalizer, differ or replayer that already existed, with the caller's
flags passed through verbatim. That is a deliberate constraint, not laziness: the
oracle's whole value is that a cell recorded a year ago is judged today by the
same code that recorded it, so a difference in a report means a difference in the
PRODUCT. Rewriting a driver during the move out of busbar's tree would have made
the first report through this tool unfalsifiable -- there would be no way to tell
a real regression from a port bug.

So the only thing this layer adds is LOCATION. The scripts used to find the
product and its data by climbing out of their own directory, which worked only
while the tool sat inside the product it judged. `--product-root` and `--data`
replace that climb; both become environment variables the shipped scripts read,
and both default to the old derivation so an in-tree layout still works.

Subcommands map one-to-one onto the shipped drivers:

  cells         enumerate-cells.py        derive a product's cell list from its own data module
  record        record.sh                 run a product's cells, write a recording
  replay        replay.sh                 judge a candidate recording against a golden
  diff          diff-cells.py             the verdict itself (replay's judging half)
  normalize     normalize.py              apply the norm rules to a captured cell
  merge         merge-recordings.py       fold recordings into one golden
  renormalize   renormalize.sh            re-apply the norm rules to an existing recording
  harness-rev   harness-rev.sh            the revision of what records and judges
  selftest      selftest.sh               the tool's own self-tests
  fetch-golden  fetch-golden.sh           fetch + digest-verify a pinned golden binary
  mock          mock-upstream.py          the multi-dialect mock upstream
  capture-ws    capture-ws.py             the `ws` driver: drive and record one duplex session
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# subcommand -> shipped driver. A `.sh` runs under bash, a `.py` under this interpreter.
COMMANDS = {
    "record": "record.sh",
    "replay": "replay.sh",
    "replay-selftest": "replay-selftest.sh",
    "diff": "diff-cells.py",
    "normalize": "normalize.py",
    "merge": "merge-recordings.py",
    "renormalize": "renormalize.sh",
    "harness-rev": "harness-rev.sh",
    "selftest": "selftest.sh",
    "fetch-golden": "fetch-golden.sh",
    "fetch-plugin": "fetch-plugin.sh",
    "mock": "mock-upstream.py",
    "rigs-ledger": "rigs-ledger.sh",
    "apply-mutation": "apply-mutation.py",
    "build-request": "build-request.py",
    "capture": "capture.py",
    "capture-ws": "capture-ws.py",
    "cells": "enumerate-cells.py",
    "fixture-gate-selftest": "fixture-gate-selftest.sh",
}

USAGE = """usage: busbar-oracle [--product-root DIR] [--data DIR] <command> [args...]

  --product-root DIR   the product being judged (its binary, its sources).
                       Default: the tool's own grandparent, the old in-tree layout.
  --data DIR           that product's oracle data: cells.json, golden/, the
                       accepted-difference and gap registers, the cell drivers,
                       the fixtures. Default: the tool's own directory.

commands:
""" + "".join(f"  {k:<22} {v}\n" for k, v in COMMANDS.items()) + """
Every other flag is passed through to the driver unchanged -- `busbar-oracle record
--bin ./target/release/busbar --plane all` is `record.sh --bin ... --plane all`.
"""


def _resolve(name: str) -> Path:
    p = HERE / name
    if not p.exists():
        sys.exit(f"busbar-oracle: missing shipped driver {name} (broken install?)")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    env = dict(os.environ)

    # The two location arguments are consumed here; everything else is the driver's.
    while argv and argv[0].startswith("--") and argv[0].split("=")[0] in ("--product-root", "--data"):
        arg = argv.pop(0)
        if "=" in arg:
            flag, value = arg.split("=", 1)
        else:
            flag = arg
            if not argv:
                sys.exit(f"busbar-oracle: {flag} needs a directory")
            value = argv.pop(0)
        resolved = str(Path(value).expanduser().resolve())
        if not Path(resolved).is_dir():
            sys.exit(f"busbar-oracle: {flag} {value} is not a directory")
        env["BUSBAR_ORACLE_PRODUCT_ROOT" if flag == "--product-root" else "BUSBAR_ORACLE_DATA"] = resolved

    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if argv[0] in ("-V", "--version", "version"):
        try:
            from importlib.metadata import version
            print(f"busbar-oracle {version('busbar-oracle')}")
        except Exception:
            print("busbar-oracle (version unknown: not installed as a distribution)")
        return 0

    cmd, rest = argv[0], argv[1:]
    if cmd not in COMMANDS:
        print(USAGE, file=sys.stderr)
        sys.exit(f"busbar-oracle: unknown command {cmd!r}")

    driver = _resolve(COMMANDS[cmd])
    if driver.suffix == ".sh":
        bash = shutil.which("bash")
        if not bash:
            sys.exit("busbar-oracle: bash is required to run the shipped drivers")
        cmdline = [bash, str(driver), *rest]
    else:
        cmdline = [sys.executable, str(driver), *rest]

    return subprocess.call(cmdline, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
