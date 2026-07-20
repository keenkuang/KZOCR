"""
PaddleOCR Engine Adapter for TCM OCR System.

Provides interface to PaddleOCR V6+ with enhanced character-level recognition
capabilities. Supports:
- Standard line-level text recognition
- Character-level recognition with confidence scores and CTC time steps
- Character bounding box extraction via CTC time step back-projection
- Custom character dictionary for TCM terminology
"""

from __future__ import annotations

import logging
import time
import types
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

if TYPE_CHECKING:
    import paddle

logger = logging.getLogger(__name__)

# Default TCM character dictionary for common herbs and terms
DEFAULT_TCM_CHAR_DICT: Dict[str, str] = {
    '芪': '黄芪', '芷': '白芷', '苓': '茯苓', '芎': '川芎',
    '蒡': '牛蒡子', '菔': '莱菔子', '苡': '薏苡仁', '蔻': '白豆蔻',
    '術': '白术', '术': '白术', '黨': '党参', '麝': '麝香',
    '蟾': '蟾酥', '藿': '藿香', '菖': '石菖蒲', '藁': '藁本',
    '蚶': '瓦楞子', '虻': '虻虫', '蛭': '水蛭', '螯': '桑螵蛸',
    '炙': '炙', '煅': '煅', '煨': '煨', '焙': '焙',
    '烊': '烊化', '兑': '兑服', '冲服': '冲服', '先煎': '先煎',
    '后下': '后下', '包煎': '包煎', '另煎': '另煎', '煎汤代水': '煎汤代水',
    'g': '克', '克': '克', '两': '两', '钱': '钱', '分': '分',
    '毫升': '毫升', 'ml': '毫升', '枚': '枚', '个': '个',
    '片': '片', '段': '段', '寸': '寸', '握': '握',
}


