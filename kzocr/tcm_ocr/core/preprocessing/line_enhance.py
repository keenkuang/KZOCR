"""
行级图像增强模块

提供单行文本图像的增强处理，包括：
- 亮度/对比度自适应调整
- CLAHE 对比度增强
- Unsharp Masking 锐化
- FastNLMeans 去噪
- 自适应二值化

根据 for_multimodal 参数控制增强强度：
- True：轻度增强（仅 CLAHE），保留字形细节用于多模态模型
- False：完整增强流程，面向传统 OCR 引擎
"""

from typing import Optional, Tuple

import cv2
import numpy as np


def _estimate_brightness_contrast(gray: np.ndarray) -> Tuple[float, float]:
    """估计图像的亮度和对比度。

    Parameters
    ----------
    gray :
        灰度图像，dtype uint8。

    Returns
    -------
    tuple
        (mean_brightness, contrast) 亮度均值和标准差。
    """
    if gray is None or gray.size == 0:
        return 128.0, 0.0
    mean_brightness = float(np.mean(gray))
    contrast = float(np.std(gray))
    return mean_brightness, contrast


def _unsharp_mask(
    gray: np.ndarray,
    blur_ksize: int = 3,
    alpha: float = 1.5,
    beta: float = -0.5,
    gamma: float = 0.0,
) -> np.ndarray:
    """Unsharp Masking 锐化。

    Parameters
    ----------
    gray :
        灰度图像。
    blur_ksize :
        高斯模糊核大小。
    alpha, beta, gamma :
        cv2.addWeighted 参数，输出 = alpha * 原图 + beta * 模糊图 + gamma。

    Returns
    -------
    np.ndarray
        锐化后的图像。
    """
    blurred = cv2.GaussianBlur(gray, (blur_ksize, blur_ksize), 0)
    sharpened = cv2.addWeighted(gray, alpha, blurred, beta, gamma)
    return np.clip(sharpened, 0, 255).astype(np.uint8)


