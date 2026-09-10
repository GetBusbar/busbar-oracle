# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Busbar Inc and contributors
"""The ws half of mock-upstream.py: a duplex upstream, scripted per dialect, on the existing verbs.

These drive the REAL shipped mock in a subprocess with the REAL `ws` driver, so what is under test
is the pair as record.sh will run it -- not two test doubles agreeing with each other.

The two properties that make a session recordable at all:

  * a session is not a request/response pair, so the mock cannot be a pure function of one request.
    It is a pure function of the CLIENT'S FRAME SEQUENCE instead: fixed ids, fixed usage, event ids
    counted from 1 per session, no clocks. Same script in, same frames out, every run;
  * the outage a cell orders is chosen OUT OF BAND, through the control file, so busbar's own frames
    stay byte-identical to the healthy session the recording is compared against.
"""
from __future__ import annotations

import json

import pytest

OAI_PATH = "/v1/realtime?model=m-openai-realtime"
GEMINI_PATH = "/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"


def drive(capture_ws, port, path, script, dialect=None, timeout=8):
    try:
        return capture_ws.drive({"url": f"ws://127.0.0.1:{port}{path}", "dialect": dialect,
                                 "timeout_secs": timeout, "script": script})
    except capture_ws.HarnessError as e:
        return {"harness_error": str(e)}


def texts(rec):
    return [json.loads(f["text"]) for f in rec["ws"]["frames"] if f["dir"] == "in" and f["opcode"] == "text"]


@pytest.fixture
def clean_control(mock_upstream):
    port, set_control = mock_upstream
    set_control(None)
    yield port, set_control
    set_control(None)


# ── the handshake ────────────────────────────────────────────────────────────────────────────────
def test_an_upgrade_on_a_dialect_path_completes_the_rfc_6455_handshake(clean_control, capture_ws):
    """Before 0.3.12 this mock had ZERO Upgrade handling, which is why the whole served-session
    family was unrecordable from anything."""
    port, _ = clean_control
    r = drive(capture_ws, port, OAI_PATH, [{"await": {"/type": "session.created"}}], "openai-realtime")
    assert r["status"] == 101
    assert r["ws"]["accept_ok"] is True
    assert r["headers"]["upgrade"] == "websocket"


def test_an_upgrade_on_an_unknown_path_is_a_404_not_a_session(clean_control, capture_ws):
    port, _ = clean_control
    r = drive(capture_ws, port, "/nope", [{"await": "close"}])
    assert r["status"] == 404
    assert r["ws"] is None
    assert "no websocket dialect" in r["body"]


def test_an_upgrade_missing_the_rfc_headers_is_a_400(clean_control, capture_ws, wsframe):
    """The mock refuses to complete a handshake it was not properly asked for, rather than guessing."""
    import socket
    port, _ = clean_control
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        sock.sendall(f"GET {OAI_PATH} HTTP/1.1\r\nHost: h\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n".encode())
        status, _, _ = wsframe.parse_status_line(wsframe.read_http_head(sock).split(b"\r\n\r\n", 1)[0])
        assert status == 400
    finally:
        sock.close()


def test_an_ordinary_get_is_still_the_readiness_probe(clean_control, capture_ws):
    """The ws path is additive: nothing the mock already answered changed shape."""
    import urllib.request
    port, _ = clean_control
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as f:
        assert json.load(f) == {"ok": True, "mock": "oracle-upstream"}


# ── openai-realtime ──────────────────────────────────────────────────────────────────────────────
def test_openai_realtime_opens_with_session_created(clean_control, capture_ws):
    port, _ = clean_control
    r = drive(capture_ws, port, OAI_PATH, [{"await": {"/type": "session.created"}}], "openai-realtime")
    ev = texts(r)[0]
    assert ev["type"] == "session.created"
    assert ev["session"]["id"] == "sess_oracle"
    assert ev["event_id"] == "event_oracle_0001"


def test_openai_realtime_answers_response_create_with_the_whole_turn(clean_control, capture_ws):
    port, _ = clean_control
    r = drive(capture_ws, port, OAI_PATH,
              [{"await": {"/type": "session.created"}}, {"send": {"type": "response.create"}},
               {"await": {"/type": "response.done"}}], "openai-realtime")
    assert [e["type"] for e in texts(r)] == [
        "session.created", "response.created", "response.output_item.added", "response.text.delta",
        "response.text.done", "response.output_item.done", "response.done"]


def test_the_turn_draws_the_same_usage_every_other_dialect_draws(clean_control, capture_ws):
    """11 in / 7 out, the mock's fixed IN_TOK/OUT_TOK -- so a ws cell's usage delta is comparable
    with an llm cell's and neither is a clock."""
    port, _ = clean_control
    r = drive(capture_ws, port, OAI_PATH,
              [{"await": {"/type": "session.created"}}, {"send": {"type": "response.create"}},
               {"await": {"/type": "response.done"}}], "openai-realtime")
    usage = texts(r)[-1]["response"]["usage"]
    assert usage["input_tokens"] == 11
    assert usage["output_tokens"] == 7
    assert usage["total_tokens"] == 18


