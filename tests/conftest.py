"""Test-wide guarantees.

The README says no test touches the network. That is enforced here, not promised: any attempt
to open a real socket or resolve a hostname during a test fails immediately with a clear message.
HTTP is replaced by httpx.MockTransport, the LLM by fakes, and time by an injected clock.
"""

import socket

import pytest


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise RuntimeError("a test tried to use the real network; use httpx.MockTransport or a fake")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
