"""
ShizhenGPT 本地多模态大模型客户端。

为中医现代出版物 OCR 校对系统提供本地 LLM 推理能力，
基于 transformers 的 Vision2Seq 架构，支持 4-bit 量化（GPTQ/AWQ），
峰值显存控制在 ≤14GB。

特性：
- 多模态输入（图片 + 文本 prompt）
- 超时控制（threading.Timer）
- 批量生成（用于跨页段落处理）
- GPU OOM 防护与资源自动释放
"""

from __future__ import annotations

import gc
import logging
import threading
import traceback
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import torch
from PIL import Image

logger = logging.getLogger(__name__)


class ShizhenGPTClient:
    """ShizhenGPT 本地多模态 LLM 客户端。

    使用 transformers AutoModelForVision2Seq + AutoTokenizer 加载模型，
    支持 4-bit 量化（GPTQ/AWQ/BitsAndBytes），峰值显存 ≤14GB。

    Attributes:
        model_path: 本地模型路径或 HuggingFace 模型 ID。
        gpu_id: 使用的 GPU 设备 ID。
        quantization: 量化方式，'4bit' | '8bit' | 'gptq' | 'awq' | 'none'。
        model: 加载后的 Vision2Seq 模型。
        tokenizer: 对应的分词器。
        device: 模型所在设备。
        _closed: 标记资源是否已释放。
    """

    def __init__(
        self,
        model_path: str,
        gpu_id: int = 0,
        quantization: str = "4bit",
    ) -> None:
        """初始化 ShizhenGPT 客户端并加载模型。

        Args:
            model_path: 本地模型路径或 HuggingFace 模型 ID。
            gpu_id: 使用的 GPU 设备 ID，默认 0。
            quantization: 量化方式，可选 '4bit' | '8bit' | 'gptq' | 'awq' | 'none'。
        """
        self.model_path: str = model_path
        self.gpu_id: int = gpu_id
        self.quantization: str = quantization.lower()
        self.device: torch.device = torch.device(
            f"cuda:{gpu_id}" if torch.cuda.is_available() else "cpu"
        )
        self._closed: bool = False
        self._lock: threading.Lock = threading.Lock()

        # 显存上限配置 (GB)
        self._max_memory_gb: float = 14.0

        logger.info(
            "[ShizhenGPT] 正在加载模型: %s (gpu=%d, quant=%s)",
            model_path,
            gpu_id,
            quantization,
        )
        self._load_model()
        logger.info("[ShizhenGPT] 模型加载完成，设备: %s", self.device)

    def _load_model(self) -> None:
        """加载 Vision2Seq 模型和分词器，根据 quantization 配置量化参数。"""
        try:
            from transformers import (
                AutoModelForVision2Seq,
                AutoTokenizer,
            )
        except ImportError as exc:
            raise ImportError(
                "加载 ShizhenGPT 需要 transformers 库。"
                "请安装: pip install transformers accelerate bitsandbytes"
            ) from exc

        # 构建量化配置
        quantization_config = self._build_quantization_config()

        # 计算最大显存
        max_memory = {self.gpu_id: f"{int(self._max_memory_gb)}GB"} if torch.cuda.is_available() else None

        load_kwargs: Dict[str, Any] = {
            "trust_remote_code": True,
            "torch_dtype": torch.float16 if torch.cuda.is_available() else torch.float32,
            "low_cpu_mem_usage": True,
        }

        if quantization_config is not None:
            load_kwargs["quantization_config"] = quantization_config

        if max_memory is not None:
            load_kwargs["max_memory"] = max_memory
            load_kwargs["device_map"] = "auto"
        else:
            load_kwargs["device_map"] = None

        try:
            self.tokenizer = AutoTokenizer.from_pretrained(
                self.model_path,
                trust_remote_code=True,
                use_fast=True,
            )
            self.model = AutoModelForVision2Seq.from_pretrained(
                self.model_path,
                **load_kwargs,
            )

            # 如果未使用 device_map='auto'，手动移动到设备
            if load_kwargs.get("device_map") is None:
                self.model = self.model.to(self.device)

        except Exception as exc:
            logger.error("[ShizhenGPT] 模型加载失败: %s", traceback.format_exc())
            raise RuntimeError(f"ShizhenGPT 模型加载失败: {exc}") from exc

    def _build_quantization_config(self) -> Optional[Any]:
        """根据 quantization 参数构建量化配置。

        Returns:
            BitsAndBytesConfig 或 None（不量化时）。
        """
        if self.quantization in ("none", ""):
            return None

        if self.quantization == "4bit":
            try:
                from transformers import BitsAndBytesConfig
                return BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_compute_dtype=torch.float16,
                    bnb_4bit_use_double_quant=True,
                    bnb_4bit_quant_type="nf4",
                )
            except ImportError:
                logger.warning("BitsAndBytes 不可用，回退到 8bit 量化")
                from transformers import BitsAndBytesConfig
                return BitsAndBytesConfig(load_in_8bit=True)

        if self.quantization == "8bit":
            from transformers import BitsAndBytesConfig
            return BitsAndBytesConfig(load_in_8bit=True)

        if self.quantization in ("gptq", "awq"):
            # GPTQ/AWQ 量化已在模型文件中配置，无需额外 quantization_config
            logger.info("[ShizhenGPT] 使用 %s 预量化模型", self.quantization.upper())
            return None

        logger.warning("[ShizhenGPT] 未知量化方式 '%s'，使用 4bit", self.quantization)
        from transformers import BitsAndBytesConfig
        return BitsAndBytesConfig(load_in_4bit=True)

    def _prepare_inputs(
        self,
        prompt: str,
        images: List[Union[str, np.ndarray, Image.Image]],
    ) -> Dict[str, Any]:
        """构造多模态输入（图片 + 文本 prompt）。

        Args:
            prompt: 文本 prompt。
            images: 图片列表，支持文件路径、numpy 数组或 PIL Image。

        Returns:
            模型输入字典，包含 pixel_values 和 input_ids 等。
        """
        pil_images: List[Image.Image] = []
        for img in images:
            if isinstance(img, str):
                pil_images.append(Image.open(img).convert("RGB"))
            elif isinstance(img, np.ndarray):
                pil_images.append(Image.fromarray(img).convert("RGB"))
            elif isinstance(img, Image.Image):
                pil_images.append(img.convert("RGB"))
            else:
                raise TypeError(f"不支持的图片类型: {type(img)}")

        # 使用 processor / tokenizer 构造多模态输入
        if hasattr(self.model, "chat"):
            # 支持 Qwen-VL / CogVLM 风格的 chat 接口
            content = []
            for pil_img in pil_images:
                content.append({"image": pil_img})
            content.append({"text": prompt})
            query = self.tokenizer.from_list_format(content)

            inputs = self.tokenizer(query, return_tensors="pt", padding=True)
            if "pixel_values" in inputs:
                inputs["pixel_values"] = inputs["pixel_values"].to(self.device)
            inputs["input_ids"] = inputs["input_ids"].to(self.device)
            if "attention_mask" in inputs:
                inputs["attention_mask"] = inputs["attention_mask"].to(self.device)
            return inputs

        # 标准 Vision2Seq 处理器
        if hasattr(self.tokenizer, "image_processor"):
            from transformers import AutoProcessor
            processor = AutoProcessor.from_pretrained(self.model_path, trust_remote_code=True)
            inputs = processor(
                text=[prompt],
                images=pil_images if pil_images else None,
                return_tensors="pt",
            )
        else:
            # 简单 fallback
            text_input = self.tokenizer(
                prompt,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=4096,
            )
            inputs = {k: v.to(self.device) for k, v in text_input.items()}

        # 将所有张量移动到目标设备
        for key in list(inputs.keys()):
            if isinstance(inputs[key], torch.Tensor):
                inputs[key] = inputs[key].to(self.device)
        return inputs

    def generate(
        self,
        prompt: str,
        images: List[Union[str, np.ndarray, Image.Image]],
        max_tokens: int = 4096,
        temperature: float = 0.1,
        timeout: int = 60,
    ) -> str:
        """生成文本，带超时控制。

        Args:
            prompt: 输入文本 prompt。
            images: 输入图片列表。
            max_tokens: 最大生成 token 数。
            temperature: 采样温度，默认 0.1（低温度，确定性输出）。
            timeout: 超时时间（秒），默认 60 秒。

        Returns:
            生成的 JSON 字符串。

        Raises:
            TimeoutError: 生成超时。
            RuntimeError: 生成过程中发生错误。
        """
        if self._closed:
            raise RuntimeError("ShizhenGPTClient 已关闭，无法生成")

        with self._lock:
            result_container: List[str] = []
            exception_container: List[Exception] = []

            def _generate_worker() -> None:
                """在独立线程中执行生成。"""
                try:
                    inputs = self._prepare_inputs(prompt, images)

                    generate_kwargs: Dict[str, Any] = {
                        "max_new_tokens": max_tokens,
                        "temperature": temperature,
                        "do_sample": temperature > 0.0,
                        "pad_token_id": self.tokenizer.pad_token_id or self.tokenizer.eos_token_id,
                        "eos_token_id": self.tokenizer.eos_token_id,
                    }

                    with torch.no_grad():
                        output_ids = self.model.generate(**inputs, **generate_kwargs)

                    # 仅解码新生成的 token
                    prompt_len = inputs["input_ids"].shape[1]
                    new_tokens = output_ids[0][prompt_len:]
                    generated_text = self.tokenizer.decode(
                        new_tokens, skip_special_tokens=True
                    )
                    result_container.append(generated_text)

                except torch.cuda.OutOfMemoryError as oom:
                    logger.error("[ShizhenGPT] GPU OOM: %s", oom)
                    torch.cuda.empty_cache()
                    gc.collect()
                    exception_container.append(oom)
                except Exception as exc:
                    logger.error("[ShizhenGPT] 生成失败: %s", traceback.format_exc())
                    exception_container.append(exc)

            # 启动生成线程
            gen_thread = threading.Thread(target=_generate_worker)
            gen_thread.start()
            gen_thread.join(timeout=timeout)

            if gen_thread.is_alive():
                # 超时处理：无法安全中断线程，但标记结果并继续
                logger.warning("[ShizhenGPT] 生成超时 (%d 秒)", timeout)
                raise TimeoutError(
                    f"ShizhenGPT 生成超时（设定 {timeout} 秒）。"
                    "可能原因：模型过大、输入过长或 GPU 负载过高。"
                )

            if exception_container:
                raise RuntimeError(f"生成失败: {exception_container[0]}")

            if not result_container:
                raise RuntimeError("生成结果为空")

            generated = result_container[0].strip()
            logger.debug("[ShizhenGPT] 生成完成，长度=%d", len(generated))
            return generated

    def batch_generate(
        self,
        prompts_with_images: List[Tuple[str, List[Union[str, np.ndarray, Image.Image]]]],
        max_tokens: int = 4096,
        temperature: float = 0.1,
        timeout: int = 60,
    ) -> List[str]:
        """批量处理多个 prompt（用于跨页段落）。

        按顺序处理每个 prompt，避免同时加载多个大批量数据导致 OOM。

        Args:
            prompts_with_images: (prompt, images) 元组列表。
            max_tokens: 每个 prompt 最大生成 token 数。
            temperature: 采样温度。
            timeout: 每个 prompt 的超时时间。

        Returns:
            生成结果字符串列表，与输入顺序对应。
            失败的项返回空字符串并记录日志。
        """
        results: List[str] = []
        for idx, (prompt, images) in enumerate(prompts_with_images):
            logger.info("[ShizhenGPT] 批量处理 %d/%d", idx + 1, len(prompts_with_images))
            try:
                result = self.generate(
                    prompt=prompt,
                    images=images,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=timeout,
                )
                results.append(result)
            except TimeoutError:
                logger.error("[ShizhenGPT] 第 %d 项超时", idx)
                results.append("")
            except Exception as exc:
                logger.error("[ShizhenGPT] 第 %d 项失败: %s", idx, exc)
                results.append("")

            # 每处理完一项清理一次显存
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        return results

    def close(self) -> None:
        """释放 GPU 资源，关闭模型。"""
        if self._closed:
            return

        logger.info("[ShizhenGPT] 正在释放资源...")

        with self._lock:
            # 删除模型和分词器
            if hasattr(self, "model"):
                del self.model
            if hasattr(self, "tokenizer"):
                del self.tokenizer

            # 强制垃圾回收 + 显存清理
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.synchronize()

        self._closed = True
        logger.info("[ShizhenGPT] 资源已释放")

    def __del__(self) -> None:
        """析构时自动释放资源。"""
        try:
            self.close()
        except Exception:
            pass

    def __enter__(self) -> "ShizhenGPTClient":
        """上下文管理器入口。"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """上下文管理器出口，自动关闭。"""
        self.close()
