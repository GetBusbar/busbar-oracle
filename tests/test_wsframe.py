# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Busbar Inc and contributors
"""wsframe: the RFC 6455 framing both ends of a recorded session share.

Everything here is about the two properties a recording depends on -- that the bytes are the RFC's
bytes, and that nothing volatile leaks in.
"""
from __future__ import annotations

import socket
import threading

import pytest


def roundtrip(wsframe, a, b, data):
    """Send on `a`, read on `b`. The send runs on its own thread because a socketpair's kernel
    buffer is a few KiB and the large-payload cases are longer than it -- a straight sendall would
    block forever on the one reader being the next line of this same thread."""
    t = threading.Thread(target=a.sendall, args=(data,), daemon=True)
    t.start()
    out = wsframe.recv_frame(b)
    t.join(5)
    return out


@pytest.fixture
def pair():
    a, b = socket.socketpair()
    try:
        yield a, b
    finally:
        a.close()
        b.close()


def test_the_rfc_sample_key_yields_the_rfc_sample_accept(wsframe):
    """RFC 6455 §1.3's own worked example, so a reader can verify the handshake by hand."""
    assert wsframe.accept_key(wsframe.SAMPLE_KEY) == wsframe.SAMPLE_ACCEPT
    assert wsframe.SAMPLE_KEY == "dGhlIHNhbXBsZSBub25jZQ=="
    assert wsframe.SAMPLE_ACCEPT == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


@pytest.mark.parametrize("n", [0, 1, 125, 126, 127, 65535, 65536])
def test_a_masked_client_frame_round_trips_at_every_length_class(wsframe, pair, n):
    """7-bit, 16-bit and 64-bit length headers, and both sides of each boundary."""
    a, b = pair
    payload = bytes(i & 0xFF for i in range(n))
    op, got, fin = roundtrip(wsframe, a, b, wsframe.encode_frame(wsframe.OP_BINARY, payload, wsframe.CLIENT_MASK))
    assert (op, got, fin) == (wsframe.OP_BINARY, payload, True)


@pytest.mark.parametrize("n", [0, 1, 125, 126, 65535, 65536])
def test_an_unmasked_server_frame_round_trips_at_every_length_class(wsframe, pair, n):
    a, b = pair
    payload = bytes(i & 0xFF for i in range(n))
    op, got, fin = roundtrip(wsframe, b, a, wsframe.encode_frame(wsframe.OP_TEXT, payload))
    assert (op, got, fin) == (wsframe.OP_TEXT, payload, True)


def test_the_client_mask_is_a_constant_so_the_wire_bytes_are_reproducible(wsframe):
    """The RFC wants an unpredictable mask to stop a hostile page poisoning a cache, which is not a
    threat a harness poses to its own mock. A constant is what makes two recordings byte-identical."""
    one = wsframe.encode_frame(wsframe.OP_TEXT, b"hello", wsframe.CLIENT_MASK)
    assert one == wsframe.encode_frame(wsframe.OP_TEXT, b"hello", wsframe.CLIENT_MASK)
    assert one[2:6] == wsframe.CLIENT_MASK          # the key is on the wire, where the RFC puts it
    assert one[6:] != b"hello"                      # and the payload really is masked with it


def test_a_fragmented_message_is_joined_onto_its_opening_opcode(wsframe, pair):
    """Fragmentation is transport, not a contract: the recorder records the message the peer meant."""
    a, b = pair
    a.sendall(wsframe.encode_frame(wsframe.OP_TEXT, b"hel", wsframe.CLIENT_MASK, fin=False)
              + wsframe.encode_frame(wsframe.OP_CONT, b"lo", wsframe.CLIENT_MASK))
    assert wsframe.recv_message(b) == (wsframe.OP_TEXT, b"hello")


def test_a_continuation_frame_with_no_opener_is_an_error_not_a_guess(wsframe, pair):
    a, b = pair
    a.sendall(wsframe.encode_frame(wsframe.OP_TEXT, b"x", wsframe.CLIENT_MASK, fin=False)
              + wsframe.encode_frame(wsframe.OP_TEXT, b"y", wsframe.CLIENT_MASK))
    with pytest.raises(ValueError):
        wsframe.recv_message(b)


def test_a_close_frame_carries_its_code_and_reason(wsframe, pair):
    a, b = pair
    b.sendall(wsframe.encode_close(1011, "why"))
    op, payload, _ = wsframe.recv_frame(a)
    assert op == wsframe.OP_CLOSE
    assert wsframe.decode_close(payload) == (1011, "why")


def test_an_empty_close_payload_is_no_code_never_1005(wsframe):
    """1005 is a status the RFC reserves for APIs to report; no peer can put it on the wire. A close
    with no code recorded as 1005 would be a byte the door never sent."""
    assert wsframe.decode_close(b"") == (None, "")
    assert wsframe.encode_close(None) == wsframe.encode_frame(wsframe.OP_CLOSE, b"")


def test_a_peer_that_vanished_without_a_close_frame_raises_for_the_caller_to_record(wsframe):
    """The `cut` arm. Hiding this would record 'the session ended' where the truth is 'the upstream
    disappeared mid-session', which are different products."""
    a, b = socket.socketpair()
    b.close()
    try:
        with pytest.raises(EOFError):
            wsframe.recv_frame(a)
    finally:
        a.close()


def test_no_control_frame_is_answered_for_you(wsframe, pair):
    """recv_frame hands back ping, pong and close in arrival order and answers none of them: a
    recorder that silently answered a ping would be hiding a frame the other side sent."""
    a, b = pair
    b.sendall(wsframe.encode_frame(wsframe.OP_PING, b"hb")
              + wsframe.encode_frame(wsframe.OP_PONG, b"hb")
              + wsframe.encode_close(1000, ""))
    assert [wsframe.recv_frame(a)[0] for _ in range(3)] == [wsframe.OP_PING, wsframe.OP_PONG, wsframe.OP_CLOSE]
    # ...and nothing came back the other way: no pong was generated on our behalf
    b.setblocking(False)
    with pytest.raises(BlockingIOError):
        b.recv(1)


def test_an_http_head_parses_to_status_reason_and_lowercased_joined_headers(wsframe):
    st, reason, hdrs = wsframe.parse_status_line(
        b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nX-A: 1\r\nX-A: 2\r\n\r\n")
    assert (st, reason) == (101, "Switching Protocols")
    assert hdrs == {"upgrade": "websocket", "x-a": "1, 2"}


def test_the_shipped_selftest_is_green(wsframe):
    """The `--selftest` a maintainer runs on a machine with nothing installed stays green too."""
    assert wsframe.selftest() == 0
