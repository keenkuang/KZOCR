"""
MinerU v3 Adapter for TCM OCR System.

Uses MinerU v3's PPDocLayoutV2 for layout analysis and shared PytorchPaddleOCR
for text recognition, via the MinerU shared model pool (custom_model_init).

Layout model detects 8 block types:
  text, title, figure, figure_caption, table, table_caption, header, footer, formula
"""

import logging
import time
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Layout labels from PPDocLayoutV2
LAYOUT_LABELS: Dict[int, str] = {
    0: "text", 1: "title", 2: "figure", 3: "figure_caption",
    4: "table", 5: "table_caption", 6: "header", 7: "footer",
}


class MinerUAdapter:
    """Adapter for MinerU v3 document analysis engine.

    Provides layout analysis via PPDocLayoutV2 and text recognition via
    MinerU's shared PytorchPaddleOCR model pool.

    Attributes:
        device (str): Computing device ('cpu' or 'cuda:N').
        _closed (bool): Flag indicating if the adapter has been closed.
    """

    def __init__(self, device: str = 'cpu') -> None:
        """Initialize the MinerU v3 adapter.

        Args:
            device: Computing device for inference. Defaults to 'cpu'.
        """
        self.device: str = device
        self.layout_model: Any = None
        self.ocr: Any = None
        self._closed: bool = False
        self._init_engine()

    def _init_engine(self) -> None:
        """Initialize MinerU v3 models via shared model pool."""
        try:
            from mineru.backend.pipeline.pipeline_analyze import custom_model_init
            import paddle

            paddle.set_device(self.device)

            pipeline = custom_model_init(lang='ch', formula_enable=False, table_enable=False)
            self.layout_model = pipeline.layout_model  # PPDocLayoutV2LayoutModel
            self.ocr = pipeline.ocr_model  # PytorchPaddleOCR
            logger.info("MinerU v3 initialized (layout + ocr, device=%s)", self.device)
        except ImportError:
            logger.warning("MinerU v3 not installed, falling back to stub mode")
            self.layout_model = None
            self.ocr = None
            raise
        except Exception as e:
            logger.error("Failed to initialize MinerU v3: %s", e)
            raise

    def analyze(self, page_img: np.ndarray) -> List[Dict[str, Any]]:
        """Run layout analysis on a page image and return detected blocks.

        Uses PytorchPaddleOCR full-page detection to find all text regions,
        producing more accurate blocks than the simple fallback.

        Args:
            page_img: Full page image as numpy array (H, W, C) in RGB.

        Returns:
            List of block dictionaries with 'type', 'bbox', 'text'.
        """
        if self._closed:
            raise RuntimeError("MinerUAdapter has been closed.")
        if self.ocr is None:
            return self._fallback_layout(page_img)

        start_time = time.time()
        blocks: List[Dict[str, Any]] = []

        try:
            # Full-page OCR with detection: returns [[[bbox, (text, conf)], ...]]
            results = self.ocr.ocr(page_img.copy(), det=True)
            if results and len(results) > 0:
                for (bbox, text_conf) in results[0]:
                    if not bbox or not text_conf:
                        continue
                    text, conf = text_conf if isinstance(text_conf, tuple) else (str(text_conf), 0)
                    if conf < 0.3:
                        continue
                    # bbox is [[x1,y1],[x2,y1],[x2,y2],[x1,y2]] format
                    x_coords = [p[0] for p in bbox]
                    y_coords = [p[1] for p in bbox]
                    x1, y1 = int(min(x_coords)), int(min(y_coords))
                    x2, y2 = int(max(x_coords)), int(max(y_coords))
                    blocks.append({
                        "bbox": [x1, y1, x2, y2],
                        "type": "text",
                        "text": text,
                    })
        except Exception as e:
            logger.error("MinerU layout analysis failed: %s", e)
            return self._fallback_layout(page_img)

        elapsed = time.time() - start_time
        logger.debug("MinerU layout analyzed %d blocks in %.3fs", len(blocks), elapsed)
        return blocks

    def _fallback_layout(self, page_img: np.ndarray) -> List[Dict[str, Any]]:
        """Simple line-based fallback when layout model is unavailable."""
        h, w = page_img.shape[:2]
        gray = cv2.cvtColor(page_img, cv2.COLOR_RGB2GRAY)
        # Horizontal projection to find text lines
        proj = np.mean(gray < 128, axis=1)  # dark pixel ratio per row
        in_block = False
        blocks = []
        for y in range(h):
            if proj[y] > 0.02 and not in_block:
                y_start = y
                in_block = True
            elif proj[y] <= 0.02 and in_block:
                if y - y_start > 10:
                    blocks.append({
                        "bbox": [0, y_start, w, y],
                        "type": "text",
                        "confidence": 0.5,
                    })
                in_block = False
        if in_block and h - y_start > 10:
            blocks.append({"bbox": [0, y_start, w, h], "type": "text", "confidence": 0.5})
        return blocks

    def recognize(self, line_img: np.ndarray) -> str:
        """Recognize text from a cropped line image.

        Delegates to the shared PytorchPaddleOCR model from MinerU's pool.

        Args:
            line_img: Cropped line image as numpy array (H, W) or (H, W, C).

        Returns:
            Recognized text string.
        """
        if self._closed:
            raise RuntimeError("MinerUAdapter has been closed.")
        if self.ocr is None:
            return ""

        try:
            rec_result = self.ocr.ocr(line_img.copy(), det=False)
            if rec_result and len(rec_result) > 0:
                page_lines = rec_result[0]
                if page_lines and len(page_lines) > 0:
                    text_conf = page_lines[0]
                    if isinstance(text_conf, tuple) and len(text_conf) >= 1:
                        return str(text_conf[0])
        except Exception as e:
            logger.debug("MinerU OCR recognition failed: %s", e)
        return ""

    def close(self) -> None:
        """Release MinerU resources."""
        if self._closed:
            return
        try:
            self.layout_model = None
            self.ocr = None
            logger.info("MinerU adapter closed successfully")
        except Exception as e:
            logger.error("Error closing MinerU adapter: %s", e)
        finally:
            self._closed = True

    def __del__(self) -> None:
        self.close()

    def __enter__(self) -> 'MinerUAdapter':
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
