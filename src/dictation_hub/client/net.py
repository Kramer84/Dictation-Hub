"""HTTP sessions for talking to the dictation server.

Some paths between client and server (hotel wifi, a 4G dongle) silently drop
packets larger than about 1216 bytes, below the 1280 that Tailscale sends.
Small requests then work while uploads and longer answers stall forever. The
client therefore asks for smaller TCP segments on its own connections, which
caps the packets it sends and those the server sends back.

DICTATION_TCP_MSS sets the segment size in bytes; 0 turns the cap off.
"""
import os
import socket

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection

DEFAULT_MSS = 1100  # bytes; packets of 1140, well under the 1216 seen to pass


def tcp_mss() -> int:
    try:
        return int(os.environ.get("DICTATION_TCP_MSS", DEFAULT_MSS))
    except ValueError:
        return DEFAULT_MSS


class _SmallSegments(HTTPAdapter):
    def __init__(self, mss: int, **kwargs):
        self.mss = mss
        super().__init__(**kwargs)

    def init_poolmanager(self, *args, **kwargs):
        kwargs["socket_options"] = HTTPConnection.default_socket_options + [
            (socket.IPPROTO_TCP, socket.TCP_MAXSEG, self.mss)
        ]
        super().init_poolmanager(*args, **kwargs)


def session() -> requests.Session:
    s = requests.Session()
    mss = tcp_mss()
    if mss > 0 and hasattr(socket, "TCP_MAXSEG"):
        adapter = _SmallSegments(mss)
        s.mount("http://", adapter)
        s.mount("https://", adapter)
    return s
