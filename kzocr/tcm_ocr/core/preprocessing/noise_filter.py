"""
版面噪声过滤模块（后置策略，MinerU 分析后过滤）

本模块在 MinerU 版面分析完成后执行噪声过滤，属于后置处理策略。
过滤目标：页眉、页脚、页码、噪声区域等不影响正文内容的版面元素。

常量定义：
- BLOCK_TYPE_DISCARD: 应直接丢弃的 block 类型
- BLOCK_TYPE_KEEP: 应保留的 block 类型

函数：
- post_mineru_noise_filter: MinerU block 列表的主过滤入口
- infer_body_region: 基于保留正文块计算动态版心（仅参考/显示用）
- ppocr_noise_filter: PP-OCR 独立检测框的兜底过滤
- is_near_edge: 检测 bbox 是否靠近页面边缘
"""

import re
from typing import Any, Dict, List, Optional, Set, Tuple

import cv2
import numpy as np


# --------------------------------------------------------------------------- #
# 常量定义
# --------------------------------------------------------------------------- #

BLOCK_TYPE_DISCARD: Set[str] = {
    'header',      # 页眉
    'footer',      # 页脚
    'page_number', # 页码
    'noise',       # 噪声区域
    'watermark',   # 水印
    'margin_note', # 边注（通常不是正文）
    'stamp',       # 图章
}

BLOCK_TYPE_KEEP: Set[str] = {
    'text',        # 正文文本
    'paragraph',   # 段落
    'heading',     # 标题
    'chapter_title',  # 章节标题
    'formula',     # 公式/方荆
    'list',        # 列表
    'caption',     # 图表标题
    'table_cell',  # 表格单元格文本
}

# 章节标题模式：匹配中文章节编号格式
_CHAPTER_PATTERN = re.compile(
    r'^(第[一二三四五六七八九十百千万0-9]+章'
    r'|第[0-9]+章'
    r'|[一二三四五六七八九十]、'
    r'\([0-9]+\)'
    r'（[0-9]+）'
    r'[0-9]+\.'
    r'\([一二三四五六七八九十]+\)'
    r'（[一二三四五六七八九十]+）'
    r'[\u4e00-\u9fff]+(?:方剂|方荆|证治|证候|治法|药物|组成|用法))'
)

# 脚注标记模式
_FOOTNOTE_MARKER_PATTERN = re.compile(
    r'^\s*\*+\s*$|^\s*\d+\s*[.,;]?\s*$|^\s*注[：:]\s*'
)

# 孤立数字模式（不应在正文中单独出现）
_ISOLATED_NUMBER_PATTERN = re.compile(r'^\s*[0-9]+\s*$')


# --------------------------------------------------------------------------- #
# 辅助函数
# --------------------------------------------------------------------------- #

def is_near_edge(
    bbox: List[float],
    page_w: int,
    page_h: int,
    margin: float = 0.03,
) -> bool:
    """检测 bbox 是否靠近页面边缘。

    Parameters
    ----------
    bbox :
        [x1, y1, x2, y2] 或 [x, y, w, h] 格式的边界框。
        若为 [x, y, w, h] 则内部转换为 [x1, y1, x2, y2]。
    page_w :
        页面宽度（像素）。
    page_h :
        页面高度（像素）。
    margin :
        边缘比例阈值，默认 0.03（3%）。

    Returns
    -------
    bool
        True 表示 bbox 靠近页面边缘。
    """
    if not bbox or len(bbox) < 4:
        return False

    if len(bbox) == 4 and (bbox[2] <= bbox[0] or bbox[3] <= bbox[1]):
        # 可能是 [x, y, w, h] 格式
        x1, y1, w, h = bbox
        x2, y2 = x1 + w, y1 + h
    else:
        x1, y1, x2, y2 = bbox[:4]

    edge_threshold_x = page_w * margin
    edge_threshold_y = page_h * margin

    near_left = x1 < edge_threshold_x
    near_right = x2 > (page_w - edge_threshold_x)
    near_top = y1 < edge_threshold_y
    near_bottom = y2 > (page_h - edge_threshold_y)

    return near_left or near_right or near_top or near_bottom


