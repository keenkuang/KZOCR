"""
Line-Level OCR Consensus Fusion Module.

Implements multi-engine consensus algorithms for combining OCR results:
- Single engine fallback with dispute marking
- Two-engine consensus with term KB correction
- Multi-engine (3-4) majority voting with character-level alignment
- Needleman-Wunsch sequence alignment for character positioning
- Term knowledge base weighted voting for TCM terminology
"""

import logging
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Minimum confidence threshold for accepting a character vote
MIN_CHAR_CONFIDENCE = 0.4

# Dispute marker for unresolved characters
DISPUTED_MARKER = '[DISPUTED]'

# Weight bonuses for different engine characteristics
WEIGHT_TERM_MATCH_BONUS = 0.3
WEIGHT_ALNUM_MATCH_BONUS = 0.2
WEIGHT_LENGTH_PENALTY = 0.1


def line_consensus(
    engine_results: Dict[str, str],
    term_kb: object,
    book_meta: Optional[Dict[str, Any]] = None,
    line_id: Optional[str] = None
) -> Dict[str, Any]:
    """Determine consensus text from multiple OCR engine results.

    Routes to the appropriate consensus algorithm based on the number
    of available engine results.

    Args:
        engine_results: Dictionary mapping engine name to recognized text.
                       Example: {'mineru': '黄芪15g', 'paddleocr': '黄氏15g'}
        term_kb: Term knowledge base for contextual correction.
        book_meta: Optional book metadata (publisher, year, etc.).
        line_id: Optional line identifier for logging.

    Returns:
        Consensus result dictionary containing:
            - final_text (str): Best consensus text
            - confidence (float): Overall confidence score
            - raw_vote_text (str): Raw voting result before corrections
            - auto_corrected (bool): Whether term KB was used
            - char_confidences (list): Per-character confidence scores
            - disputed (bool): Whether any positions are disputed
            - disputed_positions (list): Indices of disputed characters
            - method (str): Consensus method used
            - engine_count (int): Number of engines participating

    Example:
        >>> results = {'mineru': '黄芪15g', 'paddleocr': '黄氏15g'}
        >>> consensus = line_consensus(results, term_kb)
        >>> print(consensus['final_text'])
        '黄芪15g'
    """
    book_meta = book_meta or {}

    # Filter empty results
    valid_results = {k: v for k, v in engine_results.items() if v and v.strip()}
    engine_count = len(valid_results)

    logger.debug("Line %s: %d engines with valid results",
                line_id or 'unknown', engine_count)

    if engine_count == 0:
        return {
            'final_text': '',
            'confidence': 0.0,
            'raw_vote_text': '',
            'auto_corrected': False,
            'char_confidences': [],
            'disputed': True,
            'disputed_positions': [],
            'method': 'no_engines',
            'engine_count': 0,
        }

    if engine_count == 1:
        # Single engine - return with dispute marking
        engine_name = list(valid_results.keys())[0]
        text = valid_results[engine_name]

        # Check for term conflicts even with single engine
        has_conflict = False
        if term_kb and hasattr(term_kb, 'has_conflict'):
            has_conflict = term_kb.has_conflict(text)

        return {
            'final_text': text,
            'confidence': 0.7,
            'raw_vote_text': text,
            'auto_corrected': False,
            'char_confidences': [0.7] * len(text),
            'disputed': True,  # Single engine always disputed
            'disputed_positions': list(range(len(text))) if has_conflict else [],
            'method': 'single_engine_fallback',
            'engine_count': 1,
            'single_engine': engine_name,
        }

    if engine_count == 2:
        # Two-engine consensus
        raw_texts = list(valid_results.values())
        result = two_engine_consensus(raw_texts, term_kb, book_meta, line_id)
        result['engine_count'] = 2
        result['engine_names'] = list(valid_results.keys())
        return result

    # Three or more engines - multi-engine voting
    result = multi_engine_consensus(valid_results, term_kb, book_meta, line_id)
    result['engine_count'] = engine_count
    return result


