"""
图像预处理模块

提供中医现代出版物 OCR 系统的完整图像预处理功能：

- 整页增强 (page_enhance): 去黑边、倾斜校正、阴影去除、CLAHE 增强
- 行级增强 (line_enhance): 单行文本的亮度/对比度/锐化/去噪/二值化
- 版面噪声过滤 (noise_filter): MinerU 分析后的块级噪声过滤
- PDF 渲染 (pdf_renderer): 按需渲染 PDF 页面，带 LRU 缓存
- 版式分类 (layout_classifier): 页面版式自动分类（文本/表格/多栏）

导出接口：
    preprocess_before_mineru, detect_skew_angle, rotate_image,
    remove_shadow, detect_print_quality, page_level_enhance,
    line_level_enhance, post_mineru_noise_filter, infer_body_region,
    ppocr_noise_filter, is_near_edge, BLOCK_TYPE_DISCARD, BLOCK_TYPE_KEEP,
    PDFRenderer, LayoutClassifier
"""

from .page_enhance import (
    detect_print_quality,
    detect_skew_angle,
    page_level_enhance,
    preprocess_before_mineru,
    remove_black_borders,
    remove_shadow,
    rotate_image,
)
from .line_enhance import line_level_enhance
from .noise_filter import (
    BLOCK_TYPE_DISCARD,
    BLOCK_TYPE_KEEP,
    infer_body_region,
    is_near_edge,
    post_mineru_noise_filter,
    ppocr_noise_filter,
)
from .pdf_renderer import PDFRenderer
from .layout_classifier import LayoutClassifier

__all__ = [
    # page_enhance
    'preprocess_before_mineru',
    'detect_skew_angle',
    'rotate_image',
    'remove_shadow',
    'remove_black_borders',
    'detect_print_quality',
    'page_level_enhance',
    # line_enhance
    'line_level_enhance',
    # noise_filter
    'BLOCK_TYPE_DISCARD',
    'BLOCK_TYPE_KEEP',
    'post_mineru_noise_filter',
    'infer_body_region',
    'ppocr_noise_filter',
    'is_near_edge',
    # pdf_renderer
    'PDFRenderer',
    # layout_classifier
    'LayoutClassifier',
]
