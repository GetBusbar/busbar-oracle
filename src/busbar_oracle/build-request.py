#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Busbar Inc and contributors
"""Build the CLIENT-SIDE ingress request for one LLM oracle cell.

The request shape is fixed by each dialect's wire protocol (what a real client sends busbar), not by
our config — so this is independent of the oracle's busbar configuration. busbar's route table fixes
the INGRESS dialect by path; the `model` (`m-<egress_dialect>`) selects the lane and therefore the
EGRESS dialect. ingress != egress is a cross-protocol cell.

Reads a cell (JSON on stdin or --cell '<json>') and prints:
  {"method": "POST", "path": "...", "headers": {...}, "body": "<str>", "auth": "bearer"|"sigv4",
   "stream": true|false, "note": "..."}

`stream` is the CELL's declaration of its own request shape (see declares_stream), never a guess made
from the outcome's name, and it is emitted so no consumer has to re-derive it from the body — which
for gemini and bedrock is not in the body at all but in the path.
The recorder adds the Authorization header for the cell's outcome (or omits it for
`unauthenticated`) and, for `upstream_down`, the X-Oracle-Upstream: down control the mock honors is
NOT sent by the client — the recorder flips the mock instead (busbar must not see a control header).

Named gap: bedrock INGRESS authenticates with SigV4 (busbar verifies AWS-style signatures on that
door), which a bearer key cannot satisfy. Such cells are emitted with auth="sigv4" so the recorder
records them as MOCK-UNSUPPORTED (a visible gap, never a silent pass) until a SigV4 signer is added.
"""
import json
import os
import sys

PING = "ping"
# The ask a SOURCED answer belongs to. `ok_citation` is still a happy path — the only thing that
# makes it different from `ok` is that the answer cites where it came from — so the request is an
# ordinary one, worded as a question rather than the bare ping the other cells send.
CITATION_ASK = "what does the published spec say?"

# qa/field-inventory.json names the OpenAI dialects `openai` / `responses`; the oracle config
# (oracle-config.sh ORACLE_DIALECTS) and this builder use the fuller `openai-chat` / `openai-responses`.
# One map, applied to both axes, so a cell id never silently targets a model that does not exist.
DIALECT_ALIAS = {"openai": "openai-chat", "responses": "openai-responses"}


def canon(d: str) -> str:
    return DIALECT_ALIAS.get(d, d)


# The two outcomes whose NAME is the only declaration they carry. They are the LAST thing
# declares_stream() consults, not the first — see its docstring.
STREAM_BY_NAME = ("ok_stream", "ok_stream_array")
# Mock controls a BUFFERED request can never reach, so a cell that asks for one has declared that its
# request is a stream. `stream-error` is gated in mock-upstream.py on `want_stream and stream_error`;
# on the gemini/bedrock doors the same verb is reached only down the streaming arm.
STREAM_ONLY_CONTROLS = ("stream-error",)


def declares_stream(cell: dict) -> bool:
    """Is THIS CELL's request a streamed one? Read off the CELL, never off the outcome's name alone.

    This used to be `oc in ("ok_stream", "ok_stream_array")` and nothing else, and that is a defect
    with a measurement behind it. The six `llm|<d>|<d>|request|stream_upstream_error` cells declare
    `mock_control: {"stream-error": true}`; the mock gates that fault on `want_stream and
    stream_error`, so a BUFFERED request can never reach it. All six were therefore sent buffered and
    recorded the HAPPY PATH under the name of the failure — measured against 1.5.5 with their
    `needs_fixture` lifted: `usage delta {"requests":1,"spend_cents":250,"tokens":18}` and a buffered
    completion body, identically on both binaries. A cell that records the opposite of what it is
    named is worse than an unrecorded one, because it is green.

    THE ORDER, most specific first:
      1. `stream` — the cell's own explicit field. A corpus that spells the request shape out is
         believed, in BOTH directions, and never has to argue with a list of names living in the tool.
      2. a `mock_control` only a stream can reach (STREAM_ONLY_CONTROLS). Asking the upstream to fail
         PART WAY THROUGH a stream is a statement about the request, not just about the mock.
      3. the outcome NAME, for the two outcomes that carry no other declaration. Last, because a name
         is a convention and a declaration is a shape — and because it was being FIRST that made the
         six cells above unrecordable.
    """
    if "stream" in cell:
        return bool(cell["stream"])
    control = cell.get("mock_control") or {}
    if any(control.get(k) for k in STREAM_ONLY_CONTROLS):
        return True
    return cell.get("outcome") in STREAM_BY_NAME


