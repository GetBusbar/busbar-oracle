# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Busbar Inc and contributors
"""The `ws` driver: what it puts on the wire, and what it records having happened.

Two properties, and every test below is one of them:

  * the STIMULUS is a pure function of the cell -- the same handshake bytes, the same mask, the same
    script, every run -- because a recording made twice from one binary must be byte-identical;
  * the RECORD is what happened, including the parts a friendlier client would smooth over: the
    frames the door sent before the one we were waiting for, the pings, the close code, and the
    difference between "the door closed" and "the socket died".
"""
from __future__ import annotations

import base64
import json
import os

import pytest


def ops(rec):
    return [(f["dir"], f["opcode"]) for f in rec["ws"]["frames"]]


# ── the stimulus ─────────────────────────────────────────────────────────────────────────────────
def test_the_handshake_uses_the_rfc_sample_nonce_and_verifies_the_accept(scripted_server, wsframe):
    _, run = scripted_server
    r = run([("send", 1, b'{"type":"session.created"}'), ("expect_close",)],
            {"script": [{"await": {"/type": "session.created"}}]})
    assert r["status"] == 101
    assert r["ws"]["accept_ok"] is True
    assert r["_peer"]["headers"]["sec-websocket-key"] == wsframe.SAMPLE_KEY


def test_the_request_head_is_the_cells_target_in_one_fixed_order(scripted_server):
    _, run = scripted_server
    r = run([("send", 1, b'{"t":1}'), ("expect_close",)],
            {"headers": {"Authorization": "Bearer t", "X-Cell": "one"}, "script": [{"await": {"/t": 1}}]})
    assert r["_peer"]["request"] == "GET /v1/realtime?model=m HTTP/1.1"
    assert r["_peer"]["headers"]["authorization"] == "Bearer t"
    assert r["_peer"]["headers"]["x-cell"] == "one"
    assert r["_peer"]["headers"]["sec-websocket-version"] == "13"


def test_a_cell_may_name_its_own_key_and_the_accept_is_checked_against_that_one(scripted_server, wsframe):
    """`accept_ok` is derived, never copied: a door that echoed a constant accept value would fail."""
    _, run = scripted_server
    r = run([("send", 1, b'{"t":1}'), ("expect_close",)],
            {"key": "AAAAAAAAAAAAAAAAAAAAAA==", "script": [{"await": {"/t": 1}}]})
    assert r["_peer"]["headers"]["sec-websocket-key"] == "AAAAAAAAAAAAAAAAAAAAAA=="
    assert r["ws"]["accept_ok"] is True
    # `drive` writes the key at the top level; `assemble` folds it into the cell's ws block
    assert r["key"] == "AAAAAAAAAAAAAAAAAAAAAA=="


def test_a_send_step_writes_canonical_json_so_key_order_is_never_the_dicts(scripted_server):
    _, run = scripted_server
    r = run([("echo",), ("expect_close",)],
            {"script": [{"send": {"b": 1, "a": 2}}, {"await": {"/a": 2}}]})
    assert r["ws"]["frames"][0] == {"dir": "out", "opcode": "text", "text": '{"a":2,"b":1}'}


def test_a_send_text_step_writes_exactly_those_bytes(scripted_server):
    _, run = scripted_server
    r = run([("echo",), ("expect_close",)], {"script": [{"send_text": '{ "b" : 1 }'}, {"await": {"/b": 1}}]})
    assert r["ws"]["frames"][0]["text"] == '{ "b" : 1 }'


def test_a_binary_send_is_recorded_as_base64_with_its_direction(scripted_server):
    _, run = scripted_server
    r = run([("echo",), ("expect_close",)],
            {"script": [{"send_binary_base64": "AAEC"}, {"await_opcode": "binary"}]})
    assert r["ws"]["frames"][0] == {"dir": "out", "opcode": "binary", "base64": "AAEC"}
    assert r["ws"]["frames"][1] == {"dir": "in", "opcode": "binary", "base64": "AAEC"}


def test_await_opcode_waits_for_a_frame_that_has_no_json_to_point_at(scripted_server):
    """`await` matches by RFC 6901 pointer and so can only ever match a TEXT frame. A cell about the
    door streaming audio back has nothing to point at, and guessing a frame count is not a cell."""
    _, run = scripted_server
    r = run([("send", 1, b'{"t":1}'), ("send", 2, b"\x00\x01"), ("expect_close",)],
            {"script": [{"await_opcode": "binary"}]})
    assert [(f["dir"], f["opcode"]) for f in r["ws"]["frames"]][:2] == [("in", "text"), ("in", "binary")]


def test_await_opcode_refuses_a_name_that_is_no_opcode(scripted_server):
    _, run = scripted_server
    r = run([("expect_close",)], {"script": [{"await_opcode": "audio"}]})
    assert "harness_error" in r and "names no opcode" in r["harness_error"]


