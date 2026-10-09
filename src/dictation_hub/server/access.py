"""Who may talk to the server: localhost and Tailscale peers by default.

A second line of defense behind the bind address: even when the server listens
on every interface (HOST_BIND_IP="0.0.0.0"), Wi-Fi, LAN and Docker clients are
refused. HOST_ALLOWED_NETWORKS (comma-separated CIDRs) replaces the default.
"""
import ipaddress
import os
from typing import List, Optional

from starlette.responses import JSONResponse

# Loopback, Tailscale's IPv4 range (CGNAT 100.64.0.0/10) and its IPv6 prefix.
DEFAULT_NETWORKS = "127.0.0.0/8,::1/128,100.64.0.0/10,fd7a:115c:a1e0::/48"


def allowed_networks(spec: Optional[str]) -> List[ipaddress._BaseNetwork]:
    return [ipaddress.ip_network(n.strip()) for n in (spec or DEFAULT_NETWORKS).split(",") if n.strip()]


def client_allowed(ip: Optional[str], networks) -> bool:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(address in network for network in networks)


class AllowedClientsOnly:
    """ASGI middleware refusing HTTP (403) and WebSocket clients outside ``networks``."""

    def __init__(self, app, networks=None):
        self.app = app
        self.networks = networks or allowed_networks(os.environ.get("HOST_ALLOWED_NETWORKS"))

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            client = scope.get("client") or (None, None)
            if not client_allowed(client[0], self.networks):
                if scope["type"] == "http":
                    response = JSONResponse({"error": "Client not allowed."}, status_code=403)
                    await response(scope, receive, send)
                else:
                    await send({"type": "websocket.close", "code": 1008})
                return
        await self.app(scope, receive, send)