def multi_engine_consensus(
    engine_results: Dict[str, str],
    term_kb: object,
    book_meta: Optional[Dict[str, Any]] = None,
    line_id: Optional[str] = None
) -> Dict[str, Any]:
    """Multi-engine consensus via character-level voting.

    Algorithm:
    1. Select the longest text as the alignment base
    2. Align all other engine results to the base
    3. For each character position, count votes across all engines
    4. Apply term knowledge base weighting for TCM terms
    5. If all engines disagree at a position, mark as [DISPUTED]
    6. If majority is below 50%, mark as [DISPUTED]
    7. Attempt term KB error pattern correction for disputed positions

    Args:
        engine_results: Dictionary mapping engine name to recognized text.
        term_kb: Term knowledge base for weighted voting and correction.
        book_meta: Optional book metadata.
        line_id: Optional line identifier.

    Returns:
        Consensus result dictionary with voting details.
    """
    book_meta = book_meta or {}

    # Step 1: Select base text (longest text preferred)
    list(engine_results.keys())
    texts = list(engine_results.values())

    # Find the longest text as base
    base_text = max(texts, key=len)
    base_engine = None
    for name, text in engine_results.items():
        if text == base_text:
            base_engine = name
            break

    logger.debug("Multi-engine consensus: base='%s' from %s, %d engines",
                base_text, base_engine, len(engine_results))

    # Step 2: Align all texts to base
    aligned_results: List[Tuple[str, List[Optional[str]]]] = []
    for name, text in engine_results.items():
        aligned_chars, insertions = align_to_base(base_text, text)
        aligned_results.append((name, aligned_chars))

    # Step 3: Character-level voting
    vote_result = _vote_on_aligned_chars(
        aligned_results, base_text, term_kb, engine_results
    )

    raw_vote_text = vote_result['text']
    char_confidences = vote_result['confidences']
    disputed_positions = vote_result['disputed_positions']

    # Step 4: Check for all-engine disagreement or low majority
    all_chars_disputed = all(pos in disputed_positions for pos in range(len(raw_vote_text)))
    majority_ratio = vote_result.get('majority_ratio', 0.0)

    auto_corrected = False
    final_text = raw_vote_text

    if all_chars_disputed:
        # All engines completely disagree
        final_text = DISPUTED_MARKER
        disputed = True
        confidence = 0.1
    elif majority_ratio < 0.5 and len(engine_results) >= 3:
        # Majority below 50% threshold
        final_text = DISPUTED_MARKER
        disputed = True
        confidence = 0.2
    else:
        disputed = len(disputed_positions) > 0
        confidence = vote_result.get('confidence', 0.5)

        # Step 5: Attempt term KB error correction for disputed positions
        if disputed and term_kb and hasattr(term_kb, 'match_error_pattern_in_context'):
            corrected_text, corrections = _attempt_error_correction(
                raw_vote_text, disputed_positions, engine_results, term_kb
            )
            if corrections > 0:
                final_text = corrected_text
                auto_corrected = True
                # Recalculate disputed positions after correction
                disputed_positions = [
                    pos for pos in disputed_positions
                    if final_text[pos] == DISPUTED_MARKER
                ] if DISPUTED_MARKER in final_text else []
                disputed = len(disputed_positions) > 0

    return {
        'final_text': final_text,
        'confidence': confidence,
        'raw_vote_text': raw_vote_text,
        'auto_corrected': auto_corrected,
        'char_confidences': char_confidences,
        'disputed': disputed,
        'disputed_positions': disputed_positions,
        'method': 'multi_engine_majority_vote',
        'majority_ratio': majority_ratio,
        'base_engine': base_engine,
        'base_text': base_text,
    }


