"""
PDF 按需渲染模块

提供 PDF 页面的按需渲染功能，支持：
- 指定 DPI 渲染（默认 300）
- LRU 内存缓存最近 N 页
- 渲染到内存或文件
- 自动资源释放

依赖：
- PyMuPDF (fitz) 用于 PDF 渲染
- Pillow 用于图像格式转换
"""

import os
import threading
from collections import OrderedDict
from typing import Optional

import cv2
import numpy as np

# PyMuPDF 可选导入，若未安装则提供提示
try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


class PDFRenderer:
    """PDF 页面按需渲染器。

    使用 PyMuPDF (fitz) 进行高质量 PDF 页面渲染，支持 LRU 内存缓存
    以减少重复渲染开销。

    Attributes
    ----------
    pdf_path : str
        PDF 文件路径。
    dpi : int
        渲染 DPI，默认 300。
    cache_size : int
        LRU 缓存大小，默认 50 页。

    Examples
    --------
    >>> renderer = PDFRenderer("book.pdf", dpi=300, cache_size=50)
    >>> img = renderer.render_page(0)  # 渲染第 1 页（0-based）
    >>> renderer.close()
    """

    def __init__(
        self,
        pdf_path: str,
        dpi: int = 300,
        cache_size: int = 50,
    ):
        """初始化 PDF 渲染器。

        Parameters
        ----------
        pdf_path :
            PDF 文件路径。
        dpi :
            渲染分辨率（点每英寸），默认 300。
        cache_size :
            LRU 缓存最大页数，默认 50。

        Raises
        ------
        ImportError
            若 PyMuPDF (fitz) 未安装。
        FileNotFoundError
            若 PDF 文件不存在。
        RuntimeError
            若 PDF 文件无法打开。
        """
        if not HAS_FITZ:
            raise ImportError(
                "PyMuPDF (fitz) is required for PDF rendering. "
                "Install it with: pip install PyMuPDF"
            )

        if not os.path.isfile(pdf_path):
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        self.pdf_path: str = pdf_path
        self.dpi: int = max(72, int(dpi))
        self.cache_size: int = max(1, int(cache_size))

        # LRU 缓存: OrderedDict[int, np.ndarray]
        self._cache: OrderedDict[int, np.ndarray] = OrderedDict()
        self._cache_lock = threading.Lock()

        # 打开 PDF 文档
        try:
            self._doc = fitz.open(self.pdf_path)
        except Exception as e:
            raise RuntimeError(f"Failed to open PDF: {pdf_path}") from e

        # 计算缩放矩阵
        self._zoom = self.dpi / 72.0  # PDF 默认 72 DPI
        self._mat = fitz.Matrix(self._zoom, self._zoom)

    def get_page_count(self) -> int:
        """获取 PDF 总页数。

        Returns
        -------
        int
            PDF 文档的总页数。
        """
        return len(self._doc)

    def render_page(self, page_num: int) -> np.ndarray:
        """渲染指定页面到内存中的 numpy 数组。

        优先从 LRU 缓存中获取，未命中则渲染并缓存。

        Parameters
        ----------
        page_num :
            页码，0-based。

        Returns
        -------
        np.ndarray
            渲染后的页面图像，BGR 格式，dtype uint8。

        Raises
        ------
        IndexError
            若页码越界。
        RuntimeError
            若渲染失败。
        """
        if page_num < 0 or page_num >= self.get_page_count():
            raise IndexError(
                f"Page number {page_num} out of range "
                f"[0, {self.get_page_count()})"
            )

        # 检查缓存
        with self._cache_lock:
            if page_num in self._cache:
                # 移到末尾（最近使用）
                img = self._cache.pop(page_num)
                self._cache[page_num] = img
                return img.copy()

        # 缓存未命中，执行渲染
        try:
            page = self._doc.load_page(page_num)
            pix = page.get_pixmap(matrix=self._mat, alpha=False)

            # 将 pixmap 转为 numpy 数组
            img_data = np.frombuffer(pix.samples, dtype=np.uint8)
            img = img_data.reshape(pix.height, pix.width, pix.n)

            # PyMuPDF 输出为 RGB，转为 BGR
            if pix.n == 3:
                img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            elif pix.n == 4:
                img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
            elif pix.n == 1:
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

            # 更新缓存
            with self._cache_lock:
                self._cache[page_num] = img.copy()
                # 超出缓存大小则淘汰最旧项
                while len(self._cache) > self.cache_size:
                    self._cache.popitem(last=False)

            return img

        except Exception as e:
            raise RuntimeError(
                f"Failed to render page {page_num} from {self.pdf_path}"
            ) from e

    def render_page_to_file(
        self, page_num: int, output_path: str
    ) -> str:
        """渲染指定页面并保存到文件。

        Parameters
        ----------
        page_num :
            页码，0-based。
        output_path :
            输出图像文件路径，支持 .png/.jpg/.bmp/.tiff 等格式。

        Returns
        -------
        str
            实际保存的文件路径。

        Raises
        ------
        IndexError
            若页码越界。
        ValueError
            若输出路径格式不支持。
        RuntimeError
            若渲染或保存失败。
        """
        img = self.render_page(page_num)

        # 确保输出目录存在
        out_dir = os.path.dirname(output_path)
        if out_dir and not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)

        # 使用 OpenCV 保存
        success = cv2.imwrite(output_path, img)
        if not success:
            raise RuntimeError(f"Failed to save image to: {output_path}")

        return output_path

    def _evict_cache(self, page_num: int) -> None:
        """从缓存中移除指定页面。

        Parameters
        ----------
        page_num :
            要移除的页码。
        """
        with self._cache_lock:
            self._cache.pop(page_num, None)

    def clear_cache(self) -> None:
        """清空 LRU 缓存，释放内存。"""
        with self._cache_lock:
            self._cache.clear()

    def close(self) -> None:
        """关闭 PDF 文档并释放所有资源。

        调用后此实例不再可用。
        """
        self.clear_cache()
        if hasattr(self, '_doc') and self._doc:
            self._doc.close()
            self._doc = None  # type: ignore[assignment]

    def __enter__(self):
        """上下文管理器入口。"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口，确保资源释放。"""
        self.close()

    def __del__(self):
        """析构函数，尝试释放资源。"""
        try:
            self.close()
        except Exception:
            pass

    def get_cache_info(self) -> dict:
        """获取缓存状态信息。

        Returns
        -------
        dict
            包含缓存大小、命中页码列表、缓存占用字节数等信息。
        """
        with self._cache_lock:
            total_bytes = sum(
                img.nbytes for img in self._cache.values()
            )
            return {
                'cache_size': self.cache_size,
                'cached_count': len(self._cache),
                'cached_pages': list(self._cache.keys()),
                'total_bytes': total_bytes,
                'total_mb': round(total_bytes / (1024 * 1024), 2),
            }