def test_two_identical_cells_produce_identical_wire_bytes(scripted_server):
    """The property the whole recorder rests on: nothing in the client is drawn from a clock or a
    random source, so the door sees the same request twice."""
    _, run = scripted_server
    plan = [("echo",), ("expect_close",)]
    spec = {"script": [{"send": {"z": 1, "a": [2, 3]}}, {"await": {"/a/0": 2}}]}
    a, b = run(plan, spec), run(plan, spec)
    assert [f for f in a["ws"]["frames"] if f["dir"] == "out"] == [f for f in b["ws"]["frames"] if f["dir"] == "out"]
    assert a["_peer"]["request"] == b["_peer"]["request"]
    assert a["_peer"]["headers"] == b["_peer"]["headers"]


# ── awaiting, by RFC 6901 pointer ────────────────────────────────────────────────────────────────
def test_await_by_pointer_records_every_frame_up_to_the_match(scripted_server):
    """The frames the door sent before the one the cell was waiting for are the transcript too."""
    _, run = scripted_server
    r = run([("send", 1, b'{"type":"a"}'), ("send", 1, b'{"type":"b"}'), ("expect_close",)],
            {"script": [{"await": {"/type": "b"}}]})
    assert [f.get("text") for f in r["ws"]["frames"][:2]] == ['{"type":"a"}', '{"type":"b"}']


def test_await_unescapes_rfc_6901_tokens(scripted_server):
    """`~1` is a slash and `~0` a tilde -- the repo's addressing idiom, held to the RFC itself."""
    _, run = scripted_server
    body = b'{"x":{"y/z":[0,{"k~":1}]}}'
    r = run([("send", 1, body), ("expect_close",)], {"script": [{"await": {"/x/y~1z/1/k~0": 1}}]})
    assert r["ws"]["frames"][0]["text"] == body.decode()


def test_every_pointer_in_an_await_must_match_not_just_one(scripted_server):
    _, run = scripted_server
    r = run([("send", 1, b'{"type":"b","ok":false}'), ("send", 1, b'{"type":"b","ok":true}'), ("expect_close",)],
            {"script": [{"await": {"/type": "b", "/ok": True}}]})
    assert len([f for f in r["ws"]["frames"] if f["dir"] == "in" and f["opcode"] == "text"]) == 2


def test_an_await_the_door_never_satisfies_is_a_harness_error_not_a_recording(scripted_server):
    """A transcript cut off by the recorder's own clock is not what the door did. Recording it would
    make every later binary reproduce this harness's timeout and call it a pass."""
    _, run = scripted_server
    r = run([("send", 1, b'{"type":"a"}'), ("expect_close",)], {"script": [{"await": {"/type": "never"}}]})
    assert "harness_error" in r
    assert "timed out" in r["harness_error"]


def test_an_unknown_script_step_is_a_harness_error(scripted_server):
    _, run = scripted_server
    r = run([("expect_close",)], {"script": [{"teleport": 1}]})
    assert "harness_error" in r


def test_a_non_ws_url_is_refused_rather_than_dialled(capture_ws):
    """The harness is loopback; a cell that named wss:// would be recording somebody else's server."""
    with pytest.raises(capture_ws.HarnessError):
        capture_ws.drive({"url": "wss://api.openai.com/v1/realtime", "script": []})


# ── how a session ends ───────────────────────────────────────────────────────────────────────────
def test_the_clients_own_close_is_answered_and_the_doors_echo_is_recorded(scripted_server):
    """The bug this driver shipped with: `send_close` set `closed`, and the wait for the echo was
    gated on `not closed`, so the door's answering close was dropped from every clean transcript."""
    _, run = scripted_server
    r = run([("send", 1, b'{"t":1}'), ("expect_close",)], {"script": [{"await": {"/t": 1}}]})
    assert ops(r) == [("in", "text"), ("out", "close"), ("in", "close")]
    assert r["ws"]["close"] == {"by": "client", "code": 1000, "reason": "", "echo": "close"}


def test_a_door_that_never_echoes_our_close_is_recorded_not_raised(scripted_server):
    """A fact about the door, not a harness failure: the session is over either way."""
    _, run = scripted_server
    r = run([("send", 1, b'{"t":1}'), ("hang",)], {"script": [{"await": {"/t": 1}}], "timeout_secs": 1})
    assert r["ws"]["close"]["by"] == "client"
    assert r["ws"]["close"]["echo"] in ("none", "eof")


def test_a_server_initiated_close_records_its_code_and_reason(scripted_server):
    _, run = scripted_server
    r = run([("send", 1, b'{"t":1}'), ("close", 1011, "boom")], {"script": [{"await": "close"}]})
    assert r["ws"]["close"] == {"by": "server", "code": 1011, "reason": "boom"}
    assert ops(r)[-2:] == [("in", "close"), ("out", "close")]


def test_a_socket_that_dies_with_no_close_frame_is_eof_never_a_fabricated_1006(scripted_server):
    """1006 is a status no peer sends. `by: eof` with a null code says what actually happened."""
    _, run = scripted_server
    r = run([("send", 1, b'{"t":1}'), ("cut",)], {"script": [{"await": "close"}]})
    assert r["ws"]["close"]["by"] == "eof"
    assert r["ws"]["close"]["code"] is None