def _vote_on_aligned_chars(
    aligned_results: List[Tuple[str, List[Optional[str]]]],
    base_text: str,
    term_kb: object,
    engine_results: Dict[str, str]
) -> Dict[str, Any]:
    """Vote on each character position across aligned engine results.

    Args:
        aligned_results: List of (engine_name, aligned_chars) tuples.
        base_text: The base text used for alignment.
        term_kb: Term knowledge base for weighting.
        engine_results: Original engine results for weight calculation.

    Returns:
        Voting result with text, confidences, and disputed positions.
    """
    if not aligned_results:
        return {'text': '', 'confidences': [], 'disputed_positions': [], 'confidence': 0.0}

    num_positions = len(aligned_results[0][1])
    voted_chars: List[str] = []
    char_confidences: List[float] = []
    disputed_positions: List[int] = []

    total_majority_votes = 0
    total_positions = 0

    for pos in range(num_positions):
        # Collect all votes at this position with weights
        votes: Dict[str, float] = {}
        for engine_name, aligned_chars in aligned_results:
            ch = aligned_chars[pos]
            if ch is None:
                continue

            # Get base weight for this engine
            weight = get_base_weight(
                engine_name,
                engine_results.get(engine_name, ''),
                term_kb
            )

            # Apply term KB bonus
            if term_kb and hasattr(term_kb, 'is_valid_char_in_context'):
                context = base_text[max(0, pos - 2):min(len(base_text), pos + 3)]
                if term_kb.is_valid_char_in_context(ch, context):
                    weight += WEIGHT_TERM_MATCH_BONUS

            votes[ch] = votes.get(ch, 0.0) + weight

        if not votes:
            voted_chars.append(DISPUTED_MARKER)
            char_confidences.append(0.0)
            disputed_positions.append(pos)
            continue

        # Find the winner
        total_votes = sum(votes.values())
        winner = max(votes, key=votes.get)
        winner_votes = votes[winner]
        majority_ratio = winner_votes / total_votes if total_votes > 0 else 0

        total_majority_votes += majority_ratio
        total_positions += 1

        if majority_ratio >= 0.5:
            voted_chars.append(winner)
            char_confidences.append(min(majority_ratio, 0.99))
        else:
            # No clear majority
            voted_chars.append(DISPUTED_MARKER)
            char_confidences.append(majority_ratio)
            disputed_positions.append(pos)

    avg_majority = total_majority_votes / max(total_positions, 1)
    vote_text = ''.join(voted_chars)

    # Calculate overall confidence
    if disputed_positions:
        confidence = 0.5 * (1 - len(disputed_positions) / max(len(vote_text), 1))
    else:
        confidence = sum(char_confidences) / max(len(char_confidences), 1)

    return {
        'text': vote_text,
        'confidences': char_confidences,
        'disputed_positions': disputed_positions,
        'confidence': confidence,
        'majority_ratio': avg_majority,
    }


def _attempt_error_correction(
    text: str,
    disputed_positions: List[int],
    engine_results: Dict[str, str],
    term_kb: object
) -> Tuple[str, int]:
    """Attempt to correct disputed positions using term knowledge base.

    Args:
        text: Current consensus text with dispute markers.
        disputed_positions: Positions that need correction.
        engine_results: All engine results for context.
        term_kb: Term knowledge base with error patterns.

    Returns:
        Tuple of (corrected_text, number_of_corrections).
    """
    corrected = list(text)
    corrections = 0

    for pos in disputed_positions:
        # Build context around disputed position
        context_start = max(0, pos - 5)
        context_end = min(len(text), pos + 6)
        context = text[context_start:context_end]

        # Try to match error patterns
        if hasattr(term_kb, 'match_error_pattern_in_context'):
            suggestion = term_kb.match_error_pattern_in_context(context, pos - context_start)
            if suggestion and suggestion != DISPUTED_MARKER:
                corrected[pos] = suggestion
                corrections += 1

    return ''.join(corrected), corrections


