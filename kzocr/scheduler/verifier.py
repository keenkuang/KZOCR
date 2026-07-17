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
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol

import numpy as np

from kzocr.engine.types import GlyphVerdict
from kzocr.engines import leakage as _leakage
from kzocr.scheduler.cross_align import Divergence, DivergenceArbitration

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


# ── 资源缓存（进程级，避免重复 I/O）──
_RESOURCE_CACHE: dict = {}


def _load_resource(name: str):
    """从 kzocr/resources 加载 JSON，返回解析后的对象。缺失时返回 None。"""
    if name in _RESOURCE_CACHE:
        return _RESOURCE_CACHE[name]
    path = _RES_DIR / name
    if not path.is_file():
        _RESOURCE_CACHE[name] = None
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        _RESOURCE_CACHE[name] = data
        return data
    except (json.JSONDecodeError, OSError) as exc:
        _logger.warning("[verifier] 资源 %s 加载失败: %s", name, exc)
        _RESOURCE_CACHE[name] = None
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

    def verify_with_vision(
        self,
        text: str,
        context: DetectorContext,
        page_img: Optional[np.ndarray] = None,
        vision_adapter: Optional["VisionRecheckAdapter"] = None,
    ) -> GlyphVerdict:
        """两级校验：文本级 + 视觉回看，两者都必须通过。

        1. 文本级校验始终执行（5 个检测器，<50ms）
        2. 视觉回看始终执行（如有适配器，~1.5s）
        3. 两者都通过 → PASS/RARE
        4. 任一不通过 → FAIL（降级触发 Tier2 或人工）

        Args:
            text: 待验证文本
            context: 检测上下文
            page_img: 页面 numpy 图像 (H,W,3)
            vision_adapter: 视觉回看适配器实例
        """
        # ── 第 1 步：文本级校验（始终执行）──
        text_verdict = self.verify(text, context)

        # ── 第 2 步：视觉回看（始终执行，如有适配器）──
        vision_verdict: Optional[GlyphVerdict] = None
        if page_img is not None and vision_adapter is not None:
            try:
                vision_verdict = vision_adapter.recheck(
                    text=text,
                    page_img=page_img,
                    engine_label=context.engine_label,
                )
            except Exception as exc:
                _logger.warning("[verifier] 视觉回看失败: %s", exc)

        # ── 第 3 步：综合裁决 ──
        text_ok = text_verdict.status in ("PASS", "RARE")
        vision_ok = (vision_verdict is None) or (vision_verdict.status == "PASS")

        if text_ok and vision_ok:
            if text_verdict.status == "PASS" and vision_verdict is None:
                return text_verdict  # 纯文本校验通过
            # 标记视觉辅助通过
            return GlyphVerdict(
                status=text_verdict.status,
                confidence=min(text_verdict.confidence, 0.8),
                details=(
                    f"{text_verdict.details or ''}"
                    f"{';vision_recheck_passed' if vision_verdict and vision_verdict.status == 'PASS' else ''}"
                ),
                detector_name="GlyphVerifier",
            )

        # 无视觉适配器时保留 UNCERTAIN（让编排器决定：不确定页可人工复核）
        if text_verdict.status == "UNCERTAIN" and vision_verdict is None:
            return text_verdict

        # 任一不通过 → FAIL
        fail_reasons = []
        if not text_ok:
            fail_reasons.append(f"text={text_verdict.status}")
        if vision_verdict is not None and not vision_ok:
            fail_reasons.append(f"vision={vision_verdict.status}")

        return GlyphVerdict(
            status="FAIL",
            confidence=0.3,
            details=";".join(fail_reasons) + f";text_detail={text_verdict.details or ''}",
            detector_name="GlyphVerifier+Vision",
        )


# ═══════════════════════════════════════════════════════════════
# VisionRecheckAdapter — 视觉回看（§4.2.7）
# ═══════════════════════════════════════════════════════════════

# 分歧级视觉仲裁（Box-Guided VL，借鉴 ocr_pipeline_v2 cross_arbitrate + 豆包帖）
_ARB_CONF_GATE = 0.65   # conf<0.65 → 强制人工
_ARB_BOX_PAD = 8        # 裁框外扩 8px（防墨迹晕染）
_ARB_MIN_BOX = 12       # 框边长下限（px），过小跳过 VL 强制人工


