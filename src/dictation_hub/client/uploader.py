"""Stream a live recording to the dictation server while it is being made."""
import queue
import struct
import threading
from typing import Optional

import requests

_ABORT = object()


class UploadAborted(RuntimeError):
    """The upload was cancelled before the recording ended."""


def streaming_wav_header(rate: int = 16000, channels: int = 1, width: int = 2) -> bytes:
    """WAV header for a stream of unknown length (sizes set to 0xFFFFFFFF)."""
    unknown = struct.pack("<I", 0xFFFFFFFF)
    fmt = struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * channels * width, channels * width, width * 8)
    return b"RIFF" + unknown + b"WAVE" + b"fmt " + fmt + b"data" + unknown


def server_is_up(base_url: str, timeout: float = 0.5) -> bool:
    try:
        return requests.get(f"{base_url}/health", timeout=timeout).ok
    except requests.RequestException:
        return False


class ServerUpload:
    """Recorder listener that POSTs PCM blocks to the server as they arrive.

    Call it with each block, then ``None`` to finish (the server then
    transcribes and answers), or call ``abort()`` to drop the connection so
    the server discards what it received.
    """

    def __init__(self, url: str, response_timeout: float = 600.0):
        self.url = url
        self.response_timeout = response_timeout
        self._queue: "queue.Queue" = queue.Queue()
        self._session = requests.Session()
        self._result: Optional[dict] = None
        self._error: Optional[BaseException] = None
        self._aborted = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def __call__(self, block: Optional[bytes]) -> None:
        self._queue.put(block)

    def abort(self) -> None:
        self._aborted = True
        self._queue.put(_ABORT)

    def _body(self):
        yield streaming_wav_header()
        while True:
            item = self._queue.get()
            if item is None:
                return
            if item is _ABORT:
                raise UploadAborted()
            yield item

    def _run(self) -> None:
        try:
            resp = self._session.post(
                self.url, data=self._body(), timeout=(5, self.response_timeout)
            )
            resp.raise_for_status()
            self._result = resp.json()
        except BaseException as e:  # reported by result()
            self._error = e
        finally:
            self._session.close()  # drops the socket, so an abort reaches the server

    def result(self, timeout: Optional[float] = None) -> dict:
        self._thread.join(timeout)
        if self._aborted:
            raise UploadAborted()
        if self._thread.is_alive():
            raise TimeoutError("No answer from the dictation server.")
        if self._error is not None:
            raise self._error
        return self._result