def two_engine_consensus(
    raw_texts: List[str],
    term_kb: object,
    book_meta: Optional[Dict[str, Any]] = None,
    line_id: Optional[str] = None
) -> Dict[str, Any]:
    """Two-engine consensus with detailed comparison and term KB correction.

    Algorithm:
    1. If texts are identical, return with high confidence
    2. Compare character by character
    3. For differing positions, consult term knowledge base
    4. If term KB cannot resolve, mark as disputed
    5. Log degradation for disputed positions

    Args:
        raw_texts: List of exactly two engine result texts.
        term_kb: Term knowledge base for correction.
        book_meta: Optional book metadata.
        line_id: Optional line identifier.

    Returns:
        Consensus result dictionary.
    """
    if len(raw_texts) != 2:
        raise ValueError(f"Expected exactly 2 texts, got {len(raw_texts)}")

    text_a, text_b = raw_texts[0], raw_texts[1]

    # Step 1: Exact match
    if text_a == text_b:
        return {
            'final_text': text_a,
            'confidence': 0.98,
            'raw_vote_text': text_a,
            'auto_corrected': False,
            'char_confidences': [0.98] * len(text_a),
            'disputed': False,
            'disputed_positions': [],
            'method': 'two_engine_exact_match',
        }

    # Step 2: Character-by-character comparison
    max_len = max(len(text_a), len(text_b))
    result_chars: List[str] = []
    char_confidences: List[float] = []
    disputed_positions: List[int] = []
    auto_corrected = False

    for i in range(max_len):
        ch_a = text_a[i] if i < len(text_a) else ''
        ch_b = text_b[i] if i < len(text_b) else ''

        if ch_a == ch_b:
            result_chars.append(ch_a)
            char_confidences.append(0.95)
        else:
            # Step 3: Consult term KB for this position
            resolved = False
            if term_kb and hasattr(term_kb, 'prefer_char'):
                context_a = text_a[max(0, i - 3):min(len(text_a), i + 4)]
                context_b = text_b[max(0, i - 3):min(len(text_b), i + 4)]

                preferred = term_kb.prefer_char(ch_a, ch_b, context_a, context_b)
                if preferred:
                    result_chars.append(preferred)
                    char_confidences.append(0.85)
                    auto_corrected = True
                    resolved = True

            if not resolved:
                # Step 4: Mark as disputed
                result_chars.append(DISPUTED_MARKER)
                char_confidences.append(0.3)
                disputed_positions.append(i)

    final_text = ''.join(result_chars)
    has_disputes = len(disputed_positions) > 0

    # Calculate confidence
    if has_disputes:
        confidence = sum(char_confidences) / len(char_confidences) if char_confidences else 0.3
    else:
        confidence = 0.95

    # Log degradation info
    if has_disputes:
        logger.info(
            "Line %s: Two-engine consensus degraded at positions %s",
            line_id or 'unknown', disputed_positions
        )
        logger.info(
            "  Text A: '%s' | Text B: '%s' | Result: '%s'",
            text_a, text_b, final_text
        )

    return {
        'final_text': final_text,
        'confidence': confidence,
        'raw_vote_text': final_text,
        'auto_corrected': auto_corrected,
        'char_confidences': char_confidences,
        'disputed': has_disputes,
        'disputed_positions': disputed_positions,
        'method': 'two_engine_with_term_correction',
        'text_a': text_a,
        'text_b': text_b,
    }


