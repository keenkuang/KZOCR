"""ModelScope 模型池客户端——自动故障转移。

内置 10 个免费模型，每个每日 45 次调用配额（多模态 30 次）。
调用失败（限流/超时/错误）时自动换下一个模型，最大化可用配额。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from openai import OpenAI

logger = logging.getLogger(__name__)

# ModelScope 配置
_BASE_URL = "https://api-inference.modelscope.cn/v1"
_API_KEY = "ms-40d78a2b-f786-433a-92e3-8e5f4049f602"

# 模型池——优先级按速度/质量排序，失败自动降级
_TEXT_MODELS = [
    "Qwen/Qwen3.5-35B-A3B",       # MoE 快速，首选
    "ZhipuAI/GLM-4.7-Flash",      # 轻量
    "Qwen/Qwen3.5-27B",           # 中等
    "deepseek-ai/DeepSeek-V4-Flash",
    "Qwen/Qwen3.5-122B-A10B",
    "ZhipuAI/GLM-5.2",
    "ZhipuAI/GLM-4.7:DashScope",
    "deepseek-ai/DeepSeek-V4-Pro",
    "Qwen/Qwen3.5-397B-A17B",
    "moonshotai/Kimi-K2.6:DashScope",
]

# 重试/故障转移配置
_RETRY_DELAY = 1.0  # 失败后等待秒数


class ModelScopePool:
    """ModelScope 模型池，带自动故障转移。

    Usage:
        pool = ModelScopePool()
        resp = pool.chat(messages=[{"role": "user", "content": "..."}])
    """

    def __init__(self) -> None:
        self._client = OpenAI(base_url=_BASE_URL, api_key=_API_KEY)
        self._models = list(_TEXT_MODELS)
        self._current_idx = 0

    @property
    def current_model(self) -> str:
        """当前正在使用的模型 ID。"""
        return self._models[self._current_idx]

    def chat(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 4096,
        temperature: float = 0.1,
        retry_count: int = len(_TEXT_MODELS),  # 最多尝试所有模型
    ) -> Optional[str]:
        """发送聊天请求，失败时自动切换模型重试。

        Args:
            messages: OpenAI 格式的消息列表。
            max_tokens: 最大生成 token 数。
            temperature: 采样温度。
            retry_count: 最大尝试模型数（不超过模型池大小）。

        Returns:
            模型回复文本，全部失败返回 None。
        """
        last_error = ""
        attempts = 0

        for attempt in range(min(retry_count, len(self._models))):
            model = self._models[self._current_idx]
            attempts += 1

            try:
                resp = self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=60,
                )
                text = resp.choices[0].message.content
                if text:
                    logger.debug("ModelScope[%s] 成功", model)
                    return text.strip()
            except Exception as e:
                err_msg = str(e)[:100]
                logger.warning("ModelScope[%s] 失败: %s → 切换到下一模型", model, err_msg)
                last_error = err_msg

            # 切换到下一模型
            self._current_idx = (self._current_idx + 1) % len(self._models)
            time.sleep(_RETRY_DELAY)

        logger.error("ModelScope 全部 %d 个模型均失败: %s", attempts, last_error)
        return None

    def chat_with_system(
        self,
        system_prompt: str,
        user_prompt: str,
        **kwargs,
    ) -> Optional[str]:
        """带 system prompt 的便捷方法。"""
        return self.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            **kwargs,
        )
