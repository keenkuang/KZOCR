"""
PaddleOCR-VL-1.6 Adapter for TCM OCR System.

Uses PaddleOCR-VL-1.6 GGUF VLM via llama.cpp server for full-page OCR.
Requires llama-server running with the PaddleOCR-VL model and mmproj loaded,
OR starts it automatically in the background.

Inference via OpenAI-compatible HTTP API.
"""

import base64
import io
import logging
import subprocess
import time
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import requests

logger = logging.getLogger(__name__)

_MODEL_DIR = Path("/home/keen/models/paddleocr-vl-1.6-gguf-official/PaddlePaddle/PaddleOCR-VL-1___6-GGUF")
_LLAMA_SERVER = Path("/home/keen/llama.cpp/build/bin/llama-server")

_DEFAULT_PORT = 18080
_MODEL_FILE = "PaddleOCR-VL-1.6-GGUF.gguf"
_MMPROJ_FILE = "PaddleOCR-VL-1.6-GGUF-mmproj.gguf"


class PaddleOCRVl16Adapter:
    """Adapter for PaddleOCR-VL-1.6 VLM via llama.cpp server.

    Processes full-page images and returns recognized text using
    the VLM's built-in document understanding capability.

    Attributes:
        host (str): llama-server host.
        port (int): llama-server port.
        server_proc: Subprocess handle if server was auto-started.
    """

    def __init__(
        self,
        char_dict: Optional[Dict[str, str]] = None,
        device: str = 'cpu',
        host: str = '127.0.0.1',
        port: int = _DEFAULT_PORT,
        auto_start: bool = True,
    ) -> None:
        """Initialize the PaddleOCR-VL-1.6 adapter.

        Args:
            char_dict: Custom character dictionary (unused).
            device: Computing device ('cpu').
            host: llama-server host address.
            port: llama-server port.
            auto_start: Whether to start llama-server automatically.
        """
        self.host = host
        self.port = port
        self.server_proc: Optional[subprocess.Popen] = None
        self._closed = False

        if auto_start:
            self._start_server()
        else:
            self._wait_ready()

    def _start_server(self) -> None:
        """Start llama-server with the PaddleOCR-VL model in background."""
        if self._is_server_running():
            logger.info("llama-server already running on port %d", self.port)
            return

        model_path = _MODEL_DIR / _MODEL_FILE
        mmproj_path = _MODEL_DIR / _MMPROJ_FILE

        if not model_path.exists():
            raise FileNotFoundError(f"Model not found: {model_path}")
        if not mmproj_path.exists():
            raise FileNotFoundError(f"MMProj not found: {mmproj_path}")

        cmd = [
            str(_LLAMA_SERVER),
            "-m", str(model_path),
            "--mmproj", str(mmproj_path),
            "--port", str(self.port),
            "--no-ui",
            "--host", self.host,
            "-t", "10",
            "-c", "131072",  # context window
        ]

        logger.info("Starting llama-server: %s", " ".join(cmd))
        self.server_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Wait for server to be ready
        self._wait_ready(timeout=60)

    def _is_server_running(self) -> bool:
        """Check if llama-server is responding."""
        try:
            resp = requests.get(f"http://{self.host}:{self.port}/v1/models", timeout=2)
            return resp.status_code == 200
        except Exception:
            return False

    def _wait_ready(self, timeout: int = 60) -> None:
        """Wait for llama-server to become ready."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._is_server_running():
                logger.info("llama-server is ready on port %d", self.port)
                return
            time.sleep(1)
        raise RuntimeError(
            f"llama-server did not start within {timeout}s on port {self.port}. "
            f"Start manually: {_LLAMA_SERVER} -m {_MODEL_DIR / _MODEL_FILE} "
            f"--mmproj {_MODEL_DIR / _MMPROJ_FILE} --port {self.port}"
        )

    def recognize_page(self, page_img: np.ndarray, prompt: str = None) -> str:
        """Recognize a full page image using the VLM.

        Args:
            page_img: Full page image as numpy array (H, W, C) in RGB.
            prompt: Optional custom prompt. Defaults to a general OCR prompt.

        Returns:
            Recognized text from the image.
        """
        if self._closed:
            raise RuntimeError("PaddleOCRVl16Adapter has been closed.")

        from PIL import Image

        pil_img = Image.fromarray(page_img)
        # llama-server's multimodal encoder rejects PNG ("encoder png not available");
        # re-encode as JPEG before base64-encoding and sending.
        buf_io = io.BytesIO()
        pil_img.save(buf_io, format="JPEG")
        buf = buf_io.getvalue()
        img_b64 = base64.b64encode(buf).decode("utf-8")

        system_prompt = """你是一个中文古籍 OCR 工具。逐行识别图片中的文字，输出规则：

1. 严格逐行输出：图片中的每一行对应输出中的一行，绝不合并行也绝不拆分行。图片换行处输出必须换行。保留原文换行和分段格式。
2. 忠于原文：不修改任何文字、药材名、剂量、标点。不得自行扩充、改写、总结。
3. 字段分隔符统一使用中文冒号"："。例如"来源：xxx""组成：xxx"而不是其他符号。
4. 绝对不要输出页眉、页脚、页码、书名、或任何非正文装饰文字（如 R<br> 等标记）。
5. 不要对括号进行任何转义，原文是什么标点就输出什么标点。
6. 只输出图片中的正文，禁止输出自我介绍、解释、总结或补充说明。"""
        user_prompt = prompt or "逐行输出图片中所有文字，按原文顺序，保留标点和换行。"

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                    },
                    {"type": "text", "text": user_prompt},
                ],
            },
        ]

        try:
            resp = requests.post(
                f"http://{self.host}:{self.port}/v1/chat/completions",
                json={
                    "model": "paddleocr-vl",
                    "messages": messages,
                    "temperature": 0,
                    "max_tokens": 1536,
                    # 断重复生成死循环的最小改动（temp=0 下 PaddleOCR-VL 会无限重复，
                    # 实测甜点：repeat_penalty=1.2 / repeat_last_n=32 / frequency_penalty=0；
                    # 不要叠 frequency_penalty，否则大窗口会把整段正文压平导致过早 EOS）
                    "repeat_penalty": 1.2,
                    "repeat_last_n": 32,
                    "frequency_penalty": 0.0,
                },
                timeout=600,
            )
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            return text.strip()
        except Exception as e:
            logger.error("PaddleOCR-VL recognition failed: %s", e)
            return ""

    def recognize(self, line_img: np.ndarray) -> str:
        """Delegate to recognize_page (PaddleOCR-VL is a full-page model).

        If the input is a cropped line, it's treated as a full-page image.
        """
        return self.recognize_page(line_img)

    def close(self) -> None:
        """Stop llama-server if auto-started."""
        if self._closed:
            return
        try:
            if self.server_proc is not None:
                self.server_proc.terminate()
                self.server_proc.wait(timeout=5)
                logger.info("llama-server stopped")
        except Exception as e:
            logger.error("Error stopping llama-server: %s", e)
        finally:
            self._closed = True

    def __del__(self) -> None:
        self.close()

    def __enter__(self) -> 'PaddleOCRVl16Adapter':
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: None) -> None:
        self.close()
