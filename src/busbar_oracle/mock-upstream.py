#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Busbar Inc and contributors
"""A DETERMINISTIC multi-dialect LLM upstream for the shadow oracle.

The oracle records busbar's exact bytes for every (ingress dialect, egress dialect, outcome) cell.
For that to be byte-stable across runs and binaries, the upstream must be a pure function of the
request: fixed ids, fixed usage (11 in / 7 out), a fixed marker text, no clocks, no randomness.

It answers in the dialect the REQUEST PATH selects (busbar's egress lane decides the path), so one
process serves all six egress dialects:

  anthropic         POST /v1/messages
  openai-chat       POST /v1/chat/completions
  openai-responses  POST /v1/responses
  gemini            POST /v1beta/models/<model>:generateContent | :streamGenerateContent
  bedrock           POST /model/<model>/converse | /converse-stream
  cohere            POST /v2/chat

Outcome controls (the recorder sets them per cell):
  header  X-Oracle-Upstream: down   -> 503 with a fixed body (drives busbar's failover / upstream-error path)
  header  X-Oracle-Upstream: slow   -> reserved (timeout cells), currently same as down
  header  X-Oracle-Upstream: 401    -> 401 with a fixed body (the member's credential is rejected: a hard-down)
  header  X-Oracle-Upstream: stream-error
                                    -> a 200 stream that FAILS PART WAY THROUGH: the dialect's normal
                                       events up to and including the text delta, then an IN-BAND error
                                       event in that dialect's own error shape, and no terminal usage /
                                       [DONE] frame. Distinct from `cut`, which kills the socket with no
                                       error at all: here the upstream says WHY, in band, after the
                                       response has already begun — so the door cannot answer with a
                                       status code and has to translate the failure into its own stream
  header  X-Oracle-Upstream: citation
                                    -> the BUFFERED Responses answer, ANNOTATED: the same text, plus a
                                       URL citation written in the FLAT spelling the published
                                       UrlCitationBody declares (url/title/start_index/end_index on the
                                       annotation itself, not nested under `url_citation`). Every other
                                       dialect and every stream builder is byte-identical under it
  body    {"stream": true} (openai/anthropic/cohere) or the *stream* path (gemini/bedrock)
                                    -> a fixed SSE / streamed sequence in that dialect

WebSocket upstreams (0.3.12): a GET carrying `Upgrade: websocket` on a dialect's realtime path
completes the RFC 6455 handshake and then plays a fixed, scripted duplex session — see WS_DIALECTS:

  openai-realtime   GET /v1/realtime?model=<model>
  gemini-live       GET /ws/google.ai.generativelanguage.<ver>.GenerativeService.BidiGenerateContent
  echo              GET /ws/echo   (every frame straight back; the recorder's own tests)

The ordinary verbs refuse the HANDSHAKE (`down` 503, `401`, `5xx`) or kill the socket after the open
frames (`cut`); two more are the in-session dispute case, chosen out of band like every other verb:
  ws-error  answer the first client event, then the dialect's own error frame, then close 1011
  ws-close  answer the first client event, then close 1011 with no error frame first

Usage: mock-upstream.py <port> [marker] [control-file]

<control-file> is the third positional every caller in this tree actually passes (record.sh and the
script cells under scripts/): the path of a file the RECORDER writes between requests to select the
outcome for the next cell. It carries either a bare verb (down | 429 | 5xx | 401 | slow | cut,
applying to every model) or JSON {"<model>": "<verb>", ...} with "*" as the fallback — the same
vocabulary as the X-Oracle-Upstream header above, but chosen OUT OF BAND so busbar's own request is
byte-identical to the healthy cell it is being compared against. Absent file means a healthy
upstream; the recorder removes it again once the cell is captured. A FOREIGN mock answering on this
port would ignore the file entirely and serve every outage cell healthy, which is why record.sh
proves the port's owner is its own pid before it records anything.

Egress capture (opt-in, for proving what busbar actually SENT upstream — not just what came back):
  When the environment variable ORACLE_MOCK_CAPTURE_DIR is set, every handled request (whatever the
  outcome — 200, a control-file failure, a 404 for an unrouted path, all of it) is written as its own
  JSON file in that directory, named "<ns>-<pid>-<seq>.json" where <ns> is a nanosecond timestamp, so
  that filenames sort in request order. Each file holds exactly:
    {"path": <raw request path, unmodified>, "method": "POST",
     "headers": {<lowercased header name>: <value>, ...}, "body": <utf-8 str, or "base64:..." if not>}
  This lets a recorder that snapshots the directory's filename list immediately before issuing a
  request, and again immediately after, take "the filenames present after that weren't present
  before" (sorted, so ordering is preserved for cells that fire more than one egress request) as the
  egress record(s) for that one cell — no other coordination needed. When the env var is unset
  (the default), nothing is captured and this mock's behaviour is unchanged.
"""
import itertools
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

MARKER = "oracle-marker"
IN_TOK, OUT_TOK = 11, 7
_capture_seq = itertools.count()

# THE VERB VOCABULARY, IN ONE PLACE. Every outage this mock can be asked for is named here, and a
# control that resolves to none of them is refused rather than served healthy -- a mock that quietly
# ignores the outage a cell ordered records the SUCCESS path under that cell's name, identically on
# the golden and the candidate, so the cell proves the opposite of what it claims.
VERBS = frozenset({"down", "slow", "429", "5xx", "401", "cut", "stream-error", "citation", "ws-error", "ws-close"})


class UnresolvableControl(Exception):
    """A well-formed control that names neither a model nor a verb this mock implements."""


def j(obj) -> bytes:
    # Canonical, key-sorted, no whitespace variance -> byte-stable.
    return json.dumps(obj, separators=(",", ":"), sort_keys=True).encode()


# Every member below that the published spec marks `required` but Anthropic defines as nullable is
# set to a fixed `None` (never omitted) so the body is both spec-complete and byte-stable; only the
# two token counts carry a real, deterministic value.
def anthropic_usage():
    return {"input_tokens": IN_TOK, "output_tokens": OUT_TOK, "cache_creation": None,
            "cache_creation_input_tokens": None, "cache_read_input_tokens": None,
            "inference_geo": None, "output_tokens_details": None, "server_tool_use": None,
            "service_tier": None}


def anthropic_delta_usage():
    return {"output_tokens": OUT_TOK, "input_tokens": IN_TOK, "cache_creation_input_tokens": None,
            "cache_read_input_tokens": None, "output_tokens_details": None, "server_tool_use": None}


def anthropic(model, marker):
    return j({"id": "msg_oracle", "type": "message", "role": "assistant", "model": model,
              "content": [{"type": "text", "text": marker, "citations": None}], "stop_reason": "end_turn",
              "stop_sequence": None, "stop_details": None, "container": None,
              "usage": anthropic_usage()})


def openai_chat(model, marker):
    return j({"id": "chatcmpl-oracle", "object": "chat.completion", "created": 0, "model": model,
              "choices": [{"index": 0, "message": {"role": "assistant", "content": marker, "refusal": None},
                           "finish_reason": "stop", "logprobs": None}],
              "usage": {"prompt_tokens": IN_TOK, "completion_tokens": OUT_TOK,
                        "total_tokens": IN_TOK + OUT_TOK}})


