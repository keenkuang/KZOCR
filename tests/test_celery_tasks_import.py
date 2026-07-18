"""Celery 任务模块导入与重试 API 回归测试。

历史问题：`tasks.py` 顶层 `from celery.exceptions import MaxRetriesError` 在
celery 5.4+ 已移除该类，导致整个模块导入即崩、Celery worker 启动失败。
本测试锁定：模块可导入 + 重试耗尽分支改用 `self.retry()`（不再显式引用
已移除的 `MaxRetriesError`）。无需 broker / 真实引擎，CI 可跑。
"""

from __future__ import annotations

from pathlib import Path

import pytest

TASKS_SRC = Path(__file__).resolve().parents[1] / "kzocr" / "tcm_ocr" / "celery_tasks" / "tasks.py"


def test_celery_tasks_module_imports() -> None:
    """模块必须能干净导入——回归 MaxRetriesError 坏导入导致 worker 崩溃。"""
    import kzocr.tcm_ocr.celery_tasks.tasks as tasks  # noqa: F401

    assert tasks.process_book_task is not None
    assert tasks.process_book_task.name == "tcm_ocr.celery_tasks.tasks.process_book_task"


def test_no_explicit_max_retries_error_import() -> None:
    """源码不得再顶层导入或显式 raise 已移除的 MaxRetriesError。

    注意：`MaxRetriesExceededError`（celery 当前正确类名）包含子串
    `MaxRetriesError`，故用负向先行断言排除它，只禁止裸 `MaxRetriesError`。
    """
    import re

    text = TASKS_SRC.read_text(encoding="utf-8")
    bad = re.search(r"MaxRetriesError(?!ExceededError)", text)
    assert bad is None, (
        f"celery 5.4+ 已移除 MaxRetriesError，禁止使用；"
        f"命中: {bad.group(0)!r} @ {bad.start()}"
    )


def test_retry_branch_uses_self_retry() -> None:
    """重试耗尽分支应调用 self.retry()，由 celery 内部抛出正确异常。"""
    text = TASKS_SRC.read_text(encoding="utf-8")
    assert "raise self.retry(exc=exc)" in text, "重试耗尽应改调 self.retry()"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
