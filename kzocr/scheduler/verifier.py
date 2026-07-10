"""E3: GlyphVerifier（字形验证器）—— v0.7 §5。

管理一条检测器链，按优先级执行并对单页 OCR 文本给出 `GlyphVerdict` 裁决。
检测器链可外部注入（便于测试 mock），也可由 `_init_detectors` 从
`kzocr/resources` 下的词表加载真实检测器。

关键约定（采纳 traedocu 经验）：
- `verify()` **只产出裁决，绝不改写文本**；TermKB 命中 RARE 走"建议模式"，
  改写决策留待人工/下游。
- 命中后若上下文不支持则降置信而非硬判（交叉验证降权思路）。
- 性能预算：单次 `verify()` < 50ms（§5.5），资源在构造时一次性加载。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol

from kzocr.engine.types import GlyphVerdict
from kzocr.engines import leakage as _leakage

_logger = logging.getLogger(__name__)

# 资源目录（kzocr/resources 包）
_RES_DIR = Path(__file__).resolve().parent.parent / "resources"


# ── 检测器上下文（§5.1）──
@dataclass
class DetectorContext:
    """检测器上下文。每页每引擎检测时传入。"""

    page_num: int
    book_type: str = ""
    pub_era: str = ""
    engine_label: str = ""
    resources: dict = field(default_factory=dict)  # 资源字典（neighbor_texts / next_page_text 等）


# ── 检测器协议（§5.2）──
class Detector(Protocol):
    """验证检测器协议。返回 None 表示"无意见"。"""

    name: str
    enabled: bool
    priority: int

    def check(self, text: str, context: DetectorContext) -> Optional[GlyphVerdict]:
        """执行检测。返回 GlyphVerdict 表示有意见，None 表示无意见（跳过）。"""
        ...


# ── 药名条目（B3：toxic_herbs.json 行结构）──
@dataclass
class HerbEntry:
    """毒性药材条目。"""

    herb: str
    max_dosage_g: float
    usual_dosage_g: str = ""
    toxic_component: str = ""
    note: str = ""


def _load_resource(name: str):
    """从 kzocr/resources 加载 JSON，返回解析后的对象。缺失时返回 None。"""
    path = _RES_DIR / name
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _logger.warning("[verifier] 资源 %s 加载失败: %s", name, exc)
        return None


# ── 5 个预注册检测器（§5.3）──
class ToxinDoseDetector:
    """检测 OCR 结果中的药名+剂量组合是否超出安全上限。优先级 10。"""

    name = "ToxinDoseDetector"
    priority = 10

    def __init__(self, toxic_db: dict[str, HerbEntry] | None = None) -> None:
        self.toxic_db = toxic_db or {}
        self._enabled = bool(self.toxic_db)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def check(self, text: str, context: DetectorContext) -> Optional[GlyphVerdict]:
        """匹配 pattern: (药名) + (数字)(g/克/钱/两)。剂量超上限 → FAIL(critical)。"""
        for herb, info in self.toxic_db.items():
            # re.escape 防止药名含正则特殊字符（如 + / ( ）
            # 药名后须紧跟空白+数字，避免"附子汤"误匹配"附子"
            pattern = re.compile(rf"{re.escape(herb)}\s*(\d+(?:\.\d+)?)\s*(g|克|钱|两)")
            for match in pattern.finditer(text):
                dosage = float(match.group(1))
                unit = match.group(2)
                if unit == "钱":
                    dosage *= 3.0  # 1钱 ≈ 3g
                elif unit == "两":
                    dosage *= 30.0  # 1两 ≈ 30g（汉制，后世沿用）
                if dosage > info.max_dosage_g:
                    return GlyphVerdict(
                        status="FAIL",
                        confidence=1.0,
                        details=(
                            f"toxin_dose;herb={herb};dosage={match.group(1)}{unit};"
                            f"max={info.max_dosage_g}g;severity=critical"
                        ),
                        detector_name=self.name,
                    )
        return None


class LeakageDetector:
    """检测引擎后处理后的残余跨页泄漏。优先级 20。"""

    name = "LeakageDetector"
    priority = 20

    def __init__(self) -> None:
        self._enabled = True

    @property
    def enabled(self) -> bool:
        return self._enabled

    def check(self, text: str, context: DetectorContext) -> Optional[GlyphVerdict]:
        """复用 engines.leakage.LeakageDetector.detect 比较当前页与下一页（B8）。"""
        next_text = context.resources.get("next_page_text")
        if not next_text:
            return None
        leak_pos = _leakage.LeakageDetector.detect(text, next_text)
        if leak_pos is not None:
            return GlyphVerdict(
                status="FAIL",
                confidence=1.0,
                details=f"leakage;leak_start={leak_pos}",
                detector_name=self.name,
            )
        return None


class CharCountSpikeDetector:
    """检测字符数尖峰（D4 层级异常）。优先级 30。"""

    name = "CharCountSpikeDetector"
    priority = 30

    def __init__(self, multiplier: float = 3.0) -> None:
        self.multiplier = multiplier
        self._enabled = True

    @property
    def enabled(self) -> bool:
        return self._enabled

    def check(self, text: str, context: DetectorContext) -> Optional[GlyphVerdict]:
        """需要邻居页文本（context.resources['neighbor_texts']）用于比较（N1）。"""
        neighbor_texts = context.resources.get("neighbor_texts") or []
        neighbors = [t for t in neighbor_texts if t]
        if len(neighbors) < 2:
            return None  # 邻居不足，无法判定
        import statistics

        median = statistics.median(len(t) for t in neighbors)
        if median == 0:
            return None
        if len(text) > median * self.multiplier:
            return GlyphVerdict(
                status="UNCERTAIN",
                confidence=0.5,
                details=f"char_count_spike;len={len(text)};median={int(median)}",
                detector_name=self.name,
            )
        return None


class ConfusionSetDetector:
    """检测命中 confusion_set.json 的形似混淆字。优先级 40。"""

    name = "ConfusionSetDetector"
    priority = 40

    def __init__(self, confusion_set: dict | None = None) -> None:
        # confusion_set: wrong(错误字形) -> correct(正确字形)
        self.confusion_set = confusion_set or {}
        self._enabled = bool(self.confusion_set)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def check(self, text: str, context: DetectorContext) -> Optional[GlyphVerdict]:
        for wrong, correct in self.confusion_set.items():
            if wrong in text:
                return GlyphVerdict(
                    status="UNKNOWN",
                    confidence=0.6,
                    details=f"confusion;wrong={wrong};correct={correct}",
                    detector_name=self.name,
                )
        return None


class TermKBMatcher:
    """匹配知识库术语（rare_allowlist + variant_map），命中 PASS/RARE。优先级 50。"""

    name = "TermKBMatcher"
    priority = 50

    def __init__(
        self,
        rare_allowlist: set | None = None,
        variant_map: dict | None = None,
    ) -> None:
        self.rare_allowlist = rare_allowlist or set()
        self.variant_map = variant_map or {}
        self._enabled = bool(self.rare_allowlist) or bool(self.variant_map)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def check(self, text: str, context: DetectorContext) -> Optional[GlyphVerdict]:
        # 命中 rare_allowlist 中的术语 → PASS
        for term in self.rare_allowlist:
            if term and term in text:
                return GlyphVerdict(
                    status="PASS",
                    confidence=0.9,
                    details=f"rare_allowlist;term={term}",
                    detector_name=self.name,
                )
        # 命中 variant_map 中的变体 → RARE（建议模式，不改写）
        for canonical, variants in self.variant_map.items():
            for variant in variants:
                if variant and variant in text:
                    return GlyphVerdict(
                        status="RARE",
                        confidence=0.7,
                        details=f"variant;canonical={canonical};variant={variant}",
                        detector_name=self.name,
                    )
        return None


class GlyphVerifier:
    """字形验证器。管理检测器链，按优先级执行，支持短路模式。"""

    def __init__(
        self,
        detectors: list[Detector] | None = None,
        config=None,
    ) -> None:
        if detectors is None:
            detectors = self._init_detectors(config)
        self.detectors: list[Detector] = sorted(
            [d for d in (detectors or []) if d.enabled],
            key=lambda d: d.priority,
        )
        # 最近一次 verify 触发的 detector 链（供 E4 写入 EngineCallRecord.detector_chain）
        self.last_detector_chain: list[str] = []

    @staticmethod
    def _init_detectors(config=None) -> list[Detector]:
        """从 kzocr/resources 加载真实检测器链（B6 资源 list→dict/set 归一化）。"""
        detectors: list[Detector] = []

        # ToxinDoseDetector：toxic_herbs.json（list）
        toxic_raw = _load_resource("toxic_herbs.json")
        toxic_db: dict[str, HerbEntry] = {}
        if isinstance(toxic_raw, list):
            for row in toxic_raw:
                if not isinstance(row, dict) or "herb" not in row:
                    continue
                try:
                    toxic_db[row["herb"]] = HerbEntry(
                        herb=row["herb"],
                        max_dosage_g=float(row.get("max_dosage_g", 0)),
                        usual_dosage_g=row.get("usual_dosage_g", ""),
                        toxic_component=row.get("toxic_component", ""),
                        note=row.get("note", ""),
                    )
                except (TypeError, ValueError):
                    continue
        detectors.append(ToxinDoseDetector(toxic_db))

        # LeakageDetector：无需资源，常驻
        detectors.append(LeakageDetector())

        # CharCountSpikeDetector：无需资源，常驻
        detectors.append(CharCountSpikeDetector())

        # ConfusionSetDetector：confusion_set.json（list，B7 跳过正确条）
        conf_raw = _load_resource("confusion_set.json")
        confusion_set: dict = {}
        if isinstance(conf_raw, list):
            for row in conf_raw:
                if not isinstance(row, dict):
                    continue
                wrong = row.get("wrong")
                correct = row.get("correct")
                category = row.get("category", "")
                if not wrong or not correct:
                    continue
                # 跳过"正确"条目（wrong==correct 或 category=='正确'），避免误判 UNKNOWN
                if category == "正确" or wrong == correct:
                    continue
                confusion_set[wrong] = correct
        detectors.append(ConfusionSetDetector(confusion_set))

        # TermKBMatcher：rare_allowlist.json（list）+ variant_map.json（dict）
        rare_raw = _load_resource("rare_allowlist.json")
        rare_allowlist: set = set()
        if isinstance(rare_raw, list):
            for row in rare_raw:
                term = row.get("term") if isinstance(row, dict) else None
                if term:
                    rare_allowlist.add(term)
        variant_raw = _load_resource("variant_map.json")
        variant_map: dict = variant_raw if isinstance(variant_raw, dict) else {}
        detectors.append(TermKBMatcher(rare_allowlist, variant_map))

        return detectors

    def verify(self, text: str, context: DetectorContext) -> GlyphVerdict:
        """强规则短路模式：遇到 PASS 立即返回；critical FAIL 立即返回。

        短路规则（§5.4）：
        - ToxinDoseDetector 的 FAIL(critical) → 立即返回
        - LeakageDetector 的 FAIL → 继续（标记 has_fail）
        - TermKBMatcher 的 PASS → 立即返回
        - CharCountSpikeDetector 的 UNCERTAIN → 不短路
        - ConfusionSetDetector 的 UNKNOWN → 不短路
        """
        has_rare = False
        has_unknown = False
        has_fail = False
        has_uncertain = False
        chain: list[str] = []

        for detector in self.detectors:
            verdict = detector.check(text, context)
            if verdict is None:
                continue
            chain.append(detector.name)

            # 短路：PASS 或 critical FAIL 直接返回
            if verdict.status == "PASS":
                self.last_detector_chain = chain
                return verdict
            if verdict.status == "FAIL":
                if verdict.details and "severity=critical" in verdict.details:
                    self.last_detector_chain = chain
                    return verdict
                has_fail = True

            if verdict.status == "RARE":
                has_rare = True
            elif verdict.status == "UNKNOWN":
                has_unknown = True
            elif verdict.status == "UNCERTAIN":
                has_uncertain = True

        self.last_detector_chain = chain

        # 聚合逻辑（§5.4）
        if not has_fail and not has_unknown and not has_rare and not has_uncertain:
            return GlyphVerdict(
                status="PASS",
                confidence=1.0,
                details="all_detectors_passed",
                detector_name="GlyphVerifier",
            )
        if has_rare and not has_fail and not has_unknown and not has_uncertain:
            return GlyphVerdict(
                status="RARE",
                confidence=0.8,
                details="rare_terms_detected",
                detector_name="GlyphVerifier",
            )
        if has_uncertain and not has_fail and not has_unknown:
            return GlyphVerdict(
                status="UNCERTAIN",
                confidence=0.5,
                details=f"has_uncertain={has_uncertain},has_rare={has_rare}",
                detector_name="GlyphVerifier",
            )
        # FAIL/UNKNOWN 不在此处做最终裁决，由编排循环判定是否降级
        return GlyphVerdict(
            status="UNKNOWN",
            confidence=0.5,
            details=f"has_fail={has_fail},has_unknown={has_unknown}",
            detector_name="GlyphVerifier",
        )
