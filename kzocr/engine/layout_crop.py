"""cv2 投影分析 + 行检测 + 特征过滤版心裁剪。"""

from __future__ import annotations

import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def _detect_text_lines(img: np.ndarray) -> list[tuple[int, int, int, int]]:
    """水平投影检测文字行。"""
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    h, w = binary.shape
    lines: list[tuple[int, int, int, int]] = []
    h_proj = np.sum(binary, axis=1)
    threshold = np.max(h_proj) * 0.03
    in_line = False
    y_start = 0
    for y in range(h):
        if h_proj[y] > threshold and not in_line:
            in_line = True
            y_start = y
        elif h_proj[y] <= threshold and in_line:
            in_line = False
            if y - y_start >= 6:
                lines.append((0, y_start, w, y))
    if in_line:
        lines.append((0, y_start, w, h))
    return lines


def _merge_nearby(lines: list[tuple], gap: int = 10) -> list[tuple]:
    """合并相邻行（同一段落）。"""
    if not lines:
        return []
    sl = sorted(lines, key=lambda b: b[1])
    merged: list[list] = [[sl[0][0], sl[0][1], sl[0][2], sl[0][3]]]
    for x1, y1, x2, y2 in sl[1:]:
        last = merged[-1]
        if y1 - last[3] < gap:
            last[0] = min(last[0], x1)
            last[1] = min(last[1], y1)
            last[2] = max(last[2], x2)
            last[3] = max(last[3], y2)
        else:
            merged.append([x1, y1, x2, y2])
    return [(b[0], b[1], b[2], b[3]) for b in merged]


def _find_body_boundaries(img: np.ndarray, lines: list[tuple],
                          padding: int = 10,
                          page_num: int = 0) -> tuple[int, int, int, int]:
    """通过全局垂直投影 + 逐行分析确定版心边界。

    支持 page_num 奇偶对称：奇数页裁左，偶数页裁右。

    Returns:
        (top, bottom, left, right)
    """
    h, w = img.shape[:2]
    gray = np.mean(img, axis=2) if img.ndim == 3 else img

    is_odd = (page_num % 2 == 1)  # True=奇数页(侧眉在左), False=偶数页(侧眉在右)

    # -- 顶部：跳过页眉/装饰行 --
    # 如果第一条线位于顶部 20% 且高度 > 40px → 页眉，跳过前两条
    first_line = lines[0]
    first_h = first_line[3] - first_line[1]
    if len(lines) >= 3 and first_line[1] < h * 0.2 and first_h > 40:
        top_line_start = lines[2][1]
    elif len(lines) >= 2 and first_line[1] < h * 0.2 and first_h > 40:
        top_line_start = lines[1][1]
    else:
        top_line_start = lines[0][1]
    top = max(0, top_line_start - padding)
    # 顶部少裁 15px（向上移）
    top = max(0, top - 15)

    # -- 底部：像素扩展 + 页码检测 --
    last_line_end = max(b[3] for b in lines) if lines else h
    bottom = last_line_end + padding

    # 像素扩展（补漏检正文行）
    extend_to = bottom
    for y in range(bottom, min(bottom + 200, h)):
        if np.mean(gray[y, :] < 128) > 0.005:
            extend_to = y + padding
    bottom = extend_to
    # 底部加裁 80px
    bottom = min(h, bottom - 80)

    # 页码检测：检查最后几行在整体投影中是否孤立
    if len(lines) >= 2:
        last = lines[-1]
        prev = lines[-2]
        gap = last[1] - prev[3]
        last_w = last[2] - last[0]
        # 如果最后一行与上一行间距大，或者最后一行明显较窄 → 页码
        is_page_num = (gap > 30 and last_w < w * 0.4) or (last_w < w * 0.25)
        if is_page_num:
            # 有上一行时裁到上一行，否则裁掉最后行以上
            prev_bottom = prev[3] + padding if len(lines) >= 2 else last[1] - padding
            bottom = min(bottom, prev_bottom)

    # -- 左右：分析各行的垂直投影 --
    x1_list, x2_list = [], []
    for x1, y1, x2, y2 in lines:
        # 对该行范围做垂直投影
        row_gray = gray[y1:y2, :]
        col_proj = np.mean(row_gray < 128, axis=0)
        # 找左右边界（阈值 1%）
        lx = 0
        for cx in range(w):
            if col_proj[cx] > 0.01:
                lx = cx
                break
        rx = w
        for cx in range(w - 1, -1, -1):
            if col_proj[cx] > 0.01:
                rx = cx
                break
        if lx < rx:
            x1_list.append(lx)
            x2_list.append(rx)

    if x1_list:
        # 宽行：行宽超过整页一半(> w*0.5)的正文行。古籍侧眉多为窄列(旋转文字/窄竖条)，
        # 行宽远小于整页一半，从而被排除，避免其边界污染裁切。
        bw_list = [rx - lx for lx, rx in zip(x1_list, x2_list)]
        wide = [(lx, rx) for lx, rx, bw in zip(x1_list, x2_list, bw_list) if bw > w * 0.5]

        if is_odd:
            # 奇数页(侧眉在左)：左边界取宽行左边界最小值，再往左 20px
            if wide:
                left = max(0, min(lx for lx, rx in wide) - 20)
            else:
                # 兜底：无宽行时退回全部行最小左边界再左移 20px(安全超集，不切正文)
                left = max(0, min(x1_list) - 20)
            right = w
        else:
            # 偶数页(侧眉在右)：右边界取宽行右边界最大值，再往右 20px
            if wide:
                right = min(w, max(rx for lx, rx in wide) + 20)
            else:
                # 兜底：无宽行时退回全部行最大右边界再右移 20px
                right = min(w, max(x2_list) + 20)
            left = 0
    else:
        left = 0
        right = w

    return top, bottom, left, right


