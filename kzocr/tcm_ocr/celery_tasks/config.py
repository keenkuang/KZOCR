"""
Celery 配置模块。

定义 Celery 应用的所有配置参数，包括 broker、result backend、
序列化、时区、Worker 行为等。
"""

import logging
import os
from typing import Dict, List

logger = logging.getLogger(__name__)

# =============================================================================
# Broker & Backend
# =============================================================================

# 默认指向本机 Redis；部署时通过环境变量覆盖（如 docker-compose 中指向 redis 服务）。
broker_url: str = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
"""Celery broker URL，使用 Redis 数据库 0。可用 CELERY_BROKER_URL 覆盖。"""

result_backend: str = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
"""Celery result backend URL，使用 Redis 数据库 1（与 broker 隔离）。可用 CELERY_RESULT_BACKEND 覆盖。"""

# =============================================================================
# 序列化配置
# =============================================================================

task_serializer: str = "json"
"""任务消息序列化器。"""

accept_content: List[str] = ["json"]
"""Worker 接受的内容类型列表，安全考虑只接受 json。"""

result_serializer: str = "json"
"""结果序列化器。"""

# =============================================================================
# 时区配置
# =============================================================================

timezone: str = "Asia/Shanghai"
"""时区设置，使用上海时区。"""

enable_utc: bool = True
"""启用 UTC 时间戳存储。"""

# =============================================================================
# 任务追踪 & 超时
# =============================================================================

task_track_started: bool = True
"""追踪任务 started 状态，便于监控任务执行进度。"""

task_time_limit: int = 3600
"""单个硬时间限制（秒），超过后强制终止任务。

注意：这是硬限制（SIGKILL），应设置得比软限制更大。
对于书籍处理任务，设置为 1 小时。
"""

task_soft_time_limit: int = 3300
"""单个软时间限制（秒），超过后发送 SIGTERM 让任务优雅退出。

比硬限制少 5 分钟，给任务清理时间。
"""

# =============================================================================
# Worker 配置
# =============================================================================

worker_prefetch_multiplier: int = 1
"""Worker 预取乘数。

设置为 1 表示公平调度：每个 Worker 只预取 1 个任务，
避免某个 Worker 积压大量长任务而导致其他 Worker 空闲。
"""

worker_max_tasks_per_child: int = 50
"""每个 Worker 子进程最大处理任务数。

达到此数量后子进程会被替换，防止内存泄漏累积。
对于 OCR 任务（涉及大量图像处理），定期重启很重要。
"""

worker_concurrency: int = 4
"""Worker 并发数（默认，可通过命令行 -c 覆盖）。"""

# =============================================================================
# 结果配置
# =============================================================================

result_expires: int = 86400 * 7
"""任务结果过期时间（秒），默认 7 天。

OCR 结果较大，不宜永久保留。
"""

result_extended: bool = True
"""存储额外的结果元数据（如执行时间等）。"""

# =============================================================================
# 任务路由
# =============================================================================

task_routes: Dict[str, Dict[str, str]] = {
    "tcm_ocr.celery_tasks.tasks.process_book_task": {"queue": "books"},
    "tcm_ocr.celery_tasks.tasks.process_page_batch_task": {"queue": "pages"},
    "tcm_ocr.celery_tasks.tasks.recalibrate_publisher_bonus_task": {"queue": "maintenance"},
    "tcm_ocr.celery_tasks.tasks.archive_book_data_task": {"queue": "archival"},
    "tcm_ocr.celery_tasks.tasks.submit_knowledge_batch_task": {"queue": "knowledge"},
}
"""任务路由配置，按任务类型分发到不同队列。"""

task_default_queue: str = "default"
"""默认队列名。"""

# =============================================================================
# 任务注解（默认配置）
# =============================================================================

task_annotations: Dict[str, Dict[str, int]] = {
    "*": {
        "time_limit": 3600,
        "soft_time_limit": 3300,
        "max_retries": 2,
        "default_retry_delay": 60,
    },
}
"""任务默认注解，应用于所有任务。"""

# =============================================================================
# 导入模块
# =============================================================================

imports: List[str] = [
    "tcm_ocr.celery_tasks.tasks",
]
"""启动时导入的任务模块列表。"""

# =============================================================================
# Beat 定时任务（可选）
# =============================================================================

beat_schedule: Dict[str, Dict[str, str]] = {}
"""定时任务配置，由外部动态加载或覆盖。"""

# =============================================================================
# 安全 & 其他
# =============================================================================

task_always_eager: bool = False
"""禁用同步模式（生产环境必须异步）。"""

task_store_eager_result: bool = False
"""不存储同步任务结果。"""

worker_send_task_events: bool = True
"""发送任务事件（用于 Flower 监控）。"""

task_send_sent_event: bool = True
"""发送任务 sent 事件。"""

# Redis 连接池配置
broker_pool_limit: int = 10
"""Broker 连接池大小。"""

broker_connection_retry: bool = True
"""连接失败时重试。"""

broker_connection_retry_on_startup: bool = True
"""启动时连接重试。"""

broker_connection_timeout: int = 30
"""Broker 连接超时（秒）。"""

# 结果后端连接池
redis_max_connections: int = 20
"""Redis 最大连接数。"""

# 禁用速率限制（OCR 任务不适合，空字符串表示不限）
task_default_rate_limit: str = ""

# Worker 优雅关闭
worker_cancel_long_running_tasks_on_connection_loss: bool = True
"""连接丢失时取消长时间运行的任务。"""

# =============================================================================
# 配置验证 & 导出
# =============================================================================


def get_celery_config() -> Dict[str, str]:
    """导出所有 Celery 配置为字典。

    Returns:
        Celery 配置字典
    """
    config: Dict[str, str] = {}

    for key in dir(__import__(__name__)):
        if key.startswith("_"):
            continue
        value = globals().get(key)
        if value is not None and not callable(value):
            config[key] = value

    return config


def validate_config() -> List[str]:
    """验证 Celery 配置的有效性。

    Returns:
        问题列表，空列表表示无问题
    """
    issues: List[str] = []

    # 检查 broker URL
    if not broker_url:
        issues.append("broker_url 未设置")
    elif not broker_url.startswith("redis://"):
        issues.append(f"broker_url 格式不正确: {broker_url}")

    # 检查 result backend
    if not result_backend:
        issues.append("result_backend 未设置")
    elif not result_backend.startswith("redis://"):
        issues.append(f"result_backend 格式不正确: {result_backend}")

    # 检查序列化器
    if task_serializer not in ("json", "msgpack", "yaml", "pickle"):
        issues.append(f"不支持的 task_serializer: {task_serializer}")

    # 检查时区
    if not timezone:
        issues.append("timezone 未设置")

    if not issues:
        logger.info("Celery 配置验证通过")
    else:
        logger.warning("Celery 配置问题: %s", issues)

    return issues
