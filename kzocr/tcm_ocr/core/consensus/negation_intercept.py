"""
Negation Intercept Module for TCM OCR System.

Critical safety module that monitors for negation words and forbidden patterns
in OCR text, especially after LLM post-processing. Ensures that:
1. Negation words (不, 无, 非, 忌, 禁, 勿, 慎) are not accidentally removed
2. Forbidden patterns (孕妇忌服, 阴虚者禁用, etc.) remain intact
3. Any modification to negation-sensitive text triggers human review

This is essential for TCM publications where safety warnings and
contraindications must be preserved exactly.
"""

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────

# Core negation words in TCM context
NEGATION_WORDS: Set[str] = {'不', '无', '非', '忌', '禁', '勿', '慎'}

# Extended negation set including compound negations
NEGATION_WORDS_EXTENDED: Set[str] = {
    '不', '无', '非', '忌', '禁', '勿', '慎',
    '没有', '并非', '不可', '不能', '不得',
    '勿用', '禁用', '忌用', '慎用', '禁食',
    '无伤', '无毒', '无效', '不宜', '不可',
}

# Forbidden/safety-critical patterns that must be preserved exactly
FORBIDDEN_PATTERNS: List[str] = [
    '孕妇忌服', '孕妇禁用', '孕妇慎用',
    '阴虚者禁用', '阴虚者忌用', '阴虚者慎用',
    '阳虚者禁用', '阳虚者忌用', '阳虚者慎用',
    '气虚者禁用', '气虚者忌用', '气虚者慎用',
    '血虚者禁用', '血虚者忌用', '血虚者慎用',
    '忌与', '禁与', '勿与',
    '脾胃虚寒者慎用', '脾胃虚寒者禁用',
    '表虚自汗者慎用', '表虚自汗者禁用',
    '阴虚火旺者忌服', '阴虚火旺者慎用',
    '无实热者禁用', '无实热者慎用',
    '非实热证禁用', '非实热证慎用',
    '不宜久煎', '不宜久服', '不宜过量',
    '不可久服', '不可过量', '不可内服',
    '孕妇忌服', '哺乳期妇女禁用', '儿童慎用',
    '肝肾功能不全者慎用', '过敏体质者慎用',
    '服药期间忌食', '服药期间禁酒',
    '忌食生冷', '忌食辛辣', '忌食油腻',
    '反藜芦', '反乌头', '反甘草',
    '十八反', '十九畏', '妊娠禁忌',
    '有毒', '大毒', '剧毒', '小毒',
]

# Prefix patterns that indicate negation context
NEGATION_PREFIX_PATTERNS: List[str] = [
    '忌', '禁', '勿', '慎', '不宜', '不可', '不得',
]

# Characters commonly confused with negation words
CONFUSABLE_WITH_NEGATION: Dict[str, List[str]] = {
    '忌': ['已', '己', '巳', '记'],
    '禁': ['楚', '梦'],
    '勿': ['匆', '物', '忽'],
    '慎': ['真', '填', '镇'],
    '无': ['天', '夫', '元'],
    '不': ['木', '下', '末'],
}


# ── Functions ────────────────────────────────────────────────────────────


def extract_negation_bigrams(text: str) -> Set[Tuple[int, str]]:
    """Extract negation bigrams (position, negation_word) from text.

    Scans the text for negation words and returns their positions
    along with the negation word found. This helps track exactly
    where negations occur for comparison between text versions.

    Args:
        text: Input text to scan for negation words.

    Returns:
        Set of tuples (position, negation_word) for each found negation.

    Example:
        >>> extract_negation_bigrams('孕妇忌服，阴虚者禁用')
        {(2, '忌'), (8, '禁')}
        >>> extract_negation_bigrams('本品无毒，可长期服用')
        set()  # No negation words found
    """
    if not text:
        return set()

    negations: Set[Tuple[int, str]] = set()
    len(text)

    # Check for multi-character negations first
    for neg_word in sorted(NEGATION_WORDS_EXTENDED, key=len, reverse=True):
        if len(neg_word) >= 2:
            pos = 0
            while True:
                idx = text.find(neg_word, pos)
                if idx == -1:
                    break
                negations.add((idx, neg_word))
                pos = idx + len(neg_word)

    # Check for single-character negations
    for i, ch in enumerate(text):
        if ch in NEGATION_WORDS:
            # Check if this single char is not already part of a multi-char negation
            is_part_of_longer = any(
                start <= i < start + len(word)
                for start, word in negations
                if len(word) >= 2
            )
            if not is_part_of_longer:
                negations.add((i, ch))

    return negations


