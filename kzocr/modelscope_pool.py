"""云端 LLM 大池（多 provider 聚合，配置驱动）。

大池 CloudLLMPool 由多个「provider 项」组成，每个 provider 是一项独立的云端 LLM 服务：
- modelscope   ：ModelScope 10 个免费文本模型（每日 45 次配额）
- ofox         ：Ofox AI 聚合（`z-ai/glm-4.7-flash:free`）
- siliconflow  ：硅基流动（VLM / 文档 OCR / 文本校对）
- zai          ：z.ai（glm-4.6v-flash 视觉 + glm-4.7/4.5-flash 文本）
- zhipu        ：智谱主站（GLM-4.6V-Flash 视觉 + GLM-4.7-Flash 文本）
- sensenova    ：商汤（sensenova-6.7-flash-lite / sensenova-u1-fast / deepseek-v4-flash，支持视觉 OCR）
- glm          ：智谱 GLM（引擎侧 CLOUD_LLM）
- deepseek     ：DeepSeek（引擎侧 CLOUD_LLM）

每个 provider 内部做「模型级故障转移」（同 provider 多个模型轮流试），
大池再做「跨 provider 故障转移」（一个 provider 全挂了换下一个）。
视觉请求只在标记 vision=True 的 provider 间流转。

密钥统一读环境变量；缺失 key 的 provider 在初始化时自动禁用（不报错、不阻断其它 provider）。
"""

from __future__ import annotations

import base64
import logging
import os
import time
from dataclasses import dataclass, field
from typing import List, Optional

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # 可选依赖：未安装 openai 时，依赖它的 provider 在初始化时自动禁用

logger = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────────────────
# Provider 配置
# ───────────────────────────────────────────────────────────────────────────

@dataclass
class ProviderSpec:
    name: str
    base_url: str
    api_key_env: str            # 读取密钥用的环境变量名
    api_key_fallback: str = ""  # 仅占位；密钥必须从环境变量读取，缺失即禁用本 provider（禁止硬编码明文 key）
    models: List[str] = field(default_factory=list)
    vision: bool = False        # 是否支持视觉 OCR


# ModelScope 10 个文本模型（沿用此前已建好的 ModelScopePool 配置）
_MODESCOPE_MODELS = [
    "Qwen/Qwen3.5-35B-A3B",
    "ZhipuAI/GLM-4.7-Flash",
    "Qwen/Qwen3.5-27B",
    "deepseek-ai/DeepSeek-V4-Flash",
    "Qwen/Qwen3.5-122B-A10B",
    "ZhipuAI/GLM-5.2",
    "ZhipuAI/GLM-4.7:DashScope",
    "deepseek-ai/DeepSeek-V4-Pro",
    "Qwen/Qwen3.5-397B-A17B",
    "moonshotai/Kimi-K2.6:DashScope",
]

# 硅基流动：VLM / 文档 OCR / 文本
_SILICONFLOW_MODELS = [
    "Qwen/Qwen3.5-4B",                     # 通用图文理解（视觉）
    "deepseek-ai/DeepSeek-OCR",            # 文档 OCR（视觉）
    "PaddlePaddle/PaddleOCR-VL-1.5",       # 飞桨文档 VL（视觉）
    "THUDM/GLM-4-9B-0414",
    "THUDM/GLM-Z1-9B-0414",
    "Qwen/Qwen2.5-7B-Instruct",
    "Qwen/Qwen3-8B",
    "deepseek-ai/DeepSeek-R1-0528-Qwen3-8B",
    "PaddlePaddle/PaddleOCR-VL-1.5",       # 文字校对
]

# z.ai（模型名全小写）
_ZAI_MODELS = [
    "glm-4.6v-flash",   # 视觉 VLM
    "glm-4.7-flash",    # 文本
    "glm-4.5-flash",    # 文本
]

# 智谱主站（模型名大写标准）
_ZHIPU_MODELS = [
    "GLM-4.6V-Flash",   # 视觉 VLM
    "GLM-4.7-Flash",    # 文本
]

# 商汤 sensenova（支持视觉 OCR）
_SENSENOVA_MODELS = [
    "sensenova-6.7-flash-lite",
    "sensenova-u1-fast",
    "deepseek-v4-flash",
]


