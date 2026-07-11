"""
整页级图像处理模块

提供整页图像的预处理功能，包括：
- 保守去黑边（仅裁连续纯黑区域 >20px 且全黑率 >99%）
- 倾斜检测与纠偏（投影方差法，多分辨率搜索）
- 阴影去除（形态学闭运算 + cv2.divide）
- 打印质量评估（噪声水平 + 清晰度）
- 年代感知的 CLAHE 增强

所有函数接受 numpy.ndarray 输入，输出处理后图像。
"""

import math
from typing import Optional

import cv2
import numpy as np


# --------------------------------------------------------------------------- #
# 1. 去黑边
# --------------------------------------------------------------------------- #

def _trim_black_edges_single_axis(
    img: np.ndarray,
    axis: int,
    min_trim: int = 20,
    black_ratio_thresh: float = 0.99,
) -> np.ndarray:
    """沿单个轴裁剪连续纯黑边缘。

    Parameters
    ----------
    img :
        输入图像，可以是灰度或彩色，dtype 为 uint8。
    axis :
        0 表示裁剪行（上下），1 表示裁剪列（左右）。
    min_trim :
        触发裁剪的最小连续黑边长度（像素）。
    black_ratio_thresh :
        判定为黑边的全黑率阈值。

    Returns
    -------
    np.ndarray
        裁剪后的图像。
    """
    if img.size == 0:
        return img

    # 灰度化用于判断
    if len(img.shape) == 3 and img.shape[2] == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img

    if axis == 0:  # 沿行裁剪
        h = gray.shape[0]
        # 从上到下扫描
        top = 0
        for i in range(h):
            row = gray[i, :]
            black_pixels = np.sum(row <= 10)
            ratio = black_pixels / row.size if row.size > 0 else 0.0
            if ratio >= black_ratio_thresh:
                top = i + 1
            else:
                break

        # 从下到上扫描
        bottom = h
        for i in range(h - 1, -1, -1):
            row = gray[i, :]
            black_pixels = np.sum(row <= 10)
            ratio = black_pixels / row.size if row.size > 0 else 0.0
            if ratio >= black_ratio_thresh:
                bottom = i
            else:
                break

        # 检查裁剪量是否达到阈值
        if top < min_trim:
            top = 0
        if (img.shape[0] - bottom) < min_trim:
            bottom = img.shape[0]

        return img[top:bottom, ...] if img.ndim >= 2 else img

    else:  # 沿列裁剪
        w = gray.shape[1]
        # 从左到右扫描
        left = 0
        for i in range(w):
            col = gray[:, i]
            black_pixels = np.sum(col <= 10)
            ratio = black_pixels / col.size if col.size > 0 else 0.0
            if ratio >= black_ratio_thresh:
                left = i + 1
            else:
                break

        # 从右到左扫描
        right = w
        for i in range(w - 1, -1, -1):
            col = gray[:, i]
            black_pixels = np.sum(col <= 10)
            ratio = black_pixels / col.size if col.size > 0 else 0.0
            if ratio >= black_ratio_thresh:
                right = i
            else:
                break

        if left < min_trim:
            left = 0
        if (img.shape[1] - right) < min_trim:
            right = img.shape[1]

        return img[:, left:right, ...] if img.ndim >= 2 else img


def remove_black_borders(page_img: np.ndarray) -> np.ndarray:
    """保守去除连续纯黑边。

    仅当连续纯黑区域宽度/高度 > 20px 且全黑率 > 99% 时才裁剪。

    Parameters
    ----------
    page_img :
        输入整页图像，BGR uint8。

    Returns
    -------
    np.ndarray
        去黑边后的图像。
    """
    if page_img is None or page_img.size == 0:
        return page_img

    result = _trim_black_edges_single_axis(page_img, axis=0, min_trim=20)
    result = _trim_black_edges_single_axis(result, axis=1, min_trim=20)
    return result


# --------------------------------------------------------------------------- #
# 2. 倾斜检测与纠偏
# --------------------------------------------------------------------------- #

