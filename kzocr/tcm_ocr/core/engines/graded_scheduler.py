"""
Graded OCR Engine Scheduler for TCM OCR System.

Implements a multi-engine hierarchical scheduling strategy:
  1. MinerU (layout + recognition)
  2. PaddleOCR V6 (character-level recognition)
  3. Fallback engine (UniRec/Doctr/Tesseract)
  4. Vision-language engine (Paddle-VL/dots.ocr)

Features:
- Dynamic confidence thresholding with publisher/quality bonuses
- Fast consensus checking with confusable character pair handling
- Image quality estimation for adaptive processing
- Term knowledge base integration for contextual correction
"""

import logging
import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Set, Tuple

import cv2
import numpy as np

from kzocr.tcm_ocr.core.engines.mineru_adapter import MinerUAdapter
from kzocr.tcm_ocr.core.engines.paddleocr_adapter import PaddleOCRAdapter

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────

# Confusable character pairs common in TCM publications
CONFUSABLE_PAIRS: Set[Tuple[str, str]] = {
    ('术', '木'), ('芩', '苓'), ('己', '已'), ('已', '巳'),
    ('末', '未'), ('炎', '炙'), ('人', '入'), ('大', '太'),
    ('千', '干'), ('白', '日'), ('川', '州'), ('力', '刀'),
    ('上', '下'), ('土', '士'), ('王', '玉'), ('令', '今'),
    ('戊', '戌'), ('戊', '戍'), ('戌', '戍'), ('汩', '汨'),
    ('壸', '壶'), ('徵', '微'), ('薤', '藿'), ('羌', '姜'),
    ('附', '附'), ('杞', '妃'), ('萸', '英'), ('蒺', '藜'),
    ('柏', '拍'), ('黄柏', '黄拍'), ('苍术', '苍木'),
    ('白术', '白木'), ('茯苓', '茯芩'), ('黄芩', '黄苓'),
    ('炙甘草', '炎甘草'), ('吴茱萸', '吴英萸'),
}

# Publisher quality bonuses (higher = more reliable publisher)
PUBLISHER_BONUSES: Dict[str, float] = {
    '人民卫生出版社': 0.02,
    '中国中医药出版社': 0.02,
    '上海科学技术出版社': 0.015,
    '学苑出版社': 0.01,
    '中医古籍出版社': 0.005,
    '科学出版社': 0.015,
    'default': 0.0,
}

# Minimum quality threshold for fast consensus
MIN_QUALITY_THRESHOLD: float = 0.7

# Engine identifiers
ENGINE_MINERU = 'mineru'
ENGINE_PADDLEOCR = 'paddleocr'
ENGINE_FALLBACK_3 = 'engine3'  # UniRec/Doctr/Tesseract
ENGINE_VL = 'engine4'  # Paddle-VL/dots.ocr


