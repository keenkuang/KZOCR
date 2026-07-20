"""VL 仲裁预算守卫（W4，v0.21 后续）。

限制单次编排（``per_run``）与跨书当日（``per_day``）的视觉仲裁调用数，
防止大批量处理时 GLM-4V-Flash 等付费端点产生失控开销。每次实际 VL 模型
调用（``arbitrate_divergence`` / ``recheck``）前调 :meth:`VLBudgetTracker.can_spend`，
允许则 :meth:`spend` 计数；超预算时调用方应跳过 VL、将分歧留人工队列。

``per_day`` 跨进程累计依赖可注入的 ``DayStore``（默认基于 JSON 文件，
best-effort，不保证并发安全——预算为软上限守卫）。
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional, Protocol

logger = logging.getLogger(__name__)


@dataclass
class VLBudgetConfig:
    """VL 预算配置：0 = 不限。"""

    per_run: int = 0
    per_day: int = 0


class DayStore(Protocol):
    """当日累计计数存储（跨书/跨进程），供 per_day 预算使用。"""

    def get(self, day: str) -> int: ...
    def add(self, day: str, n: int) -> None: ...


class _FileDayStore:
    """基于 JSON 文件的当日累计（best-effort，不保证并发安全）。"""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or os.path.join(
            os.environ.get("KZOCR_OUTPUT_DIR", "/tmp/kzocr/output"),
            ".vl_budget.json",
        )

    def get(self, day: str) -> int:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return int(data.get(day, 0))
        except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
            return 0

    def add(self, day: str, n: int) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            data = {}
        data[day] = int(data.get(day, 0)) + n
        # 仅保留最近 7 天，避免文件无限增长
        keep = sorted(data.keys())[-7:]
        data = {k: data[k] for k in keep}
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f)


class _MemDayStore:
    """内存当日累计（测试用，可注入观察）。"""

    def __init__(self) -> None:
        self.data: dict[str, int] = {}

    def get(self, day: str) -> int:
        return self.data.get(day, 0)

    def add(self, day: str, n: int) -> None:
        self.data[day] = self.data.get(day, 0) + n


class VLBudgetTracker:
    """VL 调用预算守卫。

    创建即绑定本次编排（per_run 计数在内存）与当日（per_day 计数经 DayStore）。
    每次实际 VL 调用前 ``can_spend()`` 判余量，允许则 ``spend()`` 计数。
    """

    def __init__(
        self,
        cfg: VLBudgetConfig,
        clock: Callable[[], float] = time.time,
        day_store: Optional[DayStore] = None,
    ) -> None:
        self.per_run = cfg.per_run
        self.per_day = cfg.per_day
        self._run_used = 0
        self._clock = clock
        self._day_store: DayStore = day_store or _FileDayStore()
        self._today = date.fromtimestamp(clock()).isoformat()
        self._day_used = self._day_store.get(self._today) if self.per_day > 0 else 0

    def can_spend(self) -> bool:
        """是否还有预算额度（任一维度超限即 False）。"""
        if self.per_run > 0 and self._run_used >= self.per_run:
            return False
        if self.per_day > 0 and self._day_used >= self.per_day:
            return False
        return True

    def spend(self) -> None:
        """记一次 VL 调用（仅在确已发起调用后调用）。"""
        self._run_used += 1
        if self.per_day > 0:
            self._day_used += 1
            self._day_store.add(self._today, 1)

    @property
    def exhausted(self) -> bool:
        return not self.can_spend()

    @property
    def run_used(self) -> int:
        return self._run_used

    @property
    def day_used(self) -> int:
        return self._day_used

    def summary(self) -> str:
        return (
            f"run={self._run_used}/{self.per_run or '∞'};"
            f"day={self._day_used}/{self.per_day or '∞'}"
        )