def detect_skew_angle(
    gray: np.ndarray,
    angle_range: tuple = (-5.0, 5.1),
    step: float = 0.5,
) -> float:
    """基于投影方差法的倾斜角度检测，支持多分辨率搜索。

    先对下采样图像（长边 800px）进行粗搜（给定 step），
    再在原分辨率 ±1° 范围内以 step/2 进行细搜。

    Parameters
    ----------
    gray :
        灰度图像，dtype uint8。
    angle_range :
        搜索角度范围，(min, max) 度，默认 (-5, 5.1)。
    step :
        粗搜步长，度，默认 0.5。

    Returns
    -------
    float
        检测到的倾斜角度（度），顺时针为正。
    """
    if gray is None or gray.size == 0:
        return 0.0

    gray_work = gray.copy()
    h, w = gray_work.shape[:2]

    # 二值化用于投影方差计算
    _, binary = cv2.threshold(gray_work, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # 下采样比例（粗搜用）
    max_dim = max(h, w)
    coarse_scale = 1.0
    if max_dim > 800:
        coarse_scale = 800.0 / max_dim

    coarse_h = int(h * coarse_scale)
    coarse_w = int(w * coarse_scale)

    # 下采样后的二值图
    if coarse_scale < 1.0:
        binary_coarse = cv2.resize(
            binary, (coarse_w, coarse_h), interpolation=cv2.INTER_AREA
        )
        # 重新二值化，防止插值引入灰度
        _, binary_coarse = cv2.threshold(binary_coarse, 127, 255, cv2.THRESH_BINARY)
    else:
        binary_coarse = binary

    def _variance_at_angle(img_bin: np.ndarray, angle: float) -> float:
        """计算给定角度下水平投影的方差。"""
        rad = math.radians(angle)
        cos_a = math.cos(rad)
        sin_a = math.sin(rad)

        ih, iw = img_bin.shape[:2]
        cx, cy = iw / 2.0, ih / 2.0

        # 旋转矩阵
        M = cv2.getRotationMatrix2D((cx, cy), angle, 1.0)

        # 计算新边界
        corners = np.array([[0, 0], [iw, 0], [iw, ih], [0, ih]], dtype=np.float32)
        corners_rot = cv2.transform(corners.reshape(1, -1, 2), M).reshape(-1, 2)
        new_w = int(np.max(corners_rot[:, 0]) - np.min(corners_rot[:, 0]))
        new_h = int(np.max(corners_rot[:, 1]) - np.min(corners_rot[:, 1]))

        # 调整平移
        M[0, 2] += (new_w - iw) / 2.0
        M[1, 2] += (new_h - ih) / 2.0

        rotated = cv2.warpAffine(
            img_bin, M, (new_w, new_h), flags=cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT, borderValue=255,
        )

        # 水平投影（统计每行的黑色像素数）
        projection = np.sum(rotated < 128, axis=1).astype(np.float64)
        if projection.size == 0:
            return 0.0
        return float(np.var(projection))

    # ---- 粗搜 ----
    angles_coarse = np.arange(angle_range[0], angle_range[1], step)
    if angles_coarse.size == 0:
        angles_coarse = np.array([0.0])

    best_angle = 0.0
    best_var = -1.0

    for a in angles_coarse:
        v = _variance_at_angle(binary_coarse, float(a))
        if v > best_var:
            best_var = v
            best_angle = float(a)

    # ---- 细搜（在原分辨率 ±1° 范围内） ----
    fine_step = step / 2.0
    fine_range = (max(angle_range[0], best_angle - 1.0),
                  min(angle_range[1], best_angle + 1.0 + fine_step))
    angles_fine = np.arange(fine_range[0], fine_range[1], fine_step)
    if angles_fine.size == 0:
        angles_fine = np.array([best_angle])

    for a in angles_fine:
        v = _variance_at_angle(binary, float(a))
        if v > best_var:
            best_var = v
            best_angle = float(a)

    return best_angle


def rotate_image(gray: np.ndarray, angle: float) -> np.ndarray:
    """使用仿射变换旋转图像。

    Parameters
    ----------
    gray :
        输入图像（灰度或彩色）。
    angle :
        旋转角度（度），正值表示逆时针旋转。

    Returns
    -------
    np.ndarray
        旋转后的图像，边缘使用 BORDER_REPLICATE 填充。
    """
    if gray is None or gray.size == 0:
        return gray

    h, w = gray.shape[:2]
    cx, cy = w / 2.0, h / 2.0

    M = cv2.getRotationMatrix2D((cx, cy), angle, 1.0)

    # 计算旋转后边界
    cos_a = abs(math.cos(math.radians(angle)))
    sin_a = abs(math.sin(math.radians(angle)))
    new_w = int(w * cos_a + h * sin_a)
    new_h = int(w * sin_a + h * cos_a)

    # 调整平移
    M[0, 2] += (new_w - w) / 2.0
    M[1, 2] += (new_h - h) / 2.0

    rotated = cv2.warpAffine(
        gray, M, (new_w, new_h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return rotated


# --------------------------------------------------------------------------- #
# 3. 阴影去除
# --------------------------------------------------------------------------- #

def remove_shadow(page_img: np.ndarray) -> np.ndarray:
    """使用形态学闭运算提取背景，通过除法去除阴影。

    算法流程：
    1. 将图像转为灰度
    2. 使用大核形态学闭运算提取背景光照
    3. 对背景进行高斯模糊
    4. 使用 cv2.divide 将原图除以背景图
    5. 自适应直方图均衡恢复对比度

    Parameters
    ----------
    page_img :
        输入图像，BGR uint8。

    Returns
    -------
    np.ndarray
        去阴影后的图像，与输入同通道数。
    """
    if page_img is None or page_img.size == 0:
        return page_img

    is_color = len(page_img.shape) == 3 and page_img.shape[2] == 3

    if is_color:
        # 转换到 LAB 空间，仅对 L 通道处理
        lab = cv2.cvtColor(page_img, cv2.COLOR_BGR2LAB)
        l_channel = lab[:, :, 0]
        a_channel = lab[:, :, 1]
        b_channel = lab[:, :, 2]

        # 形态学闭运算提取背景（大核）
        kernel_size = max(25, int(max(page_img.shape[:2]) * 0.02))
        kernel_size = kernel_size | 1  # 确保奇数
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))

        background = cv2.morphologyEx(l_channel, cv2.MORPH_DILATE, kernel)
        background = cv2.medianBlur(background, 21)

        # 避免除零
        background = np.clip(background.astype(np.float32), 1.0, 255.0)
        l_float = l_channel.astype(np.float32)

        # 除法去阴影
        ratio = 255.0 / background
        l_deshadow = np.clip(l_float * ratio, 0, 255).astype(np.uint8)

        # 合并回 LAB
        lab[:, :, 0] = l_deshadow
        result = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    else:
        # 灰度图处理
        gray = page_img.copy()
        kernel_size = max(25, int(max(gray.shape[:2]) * 0.02))
        kernel_size = kernel_size | 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))

        background = cv2.morphologyEx(gray, cv2.MORPH_DILATE, kernel)
        background = cv2.medianBlur(background, 21)

        background = np.clip(background.astype(np.float32), 1.0, 255.0)
        gray_float = gray.astype(np.float32)

        ratio = 255.0 / background
        result = np.clip(gray_float * ratio, 0, 255).astype(np.uint8)

    return result


