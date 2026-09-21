"""The guard in conftest.py is itself tested: it must block the outside world and allow loopback."""

import socket

import pytest


def test_the_guard_refuses_outside_addresses_but_allows_loopback():
    with pytest.raises(RuntimeError, match="tried to reach"):
        socket.create_connection(("93.184.216.34", 80), timeout=1)
    with pytest.raises(RuntimeError, match="tried to reach"):
        socket.getaddrinfo("example.com", 80)
    assert socket.getaddrinfo("127.0.0.1", 80)  # loopback resolution is allowed
