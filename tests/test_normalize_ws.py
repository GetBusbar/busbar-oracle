# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Busbar Inc and contributors
"""Frame canonicalisation: the third of the three things the tool was owed.

Without it the first two are unusable. Every real dialect stamps its frames with values that are new
on every run -- a fresh `event_id` per OpenAI Realtime event, `response_id`/`item_id` minted per
turn, a `streamSid` and a millisecond `timestamp` on every Twilio media message -- so a session
recorded raw is a nonce: it can never be reproduced, so it can never be a diff.

The rule these tests are really holding: TAKE THE NONCE, KEEP THE CONTRACT. Anything scrubbed that a
reader would have needed is a bug in the other direction, and there are as many tests below for what
survives as for what does not.
"""
from __future__ import annotations

import base64
import hashlib
import json

import pytest

AUDIO = bytes(range(256)) * 4
AUDIO_B64 = base64.b64encode(AUDIO).decode()
AUDIO_OUT = {"bytes": len(AUDIO), "sha256": hashlib.sha256(AUDIO).hexdigest()}


def norm(normalize, dialect, frames, close=None, key=None):
    cap = {"status": 101, "headers": {}, "body": "", "effects": {},
           "ws": {"dialect": dialect, "key": key or "k", "accept_ok": True, "frames": frames,
                  "close": close or {"by": "client", "code": 1000, "reason": "", "echo": "close"}}}
    applied: set = set()
    return normalize.norm_ws(cap["ws"], applied, None), applied


def txt(doc, direction="in"):
    return {"dir": direction, "opcode": "text", "text": json.dumps(doc)}


# ── the table is data ────────────────────────────────────────────────────────────────────────────
def test_the_canonicaliser_is_a_table_keyed_by_dialect_name(normalize):
    """The whole design constraint: no `if dialect == "openai"` in neutral code. Adding a dialect is
    adding a row, and every row has the same keys."""
    keys = {"event_pointer", "id_keys", "id_pointers", "ts_keys", "audio_keys", "audio_event_keys"}
    assert set(normalize.WS_DIALECTS) >= {"openai-realtime", "gemini-live", "twilio-media", "echo"}
    for name, row in normalize.WS_DIALECTS.items():
        assert set(row) == keys, name
    assert set(normalize.NEUTRAL_WS_DIALECT) == keys


def test_an_unknown_dialect_is_loud_rather_than_silently_a_nonce(normalize):
    """`ws.dialect-unknown` joins `applied`, which is itself the norm.rules diff class -- so a cell
    recorded under a dialect nobody taught the table is visible in the report, not just wrong."""
    out, applied = norm(normalize, "some-new-provider", [txt({"event_id": "ev_abcdef123456"})])
    assert "ws.dialect-unknown" in applied
    assert out["frames"][0]["json"] == {"event_id": "ev_abcdef123456"}


def test_a_known_dialect_with_nothing_to_scrub_does_not_fire_the_unknown_rule(normalize):
    _, applied = norm(normalize, "echo", [txt({"a": 1})])
    assert "ws.dialect-unknown" not in applied


# ── ids ──────────────────────────────────────────────────────────────────────────────────────────
def test_ids_are_interned_so_the_correlation_between_frames_survives(normalize):
    """One `<ID>` for everything would throw away what the transcript is FOR: delta and done naming
    the same response is how a reader knows they are one turn."""
    out, applied = norm(normalize, "openai-realtime", [
        txt({"type": "response.created", "response": {"id": "resp_AAA111"}}),
        txt({"type": "response.text.delta", "response_id": "resp_AAA111", "item_id": "item_BBB222"}),
        txt({"type": "response.done", "response": {"id": "resp_AAA111"}}),
    ])
    got = [f["json"] for f in out["frames"]]
    assert got[0]["response"]["id"] == "<ID:1>"
    assert got[1]["response_id"] == "<ID:1>"        # the SAME token: same turn
    assert got[1]["item_id"] == "<ID:2>"            # a different value gets a different token
    assert got[2]["response"]["id"] == "<ID:1>"
    assert "ws.frame-id" in applied


