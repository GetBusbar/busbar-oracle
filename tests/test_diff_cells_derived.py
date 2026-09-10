# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Busbar Inc and contributors
"""`effects.script` figures that are a MEASUREMENT OF THE BODY, and nothing else.

THE CELL THIS IS ABOUT. `llm.stream|responses|cut` is the script-driver cell that pins what busbar
tells a caller when the upstream dies mid-stream. 1.6.0's fabricated `response.failed` terminal
carries eight more keys than 1.5.5's — a strict superset, registered by the owner — and the cell's
own script driver (`llm-stream-fault.sh`) also records `effects.stream_fault.body_bytes`, which is
`wc -c` over THE SAME BODY. So the body's registered growth showed up twice: once as `body` (rated
3, forgiven by the entry) and once as `effects.script` (rated 10, MONEY, and refused by every
register kind there is — `additive` may only ever take {body, headers, effects.stderr, status}).
No billed figure, no status, no header, no frame count and no metric moved, and the cell could not
read PASS ACCEPTED.

THE RULE THESE TESTS PIN. A script effect that is a BYTE COUNT (or digest) OF THE RESPONSE BODY
follows the BODY's verdict — and only when all three of these hold:

  (a) the `body` class is accepted by a register entry for this cell,
  (b) the derived field's value really is a measurement of the recorded body, on BOTH sides,
  (c) every other `effects.script` member is byte-identical.

Any failure of (a)-(c) leaves `effects.script` a money divergence exactly as it was, and the
acceptance is never silent: the row says `ACCEPTED derived-from-body (entry <id>): ...` out loud.

THE BYTES ARE THE REAL ONES. The golden and candidate bodies below are the actual recorded bodies
of `llm.stream|responses|cut` from EG-3's re-cut (1.5.5, Linux, v0.3.11) and its 1.6.0 candidate —
482 and 622 bytes — so the numbers in the verdict line are the numbers from the real report.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys

TOOL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "busbar_oracle")
CELL_ID = "llm.stream|responses|cut"

# The 1.5.5 golden's body, verbatim: two SSE frames, the second a FABRICATED `response.failed`.
GOLDEN_BODY = (
    'data: {"response":{"id":"resp_oracle","model":"m-openai-responses","object":"response",'
    '"status":"in_progress"},"sequence_number":0,"type":"response.created"}\n'
    '\n'
    'event: response.failed\n'
    'data: {"response":{"created_at":1789065923,"error":{"code":"server_error","message":"The '
    'response stream was interrupted."},"id":"resp_uKklk4NqmbIkC1rOYKV9EdFP9bM0ukNZxGd9UHUnhv2WtpUW",'
    '"model":"gpt-4o","object":"response","output":[],"status":"failed"},"sequence_number":0,'
    '"type":"response.failed"}\n'
    '\n'
)
# 1.6.0's, verbatim: the SAME terminal with eight more keys beside the ones 1.5.5 wrote.
CANDIDATE_BODY = (
    'data: {"response":{"id":"resp_oracle","model":"m-openai-responses","object":"response",'
    '"status":"in_progress"},"sequence_number":0,"type":"response.created"}\n'
    '\n'
    'event: response.failed\n'
    'data: {"response":{"created_at":0,"error":{"code":"server_error","message":"The '
    'response stream was interrupted."},"id":"resp_Sd3VcbjpJSdjJdlSFvHFdeCpXnwmQkJApXMKlCV38bnon0lj",'
    '"incomplete_details":null,"instructions":null,"metadata":{},"model":"gpt-4o","object":"response",'
    '"output":[],"parallel_tool_calls":true,"status":"failed","temperature":1.0,"tool_choice":"auto",'
    '"tools":[],"top_p":1.0},"sequence_number":0,"type":"response.failed"}\n'
    '\n'
)


def test_the_fixture_bytes_are_the_recorded_ones():
    """482 and 622 are the numbers in EG-3's report. If this ever fails, every verdict line below
    is about a body that is not the one the ruling was made on."""
    assert len(GOLDEN_BODY.encode("utf-8")) == 482
    assert len(CANDIDATE_BODY.encode("utf-8")) == 622


def _cell(body: str, body_bytes: int, **stream_fault):
    sf = {"dialect": "responses", "fault": "cut", "client_curl_rc": 0, "body_frames": 3,
          "body_bytes": body_bytes,
          "usage_delta_after_settle_pause": {"requests": 0, "spend_cents": 0, "tokens": 0}}
    sf.update(stream_fault)
    return {
        "status": 200,
        "headers": {"content-type": "text/event-stream"},
        "body": {"text": body},
        "applied": ["hdr.date", "id.wire", "ts.unix"],
        "effects": {"usage": {"requests": 1}, "metrics": {}, "audit": {"added": 0, "items": []},
                    "stream_fault": sf},
    }


GOLDEN_CELL = _cell(GOLDEN_BODY, 482)
CANDIDATE_CELL = _cell(CANDIDATE_BODY, 622)

BODY_ENTRY = {
    "id": "EG-4 the fabricated response.failed terminal grew eight keys",
    "cells": r"^llm\.stream\|responses\|cut$",
    "classes": ["body"],
    "kind": "improvement",
    "expected_cells": 1,
    "by": "owner ruling, 2026-09-10",
    "rationale": "1.6.0's fabricated terminal is a strict superset of 1.5.5's; nothing 1.5.5 wrote "
                 "moved or was removed, and no billed figure, status, header, frame count or metric "
                 "moved with it.",
}


def _recording(root, name, cell):
    d = os.path.join(root, name)
    os.makedirs(os.path.join(d, "cells"))
    with open(os.path.join(d, "meta.json"), "w") as f:
        json.dump({"version": "busbar " + ("1.5.5" if name == "golden" else "1.6.0"),
                   "harness_rev": "rev-under-test"}, f)
    with open(os.path.join(d, "ledger.tsv"), "w") as f:
        f.write(f"{CELL_ID}\tPASS\tHTTP 200\t\n")
    with open(os.path.join(d, "cells", CELL_ID.replace("|", "__") + ".json"), "w") as f:
        json.dump(cell, f)
    return d


def run_diff(tmp_path, golden_cell, candidate_cell, entries):
    """Run the SHIPPED differ over a one-cell recording pair. Returns (rc, ledger row, stderr)."""
    root = str(tmp_path)
    g = _recording(root, "golden", golden_cell)
    c = _recording(root, "candidate", candidate_cell)
    cells_json = os.path.join(root, "cells.json")
    with open(cells_json, "w") as f:
        json.dump({"cells": [{"id": CELL_ID, "plane": "llm", "family": "llm.stream"}]}, f)
    acc = os.path.join(root, "accepted-differences.json")
    with open(acc, "w") as f:
        json.dump({"accepted": entries}, f)
    p = subprocess.run(
        [sys.executable, os.path.join(TOOL_DIR, "diff-cells.py"), "--golden", g, "--candidate", c,
         "--out", os.path.join(root, "out"), "--cells", cells_json, "--accepted", acc, "--strict"],
        capture_output=True, text=True)
    rows = [ln for ln in p.stdout.splitlines() if ln.startswith(CELL_ID + "\t")]
    return p.returncode, (rows[0] if rows else ""), p.stderr


# ── (1) the shape the ruling is about: PASS ACCEPTED, with the derived line said out loud ────────
def test_the_responses_cut_shape_reads_pass_accepted_with_the_derived_line(tmp_path):
    rc, row, err = run_diff(tmp_path, GOLDEN_CELL, CANDIDATE_CELL, [BODY_ENTRY])
    assert rc == 0, f"strict exit {rc}\nrow: {row}\n{err}"
    fields = row.split("\t")
    assert fields[1] == "PASS", row
    assert fields[2].startswith("ACCEPTED improvement (EG-4"), row
    assert fields[2].endswith("body,effects.script"), row
    assert fields[3] == (
        "ACCEPTED derived-from-body (entry EG-4 the fabricated response.failed terminal grew eight "
        "keys): effects.script/stream_fault/body_bytes 482 -> 622 = len(body)"), row


def test_the_accepted_row_is_never_a_silent_pass(tmp_path):
    """A row that just said `PASS identical` would hide a money class being forgiven."""
    _, row, _ = run_diff(tmp_path, GOLDEN_CELL, CANDIDATE_CELL, [BODY_ENTRY])
    assert "identical" not in row
    assert "derived-from-body" in row


# ── (2) any OTHER script member that moved leaves it money ───────────────────────────────────────
def test_a_second_script_member_that_moved_is_still_money(tmp_path):
    """`client_curl_rc` 0 -> 18 is the CALLER's socket dying too — a fact about the cell that no
    body length can explain. One derived field being explicable does not launder its neighbours."""
    cand = copy.deepcopy(CANDIDATE_CELL)
    cand["effects"]["stream_fault"]["client_curl_rc"] = 18
    rc, row, err = run_diff(tmp_path, GOLDEN_CELL, cand, [BODY_ENTRY])
    assert rc == 1, f"expected a strict failure; got {rc}\nrow: {row}\n{err}"
    fields = row.split("\t")
    assert fields[1] == "FAIL", row
    assert "effects.script" in fields[2], row
    assert "derived-from-body" in fields[3] and "client_curl_rc" in fields[3], row


# ── (3) a count that is not the length of the body it claims to measure ──────────────────────────
def test_a_body_bytes_that_is_not_the_length_of_the_body_is_refused(tmp_path):
    cand = copy.deepcopy(CANDIDATE_CELL)
    cand["effects"]["stream_fault"]["body_bytes"] = 999
    rc, row, err = run_diff(tmp_path, GOLDEN_CELL, cand, [BODY_ENTRY])
    assert rc == 1, f"expected a strict failure; got {rc}\nrow: {row}\n{err}"
    fields = row.split("\t")
    assert fields[1] == "FAIL", row
    assert "effects.script" in fields[2], row
    assert "did not move with the body" in fields[3], row


# ── (4) no accepted body, no derived acceptance ──────────────────────────────────────────────────
def test_a_body_no_register_entry_accepts_carries_no_derived_acceptance(tmp_path):
    """The whole rule is 'FOLLOWS THE BODY'S VERDICT'. With no entry, the body's verdict is red."""
    rc, row, err = run_diff(tmp_path, GOLDEN_CELL, CANDIDATE_CELL, [])
    assert rc == 1, f"expected a strict failure; got {rc}\nrow: {row}\n{err}"
    fields = row.split("\t")
    assert fields[1] == "FAIL", row
    assert fields[2] == "body,effects.script", row
    assert "derived-from-body" not in row, row


# ── (5) the register still cannot name effects.script itself ─────────────────────────────────────
def test_an_additive_entry_that_names_effects_script_is_refused_at_load(tmp_path):
    """The rule is a property the differ PROVES about a pair, never a class an owner may claim."""
    entry = {**BODY_ENTRY, "kind": "additive", "classes": ["effects.script"],
             "changelog": "the terminal grew eight keys"}
    rc, row, err = run_diff(tmp_path, GOLDEN_CELL, CANDIDATE_CELL, [entry])
    assert rc != 0, f"the register accepted it: {row}"
    assert "effects.script" in err, err
    assert row == "", row
