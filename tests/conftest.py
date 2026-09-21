"""Test-wide guarantees.

No test may reach beyond this machine. That is enforced here, not promised: an attempt to connect to,
or resolve, anything that is not loopback fails immediately with a clear message. (Loopback stays open
because asyncio, and so FastAPI's TestClient, opens an in-process socket pair on Windows.)
HTTP to the outside world is replaced by httpx.MockTransport, the LLM by fakes, and time by an injected clock.
"""

import ipaddress
import socket

import pytest


def _is_loopback(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode()
    if host in ("localhost", "", None):
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def nothing_leaves_this_machine(monkeypatch):
    real_connect, real_connect_ex, real_getaddrinfo = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def refuse(target):
        raise RuntimeError(f"a test tried to reach {target!r}; use httpx.MockTransport or a fake")

    def guarded_connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_loopback(address[0]):
            refuse(address)
        return real_connect(self, address)

    def guarded_connect_ex(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_loopback(address[0]):
            refuse(address)
        return real_connect_ex(self, address)

    def guarded_getaddrinfo(host, *args, **kwargs):
        if not _is_loopback(host):
            refuse(host)
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)


@pytest.fixture(autouse=True)
def default_to_llm_triage(monkeypatch):
    """`pia` chooses Jev automatically when a key and a profile exist. Tests must never depend on the developer's
    real files, so they default to the LLM triage; Jev tests ask for it explicitly with --triage jev."""
    monkeypatch.setenv("PIA_TRIAGE", "llm")