def openai_responses(model, marker):
    # Every member the published `Response` schema marks `required` (across its allOf merge) is
    # present here: the nullable ones fixed to `None`, the rest a fixed, deterministic value.
    return j({"id": "resp_oracle", "object": "response", "status": "completed", "model": model,
              "created_at": 0, "error": None, "incomplete_details": None, "instructions": None,
              "metadata": None, "parallel_tool_calls": True, "temperature": None, "top_p": None,
              "tool_choice": "auto", "tools": [],
              "output": [{"type": "message", "id": "msg_oracle", "role": "assistant", "status": "completed",
                          "content": [{"type": "output_text", "text": marker, "annotations": [], "logprobs": []}]}],
              "usage": {"input_tokens": IN_TOK, "output_tokens": OUT_TOK, "total_tokens": IN_TOK + OUT_TOK,
                        "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                        "output_tokens_details": {"reasoning_tokens": 0}}})


def openai_responses_citation(model, marker):
    """The same answer, ANNOTATED — the one shape the bare-text fixture cannot carry.

    The annotation is written in the FLAT spelling the published `UrlCitationBody` declares: `url`,
    `title`, `start_index` and `end_index` sit directly on the annotation object, NOT nested under a
    `url_citation` member (that nesting is Chat Completions' spelling of the same thing). Writing the
    published shape is the whole point: a reader that knows only the nested one drops the citation,
    and drops it even on a same-dialect hop, where it is re-reading bytes of its own making.
    """
    body = json.loads(openai_responses(model, marker).decode())
    body["output"][0]["content"][0]["annotations"] = [{
        "type": "url_citation",
        "url": "https://example.invalid/spec",
        "title": "the published spec",
        "start_index": 0,
        "end_index": len(marker),
    }]
    return j(body)


def gemini(model, marker):
    return j({"candidates": [{"content": {"role": "model", "parts": [{"text": marker}]},
                              "finishReason": "STOP", "index": 0}],
              "usageMetadata": {"promptTokenCount": IN_TOK, "candidatesTokenCount": OUT_TOK,
                                "totalTokenCount": IN_TOK + OUT_TOK},
              "modelVersion": model})


def bedrock(model, marker):
    # `metrics.latencyMs` is a required member of ConverseResponse; fixed 0 keeps the body
    # deterministic (there is no real clock in this mock).
    return j({"output": {"message": {"role": "assistant", "content": [{"text": marker}]}},
              "stopReason": "end_turn",
              "usage": {"inputTokens": IN_TOK, "outputTokens": OUT_TOK, "totalTokens": IN_TOK + OUT_TOK},
              "metrics": {"latencyMs": 0}})


def cohere(model, marker):
    return j({"id": "cohere-oracle", "finish_reason": "COMPLETE",
              "message": {"role": "assistant", "content": [{"type": "text", "text": marker}]},
              "usage": {"billed_units": {"input_tokens": IN_TOK, "output_tokens": OUT_TOK},
                        "tokens": {"input_tokens": IN_TOK, "output_tokens": OUT_TOK}}})


# ── streamed variants: a fixed event sequence per dialect (one text delta + a terminal usage) ─────
def sse(events):
    return b"".join(f"data: {json.dumps(e, separators=(',', ':'), sort_keys=True)}\n\n".encode() for e in events)


def openai_chat_stream(model, marker):
    base = {"id": "chatcmpl-oracle", "object": "chat.completion.chunk", "created": 0, "model": model}
    return sse([
        {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": marker}, "finish_reason": None}]},
        {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": IN_TOK, "completion_tokens": OUT_TOK, "total_tokens": IN_TOK + OUT_TOK}},
    ]) + b"data: [DONE]\n\n"


def anthropic_stream(model, marker):
    def ev(t, body):
        return f"event: {t}\ndata: {json.dumps(body, separators=(',', ':'), sort_keys=True)}\n\n".encode()
    return b"".join([
        ev("message_start", {"type": "message_start", "message": {
            "id": "msg_oracle", "type": "message", "role": "assistant", "model": model, "content": [],
            "stop_reason": None, "stop_sequence": None, "stop_details": None, "container": None,
            "usage": {**anthropic_usage(), "output_tokens": 0}}}),
        ev("content_block_start", {"type": "content_block_start", "index": 0,
                                    "content_block": {"type": "text", "text": "", "citations": None}}),
        ev("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": marker}}),
        ev("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ev("message_delta", {"type": "message_delta",
                             "delta": {"stop_reason": "end_turn", "stop_sequence": None,
                                       "stop_details": None, "container": None},
                             "usage": anthropic_delta_usage()}),
        ev("message_stop", {"type": "message_stop"}),
    ])


def gemini_stream(model, marker):
    return sse([json.loads(gemini(model, marker))])


def cohere_stream(model, marker):
    return sse([
        {"type": "message-start", "id": "cohere-oracle", "delta": {"message": {"role": "assistant", "content": []}}},
        {"type": "content-delta", "index": 0, "delta": {"message": {"content": {"type": "text", "text": marker}}}},
        {"type": "message-end", "delta": {"finish_reason": "COMPLETE",
                                          "usage": {"tokens": {"input_tokens": IN_TOK, "output_tokens": OUT_TOK}}}},
    ])


# ── AWS event-stream (application/vnd.amazon.eventstream) framing for Bedrock ConverseStream ──────
# Mirrors crates/busbar-substrate/src/eventstream.rs `encode_frame` byte-for-byte: a real CRC32 (a
# native AWS SDK decoder validates both checksums), so the frames this mock emits are indistinguishable
# on the wire from a genuine Bedrock ConverseStream response.
# Imported here rather than at the top because this block is a self-contained transcription of one
# Rust file and is read against it; the two names it needs travel with it.
import struct  # noqa: E402
import zlib  # noqa: E402


def _eventstream_header(name, value):
    v = value.encode("utf-8")
    return bytes([len(name)]) + name.encode("ascii") + b"\x07" + struct.pack(">H", len(v)) + v


def _eventstream_frame(event_type, payload):
    headers = (_eventstream_header(":event-type", event_type)
               + _eventstream_header(":content-type", "application/json")
               + _eventstream_header(":message-type", "event"))
    headers_len = len(headers)
    total_len = 12 + headers_len + len(payload) + 4
    prelude = struct.pack(">II", total_len, headers_len)
    prelude_crc = zlib.crc32(prelude) & 0xFFFFFFFF
    frame = prelude + struct.pack(">I", prelude_crc) + headers + payload
    message_crc = zlib.crc32(frame) & 0xFFFFFFFF
    return frame + struct.pack(">I", message_crc)


def bedrock_stream(model, marker):
    # The event sequence and payload shapes mirror crates/busbar-llm/src/bedrock/writer.rs
    # `write_response_event` exactly: no `contentBlockStart` for a plain text block (a real Bedrock
    # ConverseStream never sends one — the text block is implied by the first `contentBlockDelta`),
    # `messageStop` carries only `stopReason`, and token usage plus the required `metrics.latencyMs`
    # (`ConverseStreamMetadataEvent` — fixed 0, there is no real clock in this mock) trail in the
    # separate `metadata` frame.
    events = [
        ("messageStart", {"role": "assistant"}),
        ("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": marker}}),
        ("contentBlockStop", {"contentBlockIndex": 0}),
        ("messageStop", {"stopReason": "end_turn"}),
        ("metadata", {"usage": {"inputTokens": IN_TOK, "outputTokens": OUT_TOK,
                                 "totalTokens": IN_TOK + OUT_TOK},
                      "metrics": {"latencyMs": 0}}),
    ]
    return b"".join(_eventstream_frame(et, j(body)) for et, body in events)


# ── the mid-stream failure: N good events, then an IN-BAND error in the dialect's own shape ───────
# The response has already been committed 200 with its first frames, so this is the one upstream
# failure a door cannot answer with a status code. Each dialect's error event below is the shape that
# dialect actually defines for it; the terminal usage/[DONE] frame is deliberately NOT sent, because
# a failed stream does not get one.
ERR_MSG = "oracle: upstream failed mid-stream"


def _sse_error_openai():
    return sse([{"error": {"type": "server_error", "code": None, "param": None, "message": ERR_MSG}}])