def _bbox_center_y(bbox: List[float]) -> float:
    """计算 bbox 的中心 Y 坐标。"""
    if len(bbox) >= 4:
        if bbox[2] > bbox[0]:
            return (bbox[1] + bbox[3]) / 2.0
        else:
            return bbox[1] + bbox[3] / 2.0
    return 0.0


def _bbox_height(bbox: List[float]) -> float:
    """计算 bbox 高度。"""
    if len(bbox) >= 4:
        if bbox[3] > bbox[1]:
            return bbox[3] - bbox[1]
        else:
            return bbox[3]
    return 0.0


def _bbox_width(bbox: List[float]) -> float:
    """计算 bbox 宽度。"""
    if len(bbox) >= 4:
        if bbox[2] > bbox[0]:
            return bbox[2] - bbox[0]
        else:
            return bbox[2]
    return 0.0


def _is_chapter_title(text: str) -> bool:
    """检查文本是否为章节标题模式。

    Parameters
    ----------
    text :
        文本内容。

    Returns
    -------
    bool
        True 如果是章节标题格式。
    """
    if not text:
        return False
    text = text.strip()
    if _CHAPTER_PATTERN.match(text):
        return True
    # 额外的启发式：短文本 + 包含特定关键词
    if len(text) <= 20 and any(kw in text for kw in [
        '章', '节', '方荆', '方剂', '证治', '概述', '简介',
        '病因', '病机', '诊断', '治疗', '辨证', '药物', '组成'
    ]):
        return True
    return False


def _is_footnote_marker(text: str) -> bool:
    """检查文本是否为脚注标记。

    Parameters
    ----------
    text :
        文本内容。

    Returns
    -------
    bool
        True 如果是脚注标记。
    """
    if not text:
        return False
    return bool(_FOOTNOTE_MARKER_PATTERN.match(text.strip()))


def _is_isolated_number(text: str) -> bool:
    """检查文本是否为孤立数字（应丢弃）。

    Parameters
    ----------
    text :
        文本内容。

    Returns
    -------
    bool
        True 如果是孤立数字。
    """
    if not text:
        return False
    return bool(_ISOLATED_NUMBER_PATTERN.match(text.strip()))


def _is_likely_header_or_footer(
    block: Dict[str, Any], page_h: int
) -> bool:
    """启发式判断 block 是否为页眉或页脚。

    Parameters
    ----------
    block :
        MinerU block 字典。
    page_h :
        页面高度。

    Returns
    -------
    bool
        True 如果判断为页眉或页脚。
    """
    bbox = block.get('bbox', [])
    if not bbox or len(bbox) < 4:
        return False

    center_y = _bbox_center_y(bbox)
    h_ratio = center_y / page_h if page_h > 0 else 0.5

    # 顶部 5% 或底部 5%
    if h_ratio < 0.05 or h_ratio > 0.95:
        return True

    return False


def _has_duplicate_text(
    block: Dict[str, Any], kept_blocks: List[Dict[str, Any]]
) -> bool:
    """检查 block 的文本是否在已保留块中重复出现。

    Parameters
    ----------
    block :
        待检查的 block。
    kept_blocks :
        已保留的 block 列表。

    Returns
    -------
    bool
        True 如果检测到重复文本（允许近似匹配）。
    """
    text = block.get('text', '') or block.get('content', '')
    if not text:
        return False

    text_clean = text.strip().replace(' ', '')
    if len(text_clean) < 4:
        return False  # 短文本不检测重复

    for kb in kept_blocks:
        kb_text = (kb.get('text', '') or kb.get('content', '')).strip().replace(' ', '')
        if kb_text == text_clean:
            return True
        # 子串重复检测
        if len(text_clean) >= 10:
            if text_clean in kb_text or kb_text in text_clean:
                return True

    return False