# ── THE NON-CHAT (LEAF) OPERATIONS ────────────────────────────────────────────────────────────────
# Every LLM cell in the corpus before 0.3.17 was `op: "chat"`, because this builder only knew how to
# word a conversation. busbar serves SEVEN operations; six of them had no recorded byte anywhere.
#
# The shapes below are what busbar's own INGRESS readers parse -- read off
# `crates/busbar-llm-codec/src/<dialect>/handler.rs` -- and several of them REFUSE a body that merely
# looks right, which is why each is spelled out rather than derived from the chat body:
#   * openai embeddings/moderation: a missing or non-string/array `input` is a 400 by name.
#   * openai image: a top-level `image` member makes the request an UnsupportedSubOp 404, not a 400.
#   * cohere embed/rerank: empty `texts`, or an empty `query`/`documents`, is a 400 by name.
#   * gemini embedContent: an empty joined `content.parts[].text` is a 400 by name.
#   * openai transcription: the door reads MULTIPART, not JSON, and a body with no `file` part is a
#     400 by name. It is the one ingress in this file that is not a JSON document.
#
# THE AUDIO PAYLOAD IS ASCII ON PURPOSE. It is carried as a JSON string in this builder's output and
# posted verbatim by the recorder; a byte outside ASCII would have to be escaped into that string and
# un-escaped back out, and the one thing this cell is about is which bytes reached busbar.
LEAF_AUDIO = "oracle-audio"
# A FIXED multipart boundary: the request is the cell's fixture, so nothing in it may be a draw.
# (busbar's own EGRESS boundary is a per-process CSPRNG draw -- that is normalised on the far side,
# scoped to the cells it is about, and is not this builder's business.)
LEAF_BOUNDARY = "----oracleboundary0000000000000000"
IMAGE_PROMPT = "a fox"
RERANK_QUERY = "which one"
RERANK_DOCS = ["the first document", "the second document"]


def multipart(fields: list[tuple[str, str]], file_part: tuple[str, str, str]) -> str:
    """A multipart/form-data body, CRLF-framed, in a fixed field order.

    Written here rather than with `email`/`requests` because the bytes ARE the fixture: a library
    that renumbered a boundary, reordered a field or folded a header would move every recorded
    transcription cell for a reason that has nothing to do with busbar.
    """
    out = []
    for name, value in fields:
        out.append(f"--{LEAF_BOUNDARY}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n")
    name, filename, ctype = file_part
    out.append(f"--{LEAF_BOUNDARY}\r\nContent-Disposition: form-data; name=\"{name}\"; "
               f"filename=\"{filename}\"\r\nContent-Type: {ctype}\r\n\r\n{LEAF_AUDIO}\r\n")
    out.append(f"--{LEAF_BOUNDARY}--\r\n")
    return "".join(out)


