"""版心裁剪：PP-DocLayoutV3 语义检测（优先）+ cv2 投影法（降级）。

主入口 `crop_by_layout`：
- 优先用 PP-DocLayoutV3 检测版面，取 text/vertical_text/标题并集为版心，
  固定 padding（左右上 15 / 下 10）；
- 模型不可用或推理失败时，降级到 cv2 水平投影 + 行检测 + 纯投影三级方案。

PP-DocLayoutV3 依赖 paddle/paddlex（重模型，真实引擎依赖），懒加载，
未安装时自动降级，不影响无依赖环境（CI / 轻量部署）。
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

# --- PP-DocLayoutV3 类别定义 ---
# 组成版心的正文/标题类
_BODY_LABELS = frozenset({"text", "vertical_text", "doc_title", "paragraph_title"})
# 需排除的页眉/页脚/侧栏/页码类（仅作说明，不参与版心并集）
_MARGIN_LABELS = frozenset(
    {"aside_text", "header", "header_image", "footer", "footer_image", "number"}
)

# 版心 padding（用户设定）：左右上 15px，下 10px
_DOC_LAYOUT_PAD_LR_T = 15
_DOC_LAYOUT_PAD_B = 10

# 懒加载模型缓存
_DOC_LAYOUT_MODEL = None
_DOC_LAYOUT_MODEL_TRIED = False


def _get_doclayout_model() -> object | None:
    """懒加载 PP-DocLayoutV3 模型（仅 paddlex 可用时）。失败返回 None。"""
    global _DOC_LAYOUT_MODEL, _DOC_LAYOUT_MODEL_TRIED
    if _DOC_LAYOUT_MODEL_TRIED:
        return _DOC_LAYOUT_MODEL
    _DOC_LAYOUT_MODEL_TRIED = True
    try:
        from paddlex import create_model
    except ImportError:
        logger.info("paddlex 未安装，跳过 PP-DocLayoutV3 版心裁剪，使用 cv2 降级")
        return None
    try:
        _DOC_LAYOUT_MODEL = create_model(model_name="PP-DocLayoutV3")
    except Exception as exc:  # 模型文件缺失/下载失败等
        logger.warning("PP-DocLayoutV3 模型加载失败，降级 cv2：%s", exc)
        return None
    return _DOC_LAYOUT_MODEL


def _extract_doclayout_boxes(res: object) -> list[dict]:
    """兼容不同 paddlex 版本结果结构，抽出 boxes 列表。"""
    raw = res.json if hasattr(res, "json") else res
    if isinstance(raw, dict):
        if "boxes" in raw:
            return raw["boxes"]
        if "res" in raw and isinstance(raw["res"], dict) and "boxes" in raw["res"]:
            return raw["res"]["boxes"]
    if isinstance(raw, list):
        return raw
    raise RuntimeError(f"无法识别 PP-DocLayoutV3 预测结果结构: {type(raw)}")


def _doclayout_rect(
    img: np.ndarray,
    pad_lr_t: int = _DOC_LAYOUT_PAD_LR_T,
    pad_b: int = _DOC_LAYOUT_PAD_B,
) -> tuple[int, int, int, int] | None:
    """用 PP-DocLayoutV3 求版心矩形 (left, top, right, bottom)，失败返回 None。

    取 label ∈ _BODY_LABELS 的检测框并集外扩 padding。左侧竖眉(aside_text，属
    _MARGIN_LABELS)本就不在 _BODY_LABELS，故 min(正文 x1) 已排除侧眉；左界不再用
    margin_x_min-20（会把左界拉到侧眉左边、反而保留侧眉），下限由 120 降至 0——
    min(正文 x1)-pad 永远位于正文最左**左侧**，绝不切到正文（宁欠裁不过裁）。
    """
    model = _get_doclayout_model()
    if model is None:
        return None
    try:
        results = list(model.predict(img, batch_size=1))
    except Exception as exc:  # 推理异常
        logger.warning("PP-DocLayoutV3 推理失败，降级 cv2：%s", exc)
        return None
    if not results:
        return None
    try:
        boxes = _extract_doclayout_boxes(results[0])
    except Exception as exc:  # 结果解析异常
        logger.warning("PP-DocLayoutV3 结果解析失败，降级 cv2：%s", exc)
        return None

    body_boxes = [b for b in boxes if b.get("label") in _BODY_LABELS]
    if not body_boxes:
        logger.info("PP-DocLayoutV3 未检出正文框，降级 cv2")
        return None

    h, w = img.shape[:2]
    xs = [b["coordinate"][0] for b in body_boxes]
    ys = [b["coordinate"][1] for b in body_boxes]
    xe = [b["coordinate"][2] for b in body_boxes]
    ye = [b["coordinate"][3] for b in body_boxes]

    # 左界候选：正文并集最左 - pad；窄正文(≤半宽)最左 - 20。
    # 不再加入 margin_x_min-20（会保留侧眉），下限 0（见函数 docstring）。
    left_candidates = [int(min(xs)) - pad_lr_t]
    narrow_body_x_min = min(
        (b["coordinate"][0] for b in body_boxes if b["coordinate"][2] - b["coordinate"][0] <= w * 0.5),
        default=None,
    )
    if narrow_body_x_min is not None:
        left_candidates.append(int(narrow_body_x_min) - 20)
    left = max(0, min(left_candidates))

    top = max(0, int(min(ys)) - pad_lr_t)
    right = min(w, int(max(xe)) + pad_lr_t)
    bottom = min(h, int(max(ye)) + pad_b)
    return (left, top, right, bottom)


def crop_by_doclayout(
    img: np.ndarray,
    pad_lr_t: int = _DOC_LAYOUT_PAD_LR_T,
    pad_b: int = _DOC_LAYOUT_PAD_B,
) -> np.ndarray | None:
    """用 PP-DocLayoutV3 检测版心并裁剪（取框逻辑见 _doclayout_rect）。"""
    rect = _doclayout_rect(img, pad_lr_t=pad_lr_t, pad_b=pad_b)
    if rect is None:
        return None
    left, top, right, bottom = rect
    return img[top:bottom, left:right].copy()


def _detect_text_lines(img: np.ndarray) -> list[tuple[int, int, int, int]]:
    """水平投影检测文字行。"""
    import cv2

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


def _compute_blocks(img: np.ndarray, lines: list[tuple], w: int) -> list[tuple]:
    """对每行做垂直投影，求真实左右边界，返回 (lx, y1, rx, y2, bw) 列表。

    与 _preview_even_formula.per_block / _preview_odd_formula.per_block 同口径：
    暗像素占比 > 1% 的列视为有墨；bw = rx - lx 即行宽，用于宽/窄行分类。

    左侧先跳过贯穿全页的实心竖边框线：该书(及类似扫描件)常在 x≈0 处有一条
    细黑竖线，会把每个正文行的投影左缘"劫持"到 x=0，使 diff 公式看不到正文真左缘
    (221 个偶数页因此所有 block x1=0)。检测最左侧连续"整列近乎全黑"的列作为
    trim_left，投影左缘从该列之后起算。仅跳过实心黑线(整列黑像素占比 > 0.9)，
    不碰侧眉/正文列。右侧不动，以免影响已验证的 user_right。
    """
    gray = np.mean(img, axis=2) if img.ndim == 3 else img
    # 跳过左侧实心竖边框线（仅当整列近乎全黑，避免误删侧眉/正文列）
    trim_left = 0
    for x in range(min(12, w)):
        col = gray[:, x] if gray.ndim == 2 else np.mean(gray[:, x, :], axis=1)
        if np.mean(col < 128) > 0.9:
            trim_left = x + 1
        else:
            break
    blocks = []
    for x1, y1, x2, y2 in lines:
        row_gray = gray[y1:y2, :]
        col_proj = np.mean(row_gray < 128, axis=0)
        if col_proj.max() <= 0.01:
            continue  # 该行无墨，跳过（避免产出伪全宽块）
        lx = next((cx for cx in range(trim_left, w) if col_proj[cx] > 0.01), trim_left)
        rx = next((cx for cx in range(w - 1, -1, -1) if col_proj[cx] > 0.01), w)
        if lx < rx:
            blocks.append((lx, y1, rx, y2, rx - lx))
    return blocks


# 左缘标定常数：本书(mi-by-ppocrv6)用 doclayout 全量反标定得到，
# 取"使 max(left - dl.left) <= 25（0 过裁）"的保守值。换书时重标定：
#   跑 _measure_user_formula.py 风格对比，取各 parity 下 max(偏移) 余量内的最大 C。
_LEFT_CALIB_ODD = 105
_LEFT_CALIB_EVEN = 75


# --- cv2 左界 calib 在线自动标定（换书零人工；仅 dl 可用时生效） ---
# 按奇偶(parity: 1=奇, 0=偶) 存储 calib，默认用本书反标定常数；dl 可用时前
# _CV2_CALIB_SAMPLE 页用 dl 真值采样每页「安全 calib 候选」并取最小值锁定。
# dl_left = 正文左缘 - 15，故正文左缘 = dl_left + 15。安全候选令校准后
#   cv2_left = diff_term + calib <= (dl_left + 15) - _CV2_CALIB_BODY_MARGIN
#   = dl_left + (15 - _CV2_CALIB_BODY_MARGIN)
# 即 cv2 左界恒在正文左缘内侧 _CV2_CALIB_BODY_MARGIN px（绝不过裁正文）；
# 对奇页(侧眉在左，diff_term≈-15)该候选同时把 calib 抬高，使 cv2 左界推到 dl 水平
# （≈ dl_left+5 > 侧眉右缘~85），侧眉因此被裁。取各页候选最小值 = 「最坏页也安全」。
# 无 paddle(dl 不可用) 时回落默认常数（CI/降级场景）。
_CV2_CALIB_SAMPLE = 60
_CV2_CALIB_BODY_MARGIN = 10
_cv2_calib: dict[int, int] = {1: _LEFT_CALIB_ODD, 0: _LEFT_CALIB_EVEN}
_cv2_calib_buf: dict[int, list[int]] = {1: [], 0: []}
_cv2_calib_count = 0
_CV2_CALIB_LOCKED = False


def _diff_term(blocks: list[tuple]) -> float:
    """用户差值公式的 calib 无关部分：(mean(x1|x1>15) - mean(x1 全部))/2 - 15。"""
    all_x1 = [b[0] for b in blocks]
    if not all_x1:
        return -15.0
    body = [x for x in all_x1 if x > 15]
    m_all = sum(all_x1) / len(all_x1)
    m_body = sum(body) / len(body) if body else m_all
    return (m_body - m_all) / 2 - 15


def _maybe_calibrate_cv2(img: np.ndarray, page_num: int, dl_left: int | None = None) -> None:
    """dl 可用时，前 _CV2_CALIB_SAMPLE 页采样每页安全 calib 候选并锁定 _cv2_calib。

    每页候选令校准后 cv2_left = diff_term + calib <= (dl_left + 15) - _CV2_CALIB_BODY_MARGIN
    （正文左缘内侧 _CV2_CALIB_BODY_MARGIN px，绝不切正文）；奇页(侧眉在左，diff_term≈-15)
    此候选同时抬高 calib，使 cv2 左界推到 dl 水平、侧眉被裁。取各页候选最小值即「最坏页也安全」。
    """
    global _cv2_calib_count, _CV2_CALIB_LOCKED
    if _CV2_CALIB_LOCKED or _cv2_calib_count >= _CV2_CALIB_SAMPLE:
        return
    if dl_left is None:
        rect = _doclayout_rect(img)
        if rect is None:
            return
        dl_left = rect[0]
    h, w = img.shape[:2]
    raw = _detect_text_lines(img)
    filt = [b for b in raw if b[3] - b[1] >= 8]
    merged = _merge_nearby(filt, gap=8) if filt else []
    if not merged:
        return
    blocks = _compute_blocks(img, merged, w)
    if not blocks:
        return
    parity = 1 if page_num % 2 == 1 else 0
    dt = _diff_term(blocks)
    # 候选 calib：校准后 cv2_left <= 正文左缘内侧 _CV2_CALIB_BODY_MARGIN px（绝不过裁正文）
    cand = int(max(0, min(200, dl_left + (15 - _CV2_CALIB_BODY_MARGIN) - dt)))
    _cv2_calib_buf[parity].append(cand)
    _cv2_calib_count += 1
    if _cv2_calib_count >= _CV2_CALIB_SAMPLE:
        _finalize_cv2_calib()


def _finalize_cv2_calib() -> None:
    """按 parity 取候选 calib 最小值：最坏页也安全（cv2_left <= dl_left - _CV2_CALIB_MARGIN）。"""
    global _CV2_CALIB_LOCKED
    for parity in (1, 0):
        buf = _cv2_calib_buf[parity]
        if not buf:
            continue
        _cv2_calib[parity] = int(max(0, min(200, min(buf))))
    _CV2_CALIB_LOCKED = True


def calibrate_cv2_left(
    images: list[np.ndarray],
    page_nums: list[int] | None = None,
    n_sample: int = _CV2_CALIB_SAMPLE,
) -> None:
    """显式标定：对前 n_sample 页采样 dl/cv2 左界偏移并锁定 _cv2_calib（需提前标定时用）。"""
    if _CV2_CALIB_LOCKED:
        return
    for i, im in enumerate(images):
        if _CV2_CALIB_LOCKED or _cv2_calib_count >= n_sample:
            break
        _maybe_calibrate_cv2(im, page_nums[i] if page_nums else i)


def reset_cv2_calib() -> None:
    """测试/换进程时重置标定状态到默认常数。"""
    global _cv2_calib_count, _CV2_CALIB_LOCKED
    _cv2_calib[1] = _LEFT_CALIB_ODD
    _cv2_calib[0] = _LEFT_CALIB_EVEN
    _cv2_calib_buf[1].clear()
    _cv2_calib_buf[0].clear()
    _cv2_calib_count = 0
    _CV2_CALIB_LOCKED = False


def _body_left_user(blocks: list[tuple], calib: int | None = None, parity: int = 1) -> int:
    """用户公式（doclayout 全量验证：偏移近似恒定，std≈20 奇 / 28 偶）。

        left = ( mean(x1 | x1>15) - mean(x1 全部) ) / 2 - 15 + calib

    mean(x1 | x1>15) 是排除最左竖排侧眉后的正文左缘；减全体左缘均值得到
    "侧眉把全体均值左拉的量"（间距大→差值大→有意义），折半再 -15 得左界。
    calib 为每书标定的常数：默认从 _cv2_calib[parity] 读取（已由 dl 自动标定，
    换书零人工），也可显式传入。取保守值可保证 0 过裁，且换书只需重标定一个常数，
    公式结构本身不变（比固定 -50/120 阈值更泛化）。
    """
    if calib is None:
        calib = _cv2_calib.get(parity, _LEFT_CALIB_ODD if parity == 1 else _LEFT_CALIB_EVEN)
    all_x1 = [b[0] for b in blocks]
    if not all_x1:
        return 0
    body = [x for x in all_x1 if x > 15]
    m_all = sum(all_x1) / len(all_x1)
    m_body = sum(body) / len(body) if body else m_all
    return int((m_body - m_all) / 2 - 15 + calib)


def _body_right_even(blocks: list[tuple], w: int, gap: int = 40, pad: int = 28) -> int:
    """已验证偶数页 right：排除右侧边栏后取 M 与 X 的中点再左移 pad。

    M = max(x2)（含右侧边栏的最右缘）；X = max(x2 where x2 <= M - gap)（正文最右缘）。
    right = M - (M - X)/2 - pad，落在正文与边栏中间并留安全边距。
    """
    if not blocks:
        return w
    M = max(b[2] for b in blocks)
    body = [b for b in blocks if b[2] <= M - gap]
    if not body:
        # 无右侧边栏(gap 内无正文)：不裁右，返回整宽，避免把正文右缘切掉(过裁)
        return w
    X = max(b[2] for b in body)
    return min(w, max(0, int(M - (M - X) / 2 - pad)))


def _body_top_bottom(blocks: list[tuple], h: int) -> tuple[int, int]:
    """页眉/页脚检测，返回 (top, bottom)。

    设计取舍：宁「欠裁」（把页眉/页脚留在裁切内、或上下多留余量）也不「过裁」
    （切掉 doclayout 正文）。因此 top 取首块上缘上移 padding、bottom 取末块下缘
    下移 padding，不做把边界下推/上提的激进裁剪——旧版页眉/页脚分支会把奇数页
    正文上下缘切掉（端到端验证发现奇数页上下共 9 页过裁）。

    页眉/页脚仍被「包含」在裁切内（欠裁，可接受），而非被排除（过裁，丢失正文）。
    """
    if not blocks:
        return 0, h
    top = max(0, blocks[0][1] - 15)
    bottom = min(h, blocks[-1][3] + 15)
    return top, bottom


def _find_body_boundaries(img: np.ndarray, lines: list[tuple],
                          padding: int = 10,
                          page_num: int = 0) -> tuple[int, int, int, int]:
    """通过逐行投影 + 奇偶对称确定版心边界。

    公式已用 PP-DocLayoutV3 正文框(doclayout 真值)全量验证：
      - 奇数页(侧眉在左)：left=用户差值公式(_body_left_user) 裁左，right 保留整宽(右侧无边栏)。
      - 偶数页(侧眉在右)：left=用户差值公式(_body_left_user) 裁左，right=user_right(排除右侧边栏)。
      - top/bottom：_body_top_bottom 取首尾块上下缘±padding（宁欠裁包含页眉/页脚，也不过裁丢正文）。

    Returns:
        (top, bottom, left, right)
    """
    h, w = img.shape[:2]
    is_odd = (page_num % 2 == 1)  # True=奇数页(侧眉在左), False=偶数页(侧眉在右)

    blocks = _compute_blocks(img, lines, w)
    if not blocks:
        return 0, h, 0, w

    top, bottom = _body_top_bottom(blocks, h)
    parity = 1 if is_odd else 0
    left = _body_left_user(blocks, parity=parity)
    if is_odd:
        # 奇数页(侧眉在左)：仅裁左，right 保留整宽(右侧无边栏)。
        right = w
    else:
        # 偶数页(侧眉在右)：right 排除右侧边栏。
        right = _body_right_even(blocks, w)
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
                   page_num: int = 0, auto_calibrate: bool = True) -> np.ndarray | None:
    """版心裁剪：优先 PP-DocLayoutV3，失败降级 cv2 三级方案。

    - PP-DocLayoutV3 可用时：版心由其检测框(text/vertical_text/标题)并集决定，
      使用固定 padding（左右上 15 / 下 10），不受 padding 参数影响。
    - 不可用时：降级到 cv2 行检测 → 纯投影（使用 padding 参数）。
    - auto_calibrate=True 且 dl 可用时，前若干页自动用 dl 真值标定 cv2 左界
      calib（换书零人工；详见 _maybe_calibrate_cv2）。
    """
    rect = _doclayout_rect(img)
    if rect is not None:
        if auto_calibrate:
            _maybe_calibrate_cv2(img, page_num, dl_left=rect[0])
        left, top, right, bottom = rect
        return img[top:bottom, left:right].copy()

    # --- cv2 降级路径 ---
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
