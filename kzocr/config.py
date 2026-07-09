"""KZOCR 配置：路径与环境变量。

本地开发时可直接指向已存在的两个项目目录，无需 clone 子模块：
    export KIMI_ENGINE_DIR=/home/keen/kimi_agent_ocr/tcm_ocr_system_v1.1
    export ZAI_DIR=/home/keen/tcm_ocr_zai
    export KHUB_BASE_URL=http://127.0.0.1:8000
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Config:
    # kimi OCR 引擎包所在目录（含 tcm_ocr/ 包）
    kimi_engine_dir: str = ""
    # zai 控制台项目目录（其 SQLite 库在 <zai_dir>/db/custom.db）
    zai_dir: str = ""
    # zai 的 Prisma/SQLite 数据库文件路径
    zai_db: str = ""
    # kHUB 服务基址（其 API 新增了 POST /documents）
    khub_base_url: str = "http://127.0.0.1:8000"
    # kHUB 本地数据库（用于自检，可选）
    khub_db: str = ""
    # 是否强制使用 mock 引擎（KZOCR_USE_MOCK=1）
    use_mock: bool = False
    # 真实引擎失败时是否抛错而非降级（KZOCR_REQUIRE_REAL=1）
    require_real: bool = False
    # VLM 直接模式（绕过 BookPipeline，用 VLM 逐页 OCR）
    use_vlm: bool = False
    # VLM 引擎选择：auto（优先 SenseNova，降级 PaddleOCR-VL）/ sensenova / paddleocr_vl16
    vlm_engine: str = "auto"
    # llama-server 地址（PaddleOCR-VL 用）
    vlm_host: str = "127.0.0.1"
    vlm_port: int = 18080
    # SenseNova 云端 API 配置
    sensenova_api_key: str = ""
    sensenova_model: str = "sensenova-6.7-flash-lite"
    sensenova_base_url: str = "https://token.sensenova.cn/v1/chat/completions"
    sensenova_timeout: int = 180
    # DeepSeek 后处理配置（设计 §2.3，TOC 分节管线后处理阶段使用）
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_rpm: int = 20
    # 是否允许把页面图像发往第三方云端 vision（数据出境许可，默认关闭）
    allow_cloud_vision: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        kimi = os.environ.get("KIMI_ENGINE_DIR", "")
        zai = os.environ.get("ZAI_DIR", "/home/keen/tcm_ocr_zai")
        zai_db = os.environ.get("ZAI_DB", os.path.join(zai, "db", "custom.db"))
        khub_db = os.path.expanduser(os.environ.get("KHUB_DB", "~/.khub/khub.db"))
        return cls(
            kimi_engine_dir=kimi,
            zai_dir=zai,
            zai_db=zai_db,
            khub_base_url=os.environ.get("KHUB_BASE_URL", "http://127.0.0.1:8000"),
            khub_db=khub_db,
            vlm_host=os.environ.get("KZOCR_VLM_HOST", "127.0.0.1"),
            vlm_port=int(os.environ.get("KZOCR_VLM_PORT", "18080")),
            sensenova_api_key=os.environ.get("SENSENOVA_API_KEY", ""),
            sensenova_model=os.environ.get("SENSENOVA_MODEL", "sensenova-6.7-flash-lite"),
            sensenova_base_url=os.environ.get("SENSENOVA_BASE_URL", "https://token.sensenova.cn/v1/chat/completions"),
            sensenova_timeout=int(os.environ.get("SENSENOVA_TIMEOUT", "180")),
            deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
            deepseek_model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            deepseek_base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            deepseek_rpm=int(os.environ.get("DEEPSEEK_RPM", "20")),
        )


# 常用默认值
KIMI_ENGINE_PKG = "tcm_ocr"  # kimi 引擎 Python 包名


def load_config() -> "Config":
    """读取环境变量并构造 Config 单例。

    除 from_env() 已有字段外，额外识别：
        KZOCR_USE_MOCK    → use_mock（是否强制 mock 引擎）
        KZOCR_REQUIRE_REAL → require_real（真实失败是否抛错）
        KZOCR_USE_VLM     → use_vlm（是否启用 VLM 直接模式）
        KZOCR_VLM_ENGINE  → vlm_engine（VLM 引擎选择：auto/sensenova/paddleocr_vl16）
    """
    cfg = Config.from_env()
    cfg.use_mock = os.environ.get("KZOCR_USE_MOCK", "0") in ("1", "true", "True")
    cfg.require_real = os.environ.get("KZOCR_REQUIRE_REAL", "0") in ("1", "true", "True")
    cfg.use_vlm = os.environ.get("KZOCR_USE_VLM", "0") in ("1", "true", "True")
    cfg.vlm_engine = os.environ.get("KZOCR_VLM_ENGINE", "auto")
    cfg.allow_cloud_vision = os.environ.get("KZOCR_ALLOW_CLOUD_VISION", "0") in ("1", "true", "True")
    return cfg


# Module-level singleton used by the engine layer
config = load_config()