def openai_chat_stream_error(model, marker):
    base = {"id": "chatcmpl-oracle", "object": "chat.completion.chunk", "created": 0, "model": model}
    return sse([
        {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": marker}, "finish_reason": None}]},
    ]) + _sse_error_openai()


def anthropic_stream_error(model, marker):
    def ev(t, body):
        return f"event: {t}\ndata: {json.dumps(body, separators=(',', ':'), sort_keys=True)}\n\n".encode()
    return b"".join([
        ev("message_start", {"type": "message_start", "message": {
            "id": "msg_oracle", "type": "message", "role": "assistant", "model": model, "content": [],
            "stop_reason": None, "stop_sequence": None, "stop_details": None, "container": None,
            "usage": {**anthropic_usage(), "output_tokens": 0}}}),
        ev("content_block_start", {"type": "content_block_start", "index": 0,
                                   "content_block": {"type": "text", "text": "", "citations": None}}),
        ev("content_block_delta", {"type": "content_block_delta", "index": 0,
                                   "delta": {"type": "text_delta", "text": marker}}),
        # Anthropic's documented in-band stream failure: an `error` event, no message_stop.
        ev("error", {"type": "error", "error": {"type": "overloaded_error", "message": ERR_MSG}}),
    ])


def gemini_stream_error(model, marker):
    good = json.loads(gemini(model, marker))
    good.pop("usageMetadata", None)
    return sse([good, {"error": {"code": 500, "message": ERR_MSG, "status": "INTERNAL"}}])


def cohere_stream_error(model, marker):
    return sse([
        {"type": "message-start", "id": "cohere-oracle", "delta": {"message": {"role": "assistant", "content": []}}},
        {"type": "content-delta", "index": 0, "delta": {"message": {"content": {"type": "text", "text": marker}}}},
        {"type": "error", "id": "cohere-oracle", "message": ERR_MSG},
    ])


def openai_responses_stream(model, marker):
    """THE RESPONSES DIALECT'S ORDINARY STREAM (0.3.11), in its own `response.*` event vocabulary:
    `response.created`, one `output_text.delta`, then `response.completed` carrying the usage — the
    terminal frame a caller reads the billing numbers off, and therefore the frame whose ABSENCE is
    the whole point of a mid-stream failure cell.

    It exists because `cut` had nothing to cut. `/v1/responses` answered BUFFERED whatever `stream`
    said, so a `cut` on this door sliced a JSON object in half (`_send` splits on `\n\n` and falls
    back to half the bytes when there is no frame boundary) — a shape no upstream produces, and one
    that cannot record what a door does when a real stream dies after its first frame. Wired ONLY to
    the fault verbs, deliberately: see do_POST."""
    return sse([
        {"type": "response.created", "sequence_number": 0,
         "response": {"id": "resp_oracle", "object": "response", "status": "in_progress", "model": model}},
        {"type": "response.output_text.delta", "sequence_number": 1, "item_id": "msg_oracle",
         "output_index": 0, "content_index": 0, "delta": marker},
        {"type": "response.completed", "sequence_number": 2,
         "response": {"id": "resp_oracle", "object": "response", "status": "completed", "model": model,
                      "output": [{"type": "message", "id": "msg_oracle", "role": "assistant",
                                  "status": "completed",
                                  "content": [{"type": "output_text", "text": marker,
                                               "annotations": [], "logprobs": []}]}],
                      "usage": {"input_tokens": IN_TOK, "output_tokens": OUT_TOK,
                                "total_tokens": IN_TOK + OUT_TOK}}},
    ])


def responses_stream_error(model, marker):
    return sse([
        {"type": "response.created", "sequence_number": 0,
         "response": {"id": "resp_oracle", "object": "response", "status": "in_progress", "model": model}},
        {"type": "response.output_text.delta", "sequence_number": 1, "item_id": "msg_oracle",
         "output_index": 0, "content_index": 0, "delta": marker},
        {"type": "error", "sequence_number": 2, "code": "server_error", "param": None, "message": ERR_MSG},
    ])


def bedrock_stream_error(model, marker):
    # A real ConverseStream reports a mid-stream failure as an EXCEPTION frame: :message-type is
    # `exception`, not `event`, and :exception-type names the modelled error.
    def exception_frame(exc_type, payload):
        headers = (_eventstream_header(":exception-type", exc_type)
                   + _eventstream_header(":content-type", "application/json")
                   + _eventstream_header(":message-type", "exception"))
        headers_len = len(headers)
        total_len = 12 + headers_len + len(payload) + 4
        prelude = struct.pack(">II", total_len, headers_len)
        frame = prelude + struct.pack(">I", zlib.crc32(prelude) & 0xFFFFFFFF) + headers + payload
        return frame + struct.pack(">I", zlib.crc32(frame) & 0xFFFFFFFF)

    good = [
        ("messageStart", {"role": "assistant"}),
        ("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": marker}}),
    ]
    return (b"".join(_eventstream_frame(et, j(body)) for et, body in good)
            + exception_frame("internalServerException", j({"message": ERR_MSG})))


# ── WebSocket upstreams: a DUPLEX session, scripted per dialect, driven by the same control file ──
# The streams plane's served sessions (openai-realtime, gemini-live, the browser sideband leg) are
# WebSocket sessions to an upstream, and until 0.3.12 this mock had zero `Upgrade` handling: the
# whole family was unrecordable, and the golden's zero `^voice` rows could only say so. A session is
# not a request/response pair, so the mock cannot be a pure function of ONE request; it is a pure
# function of the CLIENT'S FRAME SEQUENCE instead — fixed ids, fixed usage (the same 11 in / 7 out),
# the same marker text, event ids counted from 1 per session, no clocks — so a recorder that sends
# the same frames gets the same frames back, byte for byte, on every run.
#
# THE DIALECT IS A TABLE, NEVER A BRANCH. Every wire shape this mock knows is a row in WS_DIALECTS:
# how a path selects it, which key names the event, how a server frame is stamped, what is sent on
# open, what each client event is answered with, and how the dialect says "error". The session loop
# below reads the row and knows nothing about OpenAI or Google; adding a dialect is adding a row.
#
# THE VERBS ARE THE EXISTING ONES, PLUS TWO. A handshake refusal is the ordinary `down`/`401`/`5xx`
# (the upgrade is a GET like any other and gets that status). `cut` kills the socket after the open
# frames with no close frame at all. And the dispute case — the upstream that dies AFTER the door has
# accepted the session and started billing — needs the upstream to say so IN BAND, which no status
# code can: `ws-error` answers the first client event, then sends the dialect's own error shape and
# closes 1011; `ws-close` closes 1011 with no error frame first. Both chosen through the control file
# out of band, so busbar's own frames stay byte-identical to the healthy session they are compared to.
#
# Egress: the handshake is captured like any other upstream request (method GET, the upgrade
# headers, an empty body), and when the session ends the record gains `ws`: every frame busbar SENT
# in order (the money-shaped half: the prompt, the audio, the session config), the dialect that
# served it, and how the session closed — under the same `response` stamp every other egress record
# carries, so record.sh's egress_settle sees a settled record.
import hashlib  # noqa: E402
import socket  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wsframe  # noqa: E402

WS_CLOSE_MSG = "oracle: upstream closed mid-session"


def _oai_session(model):
    return {"id": "sess_oracle", "object": "realtime.session", "model": model, "modalities": ["text"],
            "instructions": "", "voice": "alloy", "input_audio_format": "pcm16",
            "output_audio_format": "pcm16", "turn_detection": None, "tools": [], "tool_choice": "auto",
            "temperature": 0.8, "max_response_output_tokens": "inf"}


