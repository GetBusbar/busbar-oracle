#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Busbar Inc and contributors
"""The `ws` driver: open a WebSocket session against the door, drive a scripted client, record it all.

  capture-ws.py --drive <spec.json>                                      > ws.json
  capture-ws.py <ws.json> <before-dir> <after-dir> [egress-file ...]     > captured.json
  capture-ws.py --selftest

Two forms, because the recorder's effect snapshots bracket the SESSION and not a Python process: the
first form is the socket work (record.sh runs it between its before- and after-snapshots), the second
assembles the captured cell exactly the way capture.py does for a curl response, reusing capture.py's
own usage/metrics/audit/egress helpers so a ws cell's `effects` can never drift from an http cell's.

WHAT A SESSION RECORDS, in the order it happened:
  status, headers   the HANDSHAKE: the 101 (or the refusal — a 401/404/503 handshake is a whole,
                    ordinary HTTP response and is recorded as one, body and all)
  ws.frames         every frame, both directions, in wire order: {"dir": "out"|"in", "opcode",
                    "text"|"base64"|("code","reason" for a close)}. The client's own frames are
                    recorded too — they are the cell's stimulus and a reader should not need cells.json
                    to know what the door was answering
  ws.close          who closed first ("server"|"client"), or "eof" (the socket died with no close
                    frame: the `cut` arm), and the code + reason. When the CLIENT closed first it
                    also carries `echo`: "close" (the door answered with its own close, the clean
                    RFC 6455 §5.5.1 two-sided shutdown), "eof" (the door hung up without one) or
                    "none" (the door said nothing at all inside the timeout). A door that stops
                    echoing is a diff on this key, not a silently shorter transcript
  ws.accept_ok      whether Sec-WebSocket-Accept was the value RFC 6455 derives from the key
  effects           usage Δ, metrics Δ, audit Δ, egress — the same closed loop every driver records

EVERYTHING THE CLIENT DOES IS FIXED. The `Sec-WebSocket-Key` is RFC 6455 §1.3's own sample nonce
unless the spec names another, the masking key is wsframe.CLIENT_MASK, the request head is written
in one fixed header order, and the script is data. So the bytes busbar receives are a pure function
of the cell, and a recording made twice from the same binary is byte-identical — which is what lets
a third recording, from a different binary, be a diff.

THE SCRIPT is a list of steps, run in order; a step is one of:
  {"send": <json>}                 a text frame carrying that JSON, canonically serialized
  {"send_text": "<str>"}           a text frame with exactly those bytes
  {"send_binary_base64": "<b64>"}  a binary frame (audio)
  {"ping": "<b64>"}                a ping with that payload
  {"await": {"<json-pointer>": <value>, ...}}
                                   read frames until a TEXT frame whose JSON has every named RFC 6901
                                   pointer equal to its value (the repo's addressing idiom: `/type`
                                   for an OpenAI event, `/serverContent/turnComplete` for Gemini)
  {"await": "close"}               read frames until the server closes (or the socket dies)
  {"close": {"code": N, "reason": "<str>"}}
                                   send a close now (the default, after the last step, is
                                   spec.close or 1000)
An `await` that the door never satisfies inside `timeout_secs` is a HARNESS failure (exit 1, a
`harness_error` line on stderr), never a recorded outcome: a transcript cut off by the recorder's
clock is not what the door did. A cell whose subject is "the door closes on me" awaits "close".

Pings from the door are answered with a pong (RFC 6455 §5.5.2 requires it) and BOTH frames are
recorded, so a door that pings is visible in the transcript and a door that stops pinging is a diff.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import sys
import time
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wsframe  # noqa: E402
from capture import audit_diff, load_egress, load_json, metrics_delta, usage_delta  # noqa: E402

DEFAULT_TIMEOUT = 15.0


def canon(obj) -> bytes:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True).encode("utf-8")


def resolve_pointer(doc, pointer: str):
    """RFC 6901: `""` is the whole document; each `/`-separated token, unescaped `~1`->`/` then
    `~0`->`~`, indexes a dict by key or a list by integer. Raises KeyError/IndexError/TypeError when
    the pointer names nothing — the caller reads that as 'does not match'."""
    if pointer == "":
        return doc
    if not pointer.startswith("/"):
        raise KeyError(f"not a JSON pointer: {pointer!r}")
    cur = doc
    for tok in pointer[1:].split("/"):
        tok = tok.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, list):
            cur = cur[int(tok)]
        elif isinstance(cur, dict):
            cur = cur[tok]
        else:
            raise TypeError("pointer descends into a scalar")
    return cur


def matches(payload: bytes, want: dict) -> bool:
    try:
        doc = json.loads(payload.decode("utf-8"))
    except ValueError:
        return False
    for ptr, val in want.items():
        try:
            if resolve_pointer(doc, ptr) != val:
                return False
        except (KeyError, IndexError, TypeError, ValueError):
            return False
    return True


class HarnessError(Exception):
    pass


class _Buffered:
    """A socket with bytes already read ahead of it. wsframe only ever calls `.recv(n)`, so the
    glued tail of the handshake read is handed out first and the socket is read only once it is
    drained — no frame boundary is ever assumed."""

    def __init__(self, sock: socket.socket, head: bytes):
        self.sock, self.buf = sock, head

    def recv(self, n: int) -> bytes:
        if self.buf:
            out, self.buf = self.buf[:n], self.buf[n:]
            return out
        return self.sock.recv(n)


def _frame_record(direction: str, op: int, payload: bytes) -> dict:
    rec = {"dir": direction, "opcode": wsframe.OPCODE_NAMES.get(op, f"0x{op:x}")}
    if op == wsframe.OP_CLOSE:
        rec["code"], rec["reason"] = wsframe.decode_close(payload)
    elif op == wsframe.OP_TEXT:
        rec["text"] = payload.decode("utf-8", "replace")
    else:
        rec["base64"] = base64.b64encode(payload).decode("ascii")
    return rec


def drive(spec: dict) -> dict:
    url = urlsplit(spec["url"])
    if url.scheme != "ws":
        raise HarnessError(f"only ws:// is driven (the harness is loopback); got {spec['url']!r}")
    host, port = url.hostname or "127.0.0.1", url.port or 80
    target = url.path or "/"
    if url.query:
        target += "?" + url.query
    key = spec.get("key") or wsframe.SAMPLE_KEY
    timeout = float(spec.get("timeout_secs") or DEFAULT_TIMEOUT)
    # THE REQUEST HEAD, IN ONE FIXED ORDER. The four RFC-required headers first, then the cell's own
    # in the order the cell wrote them (a JSON object keeps insertion order), so the bytes the door
    # sees are the cell's and not a dict's.
    head = [f"GET {target} HTTP/1.1", f"Host: {host}:{port}", "Upgrade: websocket", "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13"]
    for k, v in (spec.get("headers") or {}).items():
        head.append(f"{k}: {v}")
    sock = socket.create_connection((host, port), timeout=timeout)
    frames: list = []
    out = {"status": 0, "reason": "", "headers": {}, "body": "", "ws": None,
           "dialect": spec.get("dialect"), "key": key, "target": target}
    try:
        sock.sendall(("\r\n".join(head) + "\r\n\r\n").encode("latin-1"))
        raw_head = wsframe.read_http_head(sock)
        head_bytes, rest = raw_head.split(b"\r\n\r\n", 1)
        status, reason, headers = wsframe.parse_status_line(head_bytes)
        out.update({"status": status, "reason": reason, "headers": headers})
        if status != 101:
            # a refused handshake is an ordinary HTTP response: read its body like curl would
            body = rest
            n = headers.get("content-length")
            if n and n.isdigit():
                while len(body) < int(n):
                    chunk = sock.recv(int(n) - len(body))
                    if not chunk:
                        break
                    body += chunk
            elif headers.get("transfer-encoding", "").lower() == "chunked":
                body = _dechunk(body, sock)
            else:
                sock.settimeout(1.0)
                try:
                    while True:
                        chunk = sock.recv(65536)
                        if not chunk:
                            break
                        body += chunk
                except (socket.timeout, OSError):
                    pass
            try:
                out["body"] = body.decode("utf-8")
            except UnicodeDecodeError:
                out["body"] = "base64:" + base64.b64encode(body).decode()
            return out
        accept_ok = headers.get("sec-websocket-accept") == wsframe.accept_key(key)
        close = {"by": "none", "code": None, "reason": ""}
        closed = False
        # a frame that arrived glued to the 101 head is still the first frame: the reader drains
        # those bytes before it touches the socket again
        reader = _Buffered(sock, rest)

        def recv():
            return wsframe.recv_message(reader)

        def send(op, payload):
            sock.sendall(wsframe.encode_frame(op, payload, wsframe.CLIENT_MASK))
            frames.append(_frame_record("out", op, payload))

        def send_close(code, reason):
            nonlocal closed
            sock.sendall(wsframe.encode_close(code, reason, wsframe.CLIENT_MASK))
            frames.append({"dir": "out", "opcode": "close", "code": code, "reason": reason})
            if close["by"] == "none":
                close.update({"by": "client", "code": code, "reason": reason})
            closed = True

        def read_one(deadline) -> tuple | None:
            """Receive and record one message; answer a ping; return (op, payload) or None on a
            close/EOF, having recorded how the session ended."""
            nonlocal closed
            sock.settimeout(max(0.05, deadline - time.monotonic()))
            try:
                op, payload = recv()
            except EOFError:
                close.update({"by": "eof"} if close["by"] == "none" else {})
                closed = True
                return None
            frames.append(_frame_record("in", op, payload))
            if op == wsframe.OP_CLOSE:
                code, r = wsframe.decode_close(payload)
                if close["by"] == "none":
                    close.update({"by": "server", "code": code, "reason": r})
                    # echo the close (RFC 6455 §5.5.1) so the record shows a clean two-sided close
                    sock.sendall(wsframe.encode_close(code, "", wsframe.CLIENT_MASK) if code is not None
                                 else wsframe.encode_close(None, mask=wsframe.CLIENT_MASK))
                    frames.append({"dir": "out", "opcode": "close", "code": code, "reason": ""})
                closed = True
                return None
            if op == wsframe.OP_PING:
                send(wsframe.OP_PONG, payload)
            return op, payload

        def read_close_echo():
            """AFTER WE SENT A CLOSE: read until the door's own close frame, or until it goes away.
            Not `await_until`, and that distinction was a bug: `send_close` sets `closed`, and
            `await_until` is gated on `while not closed`, so the wait for the echo returned
            IMMEDIATELY and the door's answering close was never read and never recorded. The
            transcript then ended at our own close on every clean session — the recorder was
            dropping the last frame of the handshake it exists to record, and `close.echo` was set
            only on the timeout path, so nothing said so.

            A door that answers something else first (a frame already in flight — RFC 6455 §5.5.1
            allows it) has those frames recorded too. A door that never echoes at all is a fact
            about the door, recorded as `close.echo`, never a harness failure: the session is over
            either way and the bytes up to here are what it did."""
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    close["echo"] = "none"; return
                sock.settimeout(max(0.05, remaining))
                try:
                    op, payload = recv()
                except EOFError:
                    close["echo"] = "eof"; return
                except (socket.timeout, OSError):
                    close["echo"] = "none"; return
                frames.append(_frame_record("in", op, payload))
                if op == wsframe.OP_CLOSE:
                    close["echo"] = "close"; return

        def await_until(pred, what: str):
            deadline = time.monotonic() + timeout
            while not closed:
                if time.monotonic() >= deadline:
                    raise HarnessError(f"await {what} timed out after {timeout}s")
                try:
                    got = read_one(deadline)
                except socket.timeout:
                    raise HarnessError(f"await {what} timed out after {timeout}s") from None
                if got is None:
                    return
                if pred(*got):
                    return

        for step in spec.get("script") or []:
            if closed:
                raise HarnessError(f"the session was already closed ({close}) before step {json.dumps(step)}")
            if "send" in step:
                send(wsframe.OP_TEXT, canon(step["send"]))
            elif "send_text" in step:
                send(wsframe.OP_TEXT, str(step["send_text"]).encode("utf-8"))
            elif "send_binary_base64" in step:
                send(wsframe.OP_BINARY, base64.b64decode(step["send_binary_base64"]))
            elif "ping" in step:
                send(wsframe.OP_PING, base64.b64decode(step["ping"] or ""))
            elif "await" in step:
                want = step["await"]
                if want == "close":
                    await_until(lambda op, p: False, "close")
                elif isinstance(want, dict):
                    await_until(lambda op, p, w=want: op == wsframe.OP_TEXT and matches(p, w), json.dumps(want))
                else:
                    raise HarnessError(f"await must be \"close\" or a {{pointer: value}} object, got {want!r}")
            elif "close" in step:
                c = step["close"] or {}
                send_close(int(c.get("code", 1000)), str(c.get("reason", "")))
                read_close_echo()
            else:
                raise HarnessError(f"unknown script step {json.dumps(step)}")
        if not closed:
            c = spec.get("close") or {}
            send_close(int(c.get("code", 1000)), str(c.get("reason", "")))
            read_close_echo()
        out["ws"] = {"accept_ok": accept_ok, "frames": frames, "close": close}
        return out
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _dechunk(buf: bytes, sock: socket.socket) -> bytes:
    """Minimal chunked-body reader for a refused handshake that answers chunked."""
    def need(n):
        nonlocal buf
        while len(buf) < n:
            chunk = sock.recv(65536)
            if not chunk:
                raise EOFError("chunked body truncated")
            buf += chunk
    body = b""
    while True:
        while b"\r\n" not in buf:
            need(len(buf) + 1)
        line, buf = buf.split(b"\r\n", 1)
        size = int(line.split(b";")[0].strip() or b"0", 16)
        if size == 0:
            return body
        need(size + 2)
        body += buf[:size]; buf = buf[size + 2:]


def assemble(ws_path: str, before: str, after: str, egress_paths: list) -> dict:
    ws = json.load(open(ws_path, encoding="utf-8"))
    ab, aa = load_json(before, "audit.json"), load_json(after, "audit.json")
    cap = {
        "status": ws.get("status", 0),
        "headers": ws.get("headers") or {},
        "body": ws.get("body") or "",
        "effects": {"usage": usage_delta(before, after), "metrics": metrics_delta(before, after),
                    "audit": audit_diff(ab, aa), "egress": load_egress(egress_paths)},
    }
    if ws.get("ws") is not None:
        cap["ws"] = {"dialect": ws.get("dialect"), "key": ws.get("key"), **ws["ws"]}
    return cap


# ── selftest: a scripted server on a loopback socket, no mock, no busbar ─────────────────────────
def _serve_once(server_sock, plan):
    """Accept one connection and follow `plan`: a list of ("send", op, payload) | ("close", code,
    reason) | ("cut",) | ("refuse", status, body) | ("echo",) | ("expect_close",) steps."""
    conn, _ = server_sock.accept()
    conn.settimeout(5)
    try:
        head = wsframe.read_http_head(conn)
        req = head.decode("latin-1").split("\r\n")
        hdrs = {ln.split(":", 1)[0].strip().lower(): ln.split(":", 1)[1].strip() for ln in req[1:] if ":" in ln}
        if plan and plan[0][0] == "refuse":
            _, status, body = plan[0]
            conn.sendall((f"HTTP/1.1 {status} X\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n").encode() + body)
            return {"request": req[0], "headers": hdrs}
        conn.sendall((f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Accept: {wsframe.accept_key(hdrs['sec-websocket-key'])}\r\n\r\n").encode())
        got = []
        for step in plan:
            if step[0] == "send":
                conn.sendall(wsframe.encode_frame(step[1], step[2]))
            elif step[0] == "close":
                conn.sendall(wsframe.encode_close(step[1], step[2]))
                try:
                    op, p = wsframe.recv_message(conn); got.append((op, p))
                except (EOFError, OSError):
                    pass
            elif step[0] == "cut":
                conn.shutdown(socket.SHUT_RDWR)
            elif step[0] == "echo":
                op, p = wsframe.recv_message(conn); got.append((op, p))
                conn.sendall(wsframe.encode_frame(op, p))
            elif step[0] == "expect_close":
                op, p = wsframe.recv_message(conn); got.append((op, p))
                if op == wsframe.OP_CLOSE:
                    conn.sendall(wsframe.encode_close(*wsframe.decode_close(p)))
        return {"request": req[0], "headers": hdrs, "got": got}
    finally:
        conn.close()


def selftest() -> int:
    import threading
    fails = 0

    def say(ok, what):
        nonlocal fails
        print(f"{'PASS' if ok else 'FAIL'}  {what}")
        if not ok:
            fails += 1

    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(4)
    port = srv.getsockname()[1]
    result: dict = {}

    def run(plan, spec_extra):
        result.clear()
        def serve():
            # The scripted server is a peer, not an assertion: when a case is ABOUT the client
            # giving up (case (f)), this thread is still blocked in recv when the client hangs up,
            # and an unhandled EOFError there printed a full traceback into the selftest's output
            # that looked exactly like a failure and was not one. The client's own record is what
            # every `say` below reads; what the peer saw is a convenience.
            try:
                result.update(_serve_once(srv, plan))
            except (EOFError, OSError, ValueError) as e:
                result.update({"peer_error": f"{type(e).__name__}: {e}"})

        t = threading.Thread(target=serve, daemon=True); t.start()
        spec = {"url": f"ws://127.0.0.1:{port}/v1/realtime?model=m", "timeout_secs": 3, **spec_extra}
        try:
            got = drive(spec)
        except HarnessError as e:
            got = {"harness_error": str(e)}
        t.join(5)
        return got

    try:
        # (a) the handshake is fixed: sample key, fixed header order, and the accept is verified
        r = run([("send", wsframe.OP_TEXT, b'{"type":"session.created","event_id":"e1"}'), ("expect_close",)],
                {"headers": {"Authorization": "Bearer t"}, "script": [{"await": {"/type": "session.created"}}]})
        say(r.get("status") == 101 and r["ws"]["accept_ok"] is True, "a 101 handshake is recorded with the accept value verified against the fixed key")
        say(result["headers"].get("sec-websocket-key") == wsframe.SAMPLE_KEY and result["request"] == "GET /v1/realtime?model=m HTTP/1.1",
            "the client sends RFC 6455's sample nonce and the cell's target, in one fixed head")
        say(result["headers"].get("authorization") == "Bearer t", "the cell's own headers ride on the handshake")
        say([(f["dir"], f["opcode"]) for f in r["ws"]["frames"]] == [("in", "text"), ("out", "close"), ("in", "close")]
            and r["ws"]["close"] == {"by": "client", "code": 1000, "reason": "", "echo": "close"},
            f"after the script the client closes 1000 and the door's answering close is READ and recorded: {r['ws']['close']}")
        # (b) await by RFC 6901 pointer: frames before the match are recorded, the match ends the wait
        r = run([("send", wsframe.OP_TEXT, b'{"type":"a"}'), ("send", wsframe.OP_TEXT, b'{"type":"b","x":{"y/z":[0,{"k":1}]}}'),
                 ("expect_close",)],
                {"script": [{"await": {"/type": "b", "/x/y~1z/1/k": 1}}]})
        say(r.get("ws") and [f.get("text") for f in r["ws"]["frames"][:2]] == ['{"type":"a"}', '{"type":"b","x":{"y/z":[0,{"k":1}]}}'],
            "await by pointer records every frame up to the match, with ~1 unescaped to a slash")
        # (c) the server closes first: recorded as by=server with its code and reason, echoed once
        r = run([("send", wsframe.OP_TEXT, b'{"t":1}'), ("close", 1011, "boom")], {"script": [{"await": "close"}]})
        say(r.get("ws") and r["ws"]["close"] == {"by": "server", "code": 1011, "reason": "boom"},
            "a server-initiated close is recorded with its code and reason, by=server")
        say(result.get("got") and result["got"][0][0] == wsframe.OP_CLOSE, "…and the client echoed the close")
        # (d) the socket dies with no close frame: by=eof, never a fabricated 1006
        r = run([("send", wsframe.OP_TEXT, b'{"t":1}'), ("cut",)], {"script": [{"await": "close"}]})
        say(r.get("ws") and r["ws"]["close"]["by"] == "eof" and r["ws"]["close"]["code"] is None,
            "a socket that dies without a close frame is recorded as by=eof with no code")
        # (e) a ping is answered and both frames are in the transcript
        r = run([("send", wsframe.OP_PING, b"hb"), ("send", wsframe.OP_TEXT, b'{"t":2}'), ("expect_close",)],
                {"script": [{"await": {"/t": 2}}]})
        ops = [(f["dir"], f["opcode"]) for f in r["ws"]["frames"]] if r.get("ws") else []
        say(ops[:3] == [("in", "ping"), ("out", "pong"), ("in", "text")], f"a ping is answered with a pong and both are recorded: {ops[:3]}")
        # (f) sends: json (canonical), text (verbatim), binary; all recorded with direction
        r = run([("echo",), ("echo",), ("echo",), ("expect_close",)],
                {"script": [{"send": {"b": 1, "a": 2}}, {"await": {"/a": 2}}, {"send_text": "raw"}, {"await": "close"}]})
        # the second echo returns "raw" (not JSON) so the await for close only ends at the client's own close…
        say("harness_error" in r and "timed out" in r["harness_error"],
            "an await the door never satisfies is a harness error, not a recorded outcome")
        r = run([("echo",), ("echo",), ("expect_close",)],
                {"script": [{"send": {"b": 1, "a": 2}}, {"await": {"/a": 2}}, {"send_binary_base64": "AAEC"}]})
        fr = r["ws"]["frames"] if r.get("ws") else []
        say(fr and fr[0] == {"dir": "out", "opcode": "text", "text": '{"a":2,"b":1}'}, "a `send` step writes canonical JSON and records it as out")
        say(fr and fr[2] == {"dir": "out", "opcode": "binary", "base64": "AAEC"}, "a binary send is recorded as base64, direction out")
        # (g) a refused handshake is a whole HTTP response
        r = run([("refuse", 404, b'{"error":"no"}')], {"script": [{"await": "close"}]})
        say(r.get("status") == 404 and r.get("body") == '{"error":"no"}' and r.get("ws") is None,
            "a refused handshake records the status and body, and no session")
        # (h) assemble: the same effects helpers as capture.py, plus the ws block
        import shutil
        import tempfile
        w = tempfile.mkdtemp()
        try:
            b, a = os.path.join(w, "before"), os.path.join(w, "after")
            os.makedirs(b); os.makedirs(a)
            json.dump({"requests": 1}, open(os.path.join(b, "usage.json"), "w"))
            json.dump({"requests": 2}, open(os.path.join(a, "usage.json"), "w"))
            open(os.path.join(b, "metrics.txt"), "w").write("m 1\n"); open(os.path.join(a, "metrics.txt"), "w").write("m 3\n")
            json.dump({"items": []}, open(os.path.join(b, "audit.json"), "w")); json.dump({"items": []}, open(os.path.join(a, "audit.json"), "w"))
            ws_path = os.path.join(w, "ws.json")
            json.dump({"status": 101, "headers": {"upgrade": "websocket"}, "body": "", "dialect": "echo", "key": wsframe.SAMPLE_KEY,
                       "ws": {"accept_ok": True, "frames": [], "close": {"by": "client", "code": 1000, "reason": ""}}}, open(ws_path, "w"))
            cap = assemble(ws_path, b, a, [])
            say(cap["effects"] == {"usage": {"requests": 1}, "metrics": {"m": 2}, "audit": {"added": 0, "items": []}, "egress": []}
                and cap["ws"]["dialect"] == "echo" and cap["status"] == 101, "assemble records the deltas capture.py would, and the ws block")
        finally:
            shutil.rmtree(w, ignore_errors=True)
    finally:
        srv.close()
    print(f"\ncapture-ws selftest: {'GREEN' if not fails else f'RED ({fails} failing)'}")
    return 1 if fails else 0


def main() -> int:
    args = sys.argv[1:]
    if args[:1] == ["--selftest"]:
        return selftest()
    if args[:1] == ["--drive"]:
        spec = json.load(open(args[1], encoding="utf-8")) if args[1:] else json.load(sys.stdin)
        try:
            out = drive(spec)
        except HarnessError as e:
            sys.stderr.write(f"capture-ws: harness_error: {e}\n")
            return 1
        except OSError as e:
            sys.stderr.write(f"capture-ws: harness_error: could not drive {spec.get('url')!r}: {e}\n")
            return 1
        print(json.dumps(out, separators=(",", ":"), sort_keys=True))
        return 0
    ws_path, before, after = args[0], args[1], args[2]
    print(json.dumps(assemble(ws_path, before, after, args[3:]), separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
