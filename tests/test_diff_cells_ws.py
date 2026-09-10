# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Busbar Inc and contributors
"""The `ws` diff class: whether a changed session is actually caught, and rated.

`compare` walks status, headers, body and the effects keys. A ws cell's whole contract is a
TOP-LEVEL `ws` block, so before this class existed a candidate could drop half its frames, close
1011 where the golden closed 1000, or answer in a different dialect entirely, and the row still
printed `PASS  identical`. That is the same hole the `effects.script` sweep was added to close, one
level up, and these tests are the red-before-green for it.
"""
from __future__ import annotations

import copy

import pytest

BASE = {
    "status": 101,
    "headers": {"upgrade": "websocket"},
    "body": {"text": ""},
    "effects": {"usage": {"requests": 1}, "metrics": {}, "audit": {"added": 0, "items": []}},
    "applied": ["ws.frame-id"],
    "ws": {
        "dialect": "openai-realtime", "key": "dGhlIHNhbXBsZSBub25jZQ==", "accept_ok": True,
        "frames": [
            {"dir": "in", "opcode": "text", "json": {"type": "session.created", "event_id": "<ID:1>"}},
            {"dir": "out", "opcode": "text", "json": {"type": "response.create"}},
            {"dir": "in", "opcode": "text", "json": {"type": "response.done", "response_id": "<ID:2>"}},
            {"dir": "out", "opcode": "close", "code": 1000, "reason": ""},
            {"dir": "in", "opcode": "close", "code": 1000, "reason": ""},
        ],
        "close": {"by": "client", "code": 1000, "reason": "", "echo": "close"},
    },
}


def mutated(fn):
    c = copy.deepcopy(BASE)
    fn(c)
    return c


def diff(diff_cells, candidate):
    return diff_cells.compare(BASE, candidate)


# ── the class exists, and is rated ───────────────────────────────────────────────────────────────
def test_ws_is_a_registered_class_at_money_weight(diff_cells):
    """A frame that stopped arriving is a turn the caller paid for and did not get; a close that
    moved 1000 -> 1011 is a session that failed after the meter started. Neither moves `status`."""
    assert "ws" in diff_cells.CLASS_ORDER
    assert diff_cells.CLASS_WEIGHT["ws"] == 10
    assert "ws" in diff_cells.MONEY_CLASSES


def test_the_files_own_weight_and_money_registers_stay_in_lockstep(diff_cells):
    """The module-level assert this file already carries; naming it here means a future weight
    change fails as a test rather than as an import error somewhere else."""
    assert {k for k, w in diff_cells.CLASS_WEIGHT.items() if w == 10} == diff_cells.MONEY_CLASSES | {"missing.golden"}
    assert diff_cells.MONEY_CLASSES <= set(diff_cells.CLASS_ORDER)


def test_ws_sorts_ahead_of_headers_and_body(diff_cells):
    """The first_diff line a reader sees for a session cell should be about the session."""
    assert diff_cells.CLASS_ORDER.index("ws") < diff_cells.CLASS_ORDER.index("headers")
    assert diff_cells.CLASS_ORDER.index("ws") < diff_cells.CLASS_ORDER.index("body")


# ── what it catches ──────────────────────────────────────────────────────────────────────────────
def test_two_identical_transcripts_compare_clean(diff_cells):
    assert diff(diff_cells, copy.deepcopy(BASE)) == ([], {})


def test_a_changed_frame_payload_is_caught_and_located(diff_cells):
    classes, detail = diff(diff_cells, mutated(lambda c: c["ws"]["frames"][2]["json"].update({"type": "response.failed"})))
    assert classes == ["ws"]
    assert detail["ws"]["first_divergent_frame"]["index"] == 2
    assert "frame 2" in diff_cells.first_diff_text(classes, detail)


def test_a_dropped_frame_leads_with_the_count(diff_cells):
    """A frame missing near the front shifts every frame after it, so 'first divergent frame' alone
    reads as a content change at an arbitrary index. The actionable fact is the session is short."""
    classes, detail = diff(diff_cells, mutated(lambda c: c["ws"]["frames"].pop(1)))
    assert classes == ["ws"]
    assert detail["ws"]["count"] == {"golden": 5, "candidate": 4}
    assert "5 -> 4 frames" in diff_cells.first_diff_text(classes, detail)


def test_an_extra_frame_the_golden_never_had_is_caught(diff_cells):
    classes, detail = diff(diff_cells, mutated(
        lambda c: c["ws"]["frames"].append({"dir": "in", "opcode": "text", "json": {"type": "extra"}})))
    assert classes == ["ws"]
    assert detail["ws"]["only_on"]["side"] == "candidate"
    assert "only on the candidate" in diff_cells.first_diff_text(classes, detail)