# --------------------------------------------------------------------------- #
# 主过滤函数
# --------------------------------------------------------------------------- #

def post_mineru_noise_filter(
    mineru_blocks: List[Dict[str, Any]],
    page_w: int,
    page_h: int,
    ppocr_boxes: Optional[List[List[float]]] = None,
) -> List[Dict[str, Any]]:
    """MinerU 分析后的版面噪声主过滤入口。

    处理流程：
    1. 分离 kept（直接保留）和 suspicious（需二次验证）块
    2. suspicious 块二次验证：
       - 章节标题模式 → 保留
       - 孤立数字 → 丢弃
       - 重复文本 → 丢弃
       - 其他 → 根据位置和类型判断
    3. 返回过滤后的 block 列表

    Parameters
    ----------
    mineru_blocks :
        MinerU 输出的 block 列表，每个 block 为字典，
        至少包含 'type' 和 'bbox' 键，可选 'text'/'content'。
    page_w :
        页面宽度（像素）。
    page_h :
        页面高度（像素）。
    ppocr_boxes :
        可选的 PP-OCR 检测框列表，用于交叉验证。

    Returns
    -------
    list
        过滤后的 block 列表。
    """
    if not mineru_blocks:
        return []

    kept_blocks: List[Dict[str, Any]] = []
    suspicious_blocks: List[Dict[str, Any]] = []

    # ---- 第一遍：分离 kept / suspicious ----
    for block in mineru_blocks:
        block_type = block.get('type', 'unknown')

        if block_type in BLOCK_TYPE_KEEP:
            kept_blocks.append(block)
        elif block_type in BLOCK_TYPE_DISCARD:
            # 先放入 suspicious，后续可能救回
            suspicious_blocks.append(block)
        else:
            # 未知类型，需要二次验证
            suspicious_blocks.append(block)

    # ---- 第二遍：suspicious 块二次验证 ----
    filtered_kept: List[Dict[str, Any]] = list(kept_blocks)

    for block in suspicious_blocks:
        block_type = block.get('type', 'unknown')
        text = block.get('text', '') or block.get('content', '')
        bbox = block.get('bbox', [])

        # 规则 1：章节标题模式保留
        if _is_chapter_title(text):
            filtered_kept.append(block)
            continue

        # 规则 2：孤立数字丢弃
        if _is_isolated_number(text):
            continue

        # 规则 3：重复文本丢弃
        if _has_duplicate_text(block, filtered_kept):
            continue

        # 规则 4：header/footer 类型且靠近边缘 → 丢弃
        if block_type in ('header', 'footer', 'page_number'):
            if _is_likely_header_or_footer(block, page_h):
                continue

        # 规则 5：noise/watermark/stamp 类型 → 丢弃
        if block_type in ('noise', 'watermark', 'stamp'):
            continue

        # 规则 6：边注类型 → 默认丢弃，但含有方剂内容则保留
        if block_type == 'margin_note':
            if any(kw in text for kw in ['方', '药', '克', 'g', 'ml']):
                filtered_kept.append(block)
            continue  # 丢弃

        # 规则 7：其他类型，若靠近边缘且文本很短 → 丢弃
        if is_near_edge(bbox, page_w, page_h, margin=0.03):
            if len(text.strip()) <= 3:
                continue

        # 默认：保留
        filtered_kept.append(block)

    return filtered_kept