def _build_arbitration_prompt(divergence, confusion_set, mode: str) -> str:
    """构造分歧级仲裁 prompt：只让 VL 核对候选字字形，强制 JSON 输出。

    mode='box_guided'：图片已是裁剪好的候选字小框，无需定位；
    mode='degraded'：无 char box，给出 a_context 让 VL 在整页中定位候选字。
    """
    a, b = divergence.a_seg, divergence.b_seg
    confusion_hints = []
    if confusion_set:
        for wrong, correct in confusion_set.items():
            if wrong in (a, b) or correct in (a, b):
                confusion_hints.append(f"{wrong}≠{correct}")
    confusion_line = "；".join(confusion_hints[:12]) if confusion_hints else "（无）"

    if mode == "box_guided":
        head = (
            "图片中已裁剪出待核对的候选字符（按原图像素坐标裁框）。\n"
            f"候选字（引擎 A 识别）：{a}\n"
            f"另一引擎识别为：{b}\n"
        )
    else:
        head = (
            "请核对整页中下列文本片段里用【】标出的候选字符。\n"
            f"上下文：{divergence.a_context}\n"
            f"候选字（引擎 A 识别）：{a}\n"
            f"另一引擎识别为：{b}\n"
        )
    return (
        head
        + "可能形近混淆清单：" + confusion_line + "\n\n"
        "仅做字形认知核对，不要猜测语义。请严格只输出如下 JSON（不要任何解释文字）：\n"
        '{"candidate_char": "<候选字>", "is_match": true或false, '
        '"confidence": 0.0到1.0之间的数字, "real_char": "<你认为图片中真实字>"}'
    )