def _post_trim_borders(img: np.ndarray) -> np.ndarray:
    """后处理：裁剪边缘的装饰黑框/页码。"""
    if img.size == 0:
        return img
    h, w = img.shape[:2]
    gray = np.mean(img, axis=2) if img.ndim == 3 else img

    trim_left = 0
    trim_right = 0

    # 左侧：用最大暗像素检测细黑框线（即使 avg 很低）
    for x in range(min(10, w)):
        col_gray = gray[:, x] if gray.ndim == 2 else np.mean(gray[:, x, :], axis=1)
        if np.max(col_gray < 128) > 0.5:  # 存在纯黑像素 → 黑框线
            trim_left = x + 1
        else:
            break

    # 右侧：用最大暗像素检测细黑框线
    trim_right = 0
    for x in range(w - 1, max(w - 11, 0), -1):
        col_gray = gray[:, x] if gray.ndim == 2 else np.mean(gray[:, x, :], axis=1)
        if np.max(col_gray < 128) > 0.5:
            trim_right = w - x
        else:
            break

    if trim_left or trim_right:
        img = img[:, trim_left:w - trim_right if trim_right else w]

    return img


def crop_by_layout(img: np.ndarray, padding: int = 10,
                   page_num: int = 0) -> np.ndarray | None:
    """版心裁剪：行检测 → 跳过页眉 → 左右中位数 → 安全扩展。"""
    h, w = img.shape[:2]
    lines = _detect_text_lines(img)
    if not lines:
        return None

    # 过滤过矮的行（噪声）
    lines = [(x1, y1, x2, y2) for x1, y1, x2, y2 in lines if y2 - y1 >= 8]
    if not lines:
        return None

    merged = _merge_nearby(lines, gap=8)
    top, bottom, left, right = _find_body_boundaries(img, merged, padding, page_num=page_num)

    result = img[top:bottom, left:right]
    # 后处理：裁掉边缘装饰黑框/页码
    result = _post_trim_borders(result)
    return result
