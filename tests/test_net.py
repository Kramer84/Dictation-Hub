"""The client caps its TCP packet size, so a network path that drops
full-size packets (a Tailscale MTU black hole) does not stall uploads."""
import socket
import threading

import pytest

from dictation_hub.client import net

pytestmark = pytest.mark.skipif(not hasattr(socket, "TCP_MAXSEG"), reason="no TCP_MAXSEG on this platform")


@pytest.fixture
def mss_server():
    """A one-request HTTP server that records the segment size the client asked for."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    seen = []

    def serve():
        conn, _ = srv.accept()
        seen.append(conn.getsockopt(socket.IPPROTO_TCP, socket.TCP_MAXSEG))
        conn.recv(65536)
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
        conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.getsockname()[1]}", seen
    thread.join(timeout=5)
    srv.close()


def test_connections_use_small_packets_by_default(mss_server, monkeypatch):
    url, seen = mss_server
    monkeypatch.delenv("DICTATION_TCP_MSS", raising=False)
    assert net.session().get(url, timeout=5).text == "ok"
    assert 0 < seen[0] <= net.DEFAULT_MSS


def test_packet_size_comes_from_the_config(mss_server, monkeypatch):
    url, seen = mss_server
    monkeypatch.setenv("DICTATION_TCP_MSS", "600")
    net.session().get(url, timeout=5)
    assert seen[0] <= 600


def test_zero_turns_the_cap_off(mss_server, monkeypatch):
    url, seen = mss_server
    monkeypatch.setenv("DICTATION_TCP_MSS", "0")
    net.session().get(url, timeout=5)
    assert seen[0] > net.DEFAULT_MSS  # loopback allows far larger packets