def leaf_request_for(cell: dict) -> dict:
    """The client-side ingress request for one NON-chat cell: (ingress dialect, operation).

    The pair is what decides the door, and the cell carries both. There is no fallback arm: a pair
    this builder has no wording for is a hard error, because the alternative -- posting a chat body
    at an embeddings door -- records a 400 under a cell id that claims a happy path.
    """
    ing = canon(cell["ingress_dialect"])
    model = f"m-{canon(cell['egress_dialect'])}"
    op = cell["op"]
    hdr = {"Content-Type": "application/json"}

    if op == "embeddings":
        if ing == "openai-chat":
            path, body = "/v1/embeddings", {"model": model, "input": PING}
        elif ing == "cohere":
            path = "/v2/embed"
            body = {"model": model, "texts": [PING], "input_type": "search_document",
                    "embedding_types": ["float"]}
        elif ing == "gemini":
            path = f"/v1beta/models/{model}:embedContent"
            body = {"content": {"parts": [{"text": PING}]}}
        else:
            raise SystemExit(f"no embeddings door for ingress dialect {ing!r}")
    elif op == "image":
        if ing == "openai-chat":
            path = "/v1/images/generations"
            body = {"model": model, "prompt": IMAGE_PROMPT, "n": 1, "size": "1024x1024"}
        elif ing == "gemini":
            path = f"/v1beta/models/{model}:predict"
            body = {"instances": [{"prompt": IMAGE_PROMPT}], "parameters": {"sampleCount": 1}}
        else:
            raise SystemExit(f"no image door for ingress dialect {ing!r}")
    elif op == "moderation":
        if ing != "openai-chat":
            raise SystemExit(f"no moderation door for ingress dialect {ing!r}")
        path, body = "/v1/moderations", {"model": model, "input": PING}
    elif op == "rerank":
        if ing != "cohere":
            raise SystemExit(f"no rerank door for ingress dialect {ing!r}")
        path = "/v2/rerank"
        body = {"model": model, "query": RERANK_QUERY, "documents": RERANK_DOCS}
    elif op == "transcription":
        if ing != "openai-chat":
            raise SystemExit(f"no transcription door for ingress dialect {ing!r}")
        # `/v1/audio/translations`, NOT `/v1/audio/transcriptions`: the sibling path is claimed BY
        # NAME by the voice plane, so on the LLM plane it is not this door's. The LLM ladder's own
        # rung-14 comment says exactly that, and the cell list is derived from that ladder.
        path = "/v1/audio/translations"
        hdr = {"Content-Type": f"multipart/form-data; boundary={LEAF_BOUNDARY}"}
        raw = multipart([("model", model), ("response_format", "json")],
                        ("file", "oracle.mp3", "audio/mpeg"))
        return {"method": "POST", "path": path, "headers": hdr, "body": raw, "auth": "bearer",
                "stream": False,
                "note": "transcription ingress is multipart/form-data with a FIXED boundary"}
    elif op == "speech":
        # Measured, not assumed: no rung of the LLM plane's ladder claims a path that `op_class_for`
        # calls `speech` -- `/v1/audio/speech` belongs to the voice plane. The cell generator does not
        # emit such a cell for that reason; reaching here means one was hand-written, and answering
        # it would record a 404 under a happy-path name.
        raise SystemExit("the LLM plane claims no speech door: /v1/audio/speech is the voice plane's")
    else:
        raise SystemExit(f"unknown op {op!r}")

    raw = json.dumps(body, separators=(",", ":"), sort_keys=True)
    return {"method": "POST", "path": path, "headers": hdr, "body": raw, "auth": "bearer",
            "stream": False, "note": ""}

# ── THE TOOL-CALL ROUND TRIP ──────────────────────────────────────────────────────────────────────
# A tool call is two turns, and the corpus recorded neither. The round trip is enumerated as a PAIR
# of one-request cells rather than as one two-request cell, and that is a deliberate choice with a
# measurement behind it: the recorder's only multi-step primitive (`request.pre`) records the setup
# call's STATUS and throws its bytes away, so a "round trip" cell driven that way would record the
# second turn and silently drop the first — the half where the tool call itself is translated.
#
#   `ok_tool_call`   turn ONE. The client declares a tool; the upstream answers WITH A TOOL CALL
#                    (the mock's `tool-call` verb). What is recorded is busbar's rendering of that
#                    answer in the INGRESS dialect — the block type, the id, the argument encoding
#                    (a JSON STRING on three dialects and an OBJECT on three others) and the stop
#                    reason, none of which any recorded cell has ever contained.
#   `ok_tool_result` turn TWO. The client sends the assistant's tool-call turn back plus the tool
#                    RESULT; the upstream answers with ordinary text (no verb: the plain happy-path
#                    answer IS the final answer). What is recorded is mostly what busbar SENT
#                    UPSTREAM — `effects.egress` — because the whole subject is the translation of a
#                    tool-result turn, which has a different shape in all six dialects (a `tool`
#                    role, a `user` turn carrying a `toolResult` block, a flat `function_call_output`
#                    item, a `functionResponse` part).
#
# TOGETHER they are the round trip, and they are linked by a literal: both turns name the SAME tool
# call id, `TOOL_CALL_ID`, which is also the id the mock's answer carries. A selftest holds the three
# to one value, so the pair cannot drift into being two unrelated cells.
TOOL_NAME = "get_weather"
TOOL_ARGS = {"city": "paris"}
TOOL_RESULT = "sunny"
# The id the MOCK issues and the client echoes. Native-looking per dialect, because three of the six
# readers REFUSE an empty tool id outright (anthropic tool_use.id, the openai/cohere request paths,
# bedrock's request path) and a synthetic-looking one would be answering a different question.
TOOL_IDS = {"anthropic": "toolu_oracle0001", "openai-chat": "call_oracle0001",
            "openai-responses": "call_oracle0001", "gemini": None,
            "bedrock": "tooluse_oracle0001", "cohere": "call_oracle0001"}
