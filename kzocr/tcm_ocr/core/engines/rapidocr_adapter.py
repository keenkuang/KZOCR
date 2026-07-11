"""
RapidOCR Adapter for TCM OCR System.

Uses RapidOCR (ONNX PP-OCRv4) for line-level text recognition.
Provides fast inference via ONNX Runtime with cached models.
"""

import logging
import time
from typing import Any, Dict, Optional

import numpy as np

logger = logging.getLogger(__name__)


class RapidOCRAdapter:
    """Adapter for RapidOCR (ONNX PP-OCRv4) recognition engine.

    Provides line-level text recognition using RapidOCR's ONNX runtime
    backend. Models are automatically downloaded and cached on first use.

    Attributes:
        device (str): Computing device for inference.
        engine: The underlying RapidOCR instance.
        _closed (bool): Flag indicating if adapter has been closed.
    """

    def __init__(
        self,
        char_dict: Optional[Dict[str, str]] = None,
        device: str = 'cpu'
    ) -> None:
        """Initialize the RapidOCR adapter.

        Args:
            char_dict: Custom character dictionary (unused, kept for compat).
            device: Computing device ('cpu' or 'cuda:N').
        """
        self.device: str = device
        self.engine: Any = None
        self._closed: bool = False
        self._init_engine()

    def _init_engine(self) -> None:
        """Initialize RapidOCR engine with local ONNX models."""
        try:
            from rapidocr import RapidOCR

            # RapidOCR downloads models on first use; subsequent runs use cache
            self.engine = RapidOCR()
            logger.info("RapidOCR initialized (device=%s)", self.device)
        except ImportError:
            logger.error("RapidOCR not installed. Install: pip install rapidocr")
            raise
        except Exception as e:
            logger.error("Failed to initialize RapidOCR: %s", e)
            raise

    def recognize(self, line_img: np.ndarray) -> str:
        """Recognize a single line image and return the full text.

        Args:
            line_img: Cropped line image as numpy array (H, W) or (H, W, C).

        Returns:
            Recognized text string. Empty string if recognition fails.
        """
        if self._closed:
            raise RuntimeError("RapidOCRAdapter has been closed.")

        start_time = time.time()
        result_text = ""

        try:
            if self.engine is not None:
                # The installed RapidOCR returns a single RapidOCROutput
                # object (a dataclass), NOT a (output, elapse) tuple.
                output = self.engine(line_img.copy())

                if output is not None:
                    # output.txts is a tuple of recognized strings (one per
                    # detection box). Concatenate them into the full line text.
                    txts = getattr(output, 'txts', None)
                    if txts:
                        result_text = "".join(txts)
                        elapse = getattr(output, 'elapse', 0.0) or 0.0
                        logger.debug(
                            "RapidOCR recognized: '%s' (elapse=%.3fs)",
                            result_text, elapse,
                        )
        except Exception as e:
            logger.error("RapidOCR recognition failed: %s", e)
            result_text = ""

        elapsed = time.time() - start_time
        logger.debug("RapidOCR recognized in %.3fs: '%s'", elapsed, result_text)
        return result_text

    def close(self) -> None:
        """Release RapidOCR engine resources."""
        if self._closed:
            return
        try:
            self.engine = None
            logger.info("RapidOCR adapter closed successfully")
        except Exception as e:
            logger.error("Error closing RapidOCR adapter: %s", e)
        finally:
            self._closed = True

    def __del__(self) -> None:
        """Destructor to ensure resources are released."""
        if not self._closed:
            self.close()

    def __enter__(self) -> 'RapidOCRAdapter':
        """Context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit."""
        self.close()