# --------------------------------------------------------------------------- #
# 4. 打印质量检测
# --------------------------------------------------------------------------- #

def detect_print_quality(gray: np.ndarray) -> str:
    """基于噪声水平和清晰度评估打印质量。

    评估指标：
    - noise：使用拉普拉斯方差估计噪声水平
    - sharpness：使用 Sobel 梯度评估清晰度

    Parameters
    ----------
    gray :
        灰度图像，dtype uint8。

    Returns
    -------
    str
        'low' | 'medium' | 'high'
        - noise > 30 或 sharpness < 30 → 'low'
        - noise > 15 或 sharpness < 80 → 'medium'
        - 否则 'high'
    """
    if gray is None or gray.size == 0:
        return 'low'

    # 噪声估计：中值滤波后的残差方差
    denoised = cv2.medianBlur(gray, 3)
    noise_map = gray.astype(np.float32) - denoised.astype(np.float32)
    noise = float(np.std(noise_map))

    # 清晰度：Sobel 梯度幅值均值
    sobelx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    sobely = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    gradient_magnitude = np.sqrt(sobelx ** 2 + sobely ** 2)
    sharpness = float(np.mean(gradient_magnitude))

    if noise > 30.0 or sharpness < 30.0:
        return 'low'
    elif noise > 15.0 or sharpness < 80.0:
        return 'medium'
    else:
        return 'high'


# --------------------------------------------------------------------------- #
# 5. MinerU 前预处理
# --------------------------------------------------------------------------- #