def _oai_response(model, marker, status):
    out = [] if status == "in_progress" else [
        {"id": "item_oracle", "object": "realtime.item", "type": "message", "status": "completed",
         "role": "assistant", "content": [{"type": "text", "text": marker}]}]
    r = {"id": "resp_oracle", "object": "realtime.response", "status": status, "output": out}
    if status == "completed":
        r["usage"] = {"total_tokens": IN_TOK + OUT_TOK, "input_tokens": IN_TOK, "output_tokens": OUT_TOK,
                      "input_token_details": {"cached_tokens": 0, "text_tokens": IN_TOK, "audio_tokens": 0},
                      "output_token_details": {"text_tokens": OUT_TOK, "audio_tokens": 0}}
    return r


def _oai_session_update(ev, model, marker):
    s = _oai_session(model)
    if isinstance(ev.get("session"), dict):
        s.update(ev["session"])
    return [{"type": "session.updated", "session": s}]


def _oai_response_create(ev, model, marker):
    return [
        {"type": "response.created", "response": _oai_response(model, marker, "in_progress")},
        {"type": "response.output_item.added", "response_id": "resp_oracle", "output_index": 0,
         "item": {"id": "item_oracle", "object": "realtime.item", "type": "message", "status": "in_progress",
                  "role": "assistant", "content": []}},
        {"type": "response.text.delta", "response_id": "resp_oracle", "item_id": "item_oracle",
         "output_index": 0, "content_index": 0, "delta": marker},
        {"type": "response.text.done", "response_id": "resp_oracle", "item_id": "item_oracle",
         "output_index": 0, "content_index": 0, "text": marker},
        {"type": "response.output_item.done", "response_id": "resp_oracle", "output_index": 0,
         "item": {"id": "item_oracle", "object": "realtime.item", "type": "message", "status": "completed",
                  "role": "assistant", "content": [{"type": "text", "text": marker}]}},
        {"type": "response.done", "response": _oai_response(model, marker, "completed")},
    ]


def _oai_error(code, message):
    return {"type": "error", "error": {"type": "invalid_request_error" if code else "server_error",
                                       "code": code, "message": message, "param": None, "event_id": None}}


def _gem_content(ev, model, marker):
    return [
        {"serverContent": {"modelTurn": {"role": "model", "parts": [{"text": marker}]}}},
        {"serverContent": {"turnComplete": True},
         "usageMetadata": {"promptTokenCount": IN_TOK, "responseTokenCount": OUT_TOK,
                           "totalTokenCount": IN_TOK + OUT_TOK}},
    ]


WS_DIALECTS = {
    # OpenAI Realtime: wss://api.openai.com/v1/realtime?model=… ; every server event carries a
    # fresh `event_id`, the first is `session.created`, an unknown client event is answered with an
    # in-band `error` event and the session stays open.
    "openai-realtime": {
        "match": lambda p: p == "/v1/realtime",
        "event_key": "type",
        "stamp": lambda ev, n: {"event_id": f"event_oracle_{n:04d}", **ev},
        "on_open": lambda model, marker: [{"type": "session.created", "session": _oai_session(model)}],
        "on_event": {
            "session.update": _oai_session_update,
            "response.create": _oai_response_create,
            "input_audio_buffer.append": lambda ev, model, marker: [],
            "input_audio_buffer.commit": lambda ev, model, marker: [
                {"type": "input_audio_buffer.committed", "previous_item_id": None, "item_id": "item_oracle_in"}],
            "conversation.item.create": lambda ev, model, marker: [
                {"type": "conversation.item.created", "previous_item_id": None,
                 "item": {**(ev.get("item") or {}), "id": "item_oracle_in", "object": "realtime.item"}}],
        },
        "unknown": lambda ev, model, marker: [
            _oai_error("unknown_parameter", f"oracle: unknown client event {ev.get('type')!r}")],
        # the dispute arm: how THIS dialect says it failed mid-session — an `error` event, then 1011
        "error": lambda model, marker: [_oai_error(None, ERR_MSG)],
    },
    # Gemini Live: wss://…/ws/google.ai.generativelanguage.<ver>.GenerativeService.BidiGenerateContent
    # Nothing is sent on open; the client's `setup` is answered with `setupComplete`; there are no
    # event ids; an error is a CLOSE with a reason, never an in-band frame.
    "gemini-live": {
        "match": lambda p: p.startswith("/ws/google.ai.generativelanguage.") and p.endswith(".GenerativeService.BidiGenerateContent"),
        "event_key": None,  # the event is the one top-level key
        "stamp": lambda ev, n: ev,
        "on_open": lambda model, marker: [],
        "on_event": {
            "setup": lambda ev, model, marker: [{"setupComplete": {}}],
            "clientContent": _gem_content,
            "realtimeInput": lambda ev, model, marker: [],
        },
        "unknown": lambda ev, model, marker: [("close", 1008, f"oracle: unknown client message {sorted(ev)!r}")],
        "error": lambda model, marker: [],
    },
    # A dialect-free echo: every text/binary frame comes straight back. For the recorder's own
    # tests, and for a cell whose subject is the door's framing rather than any provider's grammar.
    "echo": {
        "match": lambda p: p == "/ws/echo",
        "event_key": None,
        "stamp": lambda ev, n: ev,
        "on_open": lambda model, marker: [],
        "on_event": {},
        "unknown": lambda ev, model, marker: [ev],
        "error": lambda model, marker: [{"error": ERR_MSG}],
    },
}


def ws_dialect_for(path):
    for name, d in WS_DIALECTS.items():
        if d["match"](path):
            return name, d
    return None, None


def ws_event_name(d, ev):
    if not isinstance(ev, dict):
        return None
    k = d["event_key"]
    if k:
        return ev.get(k)
    return next(iter(ev), None) if len(ev) == 1 else None


def _b64(b):
    import base64
    return base64.b64encode(b).decode()