TOOL_SCHEMA = {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}
TOOL_DESC = "Get the weather"
TOOL_ARGS_JSON = json.dumps(TOOL_ARGS, separators=(",", ":"), sort_keys=True)


def tool_decl(dialect: str):
    """This dialect's own spelling of ONE tool declaration. Four spellings across six dialects."""
    if dialect == "anthropic":
        return [{"name": TOOL_NAME, "description": TOOL_DESC, "input_schema": TOOL_SCHEMA}]
    if dialect in ("openai-chat", "cohere"):
        return [{"type": "function", "function": {"name": TOOL_NAME, "description": TOOL_DESC,
                                                  "parameters": TOOL_SCHEMA}}]
    if dialect == "openai-responses":
        # FLAT, not nested under `function`: the Responses tool object is its own shape.
        return [{"type": "function", "name": TOOL_NAME, "description": TOOL_DESC,
                 "parameters": TOOL_SCHEMA}]
    if dialect == "gemini":
        return [{"functionDeclarations": [{"name": TOOL_NAME, "description": TOOL_DESC,
                                           "parameters": TOOL_SCHEMA}]}]
    if dialect == "bedrock":
        return {"tools": [{"toolSpec": {"name": TOOL_NAME, "description": TOOL_DESC,
                                        "inputSchema": {"json": TOOL_SCHEMA}}}]}
    raise SystemExit(f"no tool declaration for dialect {dialect!r}")


def tool_turns(dialect: str):
    """The assistant's tool-call turn and the tool RESULT turn, in this dialect's own shape.

    Returned as a list of whatever the dialect's message/content array holds, to be appended to the
    first turn — which is what a real client does: it echoes the answer it was given and adds the
    result beside it.
    """
    tid = TOOL_IDS[dialect]
    if dialect == "anthropic":
        return [{"role": "assistant", "content": [{"type": "tool_use", "id": tid,
                                                   "name": TOOL_NAME, "input": TOOL_ARGS}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid,
                                              "content": TOOL_RESULT}]}]
    if dialect == "openai-chat":
        return [{"role": "assistant", "content": None,
                 "tool_calls": [{"id": tid, "type": "function",
                                 "function": {"name": TOOL_NAME, "arguments": TOOL_ARGS_JSON}}]},
                {"role": "tool", "tool_call_id": tid, "content": TOOL_RESULT}]
    if dialect == "cohere":
        # `tool_plan` is Cohere's own member for the assistant's reasoning about the call. busbar
        # reads it into the IR's thinking slot, so it is part of what a faithful echo carries.
        return [{"role": "assistant", "tool_plan": "I will look up the weather.",
                 "tool_calls": [{"id": tid, "type": "function",
                                 "function": {"name": TOOL_NAME, "arguments": TOOL_ARGS_JSON}}]},
                {"role": "tool", "tool_call_id": tid, "content": TOOL_RESULT}]
    if dialect == "openai-responses":
        # FLAT input items, siblings of the message — not nested in a `messages` array.
        return [{"type": "function_call", "call_id": tid, "name": TOOL_NAME,
                 "arguments": TOOL_ARGS_JSON},
                {"type": "function_call_output", "call_id": tid, "output": TOOL_RESULT}]
    if dialect == "gemini":
        # Gemini's wire carries NO tool-call id at all: a `functionResponse` correlates by NAME, and
        # it must ride a `user` turn. That is why TOOL_IDS[gemini] is None rather than a literal —
        # there is no id to echo, and inventing one would put a member on the wire that no Gemini
        # client sends.
        return [{"role": "model", "parts": [{"functionCall": {"name": TOOL_NAME, "args": TOOL_ARGS}}]},
                {"role": "user", "parts": [{"functionResponse": {"name": TOOL_NAME,
                                                                 "response": {"output": TOOL_RESULT}}}]}]
    if dialect == "bedrock":
        # Converse has no `tool` role: a toolResult is a content block on the USER turn.
        return [{"role": "assistant", "content": [{"toolUse": {"toolUseId": tid, "name": TOOL_NAME,
                                                               "input": TOOL_ARGS}}]},
                {"role": "user", "content": [{"toolResult": {"toolUseId": tid, "status": "success",
                                                             "content": [{"text": TOOL_RESULT}]}}]}]
    raise SystemExit(f"no tool turns for dialect {dialect!r}")