_PROVIDER_SPECS: List[ProviderSpec] = [
    ProviderSpec(
        name="modelscope",
        base_url="https://api-inference.modelscope.cn/v1",
        api_key_env="MODELSCOPE_API_KEY",
        api_key_fallback="",  # 密钥仅从环境变量读取；缺失即禁用本 provider
        models=_MODESCOPE_MODELS,
        vision=False,
    ),
    ProviderSpec(
        name="ofox",
        base_url="https://api.ofox.io/v1",
        api_key_env="OFOX_API_KEY",
        api_key_fallback="",  # key 在 CHANGELOG 记为有效但未给出明文，需自行注入
        models=["z-ai/glm-4.7-flash:free"],
        vision=False,
    ),
    ProviderSpec(
        name="siliconflow",
        base_url="https://api.siliconflow.cn/v1",
        api_key_env="SILICONFLOW_API_KEY",
        api_key_fallback="",  # CHANGELOG 记为已测通但未给明文
        models=_SILICONFLOW_MODELS,
        vision=True,
    ),
    ProviderSpec(
        name="zai",
        base_url="https://api.z.ai/api/paas/v4",
        api_key_env="ZAI_API_KEY",
        api_key_fallback="",  # 密钥经环境变量注入，不入库
        models=_ZAI_MODELS,
        vision=True,
    ),
    ProviderSpec(
        name="zhipu",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key_env="ZHIPU_API_KEY",
        api_key_fallback="",  # 密钥经环境变量注入，不入库
        models=_ZHIPU_MODELS,
        vision=True,
    ),
    ProviderSpec(
        name="sensenova",
        base_url="https://token.sensenova.cn/v1",
        api_key_env="SENSENOVA_API_KEY",
        api_key_fallback="",  # 密钥仅从环境变量读取；缺失即禁用本 provider
        models=_SENSENOVA_MODELS,
        vision=True,
    ),
    ProviderSpec(
        name="glm",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key_env="GLM_API_KEY",
        api_key_fallback="",
        models=["glm-4v-plus"],
        vision=True,
    ),
    ProviderSpec(
        name="deepseek",
        base_url="https://api.deepseek.com/v1",
        api_key_env="DEEPSEEK_API_KEY",
        api_key_fallback="",
        models=["deepseek-v4-pro"],
        vision=True,
    ),
]

_RETRY_DELAY = 1.0  # provider/模型切换后等待秒数


# ───────────────────────────────────────────────────────────────────────────
# 单 provider 内池
# ───────────────────────────────────────────────────────────────────────────

class _ProviderPool:
    """单个 provider 的内池：持有该 provider 的若干模型，做模型级故障转移。"""

    def __init__(self, spec: ProviderSpec) -> None:
        self.name = spec.name
        self.vision = spec.vision
        api_key = os.environ.get(spec.api_key_env, "") or spec.api_key_fallback
        self.enabled = bool(api_key)
        if self.enabled and OpenAI is None:
            self.enabled = False
            logger.warning(
                "[pool] provider '%s' 未安装 openai 依赖，已禁用", spec.name
            )
        self._models = list(spec.models)
        self._idx = 0
        self._client = (
            OpenAI(base_url=spec.base_url, api_key=api_key) if self.enabled else None
        )
        if not self.enabled:
            logger.warning("[pool] provider '%s' 未配置 key（%s），已禁用", spec.name, spec.api_key_env)

    @property
    def current_model(self) -> str:
        return self._models[self._idx] if self._models else ""

    @staticmethod
    def _image_to_data_url(image_path: str, mime: str = "image/jpeg") -> str:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return f"data:{mime};base64,{b64}"

    def _do_call(self, messages, max_tokens, temperature, timeout) -> Optional[str]:
        model = self._models[self._idx]
        resp = self._client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
        )
        return (resp.choices[0].message.content or "").strip() or None

    def chat(self, messages, max_tokens=2048, temperature=0.0, timeout=60) -> Optional[str]:
        if not self.enabled:
            return None
        for _ in range(len(self._models)):
            model = self._models[self._idx]
            try:
                text = self._do_call(messages, max_tokens, temperature, timeout)
                if text:
                    logger.debug("[%s] %s 成功", self.name, model)
                    return text
            except Exception as e:
                logger.warning("[%s] %s 失败: %s → 下一模型", self.name, model, str(e)[:120])
            self._idx = (self._idx + 1) % len(self._models)
            time.sleep(_RETRY_DELAY)
        return None

    def chat_vision(self, prompt, image_path=None, image_data_url=None,
                    max_tokens=2048, temperature=0, timeout=90) -> Optional[str]:
        if not self.enabled:
            return None
        if image_path:
            image_url = self._image_to_data_url(image_path)
        elif image_data_url:
            image_url = image_data_url
        else:
            return None
        system_prompt = "你是中医古籍OCR专家，只输出识别文本，不添加解释。"
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {"type": "text", "text": prompt},
                ],
            },
        ]
        for _ in range(len(self._models)):
            model = self._models[self._idx]
            try:
                text = self._vision_call(messages, model, max_tokens, temperature, timeout)
                if text:
                    logger.debug("[%s] %s 视觉成功", self.name, model)
                    return text
            except Exception as e:
                logger.warning("[%s] %s 视觉失败: %s → 下一模型", self.name, model, str(e)[:120])
            self._idx = (self._idx + 1) % len(self._models)
            time.sleep(_RETRY_DELAY)
        return None

    def _vision_call(self, messages, model, max_tokens, temperature, timeout) -> Optional[str]:
        """对特定模型发起视觉请求（stream 模式，自动拼接输出）。"""
        resp = self._client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            stream=True,
            extra_body={"reasoning_effort": "none"},
        )
        chunks: list[str] = []
        for chunk in resp:
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                chunks.append(chunk.choices[0].delta.content)
        text = "".join(chunks).strip()
        return text or None