def align_to_base(
    base: str,
    target: str
) -> Tuple[List[Optional[str]], List[Tuple[int, str]]]:
    """Align target text to base text using SequenceMatcher.

    Creates a position-by-position alignment where each position in the
    base text is mapped to the corresponding character in the target text.
    Gaps (insertions/deletions) are represented as None.

    Uses the Needleman-Wunsch-like alignment provided by difflib.SequenceMatcher
    to handle substitutions, insertions, and deletions.

    Args:
        base: The base text to align against.
        target: The target text to align.

    Returns:
        Tuple of:
            - aligned_chars: List of characters from target aligned to base
                            positions. None indicates a gap (deletion in target).
            - insertions: List of (position, character) tuples for characters
                         inserted in target but not in base.

    Example:
        >>> aligned, insertions = align_to_base('黄芪15g', '黄氏15克')
        >>> print(aligned)
        ['黄', '氏', '1', '5', None]  # '芪' aligned to '氏', 'g' has no match
        >>> print(insertions)
        [(4, '克')]  # '克' inserted at position 4
    """
    aligned_chars: List[Optional[str]] = [None] * len(base)
    insertions: List[Tuple[int, str]] = []

    if not base or not target:
        if not target:
            return aligned_chars, insertions
        if not base:
            # All target characters are insertions
            insertions = [(0, ch) for ch in target]
            return aligned_chars, insertions

    sm = SequenceMatcher(None, base, target)
    base_idx = 0

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            # Characters match exactly
            for offset in range(i2 - i1):
                if base_idx + offset < len(aligned_chars):
                    aligned_chars[base_idx + offset] = target[j1 + offset]
            base_idx = i2

        elif tag == 'replace':
            # Substitution: map target chars to base positions
            base_len = i2 - i1
            target_len = j2 - j1
            min_len = min(base_len, target_len)

            for offset in range(min_len):
                if base_idx + offset < len(aligned_chars):
                    aligned_chars[base_idx + offset] = target[j1 + offset]

            # If target is longer, extras are insertions
            if target_len > base_len:
                for offset in range(base_len, target_len):
                    insertions.append((base_idx + base_len, target[j1 + offset]))

            base_idx = i2

        elif tag == 'delete':
            # Characters in base but not in target - gaps remain None
            base_idx = i2

        elif tag == 'insert':
            # Characters in target but not in base
            for offset in range(j2 - j1):
                insertions.append((base_idx, target[j1 + offset]))
            # base_idx doesn't advance for insertions

    return aligned_chars, insertions


def get_base_weight(
    engine: str,
    text: str,
    term_kb: object
) -> float:
    """Calculate the voting weight for an engine based on text characteristics.

    Weight adjustments:
    - Base weight: 1.0
    - PaddleOCR bonus (1.3x): If text contains known TCM terms
    - Engine3 bonus (1.2x): If text contains digits or English letters
    - Length penalty: Shorter texts get slight penalty

    Args:
        engine: Engine identifier string.
        text: Recognized text from this engine.
        term_kb: Term knowledge base for term detection.

    Returns:
        Weighted voting weight for this engine's text.

    Example:
        >>> weight = get_base_weight('paddleocr', '黄芪15g', term_kb)
        >>> print(f"Weight: {weight:.2f}")
        'Weight: 1.30'  # Bonus for TCM terms
    """
    base_weight = 1.0

    # Check for TCM terminology (favor PaddleOCR)
    has_tcm_term = False
    if term_kb and hasattr(term_kb, 'contains_term'):
        has_tcm_term = term_kb.contains_term(text)
    else:
        # Simple heuristic: check for common herb characters
        tcm_chars = set('芪芷苓芎蒡菔苡蔻术術黨麝蟾藿菖藁蚶虻蛭螯炙煅煨')
        has_tcm_term = any(ch in text for ch in tcm_chars)

    if has_tcm_term and 'paddle' in engine.lower():
        base_weight += WEIGHT_TERM_MATCH_BONUS  # 1.3

    # Check for digits/English (favor engine3)
    has_alnum = bool(any(c.isdigit() or c.isascii() and c.isalpha() for c in text))
    if has_alnum and engine in ('engine3', 'tesseract', 'doctr', 'unirec'):
        base_weight += WEIGHT_ALNUM_MATCH_BONUS  # 1.2

    # Slight length penalty for very short texts
    if len(text) < 3:
        base_weight -= WEIGHT_LENGTH_PENALTY

    return max(base_weight, 0.5)  # Minimum weight