def _parse_arbitration_response(text: str):
    """从 VL 原始输出中稳健提取仲裁 JSON；失败返回 None。

    容错：去 ```json 代码围栏、截取首尾大括号、非法 JSON 返回 None。
    """
    if not text:
        return None
    s = text.strip()
    if "```" in s:  # 去 markdown 代码围栏
        parts = s.split("```")
        if len(parts) >= 2:
            s = parts[1]
            if s.startswith("json"):
                s = s[4:]
    lo, hi = s.find("{"), s.rfind("}")
    if lo == -1 or hi == -1 or hi <= lo:
        return None
    frag = s[lo:hi + 1]
    try:
        obj = json.loads(frag)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _gate_arbitration(parsed: dict, a_seg: str, b_seg: str):
    """按 VL 返回的 is_match/confidence/real_char 映射裁决。

    返回 (decision, real_char)。is_match=False 或 conf<0.65 → manual；
    real_char 匹配 A/B 侧 → accepted_a/b；给出第三字 → both_wrong；否则 uncertain。
    """
    is_match = str(parsed.get("is_match", "")).strip().lower() in ("true", "1", "yes", "y")
    try:
        conf = float(parsed.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    real = (parsed.get("real_char") or "").strip()
    if not is_match or conf < _ARB_CONF_GATE:
        return "manual", real
    if real == a_seg:
        return "accepted_a", real
    if real == b_seg:
        return "accepted_b", real
    if real:
        return "both_wrong", real
    return "uncertain", real


class VisionRecheckAdapter:
    """视觉回看适配器：用 VL 模型验证 OCR 文本与图像的匹配度。

    对文本级校验未放行的行，将页面图像 + OCR 文本发给 VL 模型做二次确认。
    支持 SenseNova（首选）和 OpenAI 兼容的 VL 模型。

    设计依据：
    - v0.2 ocr-engine-unification.md §4.2.7
    - §8 假设 1 裁决：协议层预留 VisionRecheckAdapter/recheck 挂点
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        model: str = "",
        max_image_pixels: int = 2048,
        timeout: int = 30,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.max_image_pixels = max_image_pixels
        self.timeout = timeout

    @classmethod
    def sensenova_default(cls) -> "VisionRecheckAdapter":
        """使用 SenseNova 6.7-flash-lite 的默认配置。"""
        return cls(
            api_key=os.environ.get("KZOCR_SENSENOVA_API_KEY", ""),
            base_url="https://token.sensenova.cn/v1",
            model="sensenova-6.7-flash-lite",
        )

    @classmethod
    def modelscope_default(cls) -> "VisionRecheckAdapter":
        """使用 ModelScope Qwen3-VL-8B 的默认配置。"""
        return cls(
            api_key=os.environ.get("KZOCR_MODELSCOPE_API_KEY", ""),
            base_url="https://api-inference.modelscope.cn/v1",
            model="Qwen/Qwen3-VL-8B-Instruct",
        )

    def _resize_image(self, img: np.ndarray) -> np.ndarray:
        """缩放图像至 VL 模型可接受的尺寸（max 2048 长边）。"""
        h, w = img.shape[:2]
        scale = min(self.max_image_pixels / max(h, w), 1.0)
        if scale >= 1.0:
            return img
        from PIL import Image as PILImage
        pil = PILImage.fromarray(img)
        pil = pil.resize((int(w * scale), int(h * scale)), PILImage.LANCZOS)
        return np.array(pil)

    def _img_to_b64(self, img: np.ndarray) -> str:
        """numpy 图像 → base64 data URL（JPEG 高质量）。"""
        from PIL import Image as PILImage
        import io
        pil = PILImage.fromarray(img)
        buf = io.BytesIO()
        pil.save(buf, format="JPEG", quality=90)
        import base64
        return f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode()}"

    def recheck(
        self,
        text: str,
        page_img: np.ndarray,
        bbox: tuple | None = None,
        engine_label: str = "",
    ) -> GlyphVerdict:
        """视觉回看主入口。

        Args:
            text: 待验证的 OCR 文本
            page_img: 页面图像 (H,W,3)
            bbox: 可选裁剪区域 (x1,y1,x2,y2)，有则只验证裁剪区域
            engine_label: 来源引擎名（仅日志）

        Returns:
            GlyphVerdict: PASS=图像确认文字正确, FAIL=文字与图像不匹配
        """
        import time as _time

        if not self.api_key or not self.base_url or not self.model:
            return GlyphVerdict(
                status="UNKNOWN",
                confidence=0.0,
                details="vision_recheck_not_configured",
                detector_name="VisionRecheckAdapter",
            )

        # 裁剪 bbox 区域（如果有）
        if bbox is not None:
            x1, y1, x2, y2 = bbox
            crop = page_img[y1:y2, x1:x2]
            if crop.size == 0:
                crop = self._resize_image(page_img)
        else:
            crop = self._resize_image(page_img)

        img_b64 = self._img_to_b64(crop)

        # 构造验证 prompt
        verify_prompt = (
            "逐字核对以下OCR识别结果与图片中的文字是否完全一致。\n\n"
            f"OCR结果：{text}\n\n"
            "核对要求：\n"
            "1. 逐字比对，包括汉字、数字、标点符号（，。、？！""''《》（）；：）、英文字母、特殊符号（§◇@#等）\n"
            "2. 检查是否多字、漏字、错字、顺序颠倒\n"
            "3. 检查数字、剂量、百分比是否完全匹配\n"
            "4. 检查全半角标点是否与图片一致\n\n"
            "如果图片中每个字符（含标点）都与OCR结果完全匹配，请仅回答 PASS。\n"
            "如果存在任何不匹配（包括标点符号差异），请仅回答 FAIL。"
        )

        import json
        import urllib.request

        url = self.base_url.rstrip("/") + "/chat/completions"
        payload = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": img_b64}},
                {"type": "text", "text": verify_prompt},
            ]}],
            "max_tokens": 2048,
            "temperature": 0.0,
            "reasoning_effort": "none",
        }).encode()

        t0 = _time.time()
        try:
            req = urllib.request.Request(url, data=payload, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", f"Bearer {self.api_key}")
            resp = urllib.request.urlopen(req, timeout=self.timeout)
            body = json.loads(resp.read().decode())
            ms = int((_time.time() - t0) * 1000)
            answer = (body.get("choices", [{}])[0]
                      .get("message", {}).get("content", "") or "").strip()
            _logger.info("[VisionRecheck] %s → %s (%dms)", engine_label or self.model, answer[:50], ms)
        except Exception as exc:
            _logger.warning("[VisionRecheck] API 调用失败: %s", exc)
            return GlyphVerdict(
                status="UNKNOWN",
                confidence=0.0,
                details=f"vision_recheck_api_error;{str(exc)[:60]}",
                detector_name="VisionRecheckAdapter",
            )

        if answer.upper().startswith("PASS"):
            return GlyphVerdict(
                status="PASS",
                confidence=0.7,
                details=f"vision_recheck_passed;latency_ms={ms}",
                detector_name="VisionRecheckAdapter",
            )
        return GlyphVerdict(
            status="FAIL",
            confidence=0.5,
            details=f"vision_recheck_failed;response={answer[:100]};latency_ms={ms}",
            detector_name="VisionRecheckAdapter",
        )

    def _post_vl(self, text_prompt: str, img_b64: str) -> str:
        """发「图 + 文」到 VL 模型，返回模型原始文本（网络异常向上抛）。

        与 `recheck` 共用同一套 chat/completions 协议；抽出来便于仲裁复用与单测 mock。
        """
        import urllib.request as _req

        url = self.base_url.rstrip("/") + "/chat/completions"
        payload = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": img_b64}},
                {"type": "text", "text": text_prompt},
            ]}],
            "max_tokens": 512,
            "temperature": 0.0,
            "reasoning_effort": "none",
        }).encode()
        req = _req.Request(url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self.api_key}")
        resp = _req.urlopen(req, timeout=self.timeout)
        body = json.loads(resp.read().decode())
        return (body.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()

    def arbitrate_divergence(
        self,
        divergence: "Divergence",
        page_img,
        confusion_set: Optional[dict] = None,
        bucket=None,
    ) -> "DivergenceArbitration":
        """分歧级视觉仲裁（Box-Guided VL）。

        借鉴 ocr_pipeline_v2 `cross_arbitrate` + 豆包帖《形近字共识错误难破的原因与应对》：
        不给 VL 整页问 PASS/FAIL，而是拿候选字 + 它的 quad 框，**只让 VL 重审那小框字形**
        （认知对比），Prompt 强制 JSON 输出，再按置信度门控映射裁决。

        Args:
            divergence: cross_align.Divergence 分歧点（.a_seg/.b_seg/.a_context/.boxes）
            page_img: 页面图像 (H,W,3) 或 None
            confusion_set: 形近字黑名单 wrong->correct（可选，用于 Prompt 提示）
            bucket: 可选 MultiTokenRateLimiter 共享进程级限流

        Returns:
            DivergenceArbitration：decision / confidence / real_char / mode / raw

        行为：
        - 未配置（无 key/base/model）或无图像 → 直接 manual，不调网络。
        - `divergence.boxes` 非空 → 精确裁框（Box-Guided）；多字框/过小框 → manual 跳过。
        - `boxes` 为空（当前 KZOCR 归一化数据无逐字 bbox）→ 退化整页缩图 + 上下文提示。
        - JSON 解析失败 / is_match=False / conf<0.65 → manual。
        """
        if not self.api_key or not self.base_url or not self.model:
            return DivergenceArbitration(
                page_no=divergence.page_no, decision="manual",
                raw="vision_recheck_not_configured", engine="",
            )
        if page_img is None:
            return DivergenceArbitration(
                page_no=divergence.page_no, decision="manual",
                raw="vision_recheck_no_image", engine=self.model,
            )

        # ── 裁框：boxes 存在则精确 Box-Guided，否则退化整页 + 上下文提示 ──
        boxes = list(divergence.boxes or [])
        mode = "degraded"
        crop = None
        if boxes:
            if len(boxes) > 1:
                return DivergenceArbitration(
                    page_no=divergence.page_no, decision="manual",
                    raw="box_multi_char_skip", mode="box_guided", engine=self.model,
                )
            x1, y1, x2, y2 = (int(v) for v in boxes[0])
            # 原始框尺寸过小（含墨迹也难辨）→ 跳过 VL 强制人工（padding 前判断）
            if (x2 - x1) < _ARB_MIN_BOX or (y2 - y1) < _ARB_MIN_BOX:
                return DivergenceArbitration(
                    page_no=divergence.page_no, decision="manual",
                    raw="box_too_small_skip", mode="box_guided", engine=self.model,
                )
            h, w = page_img.shape[:2]
            x1, x2 = max(0, x1 - _ARB_BOX_PAD), min(w, x2 + _ARB_BOX_PAD)
            y1, y2 = max(0, y1 - _ARB_BOX_PAD), min(h, y2 + _ARB_BOX_PAD)
            crop = page_img[y1:y2, x1:x2]
            mode = "box_guided"

        if crop is None:
            crop = self._resize_image(page_img)
        img_b64 = self._img_to_b64(crop)

        prompt = _build_arbitration_prompt(divergence, confusion_set, mode)
        try:
            if bucket is not None:
                bucket.acquire()
            raw = self._post_vl(prompt, img_b64)
        except Exception as exc:
            return DivergenceArbitration(
                page_no=divergence.page_no, decision="manual",
                raw=f"vision_recheck_api_error;{str(exc)[:80]}", mode=mode, engine=self.model,
            )

        parsed = _parse_arbitration_response(raw)
        if parsed is None:
            return DivergenceArbitration(
                page_no=divergence.page_no, decision="manual",
                confidence=0.0, raw=raw[:200], mode=mode, engine=self.model, real_char="",
            )
        decision, real_char = _gate_arbitration(parsed, divergence.a_seg, divergence.b_seg)
        return DivergenceArbitration(
            page_no=divergence.page_no, decision=decision,
            confidence=float(parsed.get("confidence", 0.0) or 0.0),
            real_char=real_char, raw=raw[:200], mode=mode, engine=self.model,
        )