def _adaptive_enhance(
    gray: np.ndarray, pub_year: Optional[int] = None, quality_profile: Optional[str] = None
) -> np.ndarray:
    """根据亮度和对比度自适应选择增强方式。

    Parameters
    ----------
    gray :
        灰度图像。
    pub_year :
        出版年份。
    quality_profile :
        质量等级 'low'|'medium'|'high'。

    Returns
    -------
    np.ndarray
        增强后的灰度图像。
    """
    if gray is None or gray.size == 0:
        return gray

    brightness, contrast = _estimate_brightness_contrast(gray)

    # 根据年代确定 CLAHE 参数
    if pub_year is not None and 1949 <= pub_year <= 1979:
        clip_limit = 3.0
    elif pub_year is not None and 1980 <= pub_year <= 1999:
        clip_limit = 2.5
    else:
        clip_limit = 2.0

    # 质量调整
    if quality_profile == 'low':
        clip_limit *= 1.2
    elif quality_profile == 'high':
        clip_limit *= 0.8

    clip_limit = float(np.clip(clip_limit, 1.0, 5.0))

    result = gray.copy()

    # 低亮度：提升亮度
    if brightness < 100:
        alpha = 1.0
        beta = min(30, 100 - brightness) * 0.8
        result = cv2.convertScaleAbs(result, alpha=alpha, beta=beta)

    # 低对比度：CLAHE 增强
    if contrast < 50:
        tile_h = max(4, gray.shape[0] // 8)
        tile_w = max(4, gray.shape[1] // 16)
        clahe = cv2.createCLAHE(
            clipLimit=clip_limit, tileGridSize=(tile_w, tile_h)
        )
        result = clahe.apply(result)
    elif contrast < 80:
        # 中等对比度，使用 convertScaleAbs 微调
        alpha = 1.2
        beta = -10.0
        result = cv2.convertScaleAbs(result, alpha=alpha, beta=beta)

    return result


def _denoise_line(
    gray: np.ndarray, quality_profile: Optional[str] = None
) -> np.ndarray:
    """对行图像进行去噪处理。

    Parameters
    ----------
    gray :
        灰度图像。
    quality_profile :
        质量等级，影响去噪强度。

    Returns
    -------
    np.ndarray
        去噪后的图像。
    """
    if gray is None or gray.size == 0:
        return gray

    # fastNlMeansDenoising 参数
    h = 10  # 滤波强度
    if quality_profile == 'low':
        h = 15
    elif quality_profile == 'high':
        h = 7

    search_window = 11
    block_size = 5

    try:
        denoised = cv2.fastNlMeansDenoising(
            gray, None, h=h,
            templateWindowSize=block_size,
            searchWindowSize=search_window,
        )
        return denoised
    except cv2.error:
        # 若 fastNlMeansDenoising 失败（如图像太小），使用中值滤波兜底
        ksize = 3 if quality_profile == 'high' else 5
        return cv2.medianBlur(gray, ksize)


def _binarize_line(gray: np.ndarray) -> np.ndarray:
    """自适应二值化行图像。

    Parameters
    ----------
    gray :
        增强后的灰度图像。

    Returns
    -------
    np.ndarray
        二值图像（0 和 255）。
    """
    if gray is None or gray.size == 0:
        return gray

    # 自适应高斯阈值
    block_size = max(11, int(min(gray.shape[:2]) * 0.1))
    block_size = block_size | 1  # 确保奇数
    block_size = max(block_size, 3)

    binary = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        block_size, 2,
    )
    return binary


def line_level_enhance(
    line_img: np.ndarray,
    for_multimodal: bool = False,
    pub_year: Optional[int] = None,
    quality_profile: Optional[str] = None,
) -> Tuple[np.ndarray, Optional[np.ndarray]]:
    """行级图像增强主入口。

    Parameters
    ----------
    line_img :
        输入单行图像（灰度或彩色），dtype uint8。
    for_multimodal :
        若为 True，仅做轻度增强（仅 CLAHE clipLimit=1.5），保留字形细节
        供多模态模型（如 GPT-4V）使用；
        若为 False，执行完整增强流程（亮度/对比度 → CLAHE → 锐化 →
        去噪 → 二值化），面向传统 OCR 引擎。
    pub_year :
        出版年份，用于调整增强参数。
    quality_profile :
        预设质量等级 'low'|'medium'|'high'，若为 None 则自动检测。

    Returns
    -------
    Tuple[np.ndarray, Optional[np.ndarray]]
        (增强图, 二值图)。
        - for_multimodal=True 时二值图为 None。
        - for_multimodal=False 时二值图为自适应二值化结果。
    """
    if line_img is None or line_img.size == 0:
        return line_img, None

    # 灰度化
    if len(line_img.shape) == 3 and line_img.shape[2] == 3:
        gray = cv2.cvtColor(line_img, cv2.COLOR_BGR2GRAY)
    else:
        gray = line_img.copy()

    if for_multimodal:
        # ---- 轻度增强：仅 CLAHE，保留字形细节 ----
        tile_h = max(4, gray.shape[0] // 4)
        tile_w = max(4, gray.shape[1] // 8)
        clahe = cv2.createCLAHE(
            clipLimit=1.5, tileGridSize=(tile_w, tile_h)
        )
        enhanced = clahe.apply(gray)
        return enhanced, None

    # ---- 完整增强流程 ----
    # Step 1: 亮度/对比度检测 + CLAHE / convertScaleAbs 调整
    enhanced = _adaptive_enhance(gray, pub_year=pub_year, quality_profile=quality_profile)

    # Step 2: 锐化（unsharp masking）
    try:
        enhanced = _unsharp_mask(
            enhanced, blur_ksize=3, alpha=1.5, beta=-0.5, gamma=0.0
        )
    except Exception:
        pass

    # Step 3: 去噪（fastNlMeansDenoising）
    try:
        enhanced = _denoise_line(enhanced, quality_profile=quality_profile)
    except Exception:
        pass

    # Step 4: 自适应二值化
    try:
        binary = _binarize_line(enhanced)
    except Exception:
        binary = None

    return enhanced, binary
