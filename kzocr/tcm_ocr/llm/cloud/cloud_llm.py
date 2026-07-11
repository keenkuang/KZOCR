"""
云端 LLM 仲裁客户端。

为中医 OCR 校对系统提供云端大模型调用能力，
支持 GLM-4V / DeepSeek V4 Pro 多模态 API，
实现 V5.5 分层重试策略：自动切换备选模型、指数退避重试、
总超时预算控制。

特性：
- 主力/备选模型自动切换
- 多模态输入（图片 + 文本）
- JSON 安全解析
- 完整错误追踪与日志
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import time
import traceback
from typing import Any, Dict, List, Optional, Union

import numpy as np
import requests
from PIL import Image

logger = logging.getLogger(__name__)

# ── 默认配置 ──────────────────────────────────────────
_DEFAULT_PRIMARY_MODEL: str = "glm"  # 默认主力模型
_CLOUD_API_TIMEOUT: int = 20  # 单次 API 调用超时（秒）
_CLOUD_TOTAL_TIMEOUT: int = 60  # 总超时预算（秒）
_MAX_RETRIES_PER_MODEL: int = 2  # 每模型最多尝试次数
_RETRY_BACKOFF_DELAYS: List[float] = [0, 3, 5]  # 重试间隔（秒）

# API 密钥环境变量名
_ENV_GLM_API_KEY: str = "GLM_API_KEY"
_ENV_GLM_API_BASE: str = "GLM_API_BASE"
_ENV_DEEPSEEK_API_KEY: str = "DEEPSEEK_API_KEY"
_ENV_DEEPSEEK_API_BASE: str = "DEEPSEEK_API_BASE"

# 默认 API Base URL
_DEFAULT_GLM_API_BASE: str = "https://open.bigmodel.cn/api/paas/v4"
_DEFAULT_DEEPSEEK_API_BASE: str = "https://api.deepseek.com/v1"

# 模型名称
_MODEL_GLM: str = "glm-4v-plus"
_MODEL_DEEPSEEK: str = "deepseek-v4-pro"


class CloudLLMClient:
    """云端 LLM 仲裁客户端。

    实现 V5.5 分层重试策略：
    1. 每模型最多 2 次尝试
    2. 单次调用超时 20 秒
    3. 总超时预算 60 秒
    4. 重试间隔 [0, 3, 5] 秒

    Attributes:
        primary_model: 主力模型名称（'glm' | 'deepseek'）。
        fallback_models: 备选模型列表。
        api_keys: 各模型 API 密钥字典。
        api_bases: 各模型 API Base URL 字典。
    """

    def __init__(self) -> None:
        """初始化云端 LLM 客户端。

        从环境变量读取配置：
        - CLOUD_LLM_PRIMARY: 主力模型（默认 glm）
        - GLM_API_KEY / GLM_API_BASE: GLM 配置
        - DEEPSEEK_API_KEY / DEEPSEEK_API_BASE: DeepSeek 配置
        """
        self.primary_model: str = os.environ.get("CLOUD_LLM_PRIMARY", _DEFAULT_PRIMARY_MODEL).lower().strip()
        self.fallback_models: List[str] = self._build_fallback_chain(self.primary_model)

        # 读取 API 密钥
        self.api_keys: Dict[str, Optional[str]] = {
            "glm": os.environ.get(_ENV_GLM_API_KEY),
            "deepseek": os.environ.get(_ENV_DEEPSEEK_API_KEY),
        }

        # 读取 API Base URL
        self.api_bases: Dict[str, str] = {
            "glm": os.environ.get(_ENV_GLM_API_BASE, _DEFAULT_GLM_API_BASE).rstrip("/"),
            "deepseek": os.environ.get(_ENV_DEEPSEEK_API_BASE, _DEFAULT_DEEPSEEK_API_BASE).rstrip("/"),
        }

        # 请求头缓存
        self._headers: Dict[str, Dict[str, str]] = {}

        logger.info(
            "[CloudLLM] 初始化完成 | 主力=%s | 备选=%s",
            self.primary_model,
            self.fallback_models,
        )

    def _build_fallback_chain(self, primary: str) -> List[str]:
        """构建备选模型链。

        Args:
            primary: 主力模型名称。

        Returns:
            备选模型名称列表（按优先级排序，不包含主力）。
        """
        all_models = ["glm", "deepseek"]
        return [m for m in all_models if m != primary]

    def _get_headers(self, model: str) -> Dict[str, str]:
        """获取指定模型的 HTTP 请求头。

        Args:
            model: 模型名称。

        Returns:
            HTTP 请求头字典。

        Raises:
            RuntimeError: API 密钥未配置。
        """
        if model in self._headers:
            return self._headers[model]

        api_key = self.api_keys.get(model)
        if not api_key:
            raise RuntimeError(
                f"模型 '{model}' 的 API 密钥未配置。"
                f"请设置环境变量 {_ENV_GLM_API_KEY if model == 'glm' else _ENV_DEEPSEEK_API_KEY}"
            )

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._headers[model] = headers
        return headers

    def _encode_images(
        self,
        images: List[Union[str, np.ndarray, Image.Image]],
    ) -> List[str]:
        """将图片编码为 base64 数据 URL。

        Args:
            images: 图片列表（文件路径 / numpy 数组 / PIL Image）。

        Returns:
            Base64 编码的图片字符串列表。
        """
        encoded: List[str] = []
        for img in images:
            if isinstance(img, str):
                pil_img = Image.open(img).convert("RGB")
            elif isinstance(img, np.ndarray):
                pil_img = Image.fromarray(img).convert("RGB")
            elif isinstance(img, Image.Image):
                pil_img = img.convert("RGB")
            else:
                raise TypeError(f"不支持的图片类型: {type(img)}")

            # 压缩图片以控制 token 大小
            pil_img = self._resize_for_api(pil_img)

            buffer = io.BytesIO()
            pil_img.save(buffer, format="JPEG", quality=85)
            b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
            encoded.append(f"data:image/jpeg;base64,{b64}")

        return encoded

    def _resize_for_api(self, img: Image.Image, max_size: int = 1024) -> Image.Image:
        """调整图片尺寸以适应 API 限制。

        Args:
            img: 输入图片。
            max_size: 最大边长。

        Returns:
            调整后的图片。
        """
        w, h = img.size
        if max(w, h) <= max_size:
            return img
        scale = max_size / max(w, h)
        new_w, new_h = int(w * scale), int(h * scale)
        return img.resize((new_w, new_h), Image.LANCZOS)

    # ═══════════════════════════════════════════════
    #  V5.5 分层重试策略 - 主入口
    # ═══════════════════════════════════════════════

    def call_primary(
        self,
        prompt: str,
        images: List[Union[str, np.ndarray, Image.Image]],
        timeout: int = _CLOUD_TOTAL_TIMEOUT,
    ) -> Dict[str, Any]:
        """V5.5 分层重试策略：调用云端 LLM。

        流程：
        1. 先调用主力模型（GLM/DeepSeek）
        2. 失败后按顺序切换备选模型
        3. 每模型最多 2 次尝试
        4. 单次调用超时 20 秒
        5. 总超时预算 60 秒
        6. 重试间隔 [0, 3, 5] 秒

        Args:
            prompt: 文本 prompt。
            images: 图片列表。
            timeout: 总超时预算（秒）。

        Returns:
            JSON 解析后的字典。

        Raises:
            TimeoutError: 所有模型均超时。
            RuntimeError: 所有模型均调用失败。
        """
        start_time: float = time.time()
        models_to_try: List[str] = [self.primary_model] + self.fallback_models

        last_error: Optional[Exception] = None

        for model in models_to_try:
            # 检查总超时预算
            elapsed = time.time() - start_time
            if elapsed >= timeout:
                raise TimeoutError(
                    f"云端 LLM 总超时（预算 {timeout} 秒已用尽）。"
                    f"已尝试: {models_to_try[:models_to_try.index(model)]}"
                )

            remaining_budget = timeout - elapsed

            for attempt in range(1, _MAX_RETRIES_PER_MODEL + 1):
                logger.info(
                    "[CloudLLM] 调用 %s | 第 %d/%d 次尝试 | 剩余预算 %.1f 秒",
                    model,
                    attempt,
                    _MAX_RETRIES_PER_MODEL,
                    remaining_budget,
                )

                try:
                    call_timeout = min(_CLOUD_API_TIMEOUT, remaining_budget)
                    raw_text = self._call_model(model, prompt, images, call_timeout)

                    # JSON 安全解析
                    parsed = self._safe_json_parse(raw_text)
                    parsed["_model_used"] = model
                    parsed["_attempt"] = attempt
                    logger.info("[CloudLLM] %s 调用成功", model)
                    return parsed

                except TimeoutError:
                    logger.warning("[CloudLLM] %s 第 %d 次超时", model, attempt)
                    last_error = TimeoutError(f"{model} 第 {attempt} 次超时")
                except Exception as exc:
                    logger.error(
                        "[CloudLLM] %s 第 %d 次失败: %s",
                        model,
                        attempt,
                        exc,
                    )
                    last_error = exc

                # 重试退避
                if attempt < _MAX_RETRIES_PER_MODEL:
                    delay = _RETRY_BACKOFF_DELAYS[min(attempt, len(_RETRY_BACKOFF_DELAYS) - 1)]
                    if delay > 0:
                        time.sleep(delay)

        # 所有模型均失败
        raise RuntimeError(
            f"所有云端模型调用失败（已尝试: {models_to_try}）。"
            f"最后错误: {last_error}"
        )

    def generate(
        self, prompt: str, max_tokens: int = 256, temperature: float = 0.1
    ) -> str:
        """通用的文本生成方法，兼容 page_pipeline 的 proofread 调用。

        直接使用 primary model 的 API key / base URL 发起 OpenAI 兼容调用，
        返回原始文本内容。

        Args:
            prompt: 输入提示词。
            max_tokens: 最大 token 数。
            temperature: 采样温度。

        Returns:
            模型生成的文本字符串。
        """
        import openai

        api_key = self.api_keys.get(self.primary_model) or ""
        base_url = self.api_bases.get(self.primary_model, "")

        client = openai.OpenAI(api_key=api_key, base_url=base_url)
        model = os.environ.get("GLM_MODEL", _MODEL_GLM)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return resp.choices[0].message.content or ""

    def _call_model(
        self,
        model: str,
        prompt: str,
        images: List[Union[str, np.ndarray, Image.Image]],
        timeout: float,
    ) -> str:
        """根据模型名称分发到具体调用方法。

        Args:
            model: 模型名称。
            prompt: 文本 prompt。
            images: 图片列表。
            timeout: 超时时间。

        Returns:
            模型返回的原始文本字符串。
        """
        if model == "glm":
            return self._call_glm(prompt, images, timeout)
        if model == "deepseek":
            return self._call_deepseek(prompt, images, timeout)
        raise ValueError(f"不支持的模型: {model}")

    def _safe_json_parse(self, text: str) -> Dict[str, Any]:
        """安全解析 LLM 返回的 JSON。

        先尝试直接解析，失败时尝试提取 JSON 代码块，
        再失败时包裹为原始文本字典。

        Args:
            text: LLM 返回的原始文本。

        Returns:
            解析后的字典。
        """
        text = text.strip()

        # 尝试 1: 直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 尝试 2: 提取 ```json ... ``` 代码块
        import re
        code_block = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
        if code_block:
            try:
                return json.loads(code_block.group(1))
            except json.JSONDecodeError:
                pass

        # 尝试 3: 查找第一个 JSON 对象/数组
        json_match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Fallback: 返回原始文本
        logger.warning("[CloudLLM] JSON 解析失败，返回原始文本")
        return {"raw_text": text, "parse_error": True}

    # ═══════════════════════════════════════════════
    #  GLM 多模态 API
    # ═══════════════════════════════════════════════

    def _call_glm(
        self,
        prompt: str,
        images: List[Union[str, np.ndarray, Image.Image]],
        timeout: float = _CLOUD_API_TIMEOUT,
    ) -> str:
        """调用 GLM-4V 多模态 API。

        Args:
            prompt: 文本 prompt。
            images: 图片列表。
            timeout: 超时时间（秒）。

        Returns:
            模型生成的文本内容。

        Raises:
            RuntimeError: API 调用失败。
            TimeoutError: 请求超时。
        """
        headers = self._get_headers("glm")
        api_base = self.api_bases["glm"]
        url = f"{api_base}/chat/completions"

        # 编码图片
        image_urls = self._encode_images(images)

        # 构建 GLM 消息格式
        content: List[Dict[str, Any]] = []
        for img_url in image_urls:
            content.append({"type": "image_url", "image_url": {"url": img_url}})
        content.append({"type": "text", "text": prompt})

        payload = {
            "model": os.environ.get("GLM_MODEL", _MODEL_GLM),
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.1,
            "max_tokens": 4096,
        }

        try:
            resp = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=(10, int(timeout)),
            )
            resp.raise_for_status()
            data = resp.json()

            if "choices" in data and len(data["choices"]) > 0:
                message = data["choices"][0].get("message", {})
                return message.get("content", "")
            raise RuntimeError(f"GLM API 返回异常格式: {data}")

        except requests.Timeout:
            raise TimeoutError(f"GLM API 超时（{timeout} 秒）")
        except requests.RequestException as exc:
            raise RuntimeError(f"GLM API 请求失败: {exc}") from exc

    # ═══════════════════════════════════════════════
    #  DeepSeek V4 Pro 多模态 API
    # ═══════════════════════════════════════════════

    def _call_deepseek(
        self,
        prompt: str,
        images: List[Union[str, np.ndarray, Image.Image]],
        timeout: float = _CLOUD_API_TIMEOUT,
    ) -> str:
        """调用 DeepSeek V4 Pro 多模态 API。

        Args:
            prompt: 文本 prompt。
            images: 图片列表。
            timeout: 超时时间（秒）。

        Returns:
            模型生成的文本内容。

        Raises:
            RuntimeError: API 调用失败。
            TimeoutError: 请求超时。
        """
        headers = self._get_headers("deepseek")
        api_base = self.api_bases["deepseek"]
        url = f"{api_base}/chat/completions"

        # 编码图片
        image_urls = self._encode_images(images)

        # 构建 DeepSeek 消息格式
        content: List[Dict[str, Any]] = []
        for img_url in image_urls:
            content.append({"type": "image_url", "image_url": {"url": img_url}})
        content.append({"type": "text", "text": prompt})

        payload = {
            "model": _MODEL_DEEPSEEK,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.1,
            "max_tokens": 4096,
        }

        try:
            resp = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=(10, int(timeout)),
            )
            resp.raise_for_status()
            data = resp.json()

            if "choices" in data and len(data["choices"]) > 0:
                message = data["choices"][0].get("message", {})
                return message.get("content", "")
            raise RuntimeError(f"DeepSeek API 返回异常格式: {data}")

        except requests.Timeout:
            raise TimeoutError(f"DeepSeek API 超时（{timeout} 秒）")
        except requests.RequestException as exc:
            raise RuntimeError(f"DeepSeek API 请求失败: {exc}") from exc
