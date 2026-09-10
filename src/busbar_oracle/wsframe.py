#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Busbar Inc and contributors
"""RFC 6455 on a plain socket, for the recorder and the mock — no third-party package, on purpose.

The oracle judges a workspace it must share nothing with (pyproject's `dependencies = []`), and a
WebSocket client library is exactly the kind of thing that could differ between the run that
recorded a cell and the run that replays it: a library that coalesces fragments, answers pings for
you, or picks a random `Sec-WebSocket-Key` per connection has already decided what a frame is
before the recorder sees it. The framing here is small enough to read against the RFC in one
sitting, and everything a recording depends on is FIXED and named:

  * the handshake key is the caller's (`capture-ws.py` uses RFC 6455 §1.3's own sample nonce, so the
    request bytes and the `Sec-WebSocket-Accept` a 101 must carry are both pure functions of the cell);
  * the client masking key is a constant (`CLIENT_MASK`). The RFC asks for an unpredictable key to
    defeat cache poisoning by hostile pages, which is not a threat a harness poses to its own mock;
    a constant makes the bytes on the wire identical run to run, and the mock records the UNMASKED
    payload either way;
  * no frame is answered for you: `recv_frame` hands back every frame — ping, pong, close — in the
    order it arrived, and the caller decides. A recorder that silently answered a ping would be
    hiding a frame the other side sent.

Shared by mock-upstream.py (server side: unmasked send, masked receive) and capture-ws.py (client
side: masked send, unmasked receive) so the two ends cannot drift on what a frame is. Imported the
way capture-concurrent.py imports capture.py — by directory, not by package — because the shipped
drivers are files, not modules.
"""
from __future__ import annotations

import base64
import hashlib
import socket
import struct
import threading

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
# The RFC's own worked example (§1.3): a key any reader of the standard can verify by hand.
SAMPLE_KEY = "dGhlIHNhbXBsZSBub25jZQ=="
SAMPLE_ACCEPT = "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
# A fixed client mask. Any 32-bit value is a valid mask (RFC 6455 §5.3); this one spells "orac".
CLIENT_MASK = b"orac"

OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA
OPCODE_NAMES = {OP_CONT: "continuation", OP_TEXT: "text", OP_BINARY: "binary",
                OP_CLOSE: "close", OP_PING: "ping", OP_PONG: "pong"}
OPCODES_BY_NAME = {v: k for k, v in OPCODE_NAMES.items()}


def accept_key(key: str) -> str:
    """`Sec-WebSocket-Accept` for a `Sec-WebSocket-Key` (RFC 6455 §4.2.2)."""
    return base64.b64encode(hashlib.sha1((key + GUID).encode("ascii")).digest()).decode("ascii")


def mask_bytes(payload: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % 4] for i, b in enumerate(payload))


def encode_frame(opcode: int, payload: bytes, mask: bytes | None = None, fin: bool = True) -> bytes:
    """One frame. `mask` set = a client frame (masked with exactly that key); None = a server frame."""
    head = bytes([(0x80 if fin else 0) | (opcode & 0x0F)])
    n = len(payload)
    mbit = 0x80 if mask else 0
    if n < 126:
        head += bytes([mbit | n])
    elif n < 65536:
        head += bytes([mbit | 126]) + struct.pack(">H", n)
    else:
        head += bytes([mbit | 127]) + struct.pack(">Q", n)
    if mask:
        return head + mask + mask_bytes(payload, mask)
    return head + payload


def encode_close(code: int | None, reason: str = "", mask: bytes | None = None) -> bytes:
    body = b"" if code is None else struct.pack(">H", code) + reason.encode("utf-8")
    return encode_frame(OP_CLOSE, body, mask)


def decode_close(payload: bytes) -> tuple[int | None, str]:
    """(code, reason) out of a close payload. An empty payload is a close with NO code (RFC 6455
    §5.5.1 allows it); the recorder writes that as null, never as 1005, which is a status a peer
    can never send on the wire."""
    if len(payload) < 2:
        return None, ""
    return struct.unpack(">H", payload[:2])[0], payload[2:].decode("utf-8", "replace")


def _read_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError("socket closed mid-frame" if buf else "socket closed")
        buf += chunk
    return buf


def recv_frame(sock: socket.socket) -> tuple[int, bytes, bool]:
    """The next frame: (opcode, payload, fin). Raises EOFError on a peer that went away without a
    close frame — which is a fact the caller records (the `cut` arm), not one this hides."""
    b0, b1 = _read_exact(sock, 2)
    fin = bool(b0 & 0x80)
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)
    n = b1 & 0x7F
    if n == 126:
        n = struct.unpack(">H", _read_exact(sock, 2))[0]
    elif n == 127:
        n = struct.unpack(">Q", _read_exact(sock, 8))[0]
    key = _read_exact(sock, 4) if masked else None
    payload = _read_exact(sock, n) if n else b""
    if key:
        payload = mask_bytes(payload, key)
    return opcode, payload, fin