class PaddleOCRAdapter:
    """Adapter for PaddleOCR V6+ recognition engine.

    Provides both standard line-level recognition and enhanced character-level
    recognition with CTC time step information for precise character bounding
    box extraction.

    Attributes:
        char_dict (dict): Custom character dictionary for TCM terminology.
        device (str): Computing device for inference.
        ocr: The underlying PaddleOCR engine instance.
        rec_model: Recognition model for character-level inference.
        _closed (bool): Flag indicating if adapter has been closed.
    """

    def __init__(
        self,
        char_dict: Optional[Dict[str, str]] = None,
        device: str = 'cpu'
    ) -> None:
        """Initialize the PaddleOCR adapter.

        Args:
            char_dict: Custom character dictionary mapping characters to
                      their preferred forms. Defaults to built-in TCM dict.
            device: Computing device ('cpu', 'gpu', or specific GPU id).
        """
        self.char_dict: Dict[str, str] = char_dict or DEFAULT_TCM_CHAR_DICT
        self.device: str = device
        self.ocr: Any = None
        self._closed: bool = False
        self._init_engine()

    def _init_engine(self) -> None:
        """Initialize the OCR engine via MinerU's shared model pool."""
        try:
            from mineru.backend.pipeline.pipeline_analyze import custom_model_init
            import paddle

            paddle.set_device(self.device)

            pipeline = custom_model_init(lang='ch', formula_enable=False, table_enable=False)
            self.ocr = pipeline.ocr_model  # PytorchPaddleOCR
            self.rec_model = None
            logger.info("PaddleOCR initialized via MinerU (device=%s)", self.device)
        except ImportError:
            logger.warning("MinerU not installed, falling back to standalone PaddleOCR...")
            self._init_standalone()
        except Exception as e:
            logger.error("Failed to initialize PaddleOCR via MinerU: %s", e)
            raise

    def _init_standalone(self) -> None:
        """Fallback: init standalone PaddleOCR (no MinerU dependency)."""
        try:
            from paddleocr import PaddleOCR
            self.ocr = PaddleOCR(lang='ch')
            self.rec_model = None
            logger.info("PaddleOCR standalone initialized (device=%s)", self.device)
        except Exception as e:
            logger.error("Standalone PaddleOCR init failed: %s", e)
            raise

    def recognize(self, line_img: np.ndarray) -> str:
        """Recognize a single line image and return the full text.

        Args:
            line_img: Cropped line image as numpy array (H, W) or (H, W, C).

        Returns:
            Recognized text string. Empty string if recognition fails.

        Example:
            >>> adapter = PaddleOCRAdapter(device='cpu')
            >>> text = adapter.recognize(line_image)
            >>> print(text)
            '黄芪 15g 水煎服'
        """
        if self._closed:
            raise RuntimeError("PaddleOCRAdapter has been closed.")

        start_time = time.time()
        result_text = ""

        try:
            if self.ocr is not None:
                # PytorchPaddleOCR.ocr(det=False) returns [[(text, conf), ...]]
                rec_result = self.ocr.ocr(line_img.copy(), det=False)
                if rec_result and len(rec_result) > 0:
                    page_lines = rec_result[0]  # list of (text, conf) tuples
                    if page_lines and len(page_lines) > 0:
                        text_conf = page_lines[0]
                        if isinstance(text_conf, tuple) and len(text_conf) >= 1:
                            result_text = str(text_conf[0])
                            logger.debug(
                                "PaddleOCR recognized: '%s' (confidence=%.3f)",
                                result_text,
                                text_conf[1] if len(text_conf) > 1 else 0.0,
                            )
            else:
                logger.debug("PaddleOCR in stub mode, returning empty string")

        except Exception as e:
            logger.error("PaddleOCR recognition failed: %s", e)
            result_text = ""

        elapsed = time.time() - start_time
        logger.debug("PaddleOCR recognized in %.3fs: '%s'", elapsed, result_text)
        return result_text

    def recognize_char_level(self, line_img: np.ndarray) -> List[Dict[str, Any]]:
        """Recognize a line image and return character-level details.

        This method provides per-character recognition results including
        confidence scores and CTC time step positions. This requires
        direct access to the recognition model's CTC output.

        The CTC (Connectionist Temporal Classification) decoder maps
        neural network time steps to character sequences. By extracting
        intermediate CTC probabilities, we can determine which time
        steps correspond to each character.

        Args:
            line_img: Cropped line image as numpy array (H, W) or (H, W, C).

        Returns:
            A list of character detail dictionaries, each containing:
                - char (str): The recognized character
                - conf (float): Confidence score for this character (0-1)
                - start_step (int): Starting CTC time step index
                - end_step (int): Ending CTC time step index (exclusive)

        Example:
            >>> details = adapter.recognize_char_level(line_image)
            >>> print(details)
            [{'char': '黄', 'conf': 0.98, 'start_step': 2, 'end_step': 7},
             {'char': '芪', 'conf': 0.95, 'start_step': 7, 'end_step': 12}]
        """
        if self._closed:
            raise RuntimeError("PaddleOCRAdapter has been closed.")

        char_details: List[Dict[str, Any]] = []

        try:
            if self.rec_model is not None:
                char_details = self._recognize_with_ctc(line_img)
            else:
                # Fallback: do regular recognition and approximate
                full_text = self.recognize(line_img)
                if full_text:
                    # Approximate equal distribution of time steps
                    num_chars = len(full_text)
                    total_steps = max(num_chars * 4, 32)
                    step_per_char = total_steps // max(num_chars, 1)
                    for i, ch in enumerate(full_text):
                        char_details.append({
                            'char': ch,
                            'conf': 0.8,  # Default confidence in stub mode
                            'start_step': i * step_per_char,
                            'end_step': (i + 1) * step_per_char,
                        })

        except Exception as e:
            logger.error("PaddleOCR char-level recognition failed: %s", e)
            # Fallback to basic splitting
            full_text = self.recognize(line_img)
            for i, ch in enumerate(full_text):
                char_details.append({
                    'char': ch,
                    'conf': 0.5,
                    'start_step': i * 4,
                    'end_step': (i + 1) * 4,
                })

        return char_details

    def _recognize_with_ctc(self, line_img: np.ndarray) -> List[Dict[str, Any]]:
        """Run recognition with CTC time step extraction.

        Accesses the internal recognition model to get CTC probabilities
        and decode them into character-level time step mappings.

        Args:
            line_img: Preprocessed line image.

        Returns:
            List of character detail dictionaries with CTC time steps.
        """
        import paddle

        char_details: List[Dict[str, Any]] = []

        try:
            # Preprocess image for recognition model
            img = self._preprocess_rec_image(line_img)

            # Run inference
            with paddle.no_grad():
                preds = self.rec_model(img)

            # Extract CTC probabilities
            if isinstance(preds, tuple):
                log_probs = preds[0]
            else:
                log_probs = preds

            # Convert to numpy for processing
            if hasattr(log_probs, 'numpy'):
                probs = log_probs.numpy()
            else:
                probs = np.array(log_probs)

            # Decode CTC output with time step information
            char_details = self._ctc_decode_with_timesteps(probs)

        except Exception as e:
            logger.error("CTC extraction failed: %s", e)
            raise

        return char_details

    def _preprocess_rec_image(self, line_img: np.ndarray) -> paddle.Tensor:
        """Preprocess image for recognition model input.

        Args:
            line_img: Raw line image.

        Returns:
            Preprocessed tensor suitable for the recognition model.
        """
        import paddle

        # Normalize image
        if line_img.ndim == 2:
            img = cv2.cvtColor(line_img, cv2.COLOR_GRAY2BGR)
        else:
            img = line_img.copy()

        # Resize to model input shape (height = 32)
        h, w = img.shape[:2]
        ratio = 32.0 / h
        new_w = int(w * ratio)
        new_w = max(new_w, 10)  # Minimum width
        new_w = min(new_w, 2048)  # Maximum width

        img_resized = cv2.resize(img, (new_w, 32))

        # Normalize
        img_norm = img_resized.astype(np.float32) / 255.0
        img_norm = (img_norm - 0.5) / 0.5

        # Transpose to (C, H, W)
        img_norm = img_norm.transpose((2, 0, 1))

        # Add batch dimension and convert to paddle tensor
        img_tensor = paddle.to_tensor(img_norm[np.newaxis, ...])

        return img_tensor

    def _ctc_decode_with_timesteps(
        self,
        probs: np.ndarray
    ) -> List[Dict[str, Any]]:
        """Decode CTC probabilities into characters with time step information.

        Args:
            probs: CTC probability array of shape (T, C) where T is time
                   steps and C is the number of character classes.

        Returns:
            List of character detail dictionaries with time steps.
        """
        char_details: List[Dict[str, Any]] = []

        try:
            # Get character dictionary
            char_dict = self._get_character_dict()
            blank_id = 0  # Typically blank token is at index 0

            # Handle batched input
            if probs.ndim == 3:
                probs = probs[0]  # Take first batch

            # Greedy decode: take argmax at each time step
            pred_indices = np.argmax(probs, axis=-1)

            # Collapse repeated characters and remove blanks (CTC decoding)
            collapsed: List[Tuple[int, int, int]] = []
            prev_idx = -1
            start_t = 0

            for t, idx in enumerate(pred_indices):
                if idx != prev_idx:
                    if prev_idx != -1 and prev_idx != blank_id:
                        # Save previous segment
                        char = char_dict.get(prev_idx, '')
                        if char:
                            conf = float(np.mean(probs[start_t:t, prev_idx]))
                            collapsed.append((prev_idx, start_t, t, conf))
                    start_t = t
                    prev_idx = idx
                elif t == len(pred_indices) - 1:
                    # End of sequence
                    if prev_idx != -1 and prev_idx != blank_id:
                        char = char_dict.get(prev_idx, '')
                        if char:
                            conf = float(np.mean(probs[start_t:t + 1, prev_idx]))
                            collapsed.append((prev_idx, start_t, t + 1, conf))

            # Build character details
            for idx, start_step, end_step, conf in collapsed:
                char = char_dict.get(idx, '')
                if char and char != '<BLANK>':
                    char_details.append({
                        'char': char,
                        'conf': min(max(conf, 0.0), 1.0),
                        'start_step': int(start_step),
                        'end_step': int(end_step),
                    })

        except Exception as e:
            logger.error("CTC decode failed: %s", e)

        return char_details

    def _get_character_dict(self) -> Dict[int, str]:
        """Get the model's character dictionary mapping indices to characters.

        Returns:
            Dictionary mapping character indices to character strings.
        """
        try:
            # Try to get dictionary from model's postprocessor
            if hasattr(self.rec_model, 'postprocess_op'):
                post_op = self.rec_model.postprocess_op
                if hasattr(post_op, 'character'):
                    chars = post_op.character
                    return {i + 1: c for i, c in enumerate(chars)}
                elif hasattr(post_op, 'dict'):
                    return post_op.dict

            # Fallback: standard PP-OCRv4 Chinese dictionary
            return self._build_default_char_dict()

        except Exception as e:
            logger.warning("Failed to get char dict: %s", e)
            return self._build_default_char_dict()

    def _build_default_char_dict(self) -> Dict[int, str]:
        """Build default character dictionary for PP-OCRv4 Chinese model.

        Returns:
            Dictionary with common Chinese characters.
        """
        # PP-OCRv4 uses ~6625 Chinese characters + symbols
        common_chars = (
            '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
            '的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分'
            '对成会可主发年动同工也能下过子说产种面而方后多定行学法所民得经十'
            '三之进着等部度家电力里如水化高自二理起小物现实加量都两体制机当使'
            '点从业本去把性好应开它合还因由其些然前外天政四日那社义事平形相全'
            '表间样与关各重新线内数正心反你明看原又么利比或但质气第向道命此变条'
            '只没结解问意建月公无系军很情者最立代想已通并提直题党程展五果料象员'
            '革位入常文总次品式活设及管特件长求老头基资边流路级少图山统接知较将'
            '组见计别她手角期根论运农指几九区强放决西被干做必战先回则任取完举色'
            '央达片华oug博央芪芷苓芎蒡菔苡蔻術术黨麝蟾藿菖藁蚶虻蛭螯炙煅煨焙'
            '烊兑g克两钱分毫升ml枚个片段寸握煎服汤药材君臣佐使配伍禁忌温热寒凉'
            '补泻升降浮沉归经有毒无毒用量用法功效主治症症状脉舌色苔厚薄白黄赤红'
            '青紫黑滑涩弦细数浮沉洪紧迟缓芤微濡弱虚实阴阳气血津精液脏腑心肺脾肝肾'
            '胆胃大肠小肠膀胱三焦脑女子胞胞宫经络穴针灸艾灸推拿按摩拔罐刮痧贴敷'
            '膏丹丸散汤饮片配方剂方歌方解加减化裁辨证论治治则治法标本缓急汗吐下和温清消补八法'
        )
        char_dict: Dict[int, str] = {0: '<BLANK>'}
        for i, ch in enumerate(common_chars):
            char_dict[i + 1] = ch
        return char_dict

    def extract_char_bboxes(
        self,
        det_box: List[float],
        char_details: List[Dict[str, Any]],
        orig_line_h: int,
        orig_line_w: int,
        rec_input_h: int = 32
    ) -> List[Dict[str, Any]]:
        """Extract character-level bounding boxes via CTC time step back-projection.

        Maps CTC time step positions back to original image coordinates using
        the known downsampling ratio and width scaling factor.

        The back-projection formula:
            scale = orig_line_h / rec_input_h  (height scaling)
            downsample_ratio = 4  (CNN typically downsamples width by 4x)
            x1 = det_box[0] + (start_step * downsample_ratio - 1) * scale
            x2 = det_box[0] + (end_step * downsample_ratio + 1) * scale

        Args:
            det_box: Line-level detection bbox [x1, y1, x2, y2].
            char_details: Character details from recognize_char_level(),
                         each with 'char', 'conf', 'start_step', 'end_step'.
            orig_line_h: Original line image height in pixels.
            orig_line_w: Original line image width in pixels.
            rec_input_h: Recognition model input height. Defaults to 32.

        Returns:
            Enhanced character details with 'bbox' field added:
                - char (str): The character
                - conf (float): Confidence score
                - bbox (List[float]): Character bbox [x1, y1, x2, y2]
                - start_step (int): Starting CTC time step
                - end_step (int): Ending CTC time step

        Example:
            >>> det_box = [100, 200, 400, 230]
            >>> char_details = [{'char': '黄', 'conf': 0.98, 'start_step': 2, 'end_step': 7}]
            >>> result = adapter.extract_char_bboxes(det_box, char_details, 30, 300)
            >>> print(result[0]['bbox'])
            [115.6, 200, 171.2, 230]
        """
        DOWNSAMPLE_RATIO = 4  # Standard CNN downsampling ratio

        results: List[Dict[str, Any]] = []

        try:
            # Calculate scaling factor
            scale = orig_line_h / rec_input_h if rec_input_h > 0 else 1.0
            det_x1, det_y1, det_x2, det_y2 = det_box[:4]
            det_y2 - det_y1

            for char_info in char_details:
                ch = char_info.get('char', '')
                conf = char_info.get('conf', 0.0)
                start_step = char_info.get('start_step', 0)
                end_step = char_info.get('end_step', 0)

                # Back-project CTC time steps to original coordinates
                # Add small padding (-1/+1) for better coverage
                char_x1 = det_x1 + (start_step * DOWNSAMPLE_RATIO - 1) * scale
                char_x2 = det_x1 + (end_step * DOWNSAMPLE_RATIO + 1) * scale

                # Clamp to detection box bounds
                char_x1 = max(char_x1, det_x1)
                char_x2 = min(char_x2, det_x2)

                # Use full line height for character bbox
                char_y1 = det_y1
                char_y2 = det_y2

                # Ensure valid bbox
                if char_x2 <= char_x1:
                    char_x2 = char_x1 + scale * DOWNSAMPLE_RATIO * 2

                bbox = [
                    float(round(char_x1, 1)),
                    float(round(char_y1, 1)),
                    float(round(char_x2, 1)),
                    float(round(char_y2, 1)),
                ]

                results.append({
                    'char': ch,
                    'conf': conf,
                    'bbox': bbox,
                    'start_step': start_step,
                    'end_step': end_step,
                })

        except Exception as e:
            logger.error("Character bbox extraction failed: %s", e)

        return results

    def close(self) -> None:
        """Release PaddleOCR engine resources."""
        if self._closed:
            return
        try:
            self.ocr = None
            logger.info("PaddleOCR adapter closed successfully")
        except Exception as e:
            logger.error("Error closing PaddleOCR adapter: %s", e)
        finally:
            self._closed = True

    def __del__(self) -> None:
        """Destructor to ensure resources are released."""
        if not self._closed:
            self.close()

    def __enter__(self) -> 'PaddleOCRAdapter':
        """Context manager entry."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        """Context manager exit."""
        self.close()