def test_interning_is_shared_across_id_keys_and_id_pointers(normalize):
    """A value that appears as a KEY in one frame and at a POINTER in another is one id, not two."""
    out, _ = norm(normalize, "openai-realtime", [
        txt({"type": "session.created", "session": {"id": "sess_ZZZ"}}),
        txt({"type": "x", "previous_item_id": "sess_ZZZ"}),
    ])
    assert out["frames"][0]["json"]["session"]["id"] == out["frames"][1]["json"]["previous_item_id"]


def test_a_door_that_started_reusing_an_id_is_itself_a_diff(normalize):
    """The property interning buys: two distinct ids canonicalise differently from one repeated id."""
    two, _ = norm(normalize, "openai-realtime",
                  [txt({"response_id": "a1"}), txt({"response_id": "b2"})])
    one, _ = norm(normalize, "openai-realtime",
                  [txt({"response_id": "a1"}), txt({"response_id": "a1"})])
    assert [f["json"] for f in two["frames"]] != [f["json"] for f in one["frames"]]


def test_a_null_id_stays_null_rather_than_becoming_an_id(normalize):
    """`"event_id": null` is what an OpenAI error event carries, and it is a fact about the event."""
    out, _ = norm(normalize, "openai-realtime", [txt({"type": "error", "error": {"event_id": None}})])
    assert out["frames"][0]["json"]["error"]["event_id"] is None


def test_ids_are_numbered_in_wire_order(normalize):
    out, _ = norm(normalize, "openai-realtime",
                  [txt({"item_id": "z"}), txt({"item_id": "a"}), txt({"item_id": "m"})])
    assert [f["json"]["item_id"] for f in out["frames"]] == ["<ID:1>", "<ID:2>", "<ID:3>"]


# ── timestamps ───────────────────────────────────────────────────────────────────────────────────
def test_a_dialect_timestamp_is_blanked(normalize):
    out, applied = norm(normalize, "openai-realtime",
                        [txt({"type": "session.created", "session": {"created_at": 1770000000}})])
    assert out["frames"][0]["json"]["session"]["created_at"] == "<TS>"
    assert "ws.frame-ts" in applied


def test_the_shared_timestamp_keys_apply_inside_a_frame_too(normalize):
    """`created`/`expires_at`/… are already the normalizer's own list; a frame is not a different
    kind of document just because it arrived on a socket."""
    out, _ = norm(normalize, "echo", [txt({"created": 1, "updated_at": 2})])
    assert out["frames"][0]["json"] == {"created": "<TS>", "updated_at": "<TS>"}


def test_twilio_timestamp_goes_but_the_counters_stay(normalize):
    """`sequenceNumber` and `media.chunk` are counters, and a door that SKIPPED one is exactly the
    bug this family exists to catch. Scrubbing them would hide it."""
    out, _ = norm(normalize, "twilio-media", [txt({
        "event": "media", "sequenceNumber": "4", "streamSid": "MZ0123456789abcdef",
        "media": {"chunk": "3", "timestamp": "5000", "payload": AUDIO_B64}})])
    got = out["frames"][0]["json"]
    assert got["sequenceNumber"] == "4"
    assert got["media"]["chunk"] == "3"
    assert got["media"]["timestamp"] == "<TS>"
    assert got["streamSid"] == "<ID:1>"
    assert got["media"]["payload"] == AUDIO_OUT


# ── audio ────────────────────────────────────────────────────────────────────────────────────────
def test_audio_becomes_length_and_digest(normalize):
    """Tens of kilobytes of base64 make a golden unreviewable; dropping it would let a door that sent
    silence record identically to one that did not."""
    out, applied = norm(normalize, "openai-realtime",
                        [txt({"type": "input_audio_buffer.append", "audio": AUDIO_B64}, "out")])
    assert out["frames"][0]["json"]["audio"] == AUDIO_OUT
    assert "ws.audio" in applied