class H(BaseHTTPRequestHandler):
    server_version = "oracle-upstream/1"
    sys_version = ""

    def _send(self, status, body, ctype="application/json"):
        if getattr(self, "cut", False) and status == 200:
            # `cut`: headers + the first frame (or half the body), then the socket dies — the
            # "upstream died mid-response" arm of the refund table (PB-27)
            first = body.split(b"\n\n", 1)[0] + b"\n\n" if b"\n\n" in body else body[: max(1, len(body) // 2)]
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(first); self.wfile.flush()
            self._egress_response(status)
            try:
                self.connection.shutdown(1)
            except OSError:
                pass
            self.close_connection = True
            return
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self._egress_response(status)

    def _capture_egress(self, method, path, raw_body):
        # See the module docstring for the on-disk contract. Best-effort: a capture failure must
        # never take down the mock or change the response busbar gets.
        cap_dir = os.environ.get("ORACLE_MOCK_CAPTURE_DIR")
        if not cap_dir:
            return
        try:
            os.makedirs(cap_dir, exist_ok=True)
            headers = {k.lower(): v for k, v in self.headers.items()}
            try:
                body = raw_body.decode("utf-8")
            except UnicodeDecodeError:
                import base64
                body = "base64:" + base64.b64encode(raw_body).decode()
            record = {"path": path, "method": method, "headers": headers, "body": body}
            name = f"{time.time_ns()}-{os.getpid()}-{next(_capture_seq)}.json"
            self._eg_record = record
            self._eg_path = os.path.join(cap_dir, name)
            self._eg_t0 = time.monotonic()
            self._eg_write()
        except OSError:
            pass

    # ── THE ATTEMPT'S OWN ANSWER IS PART OF THE ATTEMPT ──────────────────────────────────────────
    # `effects.egress` recorded only what busbar SENT. For a failover cell that is half the evidence:
    # `route.failover|fo|primary-429` is named for a 429 carrying a Retry-After, and `|primary-slow`
    # for an attempt that ran past busbar's cap — and NEITHER was observable in the golden. The cell
    # could keep its name while the mock served a plain 500, or served the 429 with no Retry-After at
    # all, or answered instantly where the point is that it did not, and every recorded byte would be
    # identical. What a cell NAMES has to be in what it RECORDS, or the name is the only evidence.
    #
    # So each entry now also carries the upstream's own answer: the status, the Retry-After it did or
    # did not send, and how long the attempt took. The duration is captured RAW here (`elapsed_ms`)
    # and BUCKETED by normalize.py's `egress.elapsed` rule, not here — a recorder that buckets has
    # thrown the measurement away before renormalize.sh can ever re-read it, and the boundary between
    # "what happened" and "what is stable enough to compare" belongs on the normalizer's side of the
    # line, where it is one named, reviewable rule instead of a constant buried in the mock.
    def _egress_response(self, status, headers=None):
        if not getattr(self, "_eg_path", None) or getattr(self, "_eg_done", False):
            return
        try:
            hdrs = {k.lower(): v for k, v in (headers or {}).items()}
            self._eg_record["response"] = {
                "status": status,
                # Present-or-absent is the contract PB-4 rides on, so the key is ALWAYS written:
                # null records "the upstream sent none", which is a different fact from "this
                # recorder did not look" and must not be spelled the same way.
                "retry_after": hdrs.get("retry-after"),
                "elapsed_ms": int((time.monotonic() - self._eg_t0) * 1000),
            }
            self._eg_done = True
            self._eg_write()
        except (OSError, AttributeError):
            pass

    def _eg_write(self):
        # Atomic: no half-written file is ever "newest". Rewritten in place when the response lands,
        # so a request that never got one keeps its request-only record rather than vanishing.
        # The scratch name is DOT-PREFIXED: record.sh discovers these with a plain `ls`, which hides
        # dotfiles, so a half-written file can never be collected as an egress record.
        d, name = os.path.split(self._eg_path)
        tmp_path = os.path.join(d, f".{name}.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self._eg_record, f, separators=(",", ":"), sort_keys=True)
        os.replace(tmp_path, self._eg_path)

    def _resolve_control(self, model):
        """The verb for this request: the X-Oracle-Upstream header, or the control FILE the recorder
        writes per cell (see do_POST for why an empty read holds the last verb and a well-formed
        control this mock cannot act on is an error). Returns (verb, harness_error_or_None).

        THIS DUPLICATES do_POST'S CONTROL BLOCK, AND THAT IS DELIBERATE. The two are semantically
        identical and the obvious move is to fold do_POST into this. It is not made: do_POST is the
        path every cell in the existing golden was recorded through, and the oracle's whole value is
        that a cell recorded a year ago is judged today by the same code that recorded it. A
        refactor here could only be argued to be safe -- it could not be PROVEN so against a corpus
        recorded before it -- and a difference it introduced would show up as a difference in the
        PRODUCT. So the new surface carries its own copy and this comment; if the control semantics
        ever change, both change, and the tests below hold them equal."""
        ctl = (self.headers.get("X-Oracle-Upstream") or "").strip().lower()
        ctl_file = self.server.control_file  # type: ignore[attr-defined]
        if not ctl_file:
            return ctl, None
        if not os.path.exists(ctl_file):
            with self.server.control_lock:  # type: ignore[attr-defined]
                self.server.last_raw = None  # type: ignore[attr-defined]
            return "", None
        raw_ctl = self._read_control_file()
        if raw_ctl:
            try:
                ctl = self._verb_for_model(raw_ctl, model)
            except UnresolvableControl as e:
                sys.stderr.write(f"[control] UNRESOLVABLE control for model {model!r}: {e}\n")
                sys.stderr.flush()
                return "", str(e)
            except ValueError:
                raw_ctl = None
            else:
                with self.server.control_lock:  # type: ignore[attr-defined]
                    self.server.last_raw = raw_ctl  # type: ignore[attr-defined]
                return ctl, None
        with self.server.control_lock:  # type: ignore[attr-defined]
            last_raw = self.server.last_raw  # type: ignore[attr-defined]
        try:
            ctl = self._verb_for_model(last_raw, model) if last_raw else ""
        except (ValueError, UnresolvableControl):
            ctl = ""
        sys.stderr.write(
            f"[control] empty/unreadable/malformed read on {ctl_file} for model {model!r}; "
            f"holding last verb (raw={last_raw!r}) -> {ctl!r}\n")
        sys.stderr.flush()
        return ctl, None

    def _serve_ws(self, p, name, d):
        """One WebSocket session on this connection, from the upgrade to the close, scripted by the
        dialect row `d`. Records the session into this request's egress record."""
        self._capture_egress("GET", self.path, b"")
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        model = next((unquote(v) for k, _, v in (kv.partition("=") for kv in qs.split("&")) if k == "model"), None) \
            or "oracle-model"
        ctl, harness_err = self._resolve_control(model)
        if harness_err:
            return self._send(599, j({"error": {"type": "oracle_harness_error", "message": f"oracle mock: {harness_err}"}}))
        # a handshake refusal is the ordinary status the verb names — the upgrade is a GET like any
        if ctl == "down":
            return self._send(503, j({"error": {"type": "upstream_unavailable", "message": "oracle: upstream down"}}))
        if ctl == "5xx":
            return self._send(500, j({"error": {"type": "server_error", "message": "oracle: upstream exploded"}}))
        if ctl == "401":
            return self._send(401, j({"error": {"type": "authentication_error", "message": "oracle: upstream rejected the credential"}}))
        key = self.headers.get("Sec-WebSocket-Key")
        if not key or (self.headers.get("Sec-WebSocket-Version") or "").strip() != "13":
            return self._send(400, j({"error": {"type": "invalid_request_error",
                                                "message": "oracle mock: a websocket upgrade needs Sec-WebSocket-Key and Sec-WebSocket-Version: 13"}}))
        if ctl == "slow":
            time.sleep(float(os.environ.get("ORACLE_MOCK_SLOW_SECS", "8")))
        self.send_response_only(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", wsframe.accept_key(key))
        self.end_headers()
        self.wfile.flush()
        self.close_connection = True
        sock = self.connection
        sock.settimeout(float(os.environ.get("ORACLE_MOCK_WS_IDLE_SECS", "30")))
        marker = self.server.marker  # type: ignore[attr-defined]
        received = []
        seq = itertools.count(1)
        close = {"by": "none", "code": None, "reason": ""}

        def send_ev(ev):
            if isinstance(ev, tuple) and ev[0] == "close":
                return send_close(ev[1], ev[2])
            sock.sendall(wsframe.encode_frame(wsframe.OP_TEXT, j(d["stamp"](ev, next(seq)))))
            return False

        def send_close(code, reason):
            sock.sendall(wsframe.encode_close(code, reason))
            close.update({"by": "server", "code": code, "reason": reason})
            # wait for the client's close echo (or its silence) so the record says which
            try:
                while True:
                    op, payload = wsframe.recv_message(sock)
                    if op == wsframe.OP_CLOSE:
                        c, r = wsframe.decode_close(payload)
                        received.append({"opcode": "close", "code": c, "reason": r})
                        break
                    received.append(_ws_record(op, payload))
            except (EOFError, OSError, ValueError):
                pass
            return True

        def _ws_record(op, payload):
            if op == wsframe.OP_TEXT:
                return {"opcode": "text", "text": payload.decode("utf-8", "replace")}
            if op == wsframe.OP_BINARY:
                return {"opcode": "binary", "len": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
            return {"opcode": wsframe.OPCODE_NAMES.get(op, str(op)), "base64": _b64(payload)}

        try:
            for ev in d["on_open"](model, marker):
                send_ev(ev)
            if ctl == "cut":
                # the socket dies with no close frame: the "upstream vanished mid-session" arm
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            else:
                answered = 0
                while True:
                    try:
                        op, payload = wsframe.recv_message(sock)
                    except EOFError:
                        close.update({"by": "eof"}); break
                    except socket.timeout:
                        close.update({"by": "idle"})
                        try:
                            sock.sendall(wsframe.encode_close(1001, "oracle: idle"))
                        except OSError:
                            pass
                        break
                    if op == wsframe.OP_CLOSE:
                        c, r = wsframe.decode_close(payload)
                        received.append({"opcode": "close", "code": c, "reason": r})
                        close.update({"by": "client", "code": c, "reason": r})
                        sock.sendall(wsframe.encode_close(c, "") if c is not None else wsframe.encode_close(None))
                        break
                    if op == wsframe.OP_PING:
                        received.append(_ws_record(op, payload))
                        sock.sendall(wsframe.encode_frame(wsframe.OP_PONG, payload)); continue
                    if op == wsframe.OP_PONG:
                        received.append(_ws_record(op, payload)); continue
                    received.append(_ws_record(op, payload))
                    replies = []
                    if op == wsframe.OP_TEXT:
                        try:
                            ev = json.loads(payload.decode("utf-8"))
                        except ValueError:
                            ev = None
                        handler = d["on_event"].get(ws_event_name(d, ev)) if isinstance(ev, dict) else None
                        replies = handler(ev, model, marker) if handler else d["unknown"](ev, model, marker)
                    closed = False
                    for r in replies:
                        if send_ev(r):
                            closed = True; break
                    if closed:
                        break
                    answered += 1
                    if answered == 1 and ctl in ("ws-error", "ws-close"):
                        # THE DISPUTE CASE: the door has accepted the session and served one turn,
                        # and now the upstream fails in band — after the bill has started
                        if ctl == "ws-error":
                            for r in d["error"](model, marker):
                                send_ev(r)
                        send_close(1011, ERR_MSG if ctl == "ws-error" else WS_CLOSE_MSG)
                        break
        except OSError as e:
            close.update({"by": "error", "reason": str(e)})
        finally:
            if getattr(self, "_eg_record", None) is not None:
                self._eg_record["ws"] = {"dialect": name, "received": received, "close": close}
            self._egress_response(101)
            try:
                sock.close()
            except OSError:
                pass

    def do_GET(self):
        # Readiness only (fleet-fixtures wait_for_http probes with GET /). Every dialect is POST —
        # except a WebSocket upgrade, which is a GET that turns into a session (see _serve_ws).
        p = unquote(self.path.split("?", 1)[0])
        if (self.headers.get("Upgrade") or "").strip().lower() == "websocket":
            name, d = ws_dialect_for(p)
            if d is None:
                return self._send(404, j({"error": f"oracle mock: no websocket dialect for path {p}"}))
            return self._serve_ws(p, name, d)
        if p == "/":
            return self._send(200, j({"ok": True, "mock": "oracle-upstream"}))
        if p == "/__control":
            # A diagnostic echo, never a dialect: a writer of the control file (record.sh,
            # cooldown-trip.sh, ...) polls this after an atomic rename to CONFIRM this mock has
            # actually seen the new bytes before it fires the cell's real request — otherwise a
            # writer has no way to know its write landed before the request that depends on it goes
            # out. Deliberately not wired into any dialect path or _capture_egress, so a cell can
            # never record it by accident.
            return self._send(200, j({"raw": self._read_control_file()}))
        return self._send(404, j({"error": f"oracle mock: no GET route {self.path}"}))

    def _read_control_file(self):
        """A single best-effort read of the control file's raw (whitespace-stripped) text, or None
        if there is no file / it could not be read. Used both by /__control and by the outage-verb
        resolution in do_POST below."""
        ctl_file = self.server.control_file  # type: ignore[attr-defined]
        if not ctl_file or not os.path.exists(ctl_file):
            return None
        try:
            with open(ctl_file) as f:
                return f.read().strip()
        except OSError:
            return None

    @staticmethod
    def _verb_for_model(raw_ctl, model):
        """raw_ctl is either a bare verb (applies to every model), JSON {"<model>": "<verb>"}, or the
        FLAG form JSON {"<verb>": true} meaning "this verb, for every model".

        May raise ValueError on malformed JSON, or UnresolvableControl on a well-formed object that
        names neither a model this mock could be asked about nor a verb it implements -- callers
        decide how to treat each.

        The flag form is not a convenience: it is the shape the shipped corpus writes for the two
        controls that are not per-model (`{"stream-error": true}`, `{"citation": true}`). Resolving
        those by MODEL LOOKUP alone missed on every request, and the miss fell through to a healthy
        200 -- so seven cells that order an outage would have recorded the success path, identically
        on both binaries. A control that resolves to nothing is now an ERROR, never silence.
        """
        if raw_ctl.startswith("{"):
            parsed = json.loads(raw_ctl)
            if not isinstance(parsed, dict):
                raise UnresolvableControl(f"control is JSON but not an object: {raw_ctl!r}")
            v = parsed.get(model) or parsed.get("*")
            if v is None:
                # the flag form: exactly one key, and that key is a verb this mock implements
                flags = [k for k, val in parsed.items() if str(k).lower() in VERBS and val is True]
                if len(flags) == 1 and len(parsed) == 1:
                    return flags[0].lower()
                # A control naming only OTHER models is a legitimate "healthy for this one": every
                # per-model control in the corpus is keyed by a `m-…` lane name. Anything else names
                # nothing this mock understands, and answering it healthy is the silent pass above.
                if all(str(k) == "*" or str(k).startswith("m-") for k in parsed):
                    return ""
                raise UnresolvableControl(
                    f"control object names neither a model (m-…/*) nor a verb {sorted(VERBS)}: {raw_ctl!r}")
            return str(v).lower()
        v = raw_ctl.lower()
        if v and v not in VERBS:
            raise UnresolvableControl(f"unknown control verb {raw_ctl!r} (known: {sorted(VERBS)})")
        return v

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        # Capture the egress request as busbar actually sent it, before any routing/outcome logic
        # below can short-circuit — a 503/404/control-file response is still a request that was made.
        self._capture_egress("POST", self.path, raw)
        try:
            req = json.loads(raw) if raw else {}
        except Exception:
            req = {}
        model = req.get("model") or "oracle-model"
        marker = self.server.marker  # type: ignore[attr-defined]

        # Two outage controls: a header (handy for curl-by-hand) and a CONTROL FILE the recorder
        # writes per cell — busbar must never see a control header, so the file is the one the
        # oracle actually uses.
        # busbar percent-encodes the gemini `:generateContent` colon on egress; decode before matching.
        p = unquote(self.path.split("?", 1)[0])
        # the egress model for path-addressed dialects (gemini / bedrock) lives in the path
        path_model = None
        if p.startswith("/v1beta/models/"):
            path_model = p[len("/v1beta/models/"):].split(":", 1)[0]
        elif p.startswith("/model/"):
            path_model = p[len("/model/"):].split("/", 1)[0]
        model = path_model or model

        # Control: a header (handy for curl-by-hand) or the CONTROL FILE the recorder writes per cell.
        # The file is either a bare verb (applies to every model) or JSON {"<model>": "<verb>"}.
        # Verbs: down (503) | 429 (Retry-After: 7) | 5xx (500) | 401 (credential rejected: a hard-down)
        #        | slow (sleep past busbar's attempt cap)
        #        | cut (close the socket after the first streamed event / mid-body)
        #        | stream-error (N good events, then an IN-BAND error event in the dialect's own
        #          error shape — the failure a 200 stream cannot report as a status code)
        ctl = (self.headers.get("X-Oracle-Upstream") or "").strip().lower()
        ctl_file = self.server.control_file  # type: ignore[attr-defined]
        if ctl_file:
            if os.path.exists(ctl_file):
                raw_ctl = self._read_control_file()
                if raw_ctl:
                    try:
                        ctl = self._verb_for_model(raw_ctl, model)
                    except UnresolvableControl as e:
                        # A WELL-FORMED CONTROL THIS MOCK CANNOT ACT ON IS NOT "NO OUTAGE". Serving
                        # the healthy 200 here is the silent pass: the cell that ordered the outage
                        # records the success path, the golden freezes it, and the candidate — asked
                        # for the same unresolvable control — reproduces it byte for byte. Answer with
                        # a status no dialect and no verb of this mock ever produces, so the cell is
                        # unmistakably broken rather than quietly wrong.
                        sys.stderr.write(f"[control] UNRESOLVABLE control for model {model!r}: {e}\n")
                        sys.stderr.flush()
                        return self._send(599, j({"error": {"type": "oracle_harness_error",
                                                            "message": f"oracle mock: {e}"}}))
                    except ValueError:
                        raw_ctl = None  # malformed JSON: treat exactly like an empty/unreadable read
                    else:
                        with self.server.control_lock:  # type: ignore[attr-defined]
                            self.server.last_raw = raw_ctl  # type: ignore[attr-defined]
                if not raw_ctl:
                    # An EMPTY (or unreadable, or malformed-JSON) read means a writer's rename hadn't
                    # landed yet, or a transient OS hiccup -- NOT "no outage". Falling through to
                    # healthy here is exactly the bug this fix closes: a cell that ordered an outage
                    # would silently get served a 200. Hold the last verb this mock successfully read
                    # instead, and say so in mock.log so the condition is visible.
                    with self.server.control_lock:  # type: ignore[attr-defined]
                        last_raw = self.server.last_raw  # type: ignore[attr-defined]
                    try:
                        ctl = self._verb_for_model(last_raw, model) if last_raw else ""
                    except (ValueError, UnresolvableControl):
                        ctl = ""
                    sys.stderr.write(
                        f"[control] empty/unreadable/malformed read on {ctl_file} for model {model!r}; "
                        f"holding last verb (raw={last_raw!r}) -> {ctl!r}\n")
                    sys.stderr.flush()
            else:
                # No file at all is a real, intentional "no outage" (record.sh/cooldown-trip.sh clear
                # it with an atomic unlink between cells) -- not a torn read, so no verb survives it.
                with self.server.control_lock:  # type: ignore[attr-defined]
                    self.server.last_raw = None  # type: ignore[attr-defined]
                ctl = ""
        if ctl == "down":
            return self._send(503, j({"error": {"type": "upstream_unavailable", "message": "oracle: upstream down"}}))
        if ctl == "429":
            self.send_response(429); self.send_header("Content-Type", "application/json"); self.send_header("Retry-After", "7")
            body = j({"error": {"type": "rate_limit_error", "message": "oracle: upstream rate limited"}})
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            # The one response path that does not go through _send, and the ONLY one that carries a
            # Retry-After -- which is the whole point of `route.failover|fo|primary-429`.
            self._egress_response(429, {"Retry-After": "7"})
            return
        if ctl == "5xx":
            return self._send(500, j({"error": {"type": "server_error", "message": "oracle: upstream exploded"}}))
        if ctl == "401":
            return self._send(401, j({"error": {"type": "authentication_error", "message": "oracle: upstream rejected the credential"}}))
        if ctl == "slow":
            import time
            time.sleep(float(os.environ.get("ORACLE_MOCK_SLOW_SECS", "8")))
        # Per-request, not server-global: each accepted connection gets its own handler instance,
        # so storing this on `self` (not `self.server`) keeps concurrent requests from clobbering
        # each other's cut/no-cut decision once the server is threaded.
        self.cut = (ctl == "cut")

        want_stream = bool(req.get("stream"))
        # `stream-error` only ever applies to a request that IS a stream — a buffered request that
        # fails is already covered by `down`/`5xx`, and answering one with half a stream would be a
        # shape no upstream produces.
        stream_error = (ctl == "stream-error")
        # `citation` answers the BUFFERED Responses shape with a sourced annotation instead of bare
        # text. It touches nothing else: every other dialect and every stream builder is byte-
        # identical under it, so a golden recorded without the verb is unaffected by its existence.
        citation = (ctl == "citation")
        if p == "/v1/messages":
            if want_stream and stream_error:
                return self._send(200, anthropic_stream_error(model, marker), "text/event-stream")
            body = anthropic_stream(model, marker) if want_stream else anthropic(model, marker)
            return self._send(200, body, "text/event-stream" if want_stream else "application/json")
        if p == "/v1/chat/completions":
            if want_stream and stream_error:
                return self._send(200, openai_chat_stream_error(model, marker), "text/event-stream")
            body = openai_chat_stream(model, marker) if want_stream else openai_chat(model, marker)
            return self._send(200, body, "text/event-stream" if want_stream else "application/json")
        if p == "/v1/responses":
            if want_stream and stream_error:
                return self._send(200, responses_stream_error(model, marker), "text/event-stream")
            # A `cut` ON THIS DOOR NEEDS A STREAM TO CUT (0.3.11). Through 0.3.10 `/v1/responses`
            # answered BUFFERED whatever `stream` said, so `llm.stream|responses|cut` — the cell whose
            # entire subject is "headers, the first frame, then the socket dies" — recorded half a JSON
            # object instead of a first SSE frame. The dialect could not record the fault it is named
            # for, and the two arms of the same failure (`cut` and `stream-error`) disagreed about what
            # shape the door was even reading.
            #
            # WIRED TO THE FAULT VERBS, NOT TO `stream` ITSELF, AND THAT IS THE WHOLE CARE HERE. Six
            # cells already recorded against this door with `stream: true` in their egress body
            # (`llm|{anthropic,bedrock,cohere,gemini,openai,responses}|responses|request|ok_stream`),
            # every one of them recorded from the PUBLISHED 1.5.5 binary against a buffered upstream.
            # Streaming the healthy path would move all six goldens' bytes, and a golden is re-made
            # only by re-recording the released binary — never by the release that changed the judge.
            # So the healthy answer stays byte-identical and only the fault verbs, which no existing
            # golden on this door uses, gain frames. Widening this to the ordinary path is a separate
            # change with a re-record attached.
            if want_stream and self.cut:
                return self._send(200, openai_responses_stream(model, marker), "text/event-stream")
            if citation:
                return self._send(200, openai_responses_citation(model, marker))
            return self._send(200, openai_responses(model, marker))
        if p.startswith("/v1beta/models/") and ":streamGenerateContent" in p:
            if stream_error:
                return self._send(200, gemini_stream_error(model, marker), "text/event-stream")
            return self._send(200, gemini_stream(model, marker), "text/event-stream")
        if p.startswith("/v1beta/models/") and ":generateContent" in p:
            return self._send(200, gemini(model, marker))
        if p.startswith("/model/") and p.endswith("/converse-stream"):
            if stream_error:
                return self._send(200, bedrock_stream_error(model, marker), "application/vnd.amazon.eventstream")
            return self._send(200, bedrock_stream(model, marker), "application/vnd.amazon.eventstream")
        if p.startswith("/model/") and p.endswith("/converse"):
            return self._send(200, bedrock(model, marker))
        if p == "/v2/chat":
            if want_stream and stream_error:
                return self._send(200, cohere_stream_error(model, marker), "text/event-stream")
            body = cohere_stream(model, marker) if want_stream else cohere(model, marker)
            return self._send(200, body, "text/event-stream" if want_stream else "application/json")
        return self._send(404, j({"error": f"oracle mock: no dialect for path {p}"}))

    def log_message(self, *_a):  # silent: byte-stable stdout for the recorder
        pass


def check_controls(cells_path) -> int:
    """Every `mock_control` in a corpus must resolve to a verb this mock implements, for at least one
    of the models it names. A control that resolves to nothing is served as a healthy 200, so the
    cell that ordered an outage records the success path — on both binaries, agreeing."""
    corpus = json.load(open(cells_path, encoding="utf-8"))
    bad = []
    for cell in corpus.get("cells", []):
        mc = cell.get("mock_control")
        if not mc:
            continue
        raw = mc if isinstance(mc, str) else json.dumps(mc, separators=(",", ":"), sort_keys=True)
        # the models this control could ever be asked about: the ones it names, plus a stand-in for
        # "some other lane" so a per-model control is not judged by a lane it deliberately omits
        models = [k for k in (mc if isinstance(mc, dict) else {}) if str(k).startswith("m-")] or ["m-openai-chat"]
        resolved = ""
        err = ""
        for m in models:
            try:
                v = H._verb_for_model(raw, m)
            except (ValueError, UnresolvableControl) as e:
                err = str(e)
                continue
            if v:
                resolved = v
                break
        if not resolved:
            bad.append(f"{cell['id']}: mock_control {raw} -> {err or 'no verb'}")
    for line in bad:
        print(line)
    return 1 if bad else 0


def selftest() -> int:
    fails = 0

    def say(ok, what):
        nonlocal fails
        print(f"{'PASS' if ok else 'FAIL'}  {what}")
        if not ok:
            fails += 1

    v = H._verb_for_model
    say(v("down", "m-openai-chat") == "down", "a bare verb applies to every model")
    say(v('{"m-openai-chat":"cut"}', "m-openai-chat") == "cut", "a per-model control resolves for the model it names")
    say(v('{"m-openai-chat":"cut"}', "m-anthropic") == "", "…and is healthy for a model it does not name")
    say(v('{"*":"down"}', "m-anything") == "down", "the '*' fallback applies to every model")
    # THE BUG. `{"stream-error": true}` / `{"citation": true}` is the shape the corpus ships for the
    # two controls that are not per-model. Resolved by model lookup alone they miss on every request
    # and fell through to a healthy 200 — the cell records the success path it exists to refute.
    say(v('{"stream-error": true}', "m-openai-chat") == "stream-error",
        "the flag form {\"<verb>\": true} resolves to that verb (it used to resolve to nothing)")
    say(v('{"citation": true}', "m-responses") == "citation", "…for every verb written that way")
    # A control that names neither a model nor a verb is an ERROR, never silence.
    try:
        v('{"typo-verb": true}', "m-openai-chat")
        say(False, "a control naming no model and no verb was accepted as 'healthy'")
    except UnresolvableControl:
        say(True, "a control naming neither a model nor a verb is refused, not served healthy")
    try:
        v("dowm", "m-openai-chat")
        say(False, "a misspelt bare verb was accepted as 'healthy'")
    except UnresolvableControl:
        say(True, "a misspelt bare verb is refused, not served healthy")
    # …and every verb the docstring/dispatch names is in the vocabulary the refusal is judged against
    say(all(x in VERBS for x in ("down", "slow", "429", "5xx", "401", "cut", "stream-error", "citation")),
        "the verb vocabulary covers every verb do_POST dispatches on")

    # ── A `cut` ON /v1/responses CUTS A STREAM, NOT A JSON OBJECT (0.3.11) ────────────────────────
    # Driven through a REAL server on a real socket, because the thing under test is what a caller
    # receives: `_send`'s cut arm splits the body on the SSE frame boundary and resets, so "the door
    # gets a first frame" is a claim about the bytes on the wire, not about a builder's return value.
    import http.client
    from http.server import ThreadingHTTPServer as _THS

    srv = _THS(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    srv.marker = MARKER
    srv.control_file = ""
    srv.last_raw = None
    srv.control_lock = threading.Lock()
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    def post(path, body, verb=""):
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
        hdrs = {"Content-Type": "application/json"}
        if verb:
            hdrs["X-Oracle-Upstream"] = verb
        c.request("POST", path, json.dumps(body), hdrs)
        r = c.getresponse()
        try:
            got = r.read()
        except http.client.IncompleteRead as e:
            # THE FAULT ITSELF. `_send`'s cut arm announces the WHOLE body's Content-Length and then
            # resets after the first frame, so a conforming client raises here — which is the point:
            # a caller that trusted the length got less than it was promised, with no error frame
            # explaining why. The partial bytes are what the door actually received.
            got = e.partial
        out = (r.status, r.getheader("Content-Type"), got)
        c.close()
        return out

    try:
        st, ctype, got = post("/v1/responses", {"model": "m-responses", "stream": True}, "cut")
        say(st == 200 and ctype == "text/event-stream",
            "a cut /v1/responses stream answers 200 text/event-stream (it used to answer application/json)")
        # what the caller got is a WHOLE first frame — a parseable `response.created` — and nothing after it
        frames = [x for x in got.split(b"\n\n") if x.strip()]
        first_ok = False
        if len(frames) == 1 and frames[0].startswith(b"data: "):
            try:
                first_ok = json.loads(frames[0][len(b"data: "):])["type"] == "response.created"
            except (ValueError, KeyError, TypeError):
                first_ok = False
        say(first_ok, "…and delivers exactly ONE complete SSE frame (response.created) before the reset")
        say(b"response.completed" not in got,
            "…and never the terminal response.completed frame the usage is read off")

        # THE SIX ALREADY-RECORDED GOLDENS DO NOT MOVE. The healthy `stream: true` answer on this door
        # is byte-identical to what 0.3.10 served: the buffered Response object.
        st, ctype, got = post("/v1/responses", {"model": "m-responses", "stream": True})
        say(st == 200 and ctype == "application/json" and got == openai_responses("m-responses", MARKER),
            "…while the HEALTHY stream:true answer is still the buffered object, byte for byte")

        # …and the in-band failure arm is untouched.
        st, ctype, got = post("/v1/responses", {"model": "m-responses", "stream": True}, "stream-error")
        say(st == 200 and ctype == "text/event-stream" and got == responses_stream_error("m-responses", MARKER),
            "…and stream-error still answers its own in-band error stream, unchanged")
    finally:
        srv.shutdown()
        srv.server_close()
    print(f"\nmock-upstream selftest: {'GREEN' if not fails else f'RED ({fails} failing)'}")
    return 1 if fails else 0


def main():
    if sys.argv[1:2] == ["--selftest"]:
        sys.exit(selftest())
    if sys.argv[1:2] == ["--check-controls"]:
        sys.exit(check_controls(sys.argv[2]))
    port = int(sys.argv[1])
    marker = sys.argv[2] if len(sys.argv) > 2 else MARKER
    control_file = sys.argv[3] if len(sys.argv) > 3 else ""
    # Threaded so concurrency/queue/breaker cells can hit this mock with several requests in
    # flight at once — a single-threaded HTTPServer would serialize them and make a "concurrent"
    # cell impossible to record honestly. daemon_threads so a stray "slow" sleeper doesn't block
    # process exit.
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    srv.marker = marker  # type: ignore[attr-defined]
    srv.control_file = control_file  # type: ignore[attr-defined]
    # Last successfully-read raw control-file text, shared across the threaded handlers so an empty
    # or unreadable read on one request can fall back to the last verb a DIFFERENT request actually
    # read, instead of "no outage". Guarded by a lock: ThreadingHTTPServer serves overlapping
    # requests on separate threads.
    srv.last_raw = None  # type: ignore[attr-defined]
    srv.control_lock = threading.Lock()  # type: ignore[attr-defined]
    srv.serve_forever()


if __name__ == "__main__":
    main()
