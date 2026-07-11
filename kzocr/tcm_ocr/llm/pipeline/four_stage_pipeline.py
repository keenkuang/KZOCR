"""
四级决策校对链路主流程。

实现中医 OCR 校对系统的完整决策链路：
1. 第一级：本地 LLM（ShizhenGPT，60秒超时）
2. 第一道字形验证
3. 第二级：云端 LLM 仲裁（30秒超时，备选切换）
4. 第二道字形验证
5. 第三级：人工核验标记
6. 返回最终结果

辅助功能：
- 跨页段落合并与拆分
- 超长段落软拆分
- 行数守恒验证
"""

from __future__ import annotations

import logging
import time
import traceback
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from kzocr.tcm_ocr.llm.prompts import (
    build_block_prompt,
    build_cloud_proofread_prompt,
    build_local_proofread_prompt,
    extract_json_from_response,
)

logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────
# 辅助函数
# ───────────────────────────────────────────────────────

def merge_cross_page_paragraphs(
    para_list: List[Dict[str, Any]],
) -> List[List[Dict[str, Any]]]:
    """按 cross_page_group_id 合并跨页段落。

    将属于同一跨页组的段落合并为一个组，用于统一校对。

    Args:
        para_list: 段落列表，每项包含 cross_page_group_id 等字段。

    Returns:
        合并后的段落组列表，每组包含一个或多个跨页段落。
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    ungrouped: List[Dict[str, Any]] = []

    for para in para_list:
        group_id = para.get("cross_page_group_id")
        if group_id is not None and group_id != "":
            groups.setdefault(str(group_id), []).append(para)
        else:
            ungrouped.append(para)

    result: List[List[Dict[str, Any]]] = []

    # 按页码排序每个组内的段落
    for group_id, group_paras in sorted(groups.items()):
        sorted_paras = sorted(
            group_paras,
            key=lambda p: (p.get("page_number", 0), p.get("para_index", 0))
        )
        result.append(sorted_paras)

    # 单独添加非跨页段落
    for para in ungrouped:
        result.append([para])

    logger.info("[Pipeline] 跨页合并: %d 段落 → %d 组", len(para_list), len(result))
    return result


# ───────────────────────────────────────────────────────
# 方剂聚合层（按方剂/标题切块）
# ───────────────────────────────────────────────────────

BLOCK_ROLE_FORMULA = "formula"
BLOCK_ROLE_HEADING = "heading"
BLOCK_ROLE_TEXT = "text"

# _is_formula_continuation 的返回：续接关系判定结果
CONT_MERGE = "merge"            # 同方续接 → 并入上一方剂块
CONT_INDEPENDENT = "independent"  # 加减方 → 独立新方剂块（引用上一方）
CONT_NO = "no"


def _unit_consensus(unit: List[Dict[str, Any]]) -> str:
    """拼接一个段落单元（可能跨页合并）的共识文本。"""
    return "\n".join(p.get("consensus_text", "") for p in unit).strip()


def _is_formula_text(text: str) -> bool:
    """方剂文本判定，委托 extractor.is_formula_paragraph（避免逻辑复制）。"""
    if not text:
        return False
    try:
        from kzocr.tcm_ocr.knowledge.formula.extractor import is_formula_paragraph
    except ImportError:
        logger.error("[FormulaAggregation] 无法导入 extractor.is_formula_paragraph")
        return False
    return is_formula_paragraph(text)


def _looks_like_herb_continuation(text: str) -> bool:
    """是否像纯药材/剂量续行（用于标题排除与续接判定）。"""
    try:
        from kzocr.tcm_ocr.knowledge.formula.extractor import (
            CHINESE_DOSAGE_PATTERN,
            DOSAGE_UNIT_PATTERN,
            extract_herb_names,
        )
    except ImportError:
        return False
    herbs = extract_herb_names(text)
    has_dosage = bool(
        DOSAGE_UNIT_PATTERN.search(text) or CHINESE_DOSAGE_PATTERN.search(text)
    )
    return len(herbs) >= 1 and has_dosage


def _formula_markers() -> List[str]:
    try:
        from kzocr.tcm_ocr.knowledge.formula.extractor import FORMULA_MARKERS
        return FORMULA_MARKERS
    except ImportError:
        return ["组成", "方药", "处方", "用药", "方剂", "药味"]


def _classify_para_unit(unit: List[Dict[str, Any]]) -> str:
    """对单个段落单元（已跨页合并）做角色初判。

    Returns: BLOCK_ROLE_FORMULA | BLOCK_ROLE_HEADING | BLOCK_ROLE_TEXT

    heading 启发式：短 + 无方剂标记 + 不像药材续行（避免短两药片段误判标题）。
    TODO: 更准确应接 layout bbox（居中/字号大于正文）。
    """
    consensus = _unit_consensus(unit)
    if not consensus:
        return BLOCK_ROLE_TEXT

    if _is_formula_text(consensus):
        return BLOCK_ROLE_FORMULA

    if (
        len(consensus) <= 30
        and not any(m in consensus for m in _formula_markers())
        and not _looks_like_herb_continuation(consensus)
    ):
        return BLOCK_ROLE_HEADING

    return BLOCK_ROLE_TEXT


def _is_formula_continuation(
    unit: List[Dict[str, Any]],
    prev_block: Dict[str, Any],
) -> str:
    """判定 unit 对上一方剂块的续接关系（接 extractor 引用链）。

    Returns:
        CONT_MERGE      同方续接（同前/方见上页/纯药材续行）→ 并入
        CONT_INDEPENDENT 加减方（上方加味/去某味）→ 独立新方剂块
        CONT_NO         无续接关系
    """
    if prev_block.get("role") != BLOCK_ROLE_FORMULA:
        return CONT_NO
    if _classify_para_unit(unit) == BLOCK_ROLE_HEADING:
        return CONT_NO

    consensus = _unit_consensus(unit)
    try:
        from kzocr.tcm_ocr.knowledge.formula.extractor import detect_reference_type
    except ImportError:
        return CONT_NO

    ref_type = detect_reference_type(consensus)
    if ref_type in ("same_as_above", "cross_page_continued"):
        return CONT_MERGE
    if ref_type in ("add_to_above", "subtract_from"):
        return CONT_INDEPENDENT
    # 无引用模式：纯药材/剂量续行（无新方名标记）→ 视为同方续接并入
    if _looks_like_herb_continuation(consensus) and not any(
        m in consensus for m in _formula_markers()
    ):
        return CONT_MERGE
    return CONT_NO


def _extract_formula_name(unit: List[Dict[str, Any]]) -> Optional[str]:
    """从段落单元中粗略提取方剂名（占位）。

    TODO: 复用在前的 heading 块文本，或接入 extractor.extract_formula_name
    的引用链回溯，得到准确的方剂名。
    """
    consensus = _unit_consensus(unit)
    for marker in ("组成", "方药", "处方", "方剂"):
        idx = consensus.find(marker)
        if idx > 0:
            candidate = consensus[:idx].strip().split("\n")[-1]
            if candidate:
                return candidate
    return None


def _new_formula_block(
    unit: List[Dict[str, Any]],
    block_id: str,
    referenced_id: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "role": BLOCK_ROLE_FORMULA,
        "paras": list(unit),
        "formula_name": _extract_formula_name(unit),
        "referenced_id": referenced_id,
        "block_id": block_id,
    }


def group_into_formula_blocks(
    groups: List[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """把跨页合并后的段落组聚成 方剂/标题/正文 块。

    作为"方剂聚合层"的核心：在 merge_cross_page_paragraphs 之后、
    逐组 process_paragraph 之前调用。每个方剂（含跨页天然成块）聚为
    一个 formula 块；章/节标题独立成 heading 块；其余为 text 块。
    续接判定接 extractor 引用链：同方续接并入、加减方独立成块。

    Args:
        groups: merge_cross_page_paragraphs 的输出。每组是一个跨页合并
                后的段落单元（含单段或跨页多段）。

    Returns:
        块列表，每块字典:
        {
            "role": "formula" | "heading" | "text",
            "paras": List[Dict],      # 组成该块的原始段落单元（已扁平化）
            "formula_name": str|None, # 仅 formula 块填充（占位提取）
            "referenced_id": str|None, # 加减方指向所引用的基础方 block_id
            "block_id": str,
        }

    注：方块内部若仍超长，由下游 process_formula_block 调用
    _split_paras_by_chars 再切，本层只负责语义聚合。
    """
    blocks: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    for unit in groups:
        role = _classify_para_unit(unit)
        cont = CONT_NO
        if current is not None and current["role"] == BLOCK_ROLE_FORMULA:
            cont = _is_formula_continuation(unit, current)

        if cont == CONT_MERGE:
            # 同方续接 → 并入当前块
            current["paras"].extend(unit)
            continue

        if cont == CONT_INDEPENDENT:
            # 加减方 → 独立新方剂块，记录所引用的基础方 block_id
            blk_id = f"blk_{len(blocks)}"
            current = _new_formula_block(
                unit, blk_id, referenced_id=current["block_id"]
            )
            blocks.append(current)
            continue

        # 否则按 role 开新块
        blk_id = f"blk_{len(blocks)}"
        if role == BLOCK_ROLE_FORMULA:
            current = _new_formula_block(unit, blk_id)
        elif role == BLOCK_ROLE_HEADING:
            current = {
                "role": BLOCK_ROLE_HEADING,
                "paras": list(unit),
                "formula_name": None,
                "referenced_id": None,
                "block_id": blk_id,
            }
        else:  # BLOCK_ROLE_TEXT
            current = {
                "role": BLOCK_ROLE_TEXT,
                "paras": list(unit),
                "formula_name": None,
                "referenced_id": None,
                "block_id": blk_id,
            }
        blocks.append(current)

    logger.info(
        "[Pipeline] 方剂聚合: %d 段落组 → %d 块 (方剂=%d, 标题=%d, 正文=%d)",
        len(groups),
        len(blocks),
        sum(1 for b in blocks if b["role"] == BLOCK_ROLE_FORMULA),
        sum(1 for b in blocks if b["role"] == BLOCK_ROLE_HEADING),
        sum(1 for b in blocks if b["role"] == BLOCK_ROLE_TEXT),
    )
    return blocks


def split_cross_page_result(
    merged_group: List[Dict[str, Any]],
    corrected_lines: List[str],
) -> List[Dict[str, Any]]:
    """将合并校对后的结果按段落拆分回各段落。

    根据合并前各段落的行数比例，将 corrected_lines 拆分回各个段落。

    Args:
        merged_group: 合并前的段落组（按顺序）。
        corrected_lines: 校对后的行列表。

    Returns:
        每个段落的校对结果列表。
    """
    if not merged_group:
        return []

    # 计算各段落的原始行数
    para_line_counts: List[int] = []
    for para in merged_group:
        consensus = para.get("consensus_text", "")
        line_count = len(consensus.split("\n")) if consensus else 1
        para_line_counts.append(max(line_count, 1))

    total_lines = len(corrected_lines)
    total_original = sum(para_line_counts)

    results: List[Dict[str, Any]] = []
    line_offset = 0

    for idx, para in enumerate(merged_group):
        # 按比例分配行数
        if idx == len(merged_group) - 1:
            # 最后一段获得剩余所有行
            assigned_lines = corrected_lines[line_offset:]
        else:
            ratio = para_line_counts[idx] / total_original
            assigned_count = max(1, int(round(total_lines * ratio)))
            assigned_count = min(assigned_count, total_lines - line_offset)
            assigned_lines = corrected_lines[line_offset:line_offset + assigned_count]

        results.append({
            "para_id": para.get("para_id", f"para_{idx}"),
            "page_number": para.get("page_number", 0),
            "corrected_lines": assigned_lines,
        })
        line_offset += len(assigned_lines)

    return results


def validate_line_count_conservation(
    input_lines: List[str],
    corrected_lines: List[str],
) -> Tuple[bool, int, int]:
    """验证行数守恒。

    输入行数必须与输出行数相等，否则说明 LLM 违反了约束。

    Args:
        input_lines: 输入文本行列表。
        corrected_lines: 校对后的行列表。

    Returns:
        (是否守恒, 输入行数, 输出行数)。
    """
    input_count = len(input_lines)
    output_count = len(corrected_lines)
    is_conserved = input_count == output_count

    if not is_conserved:
        logger.warning(
            "[Pipeline] 行数不守恒: 输入 %d 行 → 输出 %d 行",
            input_count, output_count,
        )

    return is_conserved, input_count, output_count


def soft_split_long_paragraph(
    para: Dict[str, Any],
    max_chars: int = 1500,
) -> List[Dict[str, Any]]:
    """超长段落句末标点软拆分。

    当段落总字符数超过 max_chars 时，按句末标点（。；！？）
    将段落拆分为多个子段落，每个子段落不超过 max_chars。

    Args:
        para: 段落字典，包含 consensus_text, para_lines 等。
        max_chars: 每子段落最大字符数。

    Returns:
        拆分后的子段落列表。如果无需拆分则返回包含原段落的列表。
    """
    consensus_text: str = para.get("consensus_text", "")
    total_chars = len(consensus_text)

    if total_chars <= max_chars:
        return [para]

    para.get("para_lines", [])

    # 按行拆分并尝试在句末标点处切割
    lines = consensus_text.split("\n")
    sub_paras: List[Dict[str, Any]] = []
    current_lines: List[str] = []
    current_chars = 0
    sub_index = 0

    for line in lines:
        line_len = len(line)

        if current_chars + line_len > max_chars and current_lines:
            # 当前子段落已满，创建新子段落
            sub_paras.append({
                **para,
                "para_id": f"{para.get('para_id', 'unknown')}_sub{sub_index}",
                "consensus_text": "\n".join(current_lines),
                "is_soft_split": True,
                "original_para_id": para.get("para_id", "unknown"),
            })
            sub_index += 1
            current_lines = [line]
            current_chars = line_len
        else:
            current_lines.append(line)
            current_chars += line_len

    # 添加最后一个子段落
    if current_lines:
        sub_paras.append({
            **para,
            "para_id": f"{para.get('para_id', 'unknown')}_sub{sub_index}",
            "consensus_text": "\n".join(current_lines),
            "is_soft_split": True,
            "original_para_id": para.get("para_id", "unknown"),
        })

    logger.info(
        "[Pipeline] 超长段落拆分: %s (%d 字) → %d 子段落",
        para.get("para_id", "unknown"),
        total_chars,
        len(sub_paras),
    )

    return sub_paras


# ───────────────────────────────────────────────────────
# 四级决策流水线
# ───────────────────────────────────────────────────────

def compose_block_image(
    paras: List[Dict[str, Any]],
    max_height: Optional[int] = None,
) -> Optional[np.ndarray]:
    """把块内各段落的 para_img 纵向拼接成一张块图。

    用于 process_formula_block：一个方剂/标题块可能由多段（含跨页）
    组成，需合成整块图再送 ShizhenGPT。同时顺手修复了原
    process_paragraphs_batch 跨页组只取 group[0] 首图的缺陷。

    Args:
        paras: 已展平的单段 Dict 列表，每段含 para_img (np.ndarray)。
        max_height: 可选高度上限，超出则记录警告（拆分由调用方按文本长度处理）。

    Returns:
        纵向拼接后的 (H, W, 3) uint8 图像；无任何有效图时返回 None。
    """
    imgs: List[np.ndarray] = []
    for p in paras:
        img = p.get("para_img")
        if img is None or getattr(img, "size", 0) == 0:
            continue
        if img.ndim == 2:
            img = np.stack([img, img, img], axis=-1)
        elif img.ndim == 3 and img.shape[2] == 1:
            img = np.repeat(img, 3, axis=-1)
        imgs.append(img.astype(np.uint8))

    if not imgs:
        return None

    # 对齐宽度：以最大宽为准，右侧白边填充
    max_w = max(img.shape[1] for img in imgs)
    padded: List[np.ndarray] = []
    for img in imgs:
        h, w = img.shape[:2]
        if w < max_w:
            pad = np.full((h, max_w - w, 3), 255, dtype=np.uint8)
            img = np.concatenate([img, pad], axis=1)
        padded.append(img)

    stacked = np.concatenate(padded, axis=0)

    if max_height is not None and stacked.shape[0] > max_height:
        logger.warning(
            "[compose_block_image] 块图高度 %d 超过上限 %d，建议按文本长度拆分",
            stacked.shape[0], max_height,
        )
    return stacked


def _split_paras_by_chars(
    paras: List[Dict[str, Any]],
    max_chars: int,
) -> List[List[Dict[str, Any]]]:
    """按累计字符数把展平的段落列表切成连续子组（用于长方剂块再切）。

    与 soft_split_long_paragraph 思路一致，但作用于"多段组成的块"，
    以段落边界为切分点，保证结果回拆时映射清晰。

    Returns:
        子组列表；单个超长段若本身超限会被单独保留（由下游模型尽力处理）。
    """
    groups: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    current_chars = 0

    for p in paras:
        c = len(p.get("consensus_text", ""))
        if current and current_chars + c > max_chars:
            groups.append(current)
            current = []
            current_chars = 0
        current.append(p)
        current_chars += c

    if current:
        groups.append(current)
    return groups


class FourStagePipeline:
    """四级决策校对链路。

    实现完整的 OCR 校对决策流程：
    Stage 1: 本地 LLM（ShizhenGPT）生成初稿
    Stage 2: 第一道字形验证（Hu 矩比对）
    Stage 3: 云端 LLM 仲裁（GLM/DeepSeek）
    Stage 4: 第二道字形验证 + 人工核验标记

    Attributes:
        local_llm: 本地 LLM 客户端（ShizhenGPTClient）。
        cloud_llm: 云端 LLM 客户端（CloudLLMClient）。
        glyph_verifier: 字形验证器（GlyphVerifier）。
        config: 流水线配置字典。
    """

    def __init__(
        self,
        local_llm: Any,
        cloud_llm: Any,
        glyph_verifier: Any,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """初始化四级决策流水线。

        Args:
            local_llm: 本地 LLM 客户端实例（ShizhenGPTClient）。
            cloud_llm: 云端 LLM 客户端实例（CloudLLMClient）。
            glyph_verifier: 字形验证器实例（GlyphVerifier）。
            config: 流水线配置字典（可选）。
        """
        self.local_llm = local_llm
        self.cloud_llm = cloud_llm
        self.glyph_verifier = glyph_verifier
        self.config: Dict[str, Any] = config or {}

        # 超时配置
        self._local_timeout: int = self.config.get("local_llm_timeout", 60)
        self._cloud_timeout: int = self.config.get("cloud_llm_timeout", 30)
        self._max_chars_soft_split: int = self.config.get("max_chars_soft_split", 1500)

        # 控制开关
        self._enable_local_llm: bool = self.config.get("enable_local_llm", True)
        self._enable_cloud_llm: bool = self.config.get("enable_cloud_llm", True)
        self._enable_glyph_verify: bool = self.config.get("enable_glyph_verify", True)
        self._enable_stage2_glyph: bool = self.config.get("enable_stage2_glyph", True)

        logger.info(
            "[FourStagePipeline] 初始化 | 本地=%s | 云端=%s | 字形=%s",
            self._enable_local_llm,
            self._enable_cloud_llm,
            self._enable_glyph_verify,
        )

    def process_paragraph(
        self,
        para_img: np.ndarray,
        para_lines: List[Dict[str, Any]],
        consensus_text: str,
        engine_results: List[Dict[str, Any]],
        term_kb: Optional[Dict[str, Any]] = None,
        role: Optional[str] = None,
        formula_name: Optional[str] = None,
        referenced_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """处理单个段落（或跨页段落组）的四级决策校对。

        完整流程：
        1. 跨页检测 → merge_cross_page_paragraphs
        2. 超长段落软拆分 → soft_split_long_paragraph
        3. 第一级：本地 LLM（ShizhenGPT，60秒超时）
        4. 第一道字形验证
        5. 第二级：云端 LLM（30秒超时，备选切换）
        6. 第二道字形验证
        7. 第三级：人工核验标记
        8. 返回最终结果

        Args:
            para_img: 段落图像（numpy 数组）。
            para_lines: 行级 OCR 结果记录列表。
            consensus_text: 多引擎投票共识文本。
            engine_results: 各 OCR 引擎原始结果。
            term_kb: 术语知识库（可选）。

        Returns:
            最终结果字典：
            {
                "corrected_lines": List[str],
                "changes": List[Dict],
                "verification": Dict,
                "needs_human_review": bool,
                "review_reason": str,
                "stage_info": Dict,
            }
        """
        stage_info: Dict[str, Any] = {}
        needs_human_review = False
        review_reason = ""

        input_lines = consensus_text.split("\n") if consensus_text else []
        logger.info("[Pipeline] 开始处理段落 | 输入 %d 行", len(input_lines))

        # ═══════════════════════════════════════════════
        # Stage 1: 本地 LLM 校对
        # ═══════════════════════════════════════════════
        local_output: Dict[str, Any] = {"corrected_lines": list(input_lines), "changes": []}

        if self._enable_local_llm:
            try:
                start_time = time.time()
                if role is not None:
                    local_prompt = build_block_prompt(
                        role=role,
                        para_lines=para_lines,
                        consensus_text=consensus_text,
                        engine_results=engine_results,
                        formula_name=formula_name,
                        referenced_id=referenced_id,
                    )
                else:
                    local_prompt = build_local_proofread_prompt(
                        para_lines=para_lines,
                        consensus_text=consensus_text,
                        engine_results=engine_results,
                    )

                local_response = self.local_llm.generate(
                    prompt=local_prompt,
                    images=[para_img],
                    max_tokens=4096,
                    temperature=0.1,
                    timeout=self._local_timeout,
                )

                local_output = extract_json_from_response(local_response)
                elapsed = time.time() - start_time

                stage_info["stage1_local"] = {
                    "status": "success",
                    "elapsed": round(elapsed, 2),
                    "output_line_count": len(local_output.get("corrected_lines", [])),
                }
                logger.info("[Pipeline] Stage 1 完成 | %.1f 秒", elapsed)

            except TimeoutError:
                stage_info["stage1_local"] = {
                    "status": "timeout",
                    "timeout_limit": self._local_timeout,
                }
                logger.warning("[Pipeline] Stage 1 超时 (%d 秒)", self._local_timeout)
                needs_human_review = True
                review_reason += "本地LLM超时; "

            except Exception as exc:
                stage_info["stage1_local"] = {
                    "status": "error",
                    "error": str(exc),
                }
                logger.error("[Pipeline] Stage 1 错误: %s", traceback.format_exc())
                needs_human_review = True
                review_reason += f"本地LLM错误: {exc}; "

        # 行数守恒检查
        corrected_lines = local_output.get("corrected_lines", [])
        is_conserved, in_count, out_count = validate_line_count_conservation(
            input_lines, corrected_lines
        )

        if not is_conserved:
            # 尝试修复：截断或填充
            if out_count > in_count:
                corrected_lines = corrected_lines[:in_count]
            else:
                corrected_lines.extend(input_lines[out_count:in_count])
            local_output["corrected_lines"] = corrected_lines
            stage_info["line_count_fix"] = {"from": out_count, "to": in_count}
            needs_human_review = True
            review_reason += "行数不守恒已修复; "

        # ═══════════════════════════════════════════════
        # Stage 2: 第一道字形验证
        # ═══════════════════════════════════════════════
        stage1_verification: Dict[str, Any] = {
            "all_verified": True,
            "verified_lines": corrected_lines,
            "failed_lines": [],
            "critical_intercept": [],
        }

        if self._enable_glyph_verify:
            try:
                start_time = time.time()
                stage1_verification = self.glyph_verifier.verify_llm_output(
                    llm_output=local_output,
                    original_consensus=consensus_text,
                    para_img=para_img,
                    line_records=para_lines,
                    term_kb=term_kb,
                )
                elapsed = time.time() - start_time

                # 使用验证后的行
                corrected_lines = stage1_verification.get("verified_lines", corrected_lines)
                local_output["corrected_lines"] = corrected_lines

                stage_info["stage2_glyph_verify_1"] = {
                    "status": "success",
                    "elapsed": round(elapsed, 2),
                    "all_verified": stage1_verification.get("all_verified", False),
                    "failed_count": len(stage1_verification.get("failed_lines", [])),
                    "critical_count": len(stage1_verification.get("critical_intercept", [])),
                }
                logger.info(
                    "[Pipeline] Stage 2 完成 | 验证=%s | 失败=%d | 拦截=%d",
                    stage1_verification.get("all_verified"),
                    len(stage1_verification.get("failed_lines", [])),
                    len(stage1_verification.get("critical_intercept", [])),
                )

            except Exception as exc:
                stage_info["stage2_glyph_verify_1"] = {
                    "status": "error",
                    "error": str(exc),
                }
                logger.error("[Pipeline] Stage 2 错误: %s", traceback.format_exc())

        # ═══════════════════════════════════════════════
        # Stage 3: 云端 LLM 仲裁
        # ═══════════════════════════════════════════════
        cloud_output: Dict[str, Any] = local_output.copy()

        if self._enable_cloud_llm:
            try:
                start_time = time.time()
                cloud_prompt = build_cloud_proofread_prompt(
                    para_lines=para_lines,
                    consensus_text=consensus_text,
                    engine_results=engine_results,
                    local_output=local_output,
                    local_verification=stage1_verification,
                )

                cloud_response = self.cloud_llm.call_primary(
                    prompt=cloud_prompt,
                    images=[para_img],
                    timeout=self._cloud_timeout,
                )

                # 如果云端返回有效结果，使用云端结果
                if isinstance(cloud_response, dict) and "corrected_lines" in cloud_response:
                    cloud_output = cloud_response
                    corrected_lines = cloud_output.get("corrected_lines", corrected_lines)

                    # 检查云端是否标记需要人工复核
                    if cloud_response.get("needs_human_review"):
                        needs_human_review = True
                        review_reason += "云端LLM建议人工复核; "

                elapsed = time.time() - start_time
                stage_info["stage3_cloud"] = {
                    "status": "success",
                    "elapsed": round(elapsed, 2),
                    "model_used": cloud_response.get("_model_used", "unknown"),
                    "output_line_count": len(corrected_lines),
                }
                logger.info(
                    "[Pipeline] Stage 3 完成 | %.1f 秒 | 模型=%s",
                    elapsed,
                    cloud_response.get("_model_used", "unknown"),
                )

            except TimeoutError:
                stage_info["stage3_cloud"] = {
                    "status": "timeout",
                    "timeout_limit": self._cloud_timeout,
                }
                logger.warning("[Pipeline] Stage 3 超时 (%d 秒)", self._cloud_timeout)

            except Exception as exc:
                stage_info["stage3_cloud"] = {
                    "status": "error",
                    "error": str(exc),
                }
                logger.error("[Pipeline] Stage 3 错误: %s", traceback.format_exc())

        # 行数守恒再次检查
        corrected_lines = cloud_output.get("corrected_lines", corrected_lines)
        is_conserved, in_count, out_count = validate_line_count_conservation(
            input_lines, corrected_lines
        )
        if not is_conserved:
            if out_count > in_count:
                corrected_lines = corrected_lines[:in_count]
            else:
                corrected_lines.extend(input_lines[out_count:in_count])
            cloud_output["corrected_lines"] = corrected_lines

        # ═══════════════════════════════════════════════
        # Stage 4: 第二道字形验证
        # ═══════════════════════════════════════════════
        stage2_verification: Dict[str, Any] = {
            "all_verified": True,
            "verified_lines": corrected_lines,
            "failed_lines": [],
            "critical_intercept": [],
        }

        if self._enable_glyph_verify and self._enable_stage2_glyph:
            try:
                start_time = time.time()
                stage2_verification = self.glyph_verifier.verify_llm_output(
                    llm_output=cloud_output,
                    original_consensus=consensus_text,
                    para_img=para_img,
                    line_records=para_lines,
                    term_kb=term_kb,
                )

                corrected_lines = stage2_verification.get("verified_lines", corrected_lines)
                elapsed = time.time() - start_time

                stage_info["stage4_glyph_verify_2"] = {
                    "status": "success",
                    "elapsed": round(elapsed, 2),
                    "all_verified": stage2_verification.get("all_verified", False),
                    "failed_count": len(stage2_verification.get("failed_lines", [])),
                    "critical_count": len(stage2_verification.get("critical_intercept", [])),
                }
                logger.info(
                    "[Pipeline] Stage 4 完成 | 验证=%s | 失败=%d | 拦截=%d",
                    stage2_verification.get("all_verified"),
                    len(stage2_verification.get("failed_lines", [])),
                    len(stage2_verification.get("critical_intercept", [])),
                )

            except Exception as exc:
                stage_info["stage4_glyph_verify_2"] = {
                    "status": "error",
                    "error": str(exc),
                }
                logger.error("[Pipeline] Stage 4 错误: %s", traceback.format_exc())

        # ═══════════════════════════════════════════════
        # Stage 5: 人工核验标记
        # ═══════════════════════════════════════════════
        # 判断是否需要人工核验
        critical_intercepts = stage2_verification.get("critical_intercept", [])
        failed_lines = stage2_verification.get("failed_lines", [])

        if critical_intercepts:
            needs_human_review = True
            review_reason += f"存在 {len(critical_intercepts)} 个灾难性拦截; "

        if failed_lines:
            needs_human_review = True
            review_reason += f"存在 {len(failed_lines)} 行字形验证失败; "

        # 如果有高风险的 LLM 修改，也标记人工复核
        changes = cloud_output.get("changes", local_output.get("changes", []))
        high_risk_changes = [
            c for c in changes
            if c.get("confidence", 1.0) < 0.5
        ]
        if high_risk_changes:
            needs_human_review = True
            review_reason += f"存在 {len(high_risk_changes)} 个低置信度修改; "

        review_reason = review_reason.strip("; ") if review_reason else "无需人工复核"

        # 组装最终结果
        final_result: Dict[str, Any] = {
            "corrected_lines": corrected_lines,
            "changes": changes,
            "verification": {
                "stage1": stage1_verification,
                "stage2": stage2_verification,
            },
            "needs_human_review": needs_human_review,
            "review_reason": review_reason,
            "stage_info": stage_info,
        }

        logger.info(
            "[Pipeline] 处理完成 | 输出行数=%d | 需人工复核=%s",
            len(corrected_lines),
            needs_human_review,
        )

        return final_result

    def process_paragraphs_batch(
        self,
        para_list: List[Dict[str, Any]],
        term_kb: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """批量处理多个段落：跨页合并 → 方剂聚合 → 逐块校对。

        接线入口。先 merge_cross_page_paragraphs 把跨页段并为逻辑单元，
        再 group_into_formula_blocks 聚成 方剂/标题/正文 块，最后每块经
        process_formula_block 合成块图、校对、结果回拆到各段。

        Args:
            para_list: 段落列表，每项含 para_img, para_lines, consensus_text, engine_results 等。
            term_kb: 术语知识库（可选）。

        Returns:
            每个段落的处理结果列表（块级结果已回拆到原段落）。
        """
        # 1) 跨页合并
        merged_groups = merge_cross_page_paragraphs(para_list)
        # 2) 方剂聚合
        blocks = group_into_formula_blocks(merged_groups)
        # 3) 逐块校对（内部负责合成块图 + 结果回拆）
        all_results: List[Dict[str, Any]] = []
        for block in blocks:
            all_results.extend(self.process_formula_block(block, term_kb))
        return all_results

    def process_formula_block(
        self,
        block: Dict[str, Any],
        term_kb: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """处理单个方剂/标题/正文块，并回拆结果到各段落。

        先按文本长度把块切成连续子组（长方剂/超高图场景），每组聚合为
        一个"大段落"交给 process_paragraph（复用其全部四级校对逻辑），
        最后用 split_cross_page_result 把整块 corrected_lines 按比例回拆到
        块内各原始段落。

        Args:
            block: group_into_formula_blocks 产出的块字典
                   {"role", "paras": List[Dict], "formula_name", "block_id"}。
            term_kb: 术语知识库（可选）。

        Returns:
            块内每个段落的校对结果列表。
        """
        paras = block.get("paras", [])
        if not paras:
            return []

        # 长方剂/超高块：按累计字符数切成连续子组（段落边界为切点）
        sub_groups = _split_paras_by_chars(paras, self._max_chars_soft_split)

        full_corrected: List[str] = []
        for sub in sub_groups:
            full_corrected.extend(
                self._process_single_block_paras(
                    sub,
                    term_kb,
                    role=block.get("role", BLOCK_ROLE_TEXT),
                    formula_name=block.get("formula_name"),
                    referenced_id=block.get("referenced_id"),
                )
            )

        # 整块结果按比例回拆到各原始段落
        split_results = split_cross_page_result(paras, full_corrected)

        role = block.get("role", BLOCK_ROLE_TEXT)
        is_formula = role == BLOCK_ROLE_FORMULA
        out: List[Dict[str, Any]] = []
        for sr in split_results:
            out.append({
                "para_id": sr["para_id"],
                "page_number": sr["page_number"],
                "corrected_lines": sr["corrected_lines"],
                "block_id": block.get("block_id", ""),
                "role": role,
                "is_formula_block": is_formula,
                "referenced_id": block.get("referenced_id"),
            })
        return out

    def _process_single_block_paras(
        self,
        paras: List[Dict[str, Any]],
        term_kb: Optional[Dict[str, Any]] = None,
        role: Optional[str] = None,
        formula_name: Optional[str] = None,
        referenced_id: Optional[str] = None,
    ) -> List[str]:
        """把一组连续段落聚合成"大段落"交给 process_paragraph，返回 corrected_lines。

        合成块图（compose_block_image，顺带修复跨页只取首图的问题）、
        拼接行/文本/引擎结果，复用 process_paragraph 的四级校对逻辑。
        """
        block_img = compose_block_image(paras)
        block_lines: List[Dict[str, Any]] = []
        block_consensus_parts: List[str] = []
        block_engine: List[Dict[str, Any]] = []

        for p in paras:
            block_lines.extend(p.get("para_lines", []))
            block_consensus_parts.append(p.get("consensus_text", ""))
            block_engine.extend(p.get("engine_results", []))

        block_consensus = "\n".join(block_consensus_parts)

        result = self.process_paragraph(
            para_img=block_img,
            para_lines=block_lines,
            consensus_text=block_consensus,
            engine_results=block_engine,
            term_kb=term_kb,
            role=role,
            formula_name=formula_name,
            referenced_id=referenced_id,
        )
        return result.get("corrected_lines", [])

    def process_long_paragraph_with_split(
        self,
        para: Dict[str, Any],
        term_kb: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """处理超长段落（自动软拆分后分别处理）。

        Args:
            para: 段落字典。
            term_kb: 术语知识库（可选）。

        Returns:
            各子段落的处理结果列表。
        """
        # 软拆分
        sub_paras = soft_split_long_paragraph(para, self._max_chars_soft_split)

        results: List[Dict[str, Any]] = []
        for sub_para in sub_paras:
            result = self.process_paragraph(
                para_img=sub_para.get("para_img", para.get("para_img")),
                para_lines=sub_para.get("para_lines", []),
                consensus_text=sub_para.get("consensus_text", ""),
                engine_results=sub_para.get("engine_results", para.get("engine_results", [])),
                term_kb=term_kb,
            )
            result["para_id"] = sub_para.get("para_id", "unknown")
            result["is_soft_split"] = sub_para.get("is_soft_split", False)
            result["original_para_id"] = sub_para.get("original_para_id", "")
            results.append(result)

        return results

    def get_pipeline_stats(self) -> Dict[str, Any]:
        """获取流水线统计信息。

        Returns:
            统计信息字典。
        """
        return {
            "config": self.config,
            "enabled_stages": {
                "local_llm": self._enable_local_llm,
                "cloud_llm": self._enable_cloud_llm,
                "glyph_verify": self._enable_glyph_verify,
                "stage2_glyph": self._enable_stage2_glyph,
            },
            "timeouts": {
                "local": self._local_timeout,
                "cloud": self._cloud_timeout,
            },
            "max_chars_soft_split": self._max_chars_soft_split,
        }