def test_a_changed_close_code_is_caught(diff_cells):
    classes, detail = diff(diff_cells, mutated(
        lambda c: c["ws"]["close"].update({"by": "server", "code": 1011, "reason": "boom"})))
    assert classes == ["ws"]
    assert "ws close" in diff_cells.first_diff_text(classes, detail)


def test_a_door_that_stopped_echoing_our_close_is_caught(diff_cells):
    """The reason `close.echo` is a recorded key rather than a silently shorter frame list."""
    classes, detail = diff(diff_cells, mutated(lambda c: c["ws"]["close"].update({"echo": "none"})))
    assert classes == ["ws"]


def test_a_candidate_that_opened_no_session_at_all_is_caught(diff_cells):
    classes, detail = diff(diff_cells, mutated(lambda c: c.__setitem__("ws", None)))
    assert classes == ["ws"]
    assert diff_cells.first_diff_text(classes, detail) == "ws session -> no session"


def test_a_candidate_that_opened_a_session_where_the_golden_had_none_is_caught(diff_cells):
    """The 1.6.0-first direction: 1.5.5 answers 404 on every realtime URL, so a build that starts
    serving one is a session appearing where the golden recorded none."""
    golden = mutated(lambda c: c.__setitem__("ws", None))
    classes, detail = diff_cells.compare(golden, BASE)
    assert classes == ["ws"]
    assert diff_cells.first_diff_text(classes, detail) == "ws no session -> session"


def test_a_changed_dialect_is_caught_before_any_frame_is_read(diff_cells):
    classes, detail = diff(diff_cells, mutated(lambda c: c["ws"].__setitem__("dialect", "gemini-live")))
    assert classes == ["ws"]
    assert "dialect" in diff_cells.first_diff_text(classes, detail)


def test_a_handshake_whose_accept_stopped_verifying_is_caught(diff_cells):
    classes, detail = diff(diff_cells, mutated(lambda c: c["ws"].__setitem__("accept_ok", False)))
    assert classes == ["ws"]
    assert "accept_ok" in diff_cells.first_diff_text(classes, detail)


def test_a_reordered_transcript_is_caught(diff_cells):
    """Frame ORDER is the contract; a set comparison would call this identical."""
    def swap(c):
        c["ws"]["frames"][0], c["ws"]["frames"][1] = c["ws"]["frames"][1], c["ws"]["frames"][0]
    classes, _ = diff(diff_cells, mutated(swap))
    assert classes == ["ws"]


def test_a_frame_that_changed_direction_is_caught(diff_cells):
    """Who spoke is half of what a duplex transcript says."""
    classes, _ = diff(diff_cells, mutated(lambda c: c["ws"]["frames"][0].__setitem__("dir", "out")))
    assert classes == ["ws"]


def test_a_changed_audio_digest_is_caught(diff_cells):
    """The summary exists so a door that sent silence does not record as one that sent a turn."""
    golden = copy.deepcopy(BASE)
    golden["ws"]["frames"][1]["json"] = {"type": "input_audio_buffer.append", "audio": {"bytes": 4, "sha256": "a" * 64}}
    cand = copy.deepcopy(golden)
    cand["ws"]["frames"][1]["json"]["audio"]["sha256"] = "b" * 64
    classes, _ = diff_cells.compare(golden, cand)
    assert classes == ["ws"]


# ── it does not swallow the other classes ────────────────────────────────────────────────────────
def test_ws_and_usage_are_reported_together_not_instead_of_each_other(diff_cells):
    def both(c):
        c["ws"]["close"]["code"] = 1011
        c["effects"]["usage"] = {"requests": 9}
    classes, _ = diff(diff_cells, mutated(both))
    assert set(classes) == {"ws", "effects.usage"}


def test_a_non_ws_cell_is_unaffected_by_the_new_class(diff_cells):
    """Every existing http/llm cell has no `ws` key on either side, so the class never fires."""
    g = {"status": 200, "headers": {}, "body": {"json": {"a": 1}}, "effects": {}, "applied": []}
    c = copy.deepcopy(g)
    c["body"]["json"]["a"] = 2
    classes, _ = diff_cells.compare(g, c)
    assert classes == ["body"]


@pytest.mark.parametrize("family", ["streams", "voice.mount", "llm.stream"])
def test_ws_is_rated_ten_on_every_family(diff_cells, family):
    """Unlike `body`, whose weight is family-dependent, a session transcript is money everywhere."""
    assert diff_cells.rated_weight(family, "ws") == 10