def infer_body_region(
    kept_blocks: List[Dict[str, Any]],
) -> Optional[Tuple[float, float, float, float]]:
    """基于保留正文块 bbox 计算动态版心区域。

    使用 5%~95% 分位数计算动态版心，排除极端值。
    **注意**：仅用于阈值参考和人工界面显示，不用于裁剪！

    Parameters
    ----------
    kept_blocks :
        保留的 block 列表。

    Returns
    -------
    Optional[Tuple[float, float, float, float]]
        (x1, y1, x2, y2) 版心区域坐标，像素单位。
        若无有效块则返回 None。
    """
    if not kept_blocks:
        return None

    x1s, y1s, x2s, y2s = [], [], [], []

    for block in kept_blocks:
        bbox = block.get('bbox', [])
        if not bbox or len(bbox) < 4:
            continue

        if bbox[2] <= bbox[0]:
            # [x, y, w, h] 格式
            x, y, w, h = bbox[:4]
            bx1, by1, bx2, by2 = x, y, x + w, y + h
        else:
            bx1, by1, bx2, by2 = bbox[:4]

        if bx2 > bx1 and by2 > by1:
            x1s.append(bx1)
            y1s.append(by1)
            x2s.append(bx2)
            y2s.append(by2)

    if not x1s:
        return None

    # 5%~95% 分位数
    def _percentile(arr: List[float], p: float) -> float:
        s = sorted(arr)
        k = (len(s) - 1) * p
        f = int(k)
        c = min(f + 1, len(s) - 1)
        return s[f] + (s[c] - s[f]) * (k - f)

    body_x1 = _percentile(x1s, 0.05)
    body_y1 = _percentile(y1s, 0.05)
    body_x2 = _percentile(x2s, 0.95)
    body_y2 = _percentile(y2s, 0.95)

    if body_x2 <= body_x1 or body_y2 <= body_y1:
        return None

    return (body_x1, body_y1, body_x2, body_y2)


def ppocr_noise_filter(
    box: List[float],
    text: str,
    page_w: int,
    page_h: int,
    body_region: Optional[Tuple[float, float, float, float]] = None,
) -> bool:
    """PP-OCR 独立检测框的动态兜底过滤。

    根据检测框位置和文本内容判断是否应保留。

    Parameters
    ----------
    box :
        检测框坐标 [x1, y1, x2, y2] 或 [x, y, w, h]。
    text :
        识别文本内容。
    page_w :
        页面宽度。
    page_h :
        页面高度。
    body_region :
        动态版心区域 (x1, y1, x2, y2)，若提供则以此为边界 ±20px。

    Returns
    -------
    bool
        True 表示应保留，False 表示应丢弃。
    """
    if not box or len(box) < 4:
        return False

    if not text:
        return False

    # 统一 bbox 格式
    if box[2] <= box[0] or box[3] <= box[1]:
        # 可能是 [x, y, w, h]
        x1, y1, w, h = box[:4]
        x2, y2 = x1 + w, y1 + h
    else:
        x1, y1, x2, y2 = box[:4]

    # ---- 规则 1：章节标题模式保留 ----
    if _is_chapter_title(text):
        return True

    # ---- 规则 2：脚注标记保留 ----
    if _is_footnote_marker(text):
        return True

    # ---- 规则 3：孤立数字丢弃 ----
    if _is_isolated_number(text):
        return False

    # ---- 规则 4：位置过滤 ----
    if body_region is not None:
        bx1, by1, bx2, by2 = body_region
        tolerance = 20.0
        # 检测框中心在版心 ±20px 范围内则保留
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0

        in_body = (
            (bx1 - tolerance) <= cx <= (bx2 + tolerance)
            and (by1 - tolerance) <= cy <= (by2 + tolerance)
        )
        if not in_body:
            # 超出版心范围，检查是否可能是脚注/尾注
            if _is_footnote_marker(text):
                return True
            return False
    else:
        # 无 body_region，使用保守静态阈值
        # 顶部/底部 3%、左右 2% 为噪声区
        edge_margin_x = page_w * 0.02
        edge_margin_y = page_h * 0.03

        center_x = (x1 + x2) / 2.0
        center_y = (y1 + y2) / 2.0

        # 如果在边缘区域且文本很短 → 丢弃
        near_top = center_y < edge_margin_y
        near_bottom = center_y > (page_h - edge_margin_y)
        near_left = center_x < edge_margin_x
        near_right = center_x > (page_w - edge_margin_x)

        if (near_top or near_bottom) and len(text.strip()) <= 5:
            return False

        # 完全在边缘外 → 丢弃
        if near_left or near_right:
            if len(text.strip()) <= 2:
                return False

    return True