def preprocess_before_mineru(page_img: np.ndarray) -> np.ndarray:
    """MinerU 分析前的整页预处理流程。

    处理步骤：
    1. 保守去黑边（仅裁连续纯黑区域 >20px 且全黑率 >99%）
    2. 检测倾斜角度
    3. 纠偏旋转
    4. 去阴影
    5. 不做版心裁剪！

    Parameters
    ----------
    page_img :
        输入整页图像，BGR uint8。

    Returns
    -------
    np.ndarray
        预处理后的整页图像。
    """
    if page_img is None or page_img.size == 0:
        return page_img

    # Step 1: 保守去黑边
    result = remove_black_borders(page_img)
    if result is None or result.size == 0:
        return page_img

    # Step 2: 灰度化用于倾斜检测
    if len(result.shape) == 3 and result.shape[2] == 3:
        gray = cv2.cvtColor(result, cv2.COLOR_BGR2GRAY)
    else:
        gray = result

    # Step 3: 检测倾斜角度
    try:
        angle = detect_skew_angle(gray, angle_range=(-5.0, 5.1), step=0.5)
    except Exception:
        angle = 0.0

    # Step 4: 纠偏（对彩色图旋转）
    if abs(angle) > 0.1:
        result = rotate_image(result, angle)
        if len(result.shape) == 3 and result.shape[2] == 3:
            gray = cv2.cvtColor(result, cv2.COLOR_BGR2GRAY)
        else:
            gray = result

    # Step 5: 去阴影
    try:
        result = remove_shadow(result)
    except Exception:
        pass  # 去阴影失败则保留原图

    return result


# --------------------------------------------------------------------------- #
# 6. 整页增强（主入口）
# --------------------------------------------------------------------------- #

def page_level_enhance(
    page_img: np.ndarray,
    pub_year: Optional[int] = None,
    quality_profile: Optional[str] = None,
) -> np.ndarray:
    """整页级增强主入口。

    处理流程：
    1. 灰度化
    2. 倾斜校正
    3. 去阴影
    4. CLAHE 增强（年代感知的 clipLimit）

    年代参数：
    - 1949-1979: CLAHE clipLimit = 3.0
    - 1980-1999: CLAHE clipLimit = 2.5
    - 2000+     : CLAHE clipLimit = 2.0

    质量参数加权融合：年代权重 0.6，质量权重 0.4

    Parameters
    ----------
    page_img :
        输入整页图像，BGR uint8。
    pub_year :
        出版年份，用于调整增强强度。
    quality_profile :
        预设质量等级 'low'|'medium'|'high'，若为 None 则自动检测。

    Returns
    -------
    np.ndarray
        增强后的灰度图像，dtype uint8。
    """
    if page_img is None or page_img.size == 0:
        return page_img

    # 灰度化
    if len(page_img.shape) == 3 and page_img.shape[2] == 3:
        gray = cv2.cvtColor(page_img, cv2.COLOR_BGR2GRAY)
    else:
        gray = page_img.copy()

    # 倾斜校正
    try:
        angle = detect_skew_angle(gray, angle_range=(-5.0, 5.1), step=0.5)
        if abs(angle) > 0.1:
            gray = rotate_image(gray, angle)
    except Exception:
        pass

    # 去阴影
    try:
        # remove_shadow 期望 BGR，暂时转回
        gray_bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        gray_bgr = remove_shadow(gray_bgr)
        gray = cv2.cvtColor(gray_bgr, cv2.COLOR_BGR2GRAY)
    except Exception:
        pass

    # 自动检测质量
    if quality_profile is None:
        try:
            quality_profile = detect_print_quality(gray)
        except Exception:
            quality_profile = 'medium'

    # 确定 CLAHE 参数（年代 + 质量加权融合）
    # 年代基础 clipLimit
    era_clip = 2.0  # 默认 2000+
    if pub_year is not None:
        if 1949 <= pub_year <= 1979:
            era_clip = 3.0
        elif 1980 <= pub_year <= 1999:
            era_clip = 2.5
        elif pub_year >= 2000:
            era_clip = 2.0

    # 质量调整因子
    quality_factor = {'low': 1.3, 'medium': 1.0, 'high': 0.8}.get(
        quality_profile, 1.0
    )

    # 加权融合：年代权重 0.6，质量权重 0.4
    final_clip = era_clip * 0.6 + (2.0 * quality_factor) * 0.4
    final_clip = float(np.clip(final_clip, 1.0, 5.0))

    # CLAHE 增强
    tile_size = max(8, int(min(gray.shape[:2]) * 0.02))
    tile_size = max(tile_size, 8)
    clahe = cv2.createCLAHE(
        clipLimit=final_clip, tileGridSize=(tile_size, tile_size)
    )
    enhanced = clahe.apply(gray)

    return enhanced
