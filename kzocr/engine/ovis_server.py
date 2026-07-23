"""OvisOCR2 (multimodal GGUF) local inference server manager.

OvisOCR2 ships as a GGUF model (+ a clip/mmproj projector). The supported
runtime in this environment is the llama.cpp ``llama-server`` binary, which
exposes an OpenAI-compatible ``/v1/chat/completions`` API that accepts an
image_url content part. This module manages the subprocess lifecycle:

    server = OvisServer(model_path=..., mmproj_path=..., port=...)
    server.start()                 # spawn + wait until /v1/models answers
    base_url = server.base_url     # e.g. http://127.0.0.1:18088/v1
    server.stop()                  # graceful terminate

Default paths point at the locally available artifacts on ZFS400 and the
llama.cpp build under /home/keen. All of these are overridable via constructor
args or the ``KZOCR_LLAMA_SERVER_BIN`` env var.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
import urllib.request

_DEFAULT_LLAMA_SERVER = "/home/keen/llama.cpp/build/bin/llama-server"


class OvisServer:
    """Manage a ``llama-server`` subprocess serving an OvisOCR2 GGUF model."""

    def __init__(
        self,
        model_path: str,
        mmproj_path: str,
        port: int,
        llama_server_bin: str | None = None,
        n_threads: int = 12,
        n_ctx: int = 8192,
        verbose: bool = False,
    ) -> None:
        self.model_path = model_path
        self.mmproj_path = mmproj_path
        self.port = port
        self.llama_server_bin = (
            llama_server_bin
            or os.environ.get("KZOCR_LLAMA_SERVER_BIN")
            or _DEFAULT_LLAMA_SERVER
        )
        self.n_threads = n_threads
        self.n_ctx = n_ctx
        self.verbose = verbose
        self._proc: subprocess.Popen | None = None
        self._start_elapsed = 0.0
        self._reused = False
        self.base_url = f"http://127.0.0.1:{port}/v1"

    def _probe_port(self) -> bool:
        """Return True if a llama-server already answers /v1/models on our port."""
        try:
            req = urllib.request.Request(f"{self.base_url}/models")
            urllib.request.urlopen(req, timeout=3)
            return True
        except Exception:
            return False

    def start(self, timeout: int = 180) -> float:
        """Spawn the server and block until it answers /v1/models.

        Returns the startup wall-clock time in seconds. If a server already
        answers on the target port (externally managed or left over from a
        previous run), it is reused instead of spawning a second instance,
        which avoids port collisions and a redundant multi-minute model load.
        """
        if self._proc is not None:
            return 0.0
        if self._probe_port():
            self._reused = True
            self._start_elapsed = 0.0
            return 0.0
        cmd = [
            self.llama_server_bin,
            "-m", self.model_path,
            "--mmproj", self.mmproj_path,
            "--host", "127.0.0.1",
            "--port", str(self.port),
            "-c", str(self.n_ctx),
            "-t", str(self.n_threads),
        ]
        if not self.verbose:
            cmd.append("--no-webui")
        t0 = time.time()
        self._proc = subprocess.Popen(
            cmd,
            stdout=None if self.verbose else subprocess.DEVNULL,
            stderr=None if self.verbose else subprocess.DEVNULL,
            start_new_session=True,
        )
        self._reused = False
        for _ in range(timeout):
            time.sleep(1)
            if self.is_running():
                self._start_elapsed = time.time() - t0
                return self._start_elapsed
        self.stop()
        raise RuntimeError(f"OvisServer on port {self.port} failed to start within {timeout}s")

    def is_running(self) -> bool:
        if self._proc is None:
            return False
        if self._proc.poll() is not None:
            return False
        return self._probe_port()

    def stop(self) -> None:
        if self._proc is None:
            return
        if self._reused:
            # Reused an externally managed / leftover server: do not kill it.
            self._proc = None
            return
        try:
            # Kill the whole process group (start_new_session=True) so the
            # llama-server child does not survive if our parent is SIGTERM'd.
            pgid = os.getpgid(self._proc.pid)
            os.killpg(pgid, signal.SIGTERM)
            self._proc.wait(timeout=10)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        self._proc = None