def test_different_audio_hashes_differently(normalize):
    a, _ = norm(normalize, "openai-realtime", [txt({"audio": AUDIO_B64})])
    b, _ = norm(normalize, "openai-realtime", [txt({"audio": base64.b64encode(b"\x00" * len(AUDIO)).decode()})])
    assert a["frames"][0]["json"]["audio"] != b["frames"][0]["json"]["audio"]
    assert a["frames"][0]["json"]["audio"]["bytes"] == b["frames"][0]["json"]["audio"]["bytes"]


def test_the_audio_digest_is_not_eaten_by_the_generic_hash_rule(normalize):
    """ID_RULES' own `\\b[0-9a-fA-F]{32,}\\b` -> `<HASH>` would otherwise scrub the sha256 this
    exists to record. The payload is held as an object until the end of the walk for exactly this."""
    out, _ = norm(normalize, "openai-realtime", [txt({"audio": AUDIO_B64})])
    assert out["frames"][0]["json"]["audio"]["sha256"] == hashlib.sha256(AUDIO).hexdigest()
    assert "<HASH>" not in json.dumps(out)


def test_delta_is_text_on_a_text_event_and_audio_on_an_audio_event(normalize):
    """The reason `delta` is listed PER EVENT and not as a bare key name -- the same key carries a
    transcript on one event and base64 PCM on another."""
    out, _ = norm(normalize, "openai-realtime", [
        txt({"type": "response.text.delta", "delta": "hello"}),
        txt({"type": "response.audio.delta", "delta": AUDIO_B64}),
    ])
    assert out["frames"][0]["json"]["delta"] == "hello"
    assert out["frames"][1]["json"]["delta"] == AUDIO_OUT


def test_gemini_inline_data_is_hashed_by_its_own_rows_key(normalize):
    out, _ = norm(normalize, "gemini-live",
                  [txt({"realtimeInput": {"mediaChunks": [{"mimeType": "audio/pcm", "data": AUDIO_B64}]}}, "out")])
    chunk = out["frames"][0]["json"]["realtimeInput"]["mediaChunks"][0]
    assert chunk["data"] == AUDIO_OUT
    assert chunk["mimeType"] == "audio/pcm"


def test_an_audio_key_carrying_something_that_is_not_base64_is_named_not_swallowed(normalize):
    """The day a door changes the encoding of the one field this rule summarises, it says so."""
    out, applied = norm(normalize, "openai-realtime", [txt({"type": "input_audio_buffer.append", "audio": "!!not!!"})])
    assert "ws.audio-undecodable" in applied
    assert out["frames"][0]["json"]["audio"] == "!!not!!"


def test_a_binary_frame_is_hashed_whatever_the_dialect(normalize):
    """Neutral, not per-dialect: a binary frame on a realtime session is audio by construction."""
    out, applied = norm(normalize, "echo", [{"dir": "out", "opcode": "binary", "base64": AUDIO_B64}])
    assert out["frames"][0]["base64_audio"] == AUDIO_OUT
    assert "base64" not in out["frames"][0]
    assert "ws.binary-payload" in applied


def test_the_gemini_event_name_is_the_single_top_level_key(normalize):
    """The dialect with no event pointer at all -- proof the event rule is data too."""
    row = normalize.WS_DIALECTS["gemini-live"]
    assert row["event_pointer"] is None
    assert normalize.ws_event_name({"setupComplete": {}}, row) == "setupComplete"
    assert normalize.ws_event_name({"a": 1, "b": 2}, row) is None


# ── what survives ────────────────────────────────────────────────────────────────────────────────
def test_the_close_and_the_frame_order_are_untouched(normalize):
    """The two facts a session cell is mostly about."""
    close = {"by": "server", "code": 1011, "reason": "upstream failed", "echo": "close"}
    out, _ = norm(normalize, "openai-realtime",
                  [txt({"type": "a"}), txt({"type": "b"}), {"dir": "out", "opcode": "close", "code": 1000, "reason": ""}],
                  close=close)
    assert out["close"] == close
    assert [f["opcode"] for f in out["frames"]] == ["text", "text", "close"]
    assert [f["json"]["type"] for f in out["frames"][:2]] == ["a", "b"]