class GradedScheduler:
    """Hierarchical multi-engine OCR scheduler.

    Implements a graded fallback strategy where engines are invoked
    progressively until consensus is reached or all engines are exhausted.

    Attributes:
        config (dict): Configuration dictionary with engine settings.
        mineru (MinerUAdapter): MinerU engine adapter.
        paddleocr (PaddleOCRAdapter): PaddleOCR engine adapter.
        engine3: Third fallback engine adapter (optional).
        engine4: Fourth VL engine adapter (optional).
        char_dict (dict): Character correction dictionary.
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        """Initialize the graded scheduler with all engines.

        Args:
            config: Configuration dictionary containing:
                - mineru_device (str): MinerU device, default 'cuda:0'
                - paddle_device (str): PaddleOCR device, default 'cpu'
                - enable_engine3 (bool): Enable third fallback engine
                - enable_engine4 (bool): Enable vision-language engine
                - engine3_type (str): Type of engine3 ('unirec', 'doctr', 'tesseract')
                - engine4_type (str): Type of engine4 ('paddlevl', 'dots')
                - char_dict (dict): Custom character dictionary
                - publisher (str): Publisher name for quality bonus

        Example:
            >>> config = {
            ...     'mineru_device': 'cuda:0',
            ...     'paddle_device': 'cpu',
            ...     'enable_engine3': True,
            ...     'enable_engine4': True,
            ...     'publisher': '人民卫生出版社',
            ... }
            >>> scheduler = GradedScheduler(config)
        """
        self.config: Dict[str, Any] = config
        self.char_dict: Dict[str, str] = config.get('char_dict', {})
        self.publisher: str = config.get('publisher', 'default')

        # Initialize primary engines
        logger.info("Initializing MinerU engine on %s", config.get('mineru_device', 'cuda:0'))
        self.mineru: MinerUAdapter = MinerUAdapter(
            device=config.get('mineru_device', 'cuda:0')
        )

        logger.info("Initializing PaddleOCR engine on %s", config.get('paddle_device', 'cpu'))
        self.paddleocr: PaddleOCRAdapter = PaddleOCRAdapter(
            char_dict=self.char_dict,
            device=config.get('paddle_device', 'cpu')
        )

        # Initialize fallback engines (optional)
        self.engine3: Any = None
        self.engine4: Any = None

        if config.get('enable_engine3', False):
            self.engine3 = self._init_engine3(config)

        if config.get('enable_engine4', False):
            self.engine4 = self._init_engine4(config)

        logger.info("GradedScheduler initialized with %d engines",
                    2 + (1 if self.engine3 else 0) + (1 if self.engine4 else 0))

    def _init_engine3(self, config: Dict[str, Any]) -> Any:
        """Initialize the third fallback engine.

        Args:
            config: Configuration dictionary.

        Returns:
            Engine3 adapter or None if initialization fails.
        """
        engine_type = config.get('engine3_type', 'tesseract')
        try:
            if engine_type == 'tesseract':
                import pytesseract
                return {'type': 'tesseract', 'engine': pytesseract}
            elif engine_type == 'doctr':
                from doctr.models import ocr_predictor
                return {
                    'type': 'doctr',
                    'engine': ocr_predictor(pretrained=True)
                }
            elif engine_type == 'unirec':
                # UniRec placeholder
                return {'type': 'unirec', 'engine': None}
            else:
                logger.warning("Unknown engine3 type: %s", engine_type)
                return None
        except Exception as e:
            logger.error("Failed to initialize engine3 (%s): %s", engine_type, e)
            return None

    def _init_engine4(self, config: Dict[str, Any]) -> Any:
        """Initialize the vision-language engine.

        Args:
            config: Configuration dictionary.

        Returns:
            Engine4 adapter or None if initialization fails.
        """
        engine_type = config.get('engine4_type', 'paddlevl')
        try:
            if engine_type == 'paddlevl':
                # Paddle-VL placeholder
                return {'type': 'paddlevl', 'engine': None}
            elif engine_type == 'dots':
                # dots.ocr placeholder
                return {'type': 'dots', 'engine': None}
            else:
                logger.warning("Unknown engine4 type: %s", engine_type)
                return None
        except Exception as e:
            logger.error("Failed to initialize engine4 (%s): %s", engine_type, e)
            return None

    def recognize_line(
        self,
        page_img: np.ndarray,
        line_bbox: List[float],
        term_kb: Any,
        book_meta: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Recognize a single text line using graded engine scheduling.

        Implements the full hierarchical recognition pipeline:
        1. Crop and enhance line image
        2. Run MinerU recognition
        3. Run PaddleOCR recognition with character-level details
        4. Check for fast consensus
        5. Apply dynamic quality-adjusted threshold
        6. Run dosage validation on agreed text
        7. If no consensus, progressively invoke fallback engines
        8. Return final result with all engine outputs and dispute markers

        Args:
            page_img: Full page image as numpy array.
            line_bbox: Bounding box [x1, y1, x2, y2] of the line.
            term_kb: Term knowledge base for contextual correction.
            book_meta: Optional book metadata dict with publisher, year, etc.

        Returns:
            Result dictionary containing:
                - final_text (str): Best recognized text
                - confidence (float): Overall confidence score
                - engine_results (dict): All engine outputs
                - consensus (bool): Whether consensus was reached
                - disputed (bool): Whether there are unresolved disputes
                - disputed_positions (list): Positions of disputes
                - char_level_details (list): Character-level recognition data
                - quality_score (float): Image quality score
                - dosage_alerts (list): Any dosage validation alerts
                - needs_review (bool): Whether human review is needed

        Example:
            >>> result = scheduler.recognize_line(page_img, [100, 200, 500, 230], term_kb)
            >>> print(result['final_text'])
            '黄芪 15g 水煎服'
        """
        book_meta = book_meta or {}
        engine_results: Dict[str, Any] = {}
        all_texts: Dict[str, str] = {}

        # ── Step 1: Crop line image and apply enhancement ──
        line_img = self._crop_line(page_img, line_bbox)
        line_img = self._enhance_line_image(line_img)

        # Estimate image quality
        quality_score = estimate_image_quality(line_img)
        logger.debug("Line image quality score: %.3f", quality_score)

        # ── Step 2: MinerU recognition ──
        try:
            r1 = self.mineru.recognize(line_img)
            engine_results[ENGINE_MINERU] = r1
            all_texts[ENGINE_MINERU] = r1
            logger.debug("MinerU result: '%s'", r1)
        except Exception as e:
            logger.error("MinerU recognition failed: %s", e)
            r1 = ""
            engine_results[ENGINE_MINERU] = ""
            all_texts[ENGINE_MINERU] = ""

        # ── Step 3: PaddleOCR recognition ──
        try:
            r2 = self.paddleocr.recognize(line_img)
            r2_char_level = self.paddleocr.recognize_char_level(line_img)
            engine_results[ENGINE_PADDLEOCR] = {
                'text': r2,
                'char_level': r2_char_level,
            }
            all_texts[ENGINE_PADDLEOCR] = r2
            logger.debug("PaddleOCR result: '%s'", r2)
        except Exception as e:
            logger.error("PaddleOCR recognition failed: %s", e)
            r2 = ""
            r2_char_level = []
            engine_results[ENGINE_PADDLEOCR] = {'text': '', 'char_level': []}
            all_texts[ENGINE_PADDLEOCR] = ""

        # ── Step 4: Fast consensus check ──
        if r1 and r2:
            consensus_result = fast_consensus_check(r1, r2, term_kb)
        elif r1 or r2:
            # Single engine result
            consensus_result = {
                'final_text': r1 or r2,
                'confidence': 0.7,
                'has_term_conflict': False,
                'method': 'single_engine',
            }
        else:
            consensus_result = {
                'final_text': '',
                'confidence': 0.0,
                'has_term_conflict': False,
                'method': 'no_result',
            }

        # ── Step 5: Dynamic threshold calculation ──
        publisher_bonus = PUBLISHER_BONUSES.get(self.publisher, PUBLISHER_BONUSES['default'])
        quality_bonus = max(0, (quality_score - 0.8) * 0.02)  # Up to +0.004
        dynamic_threshold = 0.95 + publisher_bonus + quality_bonus
        dynamic_threshold = min(dynamic_threshold, 0.99)

        logger.debug("Dynamic threshold: %.3f (base=0.95, pub_bonus=%.3f, qual_bonus=%.3f)",
                    dynamic_threshold, publisher_bonus, quality_bonus)

        # ── Step 6: Check if consensus meets threshold ──
        if consensus_result.get('confidence', 0) >= dynamic_threshold and not consensus_result.get('has_term_conflict', False):
            final_text = consensus_result['final_text']

            # Run dosage scan
            from kzocr.tcm_ocr.core.consensus.dosage_validator import validate_dosages
            dosage_alerts = validate_dosages(final_text, stage='post_ocr', pub_year=book_meta.get('year'))

            return {
                'final_text': final_text,
                'confidence': consensus_result['confidence'],
                'engine_results': engine_results,
                'consensus': True,
                'disputed': False,
                'disputed_positions': [],
                'char_level_details': r2_char_level,
                'quality_score': quality_score,
                'dosage_alerts': dosage_alerts,
                'needs_review': len(dosage_alerts) > 0,
                'method': 'fast_consensus',
            }

        # ── Step 7: No consensus - invoke fallback engine 3 ──
        r3 = ""
        if self.engine3 is not None:
            try:
                r3 = self._run_engine3(line_img)
                engine_results[ENGINE_FALLBACK_3] = r3
                all_texts[ENGINE_FALLBACK_3] = r3
                logger.debug("Engine3 result: '%s'", r3)

                # Check if engine3 agrees with either primary engine
                if r3:
                    c13 = fast_consensus_check(r1, r3, term_kb) if r1 else None
                    c23 = fast_consensus_check(r2, r3, term_kb) if r2 else None

                    if c13 and c13.get('confidence', 0) >= dynamic_threshold:
                        consensus_result = c13
                        consensus_result['method'] = 'engine3_agrees_with_mineru'
                    elif c23 and c23.get('confidence', 0) >= dynamic_threshold:
                        consensus_result = c23
                        consensus_result['method'] = 'engine3_agrees_with_paddle'
            except Exception as e:
                logger.error("Engine3 failed: %s", e)

        # ── Step 8: Still no consensus - invoke engine 4 (VL) ──
        r4 = ""
        if self.engine4 is not None:
            try:
                r4 = self._run_engine4(line_img)
                engine_results[ENGINE_VL] = r4
                all_texts[ENGINE_VL] = r4
                logger.debug("Engine4 result: '%s'", r4)

                # Check if engine4 resolves the dispute
                if r4:
                    for prev_engine in [ENGINE_MINERU, ENGINE_PADDLEOCR, ENGINE_FALLBACK_3]:
                        prev_text = all_texts.get(prev_engine, '')
                        if prev_text:
                            c4 = fast_consensus_check(prev_text, r4, term_kb)
                            if c4.get('confidence', 0) >= dynamic_threshold:
                                consensus_result = c4
                                consensus_result['method'] = f'engine4_agrees_with_{prev_engine}'
                                break
            except Exception as e:
                logger.error("Engine4 failed: %s", e)

        # ── Final: Multi-engine consensus or dispute marking ──
        from kzocr.tcm_ocr.core.consensus.line_consensus import line_consensus

        valid_results = {k: v for k, v in all_texts.items() if v}
        final_result = line_consensus(valid_results, term_kb, book_meta)

        # Merge with scheduler-specific fields
        final_result.update({
            'engine_results': engine_results,
            'char_level_details': r2_char_level,
            'quality_score': quality_score,
            'dynamic_threshold': dynamic_threshold,
            'engines_used': list(valid_results.keys()),
        })

        # Run dosage validation on final text
        if final_result.get('final_text'):
            from kzocr.tcm_ocr.core.consensus.dosage_validator import validate_dosages
            dosage_alerts = validate_dosages(
                final_result['final_text'],
                stage='post_ocr',
                pub_year=book_meta.get('year')
            )
            final_result['dosage_alerts'] = dosage_alerts
            final_result['needs_review'] = (
                final_result.get('needs_review', False) or len(dosage_alerts) > 0
            )

        logger.info("Graded scheduling complete: method=%s, disputed=%s, confidence=%.3f",
                    final_result.get('method', 'unknown'),
                    final_result.get('disputed', True),
                    final_result.get('confidence', 0.0))

        return final_result

    def _crop_line(
        self,
        page_img: np.ndarray,
        line_bbox: List[float]
    ) -> np.ndarray:
        """Crop a line region from the page image.

        Args:
            page_img: Full page image.
            line_bbox: Bounding box [x1, y1, x2, y2].

        Returns:
            Cropped line image.
        """
        h, w = page_img.shape[:2]
        x1 = max(0, int(line_bbox[0]))
        y1 = max(0, int(line_bbox[1]))
        x2 = min(w, int(line_bbox[2]))
        y2 = min(h, int(line_bbox[3]))

        # Add small padding
        pad = 2
        y1 = max(0, y1 - pad)
        y2 = min(h, y2 + pad)
        x1 = max(0, x1 - pad)
        x2 = min(w, x2 + pad)

        if y2 <= y1 or x2 <= x1:
            return np.zeros((32, 100, 3), dtype=np.uint8)

        return page_img[y1:y2, x1:x2]

    def _enhance_line_image(self, line_img: np.ndarray) -> np.ndarray:
        """Apply image enhancement for better recognition.

        Args:
            line_img: Raw cropped line image.

        Returns:
            Enhanced line image.
        """
        if line_img is None or line_img.size == 0:
            return np.zeros((32, 100, 3), dtype=np.uint8)

        # Ensure 3-channel
        if line_img.ndim == 2:
            line_img = cv2.cvtColor(line_img, cv2.COLOR_GRAY2BGR)
        elif line_img.shape[2] == 4:
            line_img = cv2.cvtColor(line_img, cv2.COLOR_RGBA2BGR)

        # Mild denoising
        line_img = cv2.fastNlMeansDenoisingColored(line_img, None, 5, 5, 7, 15)

        # Adaptive contrast enhancement
        lab = cv2.cvtColor(line_img, cv2.COLOR_BGR2LAB)
        l_channel, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        l_channel = clahe.apply(l_channel)
        lab = cv2.merge([l_channel, a, b])
        line_img = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

        return line_img

    def _run_engine3(self, line_img: np.ndarray) -> str:
        """Run the third fallback engine.

        Args:
            line_img: Line image to recognize.

        Returns:
            Recognized text string.
        """
        if self.engine3 is None:
            return ""

        engine_type = self.engine3.get('type', '')
        engine = self.engine3.get('engine')

        try:
            if engine_type == 'tesseract':
                import pytesseract
                if line_img.ndim == 3:
                    rgb = cv2.cvtColor(line_img, cv2.COLOR_BGR2RGB)
                else:
                    rgb = cv2.cvtColor(line_img, cv2.COLOR_GRAY2RGB)
                text = pytesseract.image_to_string(rgb, lang='chi_sim+eng')
                return text.strip()

            elif engine_type == 'doctr' and engine is not None:
                if line_img.ndim == 2:
                    line_img = cv2.cvtColor(line_img, cv2.COLOR_GRAY2RGB)
                elif line_img.shape[2] == 3:
                    line_img = cv2.cvtColor(line_img, cv2.COLOR_BGR2RGB)
                result = engine([line_img])
                text = result.render()
                return text.strip()

            elif engine_type == 'unirec':
                # UniRec stub
                return ""

        except Exception as e:
            logger.error("Engine3 recognition failed: %s", e)

        return ""

    def _run_engine4(self, line_img: np.ndarray) -> str:
        """Run the vision-language engine.

        Args:
            line_img: Line image to recognize.

        Returns:
            Recognized text string.
        """
        if self.engine4 is None:
            return ""

        engine_type = self.engine4.get('type', '')

        try:
            if engine_type == 'paddlevl':
                # Paddle-VL stub - would call the actual model
                logger.debug("Paddle-VL not yet implemented")
                return ""
            elif engine_type == 'dots':
                # dots.ocr stub
                logger.debug("dots.ocr not yet implemented")
                return ""
        except Exception as e:
            logger.error("Engine4 recognition failed: %s", e)

        return ""

    def close(self) -> None:
        """Release all engine resources."""
        try:
            self.mineru.close()
            self.paddleocr.close()
            self.engine3 = None
            self.engine4 = None
            logger.info("GradedScheduler closed all engines")
        except Exception as e:
            logger.error("Error closing GradedScheduler: %s", e)

    def __enter__(self) -> 'GradedScheduler':
        """Context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit."""
        self.close()


