"""
系统配置管理模块。

使用 Pydantic BaseSettings 管理所有配置项，支持 .env 文件加载环境变量，
包含数据库连接、Redis、GPU、模型路径、LLM API、输出目录、Celery 等配置。
"""

import os
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv
from pydantic import Field, validator
from pydantic_settings import BaseSettings

# 加载 .env 文件（如果存在）
env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)


class GPUConfig(BaseSettings):
    """GPU 资源配置。"""

    device_map: str = Field(default="auto", description="设备映射策略")
    max_memory: Optional[Dict[int, str]] = Field(
        default=None,
        description="每 GPU 最大显存，如 {0: '24GiB', 1: '24GiB'}",
    )
    cuda_visible_devices: Optional[str] = Field(
        default=None,
        description="可见 GPU 设备，如 '0,1'",
    )

    class Config:
        env_prefix = "GPU_"


class Settings(BaseSettings):
    """中医 OCR 系统主配置类。

    所有配置项可通过环境变量或 .env 文件覆盖。
    环境变量前缀：TCM_OCR_
    """

    # ------------------------------------------------------------------
    # 应用基础配置
    # ------------------------------------------------------------------
    app_name: str = Field(default="tcm-ocr-system", description="应用名称")
    debug: bool = Field(default=False, description="调试模式")
    log_level: str = Field(default="INFO", description="日志级别")

    # ------------------------------------------------------------------
    # PostgreSQL 配置
    # ------------------------------------------------------------------
    pg_host: str = Field(default="localhost", description="PostgreSQL 主机")
    pg_port: int = Field(default=5432, description="PostgreSQL 端口")
    pg_database: str = Field(default="tcm_ocr", description="PostgreSQL 数据库名")
    pg_user: str = Field(default="tcm_ocr_user", description="PostgreSQL 用户名")
    pg_password: str = Field(default="", description="PostgreSQL 密码")

    @property
    def pg_dsn(self) -> str:
        """构建 PostgreSQL DSN 字符串。"""
        pwd_part = f":{self.pg_password}" if self.pg_password else ""
        return (
            f"postgresql://{self.pg_user}{pwd_part}@"
            f"{self.pg_host}:{self.pg_port}/{self.pg_database}"
        )

    # ------------------------------------------------------------------
    # Redis 配置
    # ------------------------------------------------------------------
    redis_host: str = Field(default="localhost", description="Redis 主机")
    redis_port: int = Field(default=6379, description="Redis 端口")
    redis_db_broker: int = Field(default=0, description="Celery broker 数据库")
    redis_db_result: int = Field(default=1, description="Celery result 数据库")
    redis_password: Optional[str] = Field(default=None, description="Redis 密码")

    @property
    def redis_url(self) -> str:
        """构建 Redis URL。"""
        pwd_part = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{pwd_part}{self.redis_host}:{self.redis_port}"

    @property
    def celery_broker_url(self) -> str:
        """Celery broker URL。"""
        return f"{self.redis_url}/{self.redis_db_broker}"

    @property
    def celery_result_backend(self) -> str:
        """Celery result backend URL。"""
        return f"{self.redis_url}/{self.redis_db_result}"

    # ------------------------------------------------------------------
    # GPU 配置
    # ------------------------------------------------------------------
    gpu: GPUConfig = Field(default_factory=GPUConfig, description="GPU 配置")

    # ------------------------------------------------------------------
    # 模型路径配置
    # ------------------------------------------------------------------
    model_base_dir: Path = Field(
        default=Path("/opt/models"),
        description="模型基础目录",
    )
    shizhengpt_model_path: Optional[str] = Field(
        default=None,
        description="ShizhenGPT 模型路径",
    )
    shizhengpt_lora_path: Optional[str] = Field(
        default=None,
        description="ShizhenGPT LoRA 适配器路径",
    )
    mineru_popo_model_path: Optional[str] = Field(
        default=None,
        description="MinerU-Popo 模型路径",
    )
    paddleocr_model_dir: Optional[str] = Field(
        default=None,
        description="PaddleOCR 模型目录",
    )

    @property
    def resolved_shizhengpt_path(self) -> Optional[str]:
        """解析后的 ShizhenGPT 模型路径。"""
        if self.shizhengpt_model_path:
            return self.shizhengpt_model_path
        return str(self.model_base_dir / "shizhengpt")

    @property
    def resolved_mineru_popo_path(self) -> Optional[str]:
        """解析后的 MinerU-Popo 模型路径。"""
        if self.mineru_popo_model_path:
            return self.mineru_popo_model_path
        return str(self.model_base_dir / "mineru-popo")

    # ------------------------------------------------------------------
    # 云端 LLM API 配置
    # ------------------------------------------------------------------
    cloud_llm_api_key: Optional[str] = Field(
        default=None,
        description="云端 LLM API Key",
    )
    cloud_llm_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        description="云端 LLM API 基础 URL",
    )
    cloud_llm_model: str = Field(
        default="qwen-max",
        description="云端 LLM 模型名",
    )
    fallback_llm_model: str = Field(
        default="qwen2.5-7b-instruct",
        description="备选本地 LLM 模型名",
    )

    # ------------------------------------------------------------------
    # 输出目录配置
    # ------------------------------------------------------------------
    output_base_dir: Path = Field(
        default=Path("/mnt/agents/output/tcm_ocr_results"),
        description="输出基础目录",
    )
    book_library_dir: Path = Field(
        default=Path("/mnt/agents/output/tcm_ocr_library"),
        description="书籍库目录（SQLite + 临时图片）",
    )
    disputed_images_dir: Path = Field(
        default=Path("/mnt/agents/output/tcm_ocr_disputed"),
        description="争议行图片目录",
    )

    @validator("output_base_dir", "book_library_dir", "disputed_images_dir", pre=True)
    def _ensure_path(cls, v: str) -> str:
        """确保路径为字符串以便 Path 解析。"""
        return str(v)

    # ------------------------------------------------------------------
    # Celery 配置
    # ------------------------------------------------------------------
    celery_timezone: str = Field(default="Asia/Shanghai", description="Celery 时区")
    celery_enable_utc: bool = Field(default=True, description="Celery 启用 UTC")
    celery_task_track_started: bool = Field(
        default=True, description="追踪任务 started 状态"
    )
    celery_task_time_limit: int = Field(
        default=3600, description="单个任务时间限制（秒）"
    )
    celery_worker_prefetch_multiplier: int = Field(
        default=1, description="Worker 预取乘数"
    )
    celery_worker_max_tasks_per_child: int = Field(
        default=50, description="Worker 子进程最大任务数"
    )
    celery_task_serializer: str = Field(default="json", description="任务序列化器")
    celery_accept_content: List[str] = Field(
        default_factory=lambda: ["json"], description="接受的内容类型"
    )
    celery_result_serializer: str = Field(default="json", description="结果序列化器")

    # ------------------------------------------------------------------
    # 预处理参数（可通过配置覆盖年代默认参数）
    # ------------------------------------------------------------------
    preprocess_noise_reduction: Optional[float] = Field(
        default=None, description="噪声消除强度覆盖"
    )
    preprocess_contrast_alpha: Optional[float] = Field(
        default=None, description="对比度增强系数覆盖"
    )
    preprocess_sharpen_sigma: Optional[float] = Field(
        default=None, description="锐化 sigma 覆盖"
    )
    preprocess_deskew_threshold: Optional[float] = Field(
        default=None, description="去歪斜阈值覆盖"
    )

    # ------------------------------------------------------------------
    # 系统行为配置
    # ------------------------------------------------------------------
    enable_human_review: bool = Field(
        default=True, description="启用人工核验环节"
    )
    enable_auto_discovery: bool = Field(
        default=True, description="启用自动发现"
    )
    enable_postgresql_archive: bool = Field(
        default=True, description="启用 PostgreSQL 归档"
    )
    max_retry_per_page: int = Field(
        default=3, description="每页最大重试次数"
    )
    parallel_page_workers: int = Field(
        default=4, description="并行页面处理 Worker 数"
    )

    class Config:
        """Pydantic 配置。"""

        env_prefix = "TCM_OCR_"
        case_sensitive = False
        # 允许从 .env 文件加载
        env_file = ".env"
        env_file_encoding = "utf-8"

    def to_celery_config(self) -> Dict:
        """导出 Celery 配置字典。

        Returns:
            Celery 兼容的配置字典
        """
        return {
            "broker_url": self.celery_broker_url,
            "result_backend": self.celery_result_backend,
            "task_serializer": self.celery_task_serializer,
            "accept_content": self.celery_accept_content,
            "result_serializer": self.celery_result_serializer,
            "timezone": self.celery_timezone,
            "enable_utc": self.celery_enable_utc,
            "task_track_started": self.celery_task_track_started,
            "task_time_limit": self.celery_task_time_limit,
            "worker_prefetch_multiplier": self.celery_worker_prefetch_multiplier,
            "worker_max_tasks_per_child": self.celery_worker_max_tasks_per_child,
        }

    def ensure_directories(self) -> None:
        """确保所有配置的输出目录存在。

        自动创建缺失的目录，权限不足时抛出 OSError。
        """
        dirs_to_create = [
            self.output_base_dir,
            self.book_library_dir,
            self.disputed_images_dir,
        ]
        for d in dirs_to_create:
            try:
                d.mkdir(parents=True, exist_ok=True)
            except PermissionError as e:
                raise OSError(f"权限不足，无法创建目录 {d}: {e}") from e
            except OSError as e:
                raise OSError(f"创建目录失败 {d}: {e}") from e


# 全局单例
def get_settings() -> Settings:
    """获取 Settings 单例。

    Returns:
        Settings 配置实例
    """
    return Settings()
