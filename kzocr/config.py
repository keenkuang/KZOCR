"""KZOCR 配置：路径与环境变量。

本地开发时可直接指向已存在的两个项目目录，无需 clone 子模块：
    export KIMI_ENGINE_DIR=/home/keen/kimi_agent_ocr/tcm_ocr_system_v1.1
    export ZAI_DIR=/home/keen/tcm_ocr_zai
    export KHUB_BASE_URL=http://127.0.0.1:8000
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)


if TYPE_CHECKING:
    from kzocr.scheduler.scheduler import Budget


def _safe_int(val: str, default: int, name: str = "") -> int:
    """安全解析整数环境变量，解析失败时回退到默认值并记录警告。"""
    try:
        return int(val)
    except (TypeError, ValueError):
        logger.warning("[config] %s='%s' 不是有效整数，使用默认值 %d", name, val, default)
        return default


def _safe_bool(val: str, default: bool) -> bool:
    """安全解析布尔环境变量，True 为 "1"/"true" 等。"""
    return val.lower() in ("1", "true", "yes") if val else default


@dataclass
class SchedulerConfig:
    """v0.7 调度器配置（§7.3）。由 ``from_env()`` 从环境变量构造。"""

    max_tier1_engines: int = 2
    max_tier2_engines: int = 1
    max_tier3_engines: int = 1
    max_pages: int = 50
    total_timeout_s: int = 7200
    max_time_per_page_ms: int = 120000
    benchmark_dir: str = ""
    trace_dir: str = ""
    engine_parallel: bool = False
    # 页级并发编排（默认关；KZOCR_PAGE_PARALLEL=1 开启）。
    # 开启后主循环用 ThreadPoolExecutor 跨页并行（每 worker 独立渲染），
    # 合并阶段串行写共享状态（db/registry/tally），彻底规避 sqlite/registry 竞态。
    page_parallel: bool = False
    # 页级并发 worker 数（0=自动 min(CPU,4)，仅 page_parallel 生效）。
    page_workers: int = 0
    allow_cloud_vision: bool = False
    tier_limit: int = 3
    cross_check: bool = True  # 自 v0.7 稳定后默认开；设 KZOCR_ENABLE_CROSS_CHECK=0 关闭
    consensus_sample_rate: float = 0.0
    persist_db: bool = False
    # W4 VL 仲裁预算（0 = 不限）：per_run 限制单次编排视觉仲裁调用数，
    # per_day 跨书当日累计（经 JSON 文件 best-effort），防止付费端点失控开销。
    vl_budget_per_run: int = 0
    vl_budget_per_day: int = 0
    db_dir: str = ""
    half_life_days: float = 7.0

    @classmethod
    def from_env(cls) -> "SchedulerConfig":
        """从环境变量构造 SchedulerConfig（§7.3 完整映射）。"""
        return cls(
            max_tier1_engines=_safe_int(os.environ.get("KZOCR_MAX_TIER1_ENGINES", "2"), 2, "KZOCR_MAX_TIER1_ENGINES"),
            max_tier2_engines=_safe_int(os.environ.get("KZOCR_MAX_TIER2_ENGINES", "1"), 1, "KZOCR_MAX_TIER2_ENGINES"),
            max_tier3_engines=_safe_int(os.environ.get("KZOCR_MAX_TIER3_ENGINES", "1"), 1, "KZOCR_MAX_TIER3_ENGINES"),
            max_pages=_safe_int(os.environ.get("KZOCR_MAX_PAGES", "50"), 50, "KZOCR_MAX_PAGES"),
            total_timeout_s=_safe_int(os.environ.get("KZOCR_TOTAL_TIMEOUT", "7200"), 7200, "KZOCR_TOTAL_TIMEOUT"),
            max_time_per_page_ms=_safe_int(os.environ.get("KZOCR_MAX_TIME_PER_PAGE_MS", "120000"), 120000, "KZOCR_MAX_TIME_PER_PAGE_MS"),
            benchmark_dir=os.environ.get("KZOCR_BENCHMARK_DIR", ""),
            trace_dir=os.environ.get("KZOCR_TRACE_DIR", ""),
            engine_parallel=_safe_bool(os.environ.get("KZOCR_ENGINE_PARALLEL", ""), False),
            page_parallel=_safe_bool(os.environ.get("KZOCR_PAGE_PARALLEL", ""), False),
            page_workers=_safe_int(os.environ.get("KZOCR_PAGE_WORKERS", "0"), 0, "KZOCR_PAGE_WORKERS"),
            allow_cloud_vision=_safe_bool(os.environ.get("KZOCR_ALLOW_CLOUD_VISION", ""), False),
            tier_limit=_safe_int(os.environ.get("KZOCR_TIER_LIMIT", "3"), 3, "KZOCR_TIER_LIMIT"),
            cross_check=_safe_bool(os.environ.get("KZOCR_ENABLE_CROSS_CHECK", ""), True),
            consensus_sample_rate=float(os.environ.get("KZOCR_CONSENSUS_SAMPLE_RATE", "0.0") or "0.0"),
            persist_db=_safe_bool(os.environ.get("KZOCR_PERSIST_DB", ""), False),
            vl_budget_per_run=_safe_int(os.environ.get("KZOCR_VL_BUDGET_PER_RUN", "0"), 0, "KZOCR_VL_BUDGET_PER_RUN"),
            vl_budget_per_day=_safe_int(os.environ.get("KZOCR_VL_BUDGET_PER_DAY", "0"), 0, "KZOCR_VL_BUDGET_PER_DAY"),
            db_dir=os.environ.get("KZOCR_DB_DIR", ""),
            half_life_days=float(os.environ.get("KZOCR_DECAY_HALF_LIFE_DAYS", "7.0") or "7.0"),
        )

    def to_budget(self) -> Budget:
        """构造 ``Budget`` 供编排主循环使用。"""
        from kzocr.scheduler.scheduler import Budget

        return Budget(
            max_pages=self.max_pages,
            max_wall_clock_ms=self.total_timeout_s * 1000,
            max_time_per_page_ms=self.max_time_per_page_ms,
            allow_cloud_vision=self.allow_cloud_vision,
        )


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
    # v0.7 编排调度系统（默认启用，v0.6 遗产路径已移除）
    use_v07: bool = True
    # v0.5 AMEND D0: VLM 缓存及中间产物输出目录
    kzocr_output_dir: str = ""  # will be set by from_env() / load_config()
    # v0.5 AMEND D0: 缓存 TTL（秒），默认 24h
    cache_ttl_seconds: int = 86400
    # v0.7 调度器配置（§7.3）
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)

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
            vlm_port=_safe_int(os.environ.get("KZOCR_VLM_PORT", "18080"), 18080, "KZOCR_VLM_PORT"),
            sensenova_api_key=os.environ.get("SENSENOVA_API_KEY", ""),
            sensenova_model=os.environ.get("SENSENOVA_MODEL", "sensenova-6.7-flash-lite"),
            sensenova_base_url=os.environ.get("SENSENOVA_BASE_URL", "https://token.sensenova.cn/v1/chat/completions"),
            sensenova_timeout=_safe_int(os.environ.get("SENSENOVA_TIMEOUT", "180"), 180, "SENSENOVA_TIMEOUT"),
            deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
            deepseek_model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            deepseek_base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            deepseek_rpm=_safe_int(os.environ.get("DEEPSEEK_RPM", "20"), 20, "DEEPSEEK_RPM"),
            kzocr_output_dir=os.environ.get("KZOCR_OUTPUT_DIR", "/tmp/kzocr/output"),
            cache_ttl_seconds=_safe_int(os.environ.get("KZOCR_CACHE_TTL", "86400"), 86400, "KZOCR_CACHE_TTL"),
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
        KZOCR_ALLOW_CLOUD_VISION → allow_cloud_vision（云端 vision 许可）
    """
    cfg = Config.from_env()
    cfg.use_mock = os.environ.get("KZOCR_USE_MOCK", "0") in ("1", "true", "True")
    cfg.require_real = os.environ.get("KZOCR_REQUIRE_REAL", "0") in ("1", "true", "True")
    cfg.use_vlm = os.environ.get("KZOCR_USE_VLM", "0") in ("1", "true", "True")
    cfg.vlm_engine = os.environ.get("KZOCR_VLM_ENGINE", "auto")
    cfg.allow_cloud_vision = os.environ.get("KZOCR_ALLOW_CLOUD_VISION", "0") in ("1", "true", "True")
    cfg.scheduler = SchedulerConfig.from_env()
    return cfg


# Module-level singleton used by the engine layer
config = load_config()
