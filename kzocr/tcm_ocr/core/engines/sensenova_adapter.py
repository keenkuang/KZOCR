"""
SenseNova API VLM Adapter for TCM OCR System.

Uses sensenova-6.7-flash-lite multimodal model via SenseNova API
for full-page OCR. Requires SENSENOVA_API_KEY environment variable.

Falls back by raising exceptions — the caller (_init_vlm_adapter) is
responsible for catching and falling back to PaddleOCR-VL-1.6.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
from typing import Any, Optional

import numpy as np
import requests
from PIL import Image

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://token.sensenova.cn/v1/chat/completions"
_DEFAULT_MODEL = "sensenova-6.7-flash-lite"


class SenseNovaAdapter:
    """Adapter for SenseNova multimodal API (sensenova-6.7-flash-lite).

    Processes full-page images and returns recognized text using
    SenseNova's cloud VLM. No local GPU/CPU needed.

    Attributes:
        api_key (str): SenseNova API key.
        model (str): Model name.
        base_url (str): API endpoint URL.
        timeout (int): Request timeout in seconds.
    """

    def __init__(
        self,
        api_key: str = "",
        model: str = _DEFAULT_MODEL,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: int = 300,
        device: str = "cpu",  # kept for API compat with adapter pattern
    ) -> None:
        """Initialize the SenseNova adapter.

        Args:
            api_key: SenseNova API key (falls back to SENSENOVA_API_KEY env).
            model: Model name (default sensenova-6.7-flash-lite).
            base_url: API base URL.
            timeout: Request timeout in seconds.
            device: Ignored (cloud API), kept for compat.
        """
        self.api_key = api_key or os.environ.get("SENSENOVA_API_KEY", "")
        if not self.api_key:
            raise RuntimeError(
                "SenseNova API key not configured. "
                "Set SENSENOVA_API_KEY env or pass api_key=."
            )
        self.model = model
        self.base_url = base_url or _DEFAULT_BASE_URL
        self.timeout = timeout
        self._closed = False
        logger.info(
            "SenseNovaAdapter initialized (model=%s, base=%s, timeout=%s)",
            self.model, self.base_url, self.timeout,
        )

    def recognize_page(self, page_img: np.ndarray, prompt: str = None) -> str:
        """Recognize a single page image. Delegates to recognize_pages."""
        return self.recognize_pages([page_img], prompt=prompt)

    def recognize_pages(
        self, page_imgs: list[np.ndarray], prompt: str = None
    ) -> str:
        """Recognize text from one or more page images.

        For multi-page input, the model sees all images and can use
        context from later pages (useful for cross-page formula continuity).
        The prompt determines which page(s) to output.

        Args:
            page_imgs: List of page images as numpy arrays (H, W, C) RGB.
            prompt: Optional custom prompt. Defaults to a concise OCR prompt.

        Returns:
            Recognized text string.
        """
        if self._closed:
            raise RuntimeError("SenseNovaAdapter has been closed.")
        if not page_imgs:
            return ""

        # Encode all images to base64 JPEG
        content_parts: list[dict] = []
        for img in page_imgs:
            pil_img = Image.fromarray(img)
            buf_io = io.BytesIO()
            pil_img.save(buf_io, format="JPEG", quality=70)
            img_b64 = base64.b64encode(buf_io.getvalue()).decode("utf-8")
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
            })

        system_prompt = """请逐行逐字识别第一张图片中的所有文字，忠于原文，不得自行扩充、改写内容，严格保留原文换行、分段格式布局。
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
        if len(page_imgs) > 1:
            system_prompt += "\n    - 只输出第一页内容，第二页图片仅供上下文衔接理解用，可以只识别第二页的前五行完成上下文衔接即可，输出内容不要包含第二页内容"
        user_prompt = prompt or "识别图片中所有文字，按原文顺序输出，保留标点和换行。"

        content_parts.insert(0, {"type": "text", "text": user_prompt})

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content_parts},
            ],
            "temperature": 0,
            "max_tokens": 4096,
            "stream": False,
            "reasoning_effort": "none",
        }

        try:
            resp = requests.post(
                self.base_url,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                err_msg = data.get("error", {}).get("message", str(data))
                logger.error("SenseNova API 返回空 choices: %s", err_msg)
                raise RuntimeError(f"SenseNova 返回空结果: {err_msg}")
            text = choices[0].get("message", {}).get("content", "").strip()
            if not text:
                reasoning = choices[0].get("message", {}).get("reasoning", "")
                if reasoning:
                    text = _extract_reasoning_output(reasoning)
                    logger.info("从 reasoning 提取 content（%d→%d）", len(reasoning), len(text))
                else:
                    logger.error("SenseNova API 返回空 content+reasoning")
                    raise RuntimeError("SenseNova 返回空内容")
            return text
        except requests.exceptions.Timeout:
            logger.error("SenseNova API 超时 (timeout=%ss)", self.timeout)
            raise
        except requests.exceptions.HTTPError as e:
            body = e.response.text[:500] if e.response else ""
            logger.error("SenseNova API HTTP %s: %s", e.response.status_code, body)
            raise
        except (KeyError, TypeError, ValueError) as e:
            logger.error("SenseNova API 响应解析失败: %s, body=%s", e, resp.text[:500])
            raise RuntimeError(f"SenseNova 响应格式异常: {e}") from e
        except Exception as e:
            logger.error("SenseNova API 调用失败: %s", e)
            raise

    def close(self) -> None:
        """Release resources (no-op for cloud API)."""
        self._closed = True

    def __del__(self) -> None:
        self.close()


def _extract_reasoning_output(reasoning: str) -> str:
    """从 reasoning 字段中提取最终的 OCR 输出正文。"""
    import re
    text = ""
    # 1. 尝试找"最终输出"标记后的正文
    for marker in ("**最终输出**", "最终输出结构规划", "最终输出："):
        if marker in reasoning:
            text = reasoning.split(marker, 1)[1].strip()
            break
    # 2. 有正文后，切掉末尾的"再检查"类后处理分析
    if text:
        for trim in ("*再检查", "再检查一遍", "我再看看"):
            pos = text.find(trim)
            if pos > len(text) // 2:
                text = text[:pos].strip()
                break
    # 3. 仍未取到，取 reasoning 主体（跳过前半段的思考分析）
    if not text:
        parts = reasoning.split("\n\n")
        cutoff = len(parts) // 3
        body = "\n\n".join(parts[cutoff:])
        for trim in ("**修正与格式化", "修正与格式化", "最终输出结构"):
            pos = body.find(trim)
            if pos > len(body) // 2:
                body = body[:pos].strip()
        text = body
    return text.strip()

    def __enter__(self) -> "SenseNovaAdapter":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
