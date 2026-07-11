"""
ShizhenGPT-7B-VL Adapter for TCM OCR System.

Uses ShizhenGPT-7B-VL GGUF VLM via llama.cpp server for full-page OCR.
Designed for TCM ancient text recognition with specialized medical knowledge.

Inference via OpenAI-compatible HTTP API (llama-server).
"""

import base64
import io
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import requests

logger = logging.getLogger(__name__)

# 剥离模型以 Markdown 代码块形式输出的围栏（```markdown / ```text / ``` 等）。
_FENCE_RE = re.compile(
    r"^\s*```[^\n`]*\n(.*?)\n```\s*$",
    re.DOTALL,
)
_LOOSE_FENCE_RE = re.compile(r"^\s*```[^\n`]*\n?|\n?```\s*$", re.DOTALL)
# 单独成行的围栏（含可选语言标识），如中间残留的 ```。
_STANDALONE_FENCE_RE = re.compile(r"^[ \t]*```[^\n`]*[ \t]*$", re.MULTILINE)


def _strip_code_fence(text: str) -> str:
    """去掉模型输出外层的 Markdown 代码围栏，返回纯文本。

    处理多种形态：
      ```markdown\n...\n```   /   ```\n...\n```   /   行首行尾残留的 ```
      /   正文中单独成行的 ```（模型提前闭合围栏后又接正文）。
    不会破坏正文内部的合法换行。
    """
    if not text:
        return text
    m = _FENCE_RE.match(text)
    if m:
        text = m.group(1)
    else:
        # 兜底：去掉行首的 ```lang 行与行尾的 ```
        text = _LOOSE_FENCE_RE.sub("", text)
    # 去掉所有单独成行的围栏（含中间残留的 ```），保留正文换行。
    text = _STANDALONE_FENCE_RE.sub("", text)
    return text.strip()

_MODEL_DIR = Path("/home/keen/下载")
_LLAMA_SERVER = Path("/home/keen/llama.cpp/build/bin/llama-server")

_DEFAULT_PORT = 18083
_MODEL_FILE = "ShizhenGPT-7B-VL.i1-Q4_K_M.gguf"
_MMPROJ_FILE = "ShizhenGPT-7B-VL.mmproj-Q8_0.gguf"

# 专用于中医古籍识别的系统提示词
_DEFAULT_SYSTEM_PROMPT = """请逐行逐字识别这张图片中的所有文字，忠于原文，不得自行扩充、改写内容，严格保留原文换行、分段格式布局。
图片是中医方剂书籍《秘方求真》的扫描件，简体中文横排，无繁体字。
书籍结构中章为某科秘验方，节为该科某病秘验方，小节为治疗某病的秘验方之一。每个方剂都独立成小节。
需要忽略内容：页眉、页脚、页码、书名"秘方求真"，和带有Rx标识的装饰，以及"某科秘验方"的竖排侧眉。
文本条目规则：图片内每小节仅会出现9类固定开头标识：来源、组成、用法、功用、方解、主治、加减、疗效、附记；原文有哪类就提取哪类，无需凑齐9项。
处理规则：所有9类标识文字后方紧跟的中文空心箭头位置全部使用全角空格填充后接续正文。
输出强制要求：
    - 只输出图片识别正文，禁止增加任何自我介绍、总结、补充说明；
    - 不修改原文方剂、药材、剂量，不增删字句；
    - 图片中每一行文字对应输出中的一行，绝不合并行，也绝不拆分行；
    - 图片中换行的地方，输出必须换行；图片中同一行的文字，输出必须写在同一行
    - 不要按语义重排、合并或拆分行，完全按图片的物理排版逐行输出"""


class ShizhenGPTAdapter:
    """Adapter for ShizhenGPT-7B-VL VLM via llama.cpp server.

    Specialized for TCM ancient text recognition with medical knowledge.

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
        self.host = host
        self.port = port
        self.server_proc: Optional[subprocess.Popen] = None
        self._closed = False

        if auto_start:
            self._start_server()
        else:
            self._wait_ready()

    def _start_server(self) -> None:
        if self._is_server_running():
            logger.info("ShizhenGPT server already running on port %d", self.port)
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
            "-c", "8192",
            "-t", "10",
        ]

        logger.info("Starting ShizhenGPT server: %s", " ".join(cmd))
        self.server_proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._wait_ready(timeout=120)

    def _is_server_running(self) -> bool:
        try:
            resp = requests.get(f"http://{self.host}:{self.port}/v1/models", timeout=2)
            return resp.status_code == 200
        except Exception:
            return False

    def _wait_ready(self, timeout: int = 120) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._is_server_running():
                logger.info("ShizhenGPT server ready on port %d", self.port)
                return
            time.sleep(2)
        raise RuntimeError(
            f"ShizhenGPT server did not start within {timeout}s. "
            f"Manual: {_LLAMA_SERVER} -m {_MODEL_DIR / _MODEL_FILE} "
            f"--mmproj {_MODEL_DIR / _MMPROJ_FILE} --port {self.port}"
        )

    def recognize_page(self, page_img: np.ndarray, prompt: str = None) -> str:
        """Recognize a full page image using ShizhenGPT VLM.

        Args:
            page_img: Full page image as numpy array (H, W, C) in RGB.
            prompt: Optional custom prompt. Defaults to TCM-specific prompt.

        Returns:
            Recognized text from the image.
        """
        if self._closed:
            raise RuntimeError("ShizhenGPTAdapter has been closed.")

        from PIL import Image
        pil_img = Image.fromarray(page_img)
        # llama.cpp's image encoder only supports JPEG (PNG -> "encoder png not available").
        # Re-encode the array as JPEG before base64-encoding and sending.
        buf = io.BytesIO()
        pil_img.save(buf, format="JPEG")
        img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        user_prompt = prompt or "完整识别图片中所有文字，按原文顺序输出。保留异体字、药名、剂量，不要篡改。"
        messages = [
            {"role": "system", "content": _DEFAULT_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                    {"type": "text", "text": user_prompt},
                ],
            },
        ]

        try:
            resp = requests.post(
                f"http://{self.host}:{self.port}/v1/chat/completions",
                json={
                    "model": "shizhengpt",
                    "messages": messages,
                    "temperature": 0.1,
                    "max_tokens": 1024,
                },
                timeout=600,
            )
            resp.raise_for_status()
            return _strip_code_fence(resp.json()["choices"][0]["message"]["content"])
        except Exception as e:
            logger.error("ShizhenGPT recognition failed: %s", e)
            return ""

    def recognize(self, line_img: np.ndarray) -> str:
        """Delegate to recognize_page (ShizhenGPT is a full-page VLM)."""
        return self.recognize_page(line_img)

    def close(self) -> None:
        if self._closed:
            return
        try:
            if self.server_proc is not None:
                self.server_proc.terminate()
                self.server_proc.wait(timeout=5)
        except Exception as e:
            logger.error("Error stopping ShizhenGPT server: %s", e)
        finally:
            self._closed = True

    def __del__(self) -> None:
        self.close()

    def __enter__(self) -> 'ShizhenGPTAdapter':
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: None) -> None:
        self.close()