# The two outcomes whose REQUEST carries tools. Named here rather than tested inline, because
# `request_for` consults the set twice and a second spelling of it is a second chance to disagree.
TOOL_OUTCOMES = ("ok_tool_call", "ok_tool_result")


def with_tools(dialect: str, body: dict, echo: bool) -> dict:
    """Put this dialect's tool DECLARATION on a chat body, and (turn two) the tool turns with it.

    `echo` is what separates the two cells: turn one declares a tool and says `ping`; turn two says
    the same thing and then ECHOES BACK the assistant's tool-call turn together with the result,
    which is what a real client does with the answer it was just given.
    """
    turns = tool_turns(dialect) if echo else []
    if dialect == "bedrock":
        # Converse keeps tools under `toolConfig`, not a top-level `tools` array.
        body["toolConfig"] = tool_decl(dialect)
        body["messages"] = body["messages"] + turns
        return body
    body["tools"] = tool_decl(dialect)
    if dialect == "gemini":
        body["contents"] = body["contents"] + turns
    elif dialect == "openai-responses":
        # `input` is a bare STRING on every other Responses cell. A tools turn cannot be worded that
        # way: the function_call / function_call_output items are FLAT siblings of the message, so
        # the string has to become the one-message array it is shorthand for before anything can sit
        # beside it. Same content, spelled the way the dialect spells a multi-item input.
        body["input"] = [{"role": "user", "content": [{"type": "input_text", "text": PING}]}] + turns
    else:
        body["messages"] = body["messages"] + turns
    return body


def request_for(cell: dict) -> dict:
    # THE OPERATION DECIDES THE DOOR BEFORE THE DIALECT DOES. Everything below this line words a
    # CONVERSATION; a non-chat cell's request is a different path, a different body and (for
    # transcription) not even a JSON document, so it is built by its own function rather than by
    # bending a chat body into shape. A cell with no `op` is a chat cell from before the axis
    # existed and is read exactly as it was.
    if cell.get("op", "chat") != "chat":
        return leaf_request_for(cell)
    ing = canon(cell["ingress_dialect"])
    model = f"m-{canon(cell['egress_dialect'])}"
    oc = cell["outcome"]
    # WHETHER it streams is the cell's own declaration (declares_stream); HOW gemini frames a stream
    # is the outcome that names the framing — `ok_stream_array` is gemini's JSON-array streaming (no
    # `alt=sse`), every other dialect streams one way only, and a gemini cell that declares a stream
    # without naming a framing gets SSE like everyone else.
    stream = declares_stream(cell)
    hdr = {"Content-Type": "application/json"}
    auth = "bearer"
    note = ""

    if ing == "anthropic":
        path = "/v1/messages"
        body = {"model": model, "max_tokens": 64, "messages": [{"role": "user", "content": PING}]}
        if stream:
            body["stream"] = True
        hdr["anthropic-version"] = "2023-06-01"
    elif ing == "openai-chat":
        path = "/v1/chat/completions"
        body = {"model": model, "messages": [{"role": "user", "content": PING}]}
        if stream:
            body["stream"] = True
    elif ing == "openai-responses":
        path = "/v1/responses"
        body = {"model": model, "input": PING}
        if oc == "ok_citation":
            # The ANSWER carries the citation, not the ask — but the ask has to be one a sourced
            # answer belongs to, so it names the question instead of the bare ping. The mock's
            # `citation` verb supplies the annotated answer.
            body["input"] = CITATION_ASK
        if stream:
            body["stream"] = True
    elif ing == "gemini":
        if stream:
            verb = "streamGenerateContent" if oc == "ok_stream_array" else "streamGenerateContent?alt=sse"
        else:
            verb = "generateContent"
        path = f"/v1beta/models/{model}:{verb}"
        body = {"contents": [{"role": "user", "parts": [{"text": PING}]}]}
    elif ing == "bedrock":
        path = f"/model/{model}/{'converse-stream' if stream else 'converse'}"
        body = {"messages": [{"role": "user", "content": [{"text": PING}]}]}
        if oc == "ok_cachepoint_document":
            # A `cachePoint` occupying a wire slot BEFORE a native `document`. The cachePoint yields
            # no IR block, so past it the wire index and the IR index disagree — which is the whole
            # point of the cell: the reader parks the document by WIRE position and the writer has to
            # find it there to suppress its own modelled copy. The leading text keeps the message a
            # normal one (Converse requires content), and the document is the smallest legal one.
            body = {"messages": [{"role": "user", "content": [
                {"text": PING},
                {"cachePoint": {"type": "default"}},
                {"document": {"format": "txt", "name": "oracle-doc",
                              "source": {"bytes": "b3JhY2xl"}}},
            ]}]}
        auth = "sigv4"
        note = "bedrock ingress is SigV4-authenticated: signed with the cell principal's AWS credential (issue_aws_credential)"
    elif ing == "cohere":
        path = "/v2/chat"
        body = {"model": model, "messages": [{"role": "user", "content": PING}]}
        if stream:
            body["stream"] = True
    else:
        raise SystemExit(f"unknown ingress dialect {ing!r}")

    # THE TOOL TURNS, after the dialect's own body exists and before it is serialised. Both tool
    # cells send the ordinary `ping` this cell family always sends; what makes them different is the
    # DECLARATION riding beside it, and (turn two) the assistant turn and the result echoed back.
    if oc in TOOL_OUTCOMES:
        body = with_tools(ing, body, echo=(oc == "ok_tool_result"))

    if oc == "malformed":
        raw = "{this is not json"
        note = (note + "; " if note else "") + "malformed body: decode must refuse with the dialect's native 400"
    else:
        raw = json.dumps(body, separators=(",", ":"), sort_keys=True)

    if auth == "sigv4":
        akid, secret, host = os.environ.get("ORACLE_AWS_AKID", ""), os.environ.get("ORACLE_AWS_SECRET", ""), os.environ.get("ORACLE_HOST", "")
        if oc == "unauthenticated" or not akid:
            # unauthenticated: a well-formed SigV4 header for an UNKNOWN AccessKeyId (the constant-time
            # DUMMY_SECRET reject path); an absent header would be the bearer arm's 401 instead
            akid, secret = "AKIAORACLEUNKNOWN000", "not-the-secret"
        hdr.update(sigv4_headers("POST", path, raw.encode(), host, akid, secret))
        auth = "sigv4-signed"

    # `stream` IS PART OF THE EMITTED SHAPE, not just a local. The recorder and the product's
    # llm-conformance validator both read this request back; a consumer that had to re-parse the body
    # (and know that gemini and bedrock say it in the PATH instead) would be a second, quieter copy of
    # the rule above, free to disagree with it.
    return {"method": "POST", "path": path, "headers": hdr, "body": raw, "auth": auth,
            "stream": stream, "note": note}


