"""
Extra Character (Adhesion) Detection Module for TCM OCR System.

Detects characters that may be merged/adhered together during OCR,
resulting in a single wide character bbox containing multiple characters.

Uses a V2 algorithm with 1D K-means clustering:
1. Extract character widths from recognition results
2. Use 1D K-means (k=2) to separate normal and wide characters
3. Strong signal (width > normal_cluster_center * 1.8) → direct adhesion
4. Weak signal (1.3-1.8x) → connected component verification
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Thresholds for adhesion detection
STRONG_ADHESION_RATIO = 1.8  # Directly flag as adhesion
WEAK_ADHESION_RATIO = 1.3    # Needs connected component verification

# Binary threshold for connected component analysis
BINARY_THRESHOLD = 180

# Minimum connected component area
MIN_COMPONENT_AREA = 30


def detect_extra_chars(
    line_record: Dict[str, Any],
    para_img: np.ndarray
) -> List[Dict[str, Any]]:
    """Detect potentially adhered (extra/merged) characters in a line.

    V2 Algorithm:
    1. Extract character widths from char_level_details
    2. Apply 1D K-means clustering (k=2) to find normal vs. wide clusters
    3. Classify each character:
       - Width > normal_center * 1.8 → Strong adhesion signal
       - Width in [1.3, 1.8] * normal_center → Weak signal, needs verification
       - Width < 1.3 * normal_center → Normal
    4. For weak signals, verify by counting connected components in char bbox
    5. Return detections with confidence scores

    Args:
        line_record: Line recognition record containing:
            - char_level_details: List of character dicts with 'bbox'
            - text: Recognized text string
            - line_bbox: Line-level bounding box [x1, y1, x2, y2]
        para_img: Original paragraph/page image as numpy array.

    Returns:
        List of extra character detection dictionaries:
            - pos (int): Character position index
            - type (str): 'adhesion' or 'suspected_adhesion'
            - confidence (float): Detection confidence (0-1)
            - width_ratio (float): Character width / normal width
            - char (str): The recognized character(s) at this position
            - bbox (List[float]): Character bounding box
            - normal_width (float): Cluster center for normal widths
            - wide_width (float): Cluster center for wide widths
            - component_count (int): Number of connected components found
            - reason (str): Human-readable detection reason

    Example:
        >>> line_rec = {
        ...     'text': '黄芪15g',
        ...     'char_level_details': [
        ...         {'char': '黄', 'bbox': [100, 200, 130, 230]},  # width=30
        ...         {'char': '芪', 'bbox': [130, 200, 160, 230]},  # width=30
        ...         {'char': '1', 'bbox': [160, 200, 175, 230]},  # width=15
        ...         {'char': '5', 'bbox': [175, 200, 190, 230]},  # width=15
        ...         {'char': 'g', 'bbox': [190, 200, 205, 230]},  # width=15
        ...     ],
        ... }
        >>> # If one bbox is 60px wide, it would be flagged as adhesion
        >>> extra = detect_extra_chars(line_rec, page_image)
        >>> print(extra)
        [{'pos': 0, 'type': 'adhesion', 'width_ratio': 2.0, ...}]
    """
    detections: List[Dict[str, Any]] = []

    # Extract character bboxes and widths
    char_details = line_record.get('char_level_details', [])
    if not char_details:
        logger.debug("No character-level details available for extra char detection")
        return detections

    # Filter out characters without valid bboxes
    valid_chars = [
        c for c in char_details
        if len(c.get('bbox', [])) >= 4 and c['bbox'][2] > c['bbox'][0]
    ]

    if len(valid_chars) < 3:
        logger.debug("Not enough character bboxes for clustering")
        return detections

    # Calculate character widths
    widths = []
    for c in valid_chars:
        bbox = c['bbox']
        w = bbox[2] - bbox[0]
        widths.append(w)

    if not widths:
        return detections

    # 1D K-means clustering to separate normal and wide characters
    normal_cluster, wide_cluster = cluster_widths(widths, k=2)

    # Determine which cluster is "normal" (smaller center)
    if normal_cluster:
        normal_center = sum(normal_cluster) / len(normal_cluster)
    else:
        normal_center = np.median(widths) if widths else 20.0

    if wide_cluster:
        wide_center = sum(wide_cluster) / len(wide_cluster)
    else:
        wide_center = normal_center * 2

    logger.debug(
        "Width clustering: normal_center=%.1f (n=%d), wide_center=%.1f (n=%d)",
        normal_center, len(normal_cluster), wide_center, len(wide_cluster)
    )

    # Analyze each character
    for i, char_info in enumerate(valid_chars):
        char_width = widths[i]
        width_ratio = char_width / normal_center if normal_center > 0 else 1.0

        # Skip if clearly normal
        if width_ratio < WEAK_ADHESION_RATIO:
            continue

        bbox = char_info['bbox']
        char_text = char_info.get('char', '')

        # Strong signal: width > 1.8x normal
        if width_ratio >= STRONG_ADHESION_RATIO:
            # Count connected components for additional evidence
            component_count = _count_components_in_bbox(bbox, para_img)

            confidence = min(0.5 + (width_ratio - STRONG_ADHESION_RATIO) * 0.2, 0.95)
            confidence = max(confidence, 0.75)

            detections.append({
                'pos': i,
                'type': 'adhesion',
                'confidence': float(round(confidence, 3)),
                'width_ratio': float(round(width_ratio, 2)),
                'char': char_text,
                'bbox': [float(v) for v in bbox],
                'normal_width': float(round(normal_center, 1)),
                'wide_width': float(round(wide_center, 1)),
                'component_count': component_count,
                'reason': (
                    f"字符宽度({char_width:.1f}px)为正常宽度({normal_center:.1f}px)的"
                    f"{width_ratio:.1f}倍，疑似{int(round(width_ratio))}个字符粘连"
                ),
            })

            logger.debug(
                "Strong adhesion at pos %d: char='%s', ratio=%.2f, components=%d",
                i, char_text, width_ratio, component_count
            )

        # Weak signal: 1.3-1.8x normal, needs verification
        elif WEAK_ADHESION_RATIO <= width_ratio < STRONG_ADHESION_RATIO:
            # Verify with connected component analysis
            component_count = _count_components_in_bbox(bbox, para_img)

            # If multiple components found, confirm adhesion
            if component_count >= 2:
                confidence = min(0.4 + (width_ratio - WEAK_ADHESION_RATIO) * 0.3, 0.7)

                detections.append({
                    'pos': i,
                    'type': 'suspected_adhesion',
                    'confidence': float(round(confidence, 3)),
                    'width_ratio': float(round(width_ratio, 2)),
                    'char': char_text,
                    'bbox': [float(v) for v in bbox],
                    'normal_width': float(round(normal_center, 1)),
                    'wide_width': float(round(wide_center, 1)),
                    'component_count': component_count,
                    'reason': (
                        f"字符宽度({char_width:.1f}px)为正常宽度的{width_ratio:.1f}倍，"
                        f"连通域分析发现{component_count}个连通域，疑似粘连"
                    ),
                })

                logger.debug(
                    "Weak adhesion confirmed at pos %d: char='%s', ratio=%.2f, "
                    "components=%d", i, char_text, width_ratio, component_count
                )
            else:
                logger.debug(
                    "Weak adhesion at pos %d dismissed: char='%s', ratio=%.2f, "
                    "components=%d", i, char_text, width_ratio, component_count
                )

    return detections


def cluster_widths(
    widths: List[float],
    k: int = 2
) -> Tuple[List[float], List[float]]:
    """1D K-means clustering on character widths.

    Clusters character widths into k groups to separate normal-width
    characters from wide (potentially adhered) characters.

    Algorithm:
    1. Initialize centroids using min/max values
    2. Assign each width to nearest centroid
    3. Update centroids as cluster means
    4. Repeat until convergence (or max 20 iterations)

    Args:
        widths: List of character widths in pixels.
        k: Number of clusters. Defaults to 2 (normal and wide).

    Returns:
        Tuple of (normal_cluster, wide_cluster) width lists.
        The cluster with smaller center is returned first.

    Example:
        >>> widths = [30, 32, 28, 31, 29, 65, 62]
        >>> normal, wide = cluster_widths(widths, k=2)
        >>> print(f"Normal: {normal}")  # ~30px widths
        [30, 32, 28, 31, 29]
        >>> print(f"Wide: {wide}")     # ~60px widths
        [65, 62]
    """
    if not widths:
        return [], []

    if len(widths) < k:
        return widths, []

    # Initialize centroids: spread evenly across range
    min_w, max_w = min(widths), max(widths)
    if min_w == max_w:
        return widths, []

    centroids = np.linspace(min_w, max_w, k)

    # K-means iterations
    max_iterations = 20
    tolerance = 0.01

    for iteration in range(max_iterations):
        # Assign each width to nearest centroid
        clusters: List[List[float]] = [[] for _ in range(k)]

        for w in widths:
            distances = [abs(w - c) for c in centroids]
            nearest = distances.index(min(distances))
            clusters[nearest].append(w)

        # Update centroids
        new_centroids = []
        for i in range(k):
            if clusters[i]:
                new_centroids.append(sum(clusters[i]) / len(clusters[i]))
            else:
                new_centroids.append(centroids[i])  # Keep old if empty

        # Check convergence
        shift = sum(abs(new_centroids[i] - centroids[i]) for i in range(k))
        centroids = new_centroids

        if shift < tolerance:
            break

    # Rebuild clusters with final centroids
    final_clusters: List[List[float]] = [[] for _ in range(k)]
    for w in widths:
        distances = [abs(w - c) for c in centroids]
        nearest = distances.index(min(distances))
        final_clusters[nearest].append(w)

    # Sort clusters by centroid (smaller first = normal)
    cluster_centers = []
    for i in range(k):
        if final_clusters[i]:
            cluster_centers.append((sum(final_clusters[i]) / len(final_clusters[i]), i))
        else:
            cluster_centers.append((float('inf'), i))

    cluster_centers.sort(key=lambda x: x[0])

    # Return normal cluster (smallest center) and wide cluster
    normal_idx = cluster_centers[0][1]
    wide_idx = cluster_centers[-1][1] if k > 1 else normal_idx

    return final_clusters[normal_idx], final_clusters[wide_idx]


def _count_components_in_bbox(
    bbox: List[float],
    img: np.ndarray
) -> int:
    """Count connected components within a bounding box.

    Extracts the region defined by bbox from the image, binarizes it,
    and counts the number of connected components.

    Args:
        bbox: Bounding box [x1, y1, x2, y2].
        img: Source image.

    Returns:
        Number of connected components (excluding background).
    """
    try:
        h, w = img.shape[:2]
        x1 = max(0, int(bbox[0]))
        y1 = max(0, int(bbox[1]))
        x2 = min(w, int(bbox[2]))
        y2 = min(h, int(bbox[3]))

        if x2 <= x1 or y2 <= y1:
            return 0

        region = img[y1:y2, x1:x2]
        if region.size == 0:
            return 0

        # Convert to grayscale
        if region.ndim == 3:
            gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        else:
            gray = region.copy()

        # Binarize (invert: text becomes white)
        _, binary = cv2.threshold(gray, BINARY_THRESHOLD, 255, cv2.THRESH_BINARY_INV)

        # Morphological operations to separate touching characters
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2))
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

        # Count connected components
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
            binary, connectivity=8
        )

        # Count components above minimum area (skip background)
        component_count = 0
        for i in range(1, num_labels):
            area = stats[i, cv2.CC_STAT_AREA]
            if area >= MIN_COMPONENT_AREA:
                component_count += 1

        return component_count

    except Exception as e:
        logger.error("Component counting failed: %s", e)
        return 0


def detect_adhesion_with_context(
    char_details: List[Dict[str, Any]],
    img: np.ndarray,
    text: str,
    term_kb: Optional[Any] = None
) -> List[Dict[str, Any]]:
    """Enhanced adhesion detection with term KB context.

    Uses term knowledge to improve detection accuracy by checking
    if an adhesion would create an invalid term.

    Args:
        char_details: Character-level recognition details.
        img: Source image.
        text: Full recognized text.
        term_kb: Optional term knowledge base.

    Returns:
        List of adhesion detections.
    """
    line_record = {
        'text': text,
        'char_level_details': char_details,
    }

    detections = detect_extra_chars(line_record, img)

    # Enhance with term KB context
    if term_kb and hasattr(term_kb, 'is_valid_ngram'):
        for det in detections:
            pos = det['pos']
            if pos < len(text):
                # Check surrounding context
                context_start = max(0, pos - 2)
                context_end = min(len(text), pos + 3)
                context = text[context_start:context_end]

                if not term_kb.is_valid_ngram(context):
                    det['confidence'] = min(det.get('confidence', 0.5) + 0.1, 0.95)
                    det['reason'] += "，且不符合已知术语模式"

    return detections
