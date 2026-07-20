"""
Missing Character Detection Module for TCM OCR System.

Detects characters that may have been missed during OCR by analyzing:
1. Gaps between character-level bounding boxes
2. Gap width relative to average character width
3. Presence of ink (connected components) in gap regions

This helps identify cases where the OCR engine failed to recognize
a character that is visually present in the image.
"""

import logging
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Gap-to-width ratio threshold for suspected missing character
MISSING_CHAR_GAP_RATIO = 1.3

# Minimum gap width in pixels to consider
MIN_GAP_WIDTH = 5

# Minimum connected component area to consider as ink
MIN_INK_AREA = 50

# Binary threshold for ink detection
BINARY_THRESHOLD = 180


def detect_missing_chars(
    line_record: Dict[str, Any],
    para_img: np.ndarray
) -> List[Dict[str, Any]]:
    """Detect potentially missing characters in a line.

    Analyzes the gaps between character-level bounding boxes. When a gap
    is significantly larger than the average character width (1.3x),
    and the gap region contains ink (dark pixels forming connected
    components), it indicates a likely missed character.

    Algorithm:
    1. Extract character bboxes from line_record
    2. Calculate average character width
    3. For each gap between consecutive characters:
       a. Compute gap width
       b. If gap > avg_width * 1.3, flag as suspicious
       c. Extract gap region from paragraph image
       d. Binarize and check for connected components (ink)
       e. If ink found with area >= 50px, confirm missing character
    4. Return list of detected missing character positions

    Args:
        line_record: Line recognition record containing:
            - char_level_details: List of character dicts with 'bbox'
            - text: Recognized text string
            - line_bbox: Line-level bounding box [x1, y1, x2, y2]
        para_img: Original paragraph/page image as numpy array.

    Returns:
        List of missing character detection dictionaries:
            - pos (int): Character position index (insertion point)
            - type (str): 'missing_char'
            - gap_width (float): Width of the gap in pixels
            - avg_char_width (float): Average character width
            - gap_ratio (float): Gap width / average char width
            - confidence (float): Detection confidence (0-1)
            - gap_bbox (List[float]): Bounding box of the gap region
            - ink_area (float): Area of detected ink in the gap

    Example:
        >>> line_rec = {
        ...     'text': '黄芪水煎服',
        ...     'char_level_details': [
        ...         {'char': '黄', 'bbox': [100, 200, 130, 230]},
        ...         {'char': '芪', 'bbox': [130, 200, 160, 230]},
        ...         # Large gap here - '水' might be missing
        ...         {'char': '煎', 'bbox': [210, 200, 240, 230]},
        ...         {'char': '服', 'bbox': [240, 200, 270, 230]},
        ...     ],
        ...     'line_bbox': [100, 200, 270, 230],
        ... }
        >>> missing = detect_missing_chars(line_rec, page_image)
        >>> print(missing)
        [{'pos': 2, 'type': 'missing_char', 'gap_width': 50.0, ...}]
    """
    detections: List[Dict[str, Any]] = []

    # Extract character bboxes
    char_details = line_record.get('char_level_details', [])
    if not char_details:
        logger.debug("No character-level details available for missing char detection")
        return detections

    # Sort characters by x-position (left to right)
    sorted_chars = sorted(char_details, key=lambda c: c.get('bbox', [0, 0, 0, 0])[0])

    # Filter out characters without valid bboxes
    valid_chars = [
        c for c in sorted_chars
        if len(c.get('bbox', [])) >= 4 and c['bbox'][2] > c['bbox'][0]
    ]

    if len(valid_chars) < 2:
        logger.debug("Not enough character bboxes for gap analysis")
        return detections

    # Calculate average character width
    char_widths = [c['bbox'][2] - c['bbox'][0] for c in valid_chars]
    avg_width = sum(char_widths) / len(char_widths) if char_widths else 20.0
    std_width = np.std(char_widths) if len(char_widths) > 1 else 0.0

    logger.debug("Average char width: %.1fpx, std: %.1fpx", avg_width, std_width)

    # Analyze gaps between consecutive characters
    for i in range(len(valid_chars) - 1):
        curr_char = valid_chars[i]
        next_char = valid_chars[i + 1]

        curr_bbox = curr_char['bbox']
        next_bbox = next_char['bbox']

        # Calculate gap between current char end and next char start
        gap_left = curr_bbox[2]  # x2 of current
        gap_right = next_bbox[0]  # x1 of next
        gap_width = gap_right - gap_left

        # Skip small gaps
        if gap_width < MIN_GAP_WIDTH:
            continue

        # Check if gap is suspiciously large
        gap_ratio = gap_width / avg_width if avg_width > 0 else 0

        if gap_ratio < MISSING_CHAR_GAP_RATIO:
            continue  # Gap within normal range

        # Suspicious gap - check for ink in the gap region
        gap_top = min(curr_bbox[1], next_bbox[1])
        gap_bottom = max(curr_bbox[3], next_bbox[3])

        # Add small margin to gap bbox
        margin = 2
        gap_bbox = [
            gap_left - margin,
            gap_top - margin,
            gap_right + margin,
            gap_bottom + margin,
        ]

        # Extract gap region from image
        h, w = para_img.shape[:2]
        x1 = max(0, int(gap_bbox[0]))
        y1 = max(0, int(gap_bbox[1]))
        x2 = min(w, int(gap_bbox[2]))
        y2 = min(h, int(gap_bbox[3]))

        if x2 <= x1 or y2 <= y1:
            continue

        gap_region = para_img[y1:y2, x1:x2]

        if gap_region.size == 0:
            continue

        # Check for ink in the gap region
        has_ink_result = has_ink(gap_region, min_area=MIN_INK_AREA)
        ink_area = _calculate_ink_area(gap_region)

        # Calculate confidence based on gap ratio and ink presence
        if has_ink_result:
            # Strong signal: large gap + ink present
            confidence = min(0.5 + (gap_ratio - MISSING_CHAR_GAP_RATIO) * 0.3, 0.95)
            confidence = max(confidence, 0.7)
        else:
            # Weak signal: large gap but no visible ink
            confidence = max(0.3, (gap_ratio - MISSING_CHAR_GAP_RATIO) * 0.2)

        detections.append({
            'pos': i + 1,  # Insertion point after character i
            'type': 'missing_char',
            'gap_width': float(round(gap_width, 1)),
            'avg_char_width': float(round(avg_width, 1)),
            'gap_ratio': float(round(gap_ratio, 2)),
            'confidence': float(round(confidence, 3)),
            'gap_bbox': [float(round(v, 1)) for v in gap_bbox],
            'ink_area': float(round(ink_area, 1)),
            'has_ink': has_ink_result,
            'prev_char': curr_char.get('char', ''),
            'next_char': next_char.get('char', ''),
        })

        logger.debug(
            "Missing char suspected at pos %d: gap=%.1fpx, ratio=%.2f, "
            "ink=%s, area=%.1f, conf=%.2f",
            i + 1, gap_width, gap_ratio, has_ink_result, ink_area, confidence
        )

    return detections