# ── Helper Functions ─────────────────────────────────────────────────────


def has_glyph_dispute_at_confusable(text_a: str, text_b: str) -> bool:
    """Check if two texts differ only at confusable character pair positions.

    This helps determine if a disagreement is due to known OCR confusions
    rather than genuine recognition errors.

    Args:
        text_a: First engine's recognized text.
        text_b: Second engine's recognized text.

    Returns:
        True if all differences are at confusable character positions.

    Example:
        >>> has_glyph_dispute_at_confusable('黄芩', '黄苓')
        True  # '芩' vs '苓' is a known confusable pair
        >>> has_glyph_dispute_at_confusable('黄芪', '黄氏')
        False  # '芪' vs '氏' is not a known confusable pair
    """
    if text_a == text_b:
        return False

    # Find differing positions
    max_len = max(len(text_a), len(text_b))
    for i in range(max_len):
        ch_a = text_a[i] if i < len(text_a) else ''
        ch_b = text_b[i] if i < len(text_b) else ''

        if ch_a != ch_b:
            # Check if this pair is in the confusable set
            pair = (ch_a, ch_b)
            reverse_pair = (ch_b, ch_a)
            if pair not in CONFUSABLE_PAIRS and reverse_pair not in CONFUSABLE_PAIRS:
                return False

    return True