# ───────────────────────────────────────────────────────────────────────────
# 大池：聚合多个 provider，跨池故障转移
# ───────────────────────────────────────────────────────────────────────────

class CloudLLMPool:
    """顶层云端 LLM 大池：聚合多个 provider 项，池内 + 跨池自动故障转移。

    Usage:
        pool = CloudLLMPool()                              # 默认启用所有已配置 key 的 provider
        pool = CloudLLMPool(["modelscope", "sensenova"])  # 只启用指定 provider
        txt = pool.chat(messages=[...])                    # 文本：仅非视觉 provider
        ocr = pool.chat_vision("识别图中文字", image_path="page.jpg")  # 视觉：仅视觉 provider
    """

    def __init__(self, only: Optional[List[str]] = None) -> None:
        specs = [s for s in _PROVIDER_SPECS if (only is None or s.name in only)]
        self._providers = [_ProviderPool(s) for s in specs]
        self._text_providers = [p for p in self._providers if not p.vision]
        self._vision_providers = [p for p in self._providers if p.vision]
        self._text_idx = 0
        self._vision_idx = 0
        enabled = [p.name for p in self._providers if p.enabled]
        disabled = [p.name for p in self._providers if not p.enabled]
        logger.info("[大池] 启用 provider=%s；未启用(缺key)=%s", enabled, disabled)

    @property
    def enabled_providers(self) -> List[str]:
        return [p.name for p in self._providers if p.enabled]

    # ---- 文本 ----
    @property
    def current_model(self) -> str:
        if not self._text_providers:
            return ""
        return self._text_providers[self._text_idx].current_model

    def chat(self, messages, **kwargs) -> Optional[str]:
        if not self._text_providers:
            logger.error("[大池] 无可用文本 provider")
            return None
        for _ in range(len(self._text_providers)):
            p = self._text_providers[self._text_idx]
            if p.enabled:
                try:
                    r = p.chat(messages, **kwargs)
                    if r:
                        return r
                except Exception as e:
                    logger.warning("[大池] 文本 provider %s 异常: %s", p.name, str(e)[:120])
            self._text_idx = (self._text_idx + 1) % len(self._text_providers)
            time.sleep(_RETRY_DELAY)
        return None

    # ---- 视觉 ----
    @property
    def current_vision_model(self) -> str:
        if not self._vision_providers:
            return ""
        return self._vision_providers[self._vision_idx].current_model

    def chat_vision(self, prompt, image_path=None, image_data_url=None, **kwargs) -> Optional[str]:
        if not self._vision_providers:
            logger.error("[大池] 无可用视觉 provider")
            return None
        for _ in range(len(self._vision_providers)):
            p = self._vision_providers[self._vision_idx]
            if p.enabled:
                try:
                    r = p.chat_vision(prompt, image_path=image_path,
                                      image_data_url=image_data_url, **kwargs)
                    if r:
                        return r
                except Exception as e:
                    logger.warning("[大池] 视觉 provider %s 异常: %s", p.name, str(e)[:120])
            self._vision_idx = (self._vision_idx + 1) % len(self._vision_providers)
            time.sleep(_RETRY_DELAY)
        return None


# 向后兼容别名：此前对外暴露的 ModelScopePool 名称
ModelScopePool = CloudLLMPool