def test_event_ids_count_from_one_per_session_and_never_from_a_clock(clean_control, capture_ws):
    port, _ = clean_control
    script = [{"await": {"/type": "session.created"}}, {"send": {"type": "response.create"}},
              {"await": {"/type": "response.done"}}]
    a = [e["event_id"] for e in texts(drive(capture_ws, port, OAI_PATH, script, "openai-realtime"))]
    b = [e["event_id"] for e in texts(drive(capture_ws, port, OAI_PATH, script, "openai-realtime"))]
    assert a == b == [f"event_oracle_{i:04d}" for i in range(1, 8)]


def test_the_same_script_twice_yields_byte_identical_frames(clean_control, capture_ws):
    """The property a golden rests on. Nothing here is drawn from a clock or a counter that survives
    the session."""
    port, _ = clean_control
    script = [{"await": {"/type": "session.created"}},
              {"send": {"type": "session.update", "session": {"voice": "echo"}}},
              {"await": {"/type": "session.updated"}}]
    a = drive(capture_ws, port, OAI_PATH, script, "openai-realtime")
    b = drive(capture_ws, port, OAI_PATH, script, "openai-realtime")
    assert a["ws"]["frames"] == b["ws"]["frames"]


def test_session_update_is_merged_onto_the_fixed_session(clean_control, capture_ws):
    port, _ = clean_control
    r = drive(capture_ws, port, OAI_PATH,
              [{"await": {"/type": "session.created"}},
               {"send": {"type": "session.update", "session": {"voice": "echo"}}},
               {"await": {"/type": "session.updated"}}], "openai-realtime")
    s = texts(r)[-1]["session"]
    assert s["voice"] == "echo"
    assert s["id"] == "sess_oracle"


def test_an_unknown_client_event_is_an_in_band_error_and_the_session_stays_open(clean_control, capture_ws):
    """The dialect's OWN way of refusing, which is what a door must be recorded relaying."""
    port, _ = clean_control
    r = drive(capture_ws, port, OAI_PATH,
              [{"await": {"/type": "session.created"}}, {"send": {"type": "nonsense.event"}},
               {"await": {"/type": "error"}}, {"send": {"type": "response.create"}},
               {"await": {"/type": "response.done"}}], "openai-realtime")
    assert r["ws"]["close"]["by"] == "client"      # the session survived the error
    assert [e["type"] for e in texts(r)][:3] == ["session.created", "error", "response.created"]


def test_an_audio_append_is_accepted_silently_and_committed_on_request(clean_control, capture_ws):
    port, _ = clean_control
    r = drive(capture_ws, port, OAI_PATH,
              [{"await": {"/type": "session.created"}},
               {"send": {"type": "input_audio_buffer.append", "audio": "AAECAwQF"}},
               {"send": {"type": "input_audio_buffer.commit"}},
               {"await": {"/type": "input_audio_buffer.committed"}}], "openai-realtime")
    assert [e["type"] for e in texts(r)] == ["session.created", "input_audio_buffer.committed"]


# ── gemini-live ──────────────────────────────────────────────────────────────────────────────────
def test_gemini_live_sends_nothing_on_open_and_answers_setup(clean_control, capture_ws):
    """A second dialect with a genuinely different shape -- no open frame, no event ids, the event
    is the single top-level key -- which is what proves the mock's dialect table is a table."""
    port, _ = clean_control
    r = drive(capture_ws, port, GEMINI_PATH,
              [{"send": {"setup": {"model": "models/x"}}}, {"await": {"/setupComplete": {}}}], "gemini-live")
    assert r["status"] == 101
    assert r["ws"]["frames"][0]["dir"] == "out"    # the client spoke first
    assert texts(r) == [{"setupComplete": {}}]


def test_gemini_live_answers_client_content_with_a_turn_and_usage_metadata(clean_control, capture_ws):
    port, _ = clean_control
    r = drive(capture_ws, port, GEMINI_PATH,
              [{"send": {"setup": {"model": "models/x"}}}, {"await": {"/setupComplete": {}}},
               {"send": {"clientContent": {"turns": [{"role": "user", "parts": [{"text": "hi"}]}], "turnComplete": True}}},
               {"await": {"/serverContent/turnComplete": True}}], "gemini-live")
    got = texts(r)
    assert got[1]["serverContent"]["modelTurn"]["parts"] == [{"text": "MARKER"}]
    assert got[2]["usageMetadata"] == {"promptTokenCount": 11, "responseTokenCount": 7, "totalTokenCount": 18}