def fast_consensus_check(
    text_a: str,
    text_b: str,
    term_kb: Any
) -> Dict[str, Any]:
    """Perform fast consensus check between two engine results.

    Compares two OCR results and determines if they agree with sufficient
    confidence. Handles confusable character pairs and checks against the
    term knowledge base.

    Args:
        text_a: First engine's recognized text.
        text_b: Second engine's recognized text.
        term_kb: Term knowledge base for conflict detection.

    Returns:
        Dictionary with:
            - final_text (str): Consensus text or best guess
            - confidence (float): Consensus confidence score
            - has_term_conflict (bool): Whether term knowledge base conflicts
            - method (str): How consensus was reached

    Example:
        >>> result = fast_consensus_check('黄芪15g', '黄芪 15g', term_kb)
        >>> print(result['confidence'])
        0.98
    """
    result: Dict[str, Any] = {
        'final_text': '',
        'confidence': 0.0,
        'has_term_conflict': False,
        'method': 'none',
    }

    # Normalize texts for comparison
    norm_a = _normalize_text(text_a)
    norm_b = _normalize_text(text_b)

    # Exact match
    if norm_a == norm_b:
        result['final_text'] = text_a
        result['confidence'] = 0.99
        result['method'] = 'exact_match'
        # Still check for term conflicts
        if term_kb and hasattr(term_kb, 'has_conflict'):
            result['has_term_conflict'] = term_kb.has_conflict(norm_a)
        return result

    # Check if differences are only at confusable positions
    if has_glyph_dispute_at_confusable(norm_a, norm_b):
        # Prefer the text that matches known terms better
        if term_kb and hasattr(term_kb, 'score_text'):
            score_a = term_kb.score_text(norm_a)
            score_b = term_kb.score_text(norm_b)
            if score_a >= score_b:
                result['final_text'] = text_a
                result['confidence'] = 0.92
            else:
                result['final_text'] = text_b
                result['confidence'] = 0.92
        else:
            result['final_text'] = text_a  # Default to first engine
            result['confidence'] = 0.92
        result['method'] = 'confusable_resolved'
        return result

    # Calculate sequence similarity
    similarity = SequenceMatcher(None, norm_a, norm_b).ratio()

    # High similarity - merge results
    if similarity >= 0.85:
        merged = _merge_texts(text_a, text_b)
        result['final_text'] = merged
        result['confidence'] = similarity
        result['method'] = 'high_similarity_merged'

        # Check term conflicts
        if term_kb and hasattr(term_kb, 'has_conflict'):
            result['has_term_conflict'] = term_kb.has_conflict(merged)

        return result

    # Low similarity - potential genuine disagreement
    result['confidence'] = similarity
    result['method'] = 'low_similarity'

    # Still prefer the longer text if it contains more known terms
    if term_kb and hasattr(term_kb, 'score_text'):
        score_a = term_kb.score_text(norm_a)
        score_b = term_kb.score_text(norm_b)
        if score_a > score_b:
            result['final_text'] = text_a
        else:
            result['final_text'] = text_b
    else:
        # Default: use text with higher character count
        result['final_text'] = text_a if len(text_a) >= len(text_b) else text_b

    return result


