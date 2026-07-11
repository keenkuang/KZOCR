"""
系统常量定义模块。

包含出版年代分组、识别阈值、超时配置、图像参数、争议阈值等
中医现代出版物 OCR 校对系统的全局常量。
"""

from enum import Enum
from typing import Dict, List, Set, Tuple


# =============================================================================
# 出版年代分组阈值
# =============================================================================

ERA_GROUP_1_END: int = 1979   # 第一档结束年份（1949-1979）
ERA_GROUP_2_END: int = 1999   # 第二档结束年份（1980-1999）
# 第三档：2000+

ERA_GROUP_THRESHOLDS: List[int] = [ERA_GROUP_1_END, ERA_GROUP_2_END]


def get_era_group(pub_year: int) -> str:
    """根据出版年份返回年代分组标识。

    Args:
        pub_year: 出版年份（4位整数）

    Returns:
        年代分组字符串: '1949-1979' | '1980-1999' | '2000+'
    """
    if pub_year <= ERA_GROUP_1_END:
        return "1949-1979"
    elif pub_year <= ERA_GROUP_2_END:
        return "1980-1999"
    else:
        return "2000+"


# =============================================================================
# 识别阈值
# =============================================================================

BASE_THRESHOLD: float = 0.95           # 基础共识置信度阈值
PUBLISHER_BONUS_DEFAULT: float = 0.02  # 出版社准确率默认奖励值

def get_threshold_with_bonus(
    base_threshold: float = BASE_THRESHOLD,
    publisher_bonus: float = PUBLISHER_BONUS_DEFAULT,
) -> float:
    """计算叠加出版社奖励后的有效阈值。

    Args:
        base_threshold: 基础阈值
        publisher_bonus: 出版社奖励值

    Returns:
        有效阈值（不超过 0.99）
    """
    return min(base_threshold + publisher_bonus, 0.99)


# =============================================================================
# 超时配置（秒）
# =============================================================================

LOCAL_LLM_TIMEOUT: int = 60    # 本地 LLM（ShizhenGPT）超时
CLOUD_LLM_TIMEOUT: int = 30    # 云端 LLM（Qwen-Max）超时
PDF_RENDER_TIMEOUT: int = 300  # PDF 渲染超时
MINERU_TIMEOUT: int = 120      # MinerU 版面分析超时

# =============================================================================
# 图像参数
# =============================================================================

DPI: int = 300                          # PDF 渲染/扫描图 DPI
LRU_CACHE_SIZE: int = 50                # 图像 LRU 缓存大小
BATCH_SIZE: int = 10                    # 批处理页面数
MAX_IMAGE_DIMENSION: int = 8192         # 最大图像尺寸（像素）
ENHANCEMENT_CONTRAST_ALPHA: float = 1.5  # 对比度增强系数
ENHANCEMENT_SHARPEN_SIGMA: float = 1.0   # 锐化sigma

# =============================================================================
# 争议阈值 —— 漏字/多字检测
# =============================================================================

MISSING_CHAR_GAP_RATIO: float = 1.3      # 漏字间距比阈值
EXTRA_CHAR_STRONG_RATIO: float = 1.8     # 强多字间距比阈值
EXTRA_CHAR_WEAK_RATIO: float = 1.3       # 弱多字间距比阈值

# =============================================================================
# 引擎置信度权重（用于共识融合）
# =============================================================================

ENGINE_CONFIDENCE_WEIGHTS: Dict[str, float] = {
    "shizhengpt": 1.15,   # 本地中医微调模型
    "paddleocr": 1.0,     # 通用 OCR
    "tesseract": 0.9,     # 开源 OCR
}

# =============================================================================
# 目录页检测关键词
# =============================================================================

TOC_KEYWORDS: List[str] = [
    "目录", "contents", "目次", "章节目录",
    "上篇", "中篇", "下篇", "附篇",
    "第一章", "第二章", "第三章",
    "第一节", "第二节", "第三节",
    "附录", "索引", "参考文献",
]

# =============================================================================
# 版面分类常量
# =============================================================================

class LayoutType(str, Enum):
    """版面类型枚举。"""
    TEXT = "text"           # 纯文本
    TABLE = "table"         # 表格
    IMAGE = "image"         # 插图
    FORMULA = "formula"     # 公式/方剂
    MIXED = "mixed"         # 混合
    HEADER = "header"       # 页眉
    FOOTER = "footer"       # 页脚
    TOC_PAGE = "toc"        # 目录页


# =============================================================================
# LLM 四级决策配置
# =============================================================================

LLM_DECISION_LEVELS: List[str] = [
    "direct_accept",      # 1级：直接采纳
    "minor_adjust",       # 2级：微调修正
    "deep_verify",        # 3级：深度验证
    "human_review",       # 4级：人工审核
]