def has_ink(img: np.ndarray, min_area: int = 50) -> bool:
    """Check if the image region contains ink (dark pixels).

    Converts the image to grayscale, applies thresholding to isolate
    dark regions, and checks if any connected component meets the
    minimum area requirement.

    Args:
        img: Image region as numpy array (H, W) or (H, W, C).
        min_area: Minimum connected component area to consider as ink.
                  Defaults to 50 pixels.

    Returns:
        True if ink (dark connected components) is detected.

    Example:
        >>> gap_region = para_img[200:230, 160:210]
        >>> if has_ink(gap_region, min_area=50):
        ...     print("Ink detected - likely missed character")
    """
    if img is None or img.size == 0:
        return False

    try:
        # Convert to grayscale if needed
        if img.ndim == 3:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        else:
            gray = img.copy()

        # Apply binary thresholding (dark pixels = text/ink)
        # Invert so text becomes white (255) on black (0)
        _, binary = cv2.threshold(gray, BINARY_THRESHOLD, 255, cv2.THRESH_BINARY_INV)

        # Find connected components
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
            binary, connectivity=8
        )

        # Check each component (skip background at index 0)
        for i in range(1, num_labels):
            area = stats[i, cv2.CC_STAT_AREA]
            if area >= min_area:
                return True

        return False

    except Exception as e:
        logger.error("Ink detection failed: %s", e)
        return False


def _calculate_ink_area(img: np.ndarray) -> float:
    """Calculate the total area of ink in an image region.

    Args:
        img: Image region as numpy array.

    Returns:
        Total ink area in pixels.
    """
    if img is None or img.size == 0:
        return 0.0

    try:
        if img.ndim == 3:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        else:
            gray = img.copy()

        _, binary = cv2.threshold(gray, BINARY_THRESHOLD, 255, cv2.THRESH_BINARY_INV)

        # Sum of all ink pixels
        ink_pixels = np.sum(binary > 0)
        return float(ink_pixels)

    except Exception:
        return 0.0


def detect_missing_chars_in_text(
    text: str,
    char_details: List[Dict[str, Any]],
    img: np.ndarray,
    term_kb: Optional[object] = None
) -> List[Dict[str, Any]]:
    """High-level missing character detection with term KB integration.

    Combines gap-based detection with term knowledge base to improve
    detection accuracy.

    Args:
        text: Recognized text.
        char_details: Character-level details with bboxes.
        img: Source image.
        term_kb: Optional term knowledge base.

    Returns:
        List of missing character detections.
    """
    line_record = {
        'text': text,
        'char_level_details': char_details,
    }

    detections = detect_missing_chars(line_record, img)

    # Enhance with term KB if available
    if term_kb and hasattr(term_kb, 'suggest_missing_char'):
        for det in detections:
            pos = det['pos']
            context = _get_context_at_position(text, pos)
            suggestion = term_kb.suggest_missing_char(context)
            if suggestion:
                det['suggested_char'] = suggestion

    return detections


def _get_context_at_position(text: str, pos: int, window: int = 3) -> str:
    """Get text context around a position.

    Args:
        text: Full text.
        pos: Position index.
        window: Context window size.

    Returns:
        Context string.
    """
    start = max(0, pos - window)
    end = min(len(text), pos + window)
    return text[start:end]