def test_gemini_live_says_error_with_a_close_never_an_in_band_frame(clean_control, capture_ws):
    """Per-dialect, and it is data: this dialect has no error event, so the row's `unknown` closes."""
    port, _ = clean_control
    r = drive(capture_ws, port, GEMINI_PATH, [{"send": {"nonsense": {}}}, {"await": "close"}], "gemini-live")
    assert r["ws"]["close"]["by"] == "server"
    assert r["ws"]["close"]["code"] == 1008


# ── the verbs ────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("verb,status", [("down", 503), ("5xx", 500), ("401", 401)])
def test_a_handshake_refusal_is_the_ordinary_status_the_verb_names(clean_control, capture_ws, verb, status):
    """An upgrade is a GET like any other, so the existing verbs need no ws spelling."""
    port, set_control = clean_control
    set_control(verb)
    r = drive(capture_ws, port, OAI_PATH, [{"await": "close"}], "openai-realtime")
    assert r["status"] == status
    assert r["ws"] is None


def test_cut_kills_the_socket_after_the_open_frames_with_no_close_frame(clean_control, capture_ws):
    """The upstream that vanished mid-session, which no status code can say."""
    port, set_control = clean_control
    set_control("cut")
    r = drive(capture_ws, port, OAI_PATH, [{"await": {"/type": "session.created"}}, {"await": "close"}], "openai-realtime")
    assert r["status"] == 101
    assert r["ws"]["close"]["by"] == "eof"
    assert r["ws"]["close"]["code"] is None


def test_ws_error_answers_the_turn_then_sends_the_dialects_error_then_closes_1011(clean_control, capture_ws):
    """THE DISPUTE CASE: the upstream fails AFTER the door accepted the session and started billing.
    The turn is answered in full first -- that is what makes it a dispute and not a refusal."""
    port, set_control = clean_control
    set_control("ws-error")
    r = drive(capture_ws, port, OAI_PATH,
              [{"await": {"/type": "session.created"}}, {"send": {"type": "response.create"}},
               {"await": "close"}], "openai-realtime")
    kinds = [e["type"] for e in texts(r)]
    assert "response.done" in kinds
    assert kinds[-1] == "error"
    assert r["ws"]["close"] == {"by": "server", "code": 1011, "reason": "oracle: upstream failed mid-stream"}


def test_ws_close_closes_1011_with_no_error_frame_first(clean_control, capture_ws):
    """The other half of the dispute: a door that saw no error frame at all still has to say
    something to its caller, and what it says is the cell."""
    port, set_control = clean_control
    set_control("ws-close")
    r = drive(capture_ws, port, OAI_PATH,
              [{"await": {"/type": "session.created"}}, {"send": {"type": "response.create"}},
               {"await": "close"}], "openai-realtime")
    assert [e["type"] for e in texts(r)][-1] == "response.done"
    assert r["ws"]["close"]["code"] == 1011
    assert r["ws"]["close"]["reason"] == "oracle: upstream closed mid-session"


def test_the_verbs_are_per_model_the_same_way_every_other_control_is(clean_control, capture_ws):
    """A control keyed by a model this session is not about leaves it healthy -- the shape the
    shipped corpus already writes, reused rather than given a ws dialect of its own."""
    port, set_control = clean_control
    set_control({"m-some-other-lane": "down"})
    r = drive(capture_ws, port, OAI_PATH, [{"await": {"/type": "session.created"}}], "openai-realtime")
    assert r["status"] == 101


def test_an_unresolvable_control_is_a_599_never_a_healthy_session(clean_control, capture_ws):
    """The silent-pass this mock's control code exists to refuse: a cell that ordered an outage must
    never record the success path. 599 is a status no dialect and no verb of this mock produces."""
    port, set_control = clean_control
    set_control({"not-a-model-or-verb": "whatever"})
    r = drive(capture_ws, port, OAI_PATH, [{"await": "close"}], "openai-realtime")
    assert r["status"] == 599
    assert "oracle_harness_error" in r["body"]


def test_clearing_the_control_restores_the_healthy_session(clean_control, capture_ws):
    port, set_control = clean_control
    set_control("down")
    assert drive(capture_ws, port, OAI_PATH, [{"await": "close"}], "openai-realtime")["status"] == 503
    set_control(None)
    assert drive(capture_ws, port, OAI_PATH, [{"await": {"/type": "session.created"}}], "openai-realtime")["status"] == 101


# ── the echo dialect ─────────────────────────────────────────────────────────────────────────────
def test_the_echo_dialect_returns_every_frame_unchanged(clean_control, capture_ws):
    """A dialect-free leg, for a cell whose subject is the door's framing rather than a provider's
    grammar."""
    port, _ = clean_control
    r = drive(capture_ws, port, "/ws/echo", [{"send": {"a": 1}}, {"await": {"/a": 1}}], "echo")
    assert texts(r) == [{"a": 1}]
