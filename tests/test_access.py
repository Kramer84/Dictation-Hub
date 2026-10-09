import pytest
from fastapi.testclient import TestClient

from dictation_hub.server import access, main


@pytest.mark.parametrize("ip", ["127.0.0.1", "::1", "100.71.34.52", "100.101.2.3", "fd7a:115c:a1e0::6601:228e"])
def test_tailscale_and_localhost_clients_are_allowed(ip):
    assert access.client_allowed(ip, access.allowed_networks(None))


@pytest.mark.parametrize("ip", ["10.50.62.105", "172.18.0.2", "192.168.1.20", "2a02:8440::1", "testclient", None])
def test_other_clients_are_refused(ip):
    assert not access.client_allowed(ip, access.allowed_networks(None))


def test_allowed_networks_can_be_configured():
    nets = access.allowed_networks("192.168.1.0/24")
    assert access.client_allowed("192.168.1.20", nets)
    assert not access.client_allowed("100.71.34.52", nets)


def test_server_refuses_a_lan_client():
    lan = TestClient(main.app, client=("10.50.62.105", 50000))
    assert lan.get("/health").status_code == 403
    tailnet = TestClient(main.app, client=("100.80.1.2", 50000))
    assert tailnet.get("/health").status_code == 200


def test_server_refuses_a_lan_websocket():
    from starlette.websockets import WebSocketDisconnect

    lan = TestClient(main.app, client=("10.50.62.105", 50000))
    with pytest.raises(WebSocketDisconnect):
        with lan.websocket_connect("/ws/transcribe?profile=standard") as ws:
            ws.receive_text()