def test_an_explicit_close_step_uses_the_cells_code_and_reason(scripted_server):
    _, run = scripted_server
    r = run([("send", 1, b'{"t":1}'), ("expect_close",)],
            {"script": [{"await": {"/t": 1}}, {"close": {"code": 1001, "reason": "bye"}}]})
    out_close = [f for f in r["ws"]["frames"] if f["dir"] == "out" and f["opcode"] == "close"][0]
    assert (out_close["code"], out_close["reason"]) == (1001, "bye")
    assert r["ws"]["close"]["by"] == "client"


def test_a_step_after_the_session_closed_is_a_harness_error(scripted_server):
    """Sending into a closed socket would record a frame the door never saw."""
    _, run = scripted_server
    r = run([("close", 1011, "gone")], {"script": [{"await": "close"}, {"send": {"a": 1}}]})
    assert "harness_error" in r


# ── control frames ───────────────────────────────────────────────────────────────────────────────
def test_a_ping_from_the_door_is_ponged_and_both_frames_are_in_the_transcript(scripted_server):
    """RFC 6455 §5.5.2 requires the pong. Recording both is what makes a door that STOPS pinging a
    diff rather than an invisible change."""
    _, run = scripted_server
    r = run([("send", 9, b"hb"), ("send", 1, b'{"t":2}'), ("expect_close",)], {"script": [{"await": {"/t": 2}}]})
    assert ops(r)[:3] == [("in", "ping"), ("out", "pong"), ("in", "text")]
    assert r["ws"]["frames"][1]["base64"] == base64.b64encode(b"hb").decode()


def test_a_ping_step_sends_the_cells_payload(scripted_server):
    _, run = scripted_server
    r = run([("echo",), ("expect_close",)], {"script": [{"ping": "aGI="}, {"await": "close"}]})
    assert r["ws"]["frames"][0] == {"dir": "out", "opcode": "ping", "base64": "aGI="}


# ── a refused handshake ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("status", [401, 404, 503])
def test_a_refused_handshake_is_recorded_as_a_whole_http_response(scripted_server, status):
    """A door that says 404 to an upgrade said it in an ordinary HTTP response, and the body is the
    contract -- that is the shape VT-6's whole voice.mount family is recorded in."""
    _, run = scripted_server
    r = run([("refuse", status, b'{"error":"no"}')], {"script": [{"await": "close"}]})
    assert r["status"] == status
    assert r["body"] == '{"error":"no"}'
    assert r["ws"] is None


# ── assembling the cell ──────────────────────────────────────────────────────────────────────────
def test_assemble_records_the_same_effects_an_http_cell_would(capture_ws, tmp_path):
    """capture.py's own helpers, so a ws cell's `effects` can never drift from an http cell's."""
    before, after = tmp_path / "before", tmp_path / "after"
    before.mkdir()
    after.mkdir()
    (before / "usage.json").write_text(json.dumps({"requests": 1}))
    (after / "usage.json").write_text(json.dumps({"requests": 3}))
    (before / "metrics.txt").write_text("m 1\n")
    (after / "metrics.txt").write_text("m 4\n")
    (before / "audit.json").write_text(json.dumps({"items": []}))
    (after / "audit.json").write_text(json.dumps({"items": []}))
    ws = tmp_path / "ws.json"
    ws.write_text(json.dumps({"status": 101, "headers": {"upgrade": "websocket"}, "body": "", "dialect": "echo",
                              "key": "k", "ws": {"accept_ok": True, "frames": [{"dir": "in", "opcode": "text", "text": "x"}],
                                                 "close": {"by": "client", "code": 1000, "reason": ""}}}))
    cap = capture_ws.assemble(str(ws), str(before), str(after), [])
    assert cap["status"] == 101
    assert cap["effects"]["usage"] == {"requests": 2}
    assert cap["effects"]["metrics"] == {"m": 3}
    assert cap["ws"]["dialect"] == "echo"
    assert cap["ws"]["frames"] == [{"dir": "in", "opcode": "text", "text": "x"}]


def test_assemble_writes_no_ws_block_for_a_refused_handshake(capture_ws, tmp_path):
    """`ws` absent is the recorded fact 'no session was opened' -- and it is what the differ's
    session arm compares against a candidate that DID open one."""
    for d in ("before", "after"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "usage.json").write_text("{}")
        (tmp_path / d / "metrics.txt").write_text("")
        (tmp_path / d / "audit.json").write_text(json.dumps({"items": []}))
    ws = tmp_path / "ws.json"
    ws.write_text(json.dumps({"status": 404, "headers": {}, "body": "{}", "ws": None}))
    cap = capture_ws.assemble(str(ws), str(tmp_path / "before"), str(tmp_path / "after"), [])
    assert "ws" not in cap
    assert cap["status"] == 404


def test_the_driver_is_reachable_through_the_entrypoints_table():
    import importlib.util
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location("cli_", os.path.join(here, "src", "busbar_oracle", "cli.py"))
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    assert cli.COMMANDS["capture-ws"] == "capture-ws.py"


def test_the_shipped_selftest_is_green(capture_ws):
    assert capture_ws.selftest() == 0
