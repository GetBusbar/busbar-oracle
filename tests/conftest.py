# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Busbar Inc and contributors
"""Shared fixtures for the driver unit tests.

THE DRIVERS ARE FILES, NOT MODULES. `capture-ws.py`, `mock-upstream.py` and `diff-cells.py` are
shipped as executables with hyphens in their names, deliberately (see the driver contract in the
README), so they cannot be imported by name. `load()` imports one by path, exactly as the tool's own
`cli.py` resolves them by path -- the tests get at the real shipped file and never a copy.

EVERY SERVER IN HERE BINDS AN EPHEMERAL PORT (port 0, the kernel picks). No test hardcodes a port
and no two tests can collide, including under `pytest -n`. This is also the only thing that works:
the port block this work was assigned, 71000-71999, is not a TCP port range at all -- a TCP port is
16 bits, so 65535 is the ceiling and `bind()` raises `OverflowError: port must be 0-65535` on
anything above it. That is reported in the hand-back, not worked around silently.
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import time

import pytest

TOOL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "busbar_oracle")


def load(filename: str):
    """Import a shipped driver by path. The drivers put their own directory on sys.path so they can
    `import wsframe` / `from capture import ...`; that happens on exec, as it does in production."""
    mod_name = "oracle_" + filename.replace("-", "_").replace(".py", "")
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    path = os.path.join(TOOL_DIR, filename)
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def wsframe():
    return load("wsframe.py")


@pytest.fixture(scope="session")
def capture_ws():
    return load("capture-ws.py")


@pytest.fixture(scope="session")
def normalize():
    return load("normalize.py")


@pytest.fixture(scope="session")
def diff_cells():
    return load("diff-cells.py")


@pytest.fixture
def scripted_server(capture_ws):
    """A one-connection WebSocket server following a plan, on an ephemeral port.

    It is `capture-ws.py`'s OWN `_serve_once`, not a second implementation: the point of these tests
    is what the driver records, and a hand-rolled peer here would be one more thing that could be
    wrong. Returns (port, run) where `run(plan, spec_extra)` drives one session and hands back the
    driver's recording -- or `{"harness_error": ...}` when the driver refused to record one."""
    import threading

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    port = srv.getsockname()[1]
    seen: dict = {}

    def run(plan, spec_extra=None, path="/v1/realtime?model=m"):
        seen.clear()

        def serve():
            try:
                seen.update(capture_ws._serve_once(srv, plan))
            except (EOFError, OSError, ValueError) as e:
                seen.update({"peer_error": f"{type(e).__name__}: {e}"})

        t = threading.Thread(target=serve, daemon=True)
        t.start()
        spec = {"url": f"ws://127.0.0.1:{port}{path}", "timeout_secs": 3, **(spec_extra or {})}
        try:
            got = capture_ws.drive(spec)
        except capture_ws.HarnessError as e:
            got = {"harness_error": str(e)}
        t.join(5)
        got["_peer"] = dict(seen)
        return got

    try:
        yield port, run
    finally:
        srv.close()


@pytest.fixture(scope="module")
def mock_upstream(tmp_path_factory):
    """The real `mock-upstream.py`, in a subprocess on an ephemeral port, driven through its control
    file -- the same way record.sh runs it. Spawned as a process rather than imported because that
    IS its interface: a port, a marker and a control file.

    Yields (port, set_control) where `set_control(verb_or_None)` writes/clears the control file with
    the atomic rename record.sh uses, so a torn read can never be mistaken for 'no outage'."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    ctl = str(tmp_path_factory.mktemp("mock") / "control.json")
    log = open(str(tmp_path_factory.mktemp("mocklog") / "mock.log"), "w+")
    proc = subprocess.Popen([sys.executable, os.path.join(TOOL_DIR, "mock-upstream.py"), str(port), "MARKER", ctl],
                            stdout=log, stderr=subprocess.STDOUT)

    def set_control(verb):
        if verb is None:
            try:
                os.unlink(ctl)
            except FileNotFoundError:
                pass
            return
        with open(ctl + ".tmp", "w") as f:
            f.write(verb if isinstance(verb, str) else json.dumps(verb))
        os.replace(ctl + ".tmp", ctl)

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            if proc.poll() is not None:
                log.seek(0)
                raise RuntimeError(f"mock-upstream died on startup:\n{log.read()}") from None
            time.sleep(0.1)
    else:
        raise RuntimeError("mock-upstream never came up")
    try:
        yield port, set_control
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