def check_negation_integrity(
    original_text: str,
    llm_modified_text: str
) -> List[Dict[str, Any]]:
    """Check if all negation words from original text are preserved in LLM text.

    Compares negation positions and words between the original OCR text
    and the LLM-processed text. Reports any negations that were:
    - Removed entirely
    - Changed to different characters
    - Moved to different positions

    Args:
        original_text: Original OCR text (pre-LLM).
        llm_modified_text: LLM-processed text (post-LLM).

    Returns:
        List of integrity issue dictionaries:
            - type (str): 'removed', 'changed', or 'moved'
            - position (int): Original position
            - original (str): Original negation word
            - modified (str): What it became (if changed)
            - context (str): Surrounding context
            - severity (str): 'critical', 'warning', or 'info'

    Example:
        >>> issues = check_negation_integrity('孕妇忌服本品', '孕妇已服本品')
        >>> print(issues[0]['type'])
        'changed'  # '忌' was changed to '已'
    """
    alerts: List[Dict[str, Any]] = []

    if not original_text:
        return alerts

    original_negations = extract_negation_bigrams(original_text)
    modified_negations = extract_negation_bigrams(llm_modified_text)

    {pos for pos, _ in modified_negations}
    modified_words = {word for _, word in modified_negations}

    for orig_pos, orig_word in original_negations:
        # Check if this exact negation is preserved
        if (orig_pos, orig_word) in modified_negations:
            continue  # Perfectly preserved

        # Check if the negation word exists elsewhere
        found_elsewhere = False
        for mod_pos, mod_word in modified_negations:
            if mod_word == orig_word and abs(mod_pos - orig_pos) <= 3:
                found_elsewhere = True
                break

        if found_elsewhere:
            continue  # Moved slightly but preserved

        # Check if the word exists at all in modified text
        if orig_word in modified_words:
            alerts.append({
                'type': 'moved',
                'position': orig_pos,
                'original': orig_word,
                'modified': orig_word,
                'context': _get_context(original_text, orig_pos, len(orig_word)),
                'severity': 'warning',
                'message': f"否定词'{orig_word}'位置发生移动",
            })
        else:
            # Check if it was changed to a confusable character
            changed_to = _check_changed_to_confusable(
                llm_modified_text, orig_pos, orig_word
            )
            if changed_to:
                alerts.append({
                    'type': 'changed',
                    'position': orig_pos,
                    'original': orig_word,
                    'modified': changed_to,
                    'context': _get_context(original_text, orig_pos, len(orig_word)),
                    'severity': 'critical',
                    'message': f"否定词'{orig_word}'被改为'{changed_to}'，疑似OCR/LLM错误",
                })
            else:
                alerts.append({
                    'type': 'removed',
                    'position': orig_pos,
                    'original': orig_word,
                    'modified': '',
                    'context': _get_context(original_text, orig_pos, len(orig_word)),
                    'severity': 'critical',
                    'message': f"否定词'{orig_word}'被删除",
                })

    return alerts