LLM_DECISION_THRESHOLD_MINOR: float = 0.90   # 触发 2级微调阈值
LLM_DECISION_THRESHOLD_VERIFY: float = 0.75   # 触发 3级深度验证阈值
LLM_DECISION_THRESHOLD_HUMAN: float = 0.60    # 触发 4级人工审核阈值

# =============================================================================
# 预处理年代参数（三档）
# =============================================================================

ERA_PREPROCESS_PARAMS: Dict[str, Dict[str, float]] = {
    "1949-1979": {
        "noise_reduction_strength": 0.8,
        "contrast_alpha": 1.8,
        "sharpen_sigma": 1.5,
        "deskew_threshold": 0.5,
        "bleedthrough_removal": True,
        "paper_yellowing_compensation": True,
    },
    "1980-1999": {
        "noise_reduction_strength": 0.5,
        "contrast_alpha": 1.4,
        "sharpen_sigma": 1.0,
        "deskew_threshold": 0.3,
        "bleedthrough_removal": False,
        "paper_yellowing_compensation": False,
    },
    "2000+": {
        "noise_reduction_strength": 0.2,
        "contrast_alpha": 1.1,
        "sharpen_sigma": 0.5,
        "deskew_threshold": 0.2,
        "bleedthrough_removal": False,
        "paper_yellowing_compensation": False,
    },
}


def get_preprocess_params(pub_year: int) -> Dict[str, float]:
    """根据出版年份获取预处理参数。

    Args:
        pub_year: 出版年份

    Returns:
        预处理参数字典
    """
    era = get_era_group(pub_year)
    return ERA_PREPROCESS_PARAMS.get(era, ERA_PREPROCESS_PARAMS["2000+"])


# =============================================================================
# 数据库表名常量
# =============================================================================

TABLE_PROOFREAD_RECORD: str = "proofread_record"
TABLE_LINE_ENGINE_RESULT: str = "line_engine_result"
TABLE_CONTENT_NODE: str = "content_node"
TABLE_FORMULA_COMPOSITION: str = "formula_composition"
TABLE_FORMULA_INGREDIENT: str = "formula_ingredient"
TABLE_LINE_CORRECTION_ARCHIVE: str = "line_correction_archive"
TABLE_OCR_LINE_RESULT_ARCHIVE: str = "ocr_line_result_archive"
TABLE_BOOK_CONTENT_TREE: str = "book_content_tree"

# =============================================================================
# 交付物相关常量
# =============================================================================

OUTPUT_SUBDIR_BODY_MD: str = "body.md"
OUTPUT_SUBDIR_FULL_MD: str = "full.md"
OUTPUT_SUBDIR_FINAL_JSON: str = "final_document.json"
OUTPUT_SUBDIR_ASSETS: str = "assets"

SHA256_MANIFEST_FILENAME: str = "checksums.sha256"

# =============================================================================
# 正则模式常量（用于元数据提取）
# =============================================================================

ISBN_PATTERN: str = r"ISBN[\s:]?(?:978-?)?\d[\d\-]{8,17}\d"
PRICE_PATTERN: str = r"(?:定价|售价|价格)[\s:]*(\d+\.?\d*)\s*元?"
PUBLISHER_PATTERN: str = r"(?:出版发行|出版|出版社|出版单位)[\s:]*([^\n]+)"
PUB_DATE_PATTERN: str = r"(\d{4})\s*年\s*(\d{1,2})\s*月"
EDITION_PATTERN: str = r"(?:第(\d+)版|(\d+)版(\d+)印)"
PRINT_COUNT_PATTERN: str = r"(\d{1,3},?\d*)\s*册"

# =============================================================================
# 字符集常量
# =============================================================================

# Unicode 中医特殊字符范围
TCM_SPECIAL_CHAR_RANGES: List[Tuple[int, int]] = [
    (0x3400, 0x4DBF),   # CJK 扩展-A
    (0x4E00, 0x9FFF),   # CJK 统一表意符号
    (0xF900, 0xFAFF),   # CJK 兼容表意符号
    (0x20000, 0x2A6DF), # CJK 扩展-B
]

# 允许的特殊标点
ALLOWED_TCM_PUNCTUATION: Set[str] = set(
    "，。、；：！？"  # 全角标点
    "（）【】《》"    # 全角括号书名号
    "〈〉「」『』"    # 其他全角引号
    "─━┅┉┈┇│┃"      # 制表符/分隔线
    "·•‥…"           # 中点省略号
    "①②③④⑤⑥⑦⑧⑨⑩" # 带圈数字
    "ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ"   # 罗马数字
    "甲乙丙丁戊己庚辛壬癸"  # 天干
    "子丑寅卯辰巳午未申酉戌亥"  # 地支
)