# ── inbound SigV4 (the Bedrock SDK's model): the verifier reads region/service from the Credential
# scope, requires x-amz-date within its skew window, refuses UNSIGNED-PAYLOAD and checks the body
# hash — so sign the real body with the current time.
def sigv4_headers(method: str, path: str, body: bytes, host: str, akid: str, secret: str,
                  region: str = "us-east-1", service: str = "bedrock") -> dict:
    import datetime
    import hashlib
    import hmac
    now = datetime.datetime.now(datetime.timezone.utc)
    amzdate, datestamp = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(body).hexdigest()
    signed = {"host": host, "x-amz-content-sha256": payload_hash, "x-amz-date": amzdate}
    signed_headers = ";".join(sorted(signed))
    canonical_headers = "".join(f"{k}:{signed[k].strip()}\n" for k in sorted(signed))
    canonical = "\n".join([method, uri_encode_path(path), "", canonical_headers, signed_headers, payload_hash])
    scope = f"{datestamp}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amzdate, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    k = hmac.new(("AWS4" + secret).encode(), datestamp.encode(), hashlib.sha256).digest()
    for part in (region, service, "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    return {"x-amz-date": amzdate, "x-amz-content-sha256": payload_hash,
            "Authorization": f"AWS4-HMAC-SHA256 Credential={akid}/{scope}, SignedHeaders={signed_headers}, Signature={sig}"}


def uri_encode_path(path: str) -> str:
    # SigV4 canonical URI: each segment percent-encoded except unreserved chars; '/' kept
    from urllib.parse import quote
    return "/".join(quote(seg, safe="-_.~") for seg in path.split("/"))


def main() -> int:
    if "--cell" in sys.argv:
        cell = json.loads(sys.argv[sys.argv.index("--cell") + 1])
    else:
        cell = json.load(sys.stdin)
    print(json.dumps(request_for(cell), separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