def estimate_image_quality(line_img: np.ndarray) -> float:
    """Estimate the quality of a line image for OCR.

    Evaluates multiple quality factors:
    - Resolution adequacy
    - Sharpness (Laplacian variance)
    - Contrast
    - Noise level

    Args:
        line_img: Line image as numpy array.

    Returns:
        Quality score between 0.0 (poor) and 1.0 (excellent).

    Example:
        >>> score = estimate_image_quality(line_img)
        >>> print(f"Quality: {score:.2f}")
        'Quality: 0.85'
    """
    if line_img is None or line_img.size == 0:
        return 0.0

    # Convert to grayscale for analysis
    if line_img.ndim == 3:
        gray = cv2.cvtColor(line_img, cv2.COLOR_BGR2GRAY)
    else:
        gray = line_img.copy()

    scores: List[float] = []

    # 1. Resolution score (height >= 20px is good)
    h, w = gray.shape[:2]
    resolution_score = min(h / 32.0, 1.0)  # 32px = ideal height
    scores.append(resolution_score)

    # 2. Sharpness score (Laplacian variance)
    laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
    sharpness_score = min(laplacian_var / 500.0, 1.0)
    scores.append(sharpness_score)

    # 3. Contrast score
    contrast = gray.std()
    contrast_score = min(contrast / 60.0, 1.0)
    scores.append(contrast_score)

    # 4. Noise score (inverse of noise level)
    noise_level = estimate_noise(gray)
    noise_score = max(0.0, 1.0 - noise_level / 30.0)
    scores.append(noise_score)

    # Weighted average
    weights = [0.25, 0.30, 0.25, 0.20]
    final_score = sum(s * w for s, w in zip(scores, weights))
    return float(round(min(max(final_score, 0.0), 1.0), 3))