def negation_intercept(
    original_text: str,
    llm_modified_text: str,
    line_id: Optional[str] = None
) -> Dict[str, Any]:
    """Main negation intercept function for LLM post-processing.

    Performs comprehensive negation safety checks:
    1. Checks negation word integrity
    2. Checks forbidden pattern preservation
    3. Determines if human review is required
    4. Generates detailed alert messages

    Args:
        original_text: Original OCR text (pre-LLM).
        llm_modified_text: LLM-processed text (post-LLM).
        line_id: Optional line identifier for logging.

    Returns:
        Dictionary with:
            - force_human_review (bool): Whether human review is mandatory
            - alerts (list): All negation-related alerts
            - negation_count_original (int): Count of negations in original
            - negation_count_modified (int): Count of negations in modified
            - forbidden_patterns_found (list): Found forbidden patterns
            - status (str): 'safe', 'warning', or 'critical'

    Example:
        >>> result = negation_intercept('孕妇忌服', '孕妇已服', line_id='L42')
        >>> print(result['force_human_review'])
        True
        >>> print(result['status'])
        'critical'
    """
    alerts: List[Dict[str, Any]] = []

    # Step 1: Check negation integrity
    integrity_alerts = check_negation_integrity(original_text, llm_modified_text)
    alerts.extend(integrity_alerts)

    # Step 2: Check forbidden patterns
    forbidden_alerts = _check_forbidden_patterns(original_text, llm_modified_text)
    alerts.extend(forbidden_alerts)

    # Step 3: Count negations
    original_negations = extract_negation_bigrams(original_text)
    modified_negations = extract_negation_bigrams(llm_modified_text)

    # Step 4: Determine severity
    has_critical = any(a['severity'] == 'critical' for a in alerts)
    has_warning = any(a['severity'] == 'warning' for a in alerts)

    if has_critical:
        status = 'critical'
        force_review = True
    elif has_warning:
        status = 'warning'
        # Force review if negation count changed
        force_review = len(original_negations) != len(modified_negations)
    else:
        status = 'safe'
        force_review = False

    # Step 5: Log results
    if force_review:
        logger.warning(
            "Negation intercept triggered for line %s: status=%s, alerts=%d",
            line_id or 'unknown', status, len(alerts)
        )
        for alert in alerts:
            logger.warning("  - %s: %s", alert['type'], alert['message'])

    return {
        'force_human_review': force_review,
        'alerts': alerts,
        'negation_count_original': len(original_negations),
        'negation_count_modified': len(modified_negations),
        'forbidden_patterns_found': [a['original'] for a in forbidden_alerts],
        'status': status,
        'line_id': line_id,
    }


# ── Internal Helper Functions ────────────────────────────────────────────


def _get_context(text: str, position: int, length: int, window: int = 8) -> str:
    """Get surrounding context for a position in text.

    Args:
        text: Full text.
        position: Start position.
        length: Length of the target segment.
        window: Context window size on each side.

    Returns:
        Context string with the target segment marked.
    """
    start = max(0, position - window)
    end = min(len(text), position + length + window)

    before = text[start:position]
    target = text[position:position + length]
    after = text[position + length:end]

    return f"...{before}[{target}]{after}..."


def _check_changed_to_confusable(
    modified_text: str,
    original_pos: int,
    original_word: str
) -> Optional[str]:
    """Check if a negation word was changed to a confusable character.

    Args:
        modified_text: The LLM-modified text.
        original_pos: Original position of the negation word.
        original_word: The original negation word.

    Returns:
        The confusable character if found, None otherwise.
    """
    if original_pos >= len(modified_text):
        return None

    # Check the character at the same position
    char_at_pos = modified_text[original_pos]

    # Check if this character is a known confusable for the negation word
    for neg_char, confusables in CONFUSABLE_WITH_NEGATION.items():
        if neg_char in original_word and char_at_pos in confusables:
            return char_at_pos

    # Also check if the original negation char is known to be confusable
    for neg_char in original_word:
        if neg_char in CONFUSABLE_WITH_NEGATION:
            if char_at_pos in CONFUSABLE_WITH_NEGATION[neg_char]:
                return char_at_pos

    return None


def _check_forbidden_patterns(
    original_text: str,
    modified_text: str
) -> List[Dict[str, Any]]:
    """Check if forbidden patterns are preserved.

    Args:
        original_text: Original text.
        modified_text: Modified text.

    Returns:
        List of alerts for any forbidden pattern issues.
    """
    alerts: List[Dict[str, Any]] = []

    for pattern in FORBIDDEN_PATTERNS:
        if pattern in original_text:
            if pattern not in modified_text:
                # Forbidden pattern was modified or removed
                alerts.append({
                    'type': 'forbidden_pattern_altered',
                    'position': original_text.find(pattern),
                    'original': pattern,
                    'modified': '',
                    'context': _get_context(original_text, original_text.find(pattern), len(pattern)),
                    'severity': 'critical',
                    'message': f"安全关键模式'{pattern}'被修改或删除",
                })

    return alerts