def test_the_direction_of_every_frame_survives(normalize):
    out, _ = norm(normalize, "echo", [txt({"a": 1}, "out"), txt({"a": 2}, "in")])
    assert [f["dir"] for f in out["frames"]] == ["out", "in"]


def test_the_handshake_facts_survive(normalize):
    out, _ = norm(normalize, "openai-realtime", [], key="dGhlIHNhbXBsZSBub25jZQ==")
    assert out["accept_ok"] is True
    assert out["key"] == "dGhlIHNhbXBsZSBub25jZQ=="
    assert out["dialect"] == "openai-realtime"


def test_a_frame_that_is_not_json_is_still_a_frame(normalize):
    out, _ = norm(normalize, "echo", [{"dir": "in", "opcode": "text", "text": "not json at all"}])
    assert out["frames"][0]["text"] == "not json at all"


def test_a_volatile_id_in_a_close_reason_is_scrubbed_by_the_shared_scalar_rules(normalize):
    out, _ = norm(normalize, "echo", [], close={"by": "server", "code": 1011, "reason": "req_0123456789abcdef failed"})
    assert out["close"]["reason"] == "req_<ID> failed"


# ── end to end, through normalize() itself ───────────────────────────────────────────────────────
def test_normalize_puts_the_ws_block_on_the_cell_and_records_its_rules(normalize):
    cap = {"status": 101, "headers": {"upgrade": "websocket", "date": "Mon, 1 Jan 2026 00:00:00 GMT"},
           "body": "", "effects": {"usage": {"requests": 1}, "metrics": {}, "audit": {"added": 0, "items": []}},
           "ws": {"dialect": "openai-realtime", "key": "k", "accept_ok": True,
                  "frames": [txt({"type": "session.created", "event_id": "event_XYZ", "session": {"id": "sess_Q", "created_at": 1}})],
                  "close": {"by": "client", "code": 1000, "reason": "", "echo": "close"}}}
    out = normalize.normalize(cap, None)
    assert out["ws"]["frames"][0]["json"] == {"type": "session.created", "event_id": "<ID:1>",
                                              "session": {"id": "<ID:2>", "created_at": "<TS>"}}
    assert {"ws.frame-id", "ws.frame-ts"} <= set(out["applied"])
    assert "date" not in out["headers"]      # the ordinary header rules still ran


def test_a_cell_with_no_session_gets_no_ws_block(normalize):
    """A refused handshake normalizes as the ordinary HTTP response it is."""
    cap = {"status": 404, "headers": {}, "body": '{"error":"no"}', "effects": {}}
    out = normalize.normalize(cap, None)
    assert "ws" not in out


def test_normalizing_twice_is_idempotent_on_the_transcript(normalize):
    """A recording re-normalized by renormalize.sh must not drift, or every golden carrying it does."""
    frames = [txt({"type": "response.done", "response_id": "resp_A", "audio": AUDIO_B64})]
    once, _ = norm(normalize, "openai-realtime", frames)
    twice, _ = norm(normalize, "openai-realtime",
                    [{"dir": "in", "opcode": "text", "text": json.dumps(once["frames"][0]["json"])}])
    # the ids re-intern to the same tokens and the already-summarised audio is left alone
    assert twice["frames"][0]["json"]["response_id"] == "<ID:1>"
    assert twice["frames"][0]["json"]["audio"] == AUDIO_OUT


@pytest.mark.parametrize("dialect", ["openai-realtime", "gemini-live", "twilio-media", "echo"])
def test_every_row_canonicalises_an_empty_session_without_raising(normalize, dialect):
    out, _ = norm(normalize, dialect, [])
    assert out["frames"] == []