def estimate_noise(gray: np.ndarray) -> float:
    """Estimate the noise level in a grayscale image.

    Uses the median absolute deviation of high-frequency components
    as a robust noise estimator.

    Args:
        gray: Grayscale image as numpy array.

    Returns:
        Estimated noise standard deviation.

    Example:
        >>> noise = estimate_noise(gray_img)
        >>> print(f"Noise level: {noise:.1f}")
        'Noise level: 8.5'
    """
    try:
        # Use median absolute deviation on Laplacian response
        laplacian = cv2.Laplacian(gray, cv2.CV_64F)
        median_abs_dev = np.median(np.abs(laplacian - np.median(laplacian)))
        # Convert MAD to std: std ≈ 1.4826 * MAD
        noise_std = 1.4826 * median_abs_dev
        return float(noise_std)
    except Exception:
        # Fallback: use simple standard deviation of difference image
        try:
            diff = cv2.absdiff(gray.astype(np.int16)[:, 1:], gray.astype(np.int16)[:, :-1])
            return float(np.std(diff))
        except Exception:
            return 15.0  # Default moderate noise estimate


def _normalize_text(text: str) -> str:
    """Normalize text for comparison.

    Removes extra whitespace and standardizes punctuation.

    Args:
        text: Raw text string.

    Returns:
        Normalized text string.
    """
    text = str(text).strip()
    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text)
    # Standardize common punctuation variants
    text = text.replace('．', '.').replace('，', ',').replace('：', ':')
    return text


def _merge_texts(text_a: str, text_b: str) -> str:
    """Merge two similar texts by taking the best parts of each.

    Uses sequence alignment to merge texts, preferring characters
    that appear in both or are from the more confident source.

    Args:
        text_a: First text.
        text_b: Second text.

    Returns:
        Merged text.
    """
    if text_a == text_b:
        return text_a

    sm = SequenceMatcher(None, text_a, text_b)
    merged_parts: List[str] = []

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            merged_parts.append(text_a[i1:i2])
        elif tag == 'replace':
            # Choose the longer segment or prefer text_a
            seg_a = text_a[i1:i2]
            seg_b = text_b[j1:j2]
            merged_parts.append(seg_a if len(seg_a) >= len(seg_b) else seg_b)
        elif tag == 'delete':
            merged_parts.append(text_a[i1:i2])
        elif tag == 'insert':
            merged_parts.append(text_b[j1:j2])

    return ''.join(merged_parts)
