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


def request_for(cell: dict) -> dict:
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
