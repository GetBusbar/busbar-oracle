#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Busbar Inc and contributors
"""Assemble one captured oracle cell for a `concurrent` cell — N parallel requests, one recording.

Unlike capture.py (one request, one response), a concurrency/queue cell fires several requests at
once and its CONTRACT is the outcome as a SET, not a single HTTP transaction: the sorted multiset of
the N http statuses that came back (a shed 503 count, an admitted-and-served count, ...), plus the
same before/after usage-and-metrics deltas capture.py computes for every other driver, so a shed
request that bills nothing, or a queued request that ends up billed exactly once, shows up in the
golden the same way it would on any other cell.

  capture-concurrent.py '[200,200,503]' <before-dir> <after-dir> [egress-file ...] > captured.json

`status` is always 0 (there is no single response to report it for) and `headers` is always empty;
the multiset lives at body.statuses. Reuses capture.py's own usage/metrics/audit/EGRESS helpers so
the two drivers can never silently drift apart on what a "delta" means.

EGRESS WAS THE LITERAL `[]`, AND `[]` IS A CONTRACT. capture.py's docstring states what an empty
egress list MEANS: "a contract for cells that must never reach upstream (e.g. a refusal at Admit)".
This driver asserted that of every concurrent cell, unconditionally, while the same cells recorded
`effects.usage = {"requests": 8, …}` beside it — eight requests that demonstrably did reach the
mock. `effects.egress` is rated 10 and is a MONEY class ("a changed egress body is a changed bill
and a changed prompt"), so on the five owed concurrency/queue cells a candidate that sent a mangled
system prompt, dropped the tool list, leaked a client header upstream or fired sixteen upstream
attempts instead of eight diverged on NOTHING: `[] == []`, and the row printed `PASS identical`.
It was the cells where concurrency makes egress hardest to reason about that had the class unarmed,
and the one other witness (`busbar_upstream_attempts_total`) is masked on this very driver by
normalize.py's `metrics.concurrent-attempts` rule.

So the recorder collects this cell's egress records the same way the single-request path does and
names them here. A concurrent cell that genuinely reaches nothing upstream still records `[]` — the
difference is that it is now a measurement rather than a literal.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from capture import audit_diff, load_egress, load_json, metrics_delta, usage_delta  # noqa: E402


def main() -> int:
    statuses = json.loads(sys.argv[1])
    before, after = sys.argv[2], sys.argv[3]
    egress = load_egress(sys.argv[4:])
    usage = usage_delta(before, after)
    ab, aa = load_json(before, "audit.json"), load_json(after, "audit.json")
    audit = audit_diff(ab, aa)
    cap = {
        "status": 0,
        "headers": {},
        "body": json.dumps({"statuses": sorted(statuses)}, separators=(",", ":"), sort_keys=True),
        "effects": {"usage": usage, "metrics": metrics_delta(before, after), "audit": audit,
                    "egress": egress},
    }
    print(json.dumps(cap, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