def recv_message(sock: socket.socket) -> tuple[int, bytes]:
    """A whole message: continuation frames are joined onto the opcode that opened them. Control
    frames (close/ping/pong) are never fragmented and come back on their own. The result is the
    message as the peer meant it — fragmentation is transport, not a contract."""
    opcode, payload, fin = recv_frame(sock)
    while not fin:
        op2, more, fin = recv_frame(sock)
        if op2 != OP_CONT:
            raise ValueError(f"expected a continuation frame, got opcode {op2}")
        payload += more
    return opcode, payload


def read_http_head(sock: socket.socket) -> bytes:
    """The bytes of an HTTP message head up to and including the blank line."""
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise EOFError("socket closed before the HTTP head completed")
        buf += chunk
        if len(buf) > 1 << 20:
            raise ValueError("HTTP head over 1 MiB")
    return buf


def parse_status_line(head: bytes) -> tuple[int, str, dict]:
    """(status, reason, headers) from an HTTP response head. Header names are lowercased; a
    repeated header is joined with ', ' as RFC 9110 §5.3 allows."""
    lines = head.split(b"\r\n")
    parts = lines[0].decode("latin-1").split(" ", 2)
    status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    reason = parts[2] if len(parts) > 2 else ""
    headers: dict = {}
    for ln in lines[1:]:
        if not ln or b":" not in ln:
            continue
        k, v = ln.decode("latin-1").split(":", 1)
        k, v = k.strip().lower(), v.strip()
        headers[k] = f"{headers[k]}, {v}" if k in headers else v
    return status, reason, headers


def selftest() -> int:
    fails = 0

    def say(ok, what):
        nonlocal fails
        print(f"{'PASS' if ok else 'FAIL'}  {what}")
        if not ok:
            fails += 1

    say(accept_key(SAMPLE_KEY) == SAMPLE_ACCEPT, "the RFC 6455 §1.3 sample key yields the sample accept value")
    # round trips through a socket pair, both directions, every length class.
    #
    # THE SEND RUNS ON ITS OWN THREAD, and that is not tidiness. A socketpair's kernel buffer is a
    # few KiB (8 KiB on macOS); a straight `a.sendall(...)` of the 65535- and 65536-byte frames
    # fills it and blocks forever, because the only reader is this same thread, on the next line.
    # The selftest deadlocked there with its output still in stdout's block buffer, so it hung
    # SILENTLY -- it printed no PASS lines at all and had to be killed. A frame longer than one
    # buffer is exactly the case the 16- and 64-bit length headers exist for, so the fix is to keep
    # the payloads and let the writer run beside the reader, not to shrink the test to fit.
    def sender(sock, data):
        t = threading.Thread(target=sock.sendall, args=(data,), daemon=True)
        t.start()
        return t

    a, b = socket.socketpair()
    try:
        for n in (0, 1, 125, 126, 65535, 65536):
            payload = bytes(i & 0xFF for i in range(n))
            t = sender(a, encode_frame(OP_BINARY, payload, CLIENT_MASK))
            op, got, fin = recv_frame(b)
            t.join(5)
            say(op == OP_BINARY and got == payload and fin, f"a masked client frame of {n} bytes round-trips")
            t = sender(b, encode_frame(OP_TEXT, payload))
            op, got, fin = recv_frame(a)
            t.join(5)
            say(op == OP_TEXT and got == payload and fin, f"an unmasked server frame of {n} bytes round-trips")
        a.sendall(encode_frame(OP_TEXT, b"hel", CLIENT_MASK, fin=False) + encode_frame(OP_CONT, b"lo", CLIENT_MASK))
        say(recv_message(b) == (OP_TEXT, b"hello"), "a fragmented message is joined onto its opening opcode")
        b.sendall(encode_close(1011, "why"))
        op, payload, _ = recv_frame(a)
        say(op == OP_CLOSE and decode_close(payload) == (1011, "why"), "a close frame carries its code and reason")
        say(decode_close(b"") == (None, ""), "an empty close payload is a close with no code (null), never 1005")
        b.close()
        try:
            recv_frame(a)
            say(False, "a peer that vanished without a close frame was not reported")
        except EOFError:
            say(True, "a peer that vanished without a close frame raises EOFError for the caller to record")
    finally:
        a.close()
    st, reason, hdrs = parse_status_line(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nX-A: 1\r\nX-A: 2\r\n\r\n")
    say(st == 101 and reason == "Switching Protocols" and hdrs == {"upgrade": "websocket", "x-a": "1, 2"},
        "an HTTP head parses to status, reason and lowercased, joined headers")
    print(f"\nwsframe selftest: {'GREEN' if not fails else f'RED ({fails} failing)'}")
    return 1 if fails else 0


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
