# F1: TOC 抽取与章节层级重建（修订版，据多角色评审 r1）

> 基于 2026-07-17 多角色评审（`docs/reviews/toc_plan_review_r1.md`）的 B1-B5 阻塞项已修正，R1-R10 建议项已采纳。

## 现状

- KZOCR 的 `BookResult` 只有逐页文本，无整书目录树。
- E4 `orchestrate_book` 返回的 `BookResult` 产出 `pages_text`，但 E4 不涉及 TOC。
- traedocu 的 `toc_parser.py` + `section_merger.py` 提供参考（VLM 版）。KZOCR 用**文本版**。

## 关键设计决策

### 1. 文本式 TOC 抽取：不做 VLM，用正则 + 启发式

- **目录页探测**（B1 修正：模糊匹配）：
  - 扫描文本中 **NFC 归一化后** 匹配"目录"、"目錄"、"總目"、"綱目"、"CONTENTS" 等标题关键词；
  - 增加编辑距离（Levenshtein ratio ≥ 0.7）容错路径：如 OCR 将"目录"识为"日录"、"目隶"、"自录"仍可触发发现；
  - 可配置关键词列表（`TOC_HEADER_KEYWORDS`），默认含繁简变体；
  - 匹配页须同时含两行以上"标题+页码"模式的行才确认为目录页。
- **目录条目解析**（B2 修正：缩进阶为辅助，主靠编号+关键词）：
  - 因 OCR 文本可能丢失缩进，缩进**不作为主判据**——仅在检测到连续空格时才提级暗示；
  - 层级推导主策略基于编号深度与前缀关键词（R5 扩展）：
    - level 1（卷/门/科）：尾部含"卷"、"科"、"门"、"部"，或无编号但为**大字类名**
    - level 2（章/篇）：含"章"、"篇"、`§N`、"第N章"、"第N篇"
    - level 3（节/小类）：含"节"、"§N"、`N.N`、"第一节"、"一、"、"（一）"（R5）
    - level 4（小节/方）：`N.N.N`、"治…方"、"方"、"药" 等
    - level 5（子条）：`N.N.N.N`、`(1)`、`①` 等
  - 页码提取（R6 采纳）：支持阿拉伯数字 + 中文数字（一→1、贰→2）+ 汉字后跟"叶/页"后缀。
- **树构建**：`TocTree` 嵌套 `TocEntry.sub_entries`，非均匀深度（1-5 层）。

### 2. 类型层（R1 采纳：去掉 `TocTree.book` 与 `total_pages`）

```python
@dataclass
class TocEntry:
    level: int                     # 1-5
    title: str
    page: int                      # 目录标注起始页码（0=无页码）；中文数字已转阿拉伯
    sub_entries: list[TocEntry] = field(default_factory=list)
    section_no: str = ""           # 编号如 "1" / "1.1" / "§3"（用于勘误去重）

@dataclass
class TocTree:
    max_depth: int = 0             # 实际深度（2-5）；0=无 TOC
    entries: list[TocEntry] = field(default_factory=list)
```

`BookResult.toc: Optional[TocTree] = None`（默认 None，兼容无目录书）。

### 3. 提取模块：独立 `kzocr/engine/toc.py`

核心函数：

```python
def discover_toc_pages(pages_text: list[str],
                       keywords: Optional[list[str]] = None) -> list[int]:
    """B1 修正：NFC 归一化 + 编辑距离模糊匹配关键词（默认"目录//目錄/總目/綱目/目録/CONTENTS"）；
    匹配页须同时含 ≥2 行条目模式。"""

def parse_toc(pages_text: list[str], toc_page_nums: list[int],
              cn2arabic: Optional[dict[str, int]] = None) -> list[dict]:
    """R3 采纳：正则均加 {1,200} 量词上限；
    层级推导按 编号深度/前缀关键词/分隔符形态 判定 level 1-5（不做缩进主判据，B2）；
    中文数字页码转换（R6）；无页码行跳过（不抛错）。"""

def build_toc_tree(entries: list[dict]) -> TocTree | None:
    """面包屑路径 O(n)。section_no 重复时记录 warnings 但不崩（R9 采纳）。"""

def build_toc(pages_text: list[str]) -> TocTree | None:
    """便利函数：discover → parse → build。无目录页时返回 None（B5 采纳）。"""

def enrich_book_result(result: BookResult) -> BookResult:
    """B3 修正：docstring 显式注明"就地修改 result.toc 后返回同一引用"；
    后续可改为纯函数版本（返回新 BookResult）。交叉验证（R2 采纳，默认关闭，
    可选传 heading_level 表做对齐校验）。"""
```

### 4. 集成到 E4

```python
result = orchestrate_book(...)
enrich_book_result(result)  # 就地挂 TOC
# 或纯函数风格后续补
```

交叉验证（R2，默认关闭）：`build_toc` 接受可选 `heading_map: dict[int, int]`（page → heading_level），校验 TOC 条目标注的层级与 page 内的实际 heading_level 是否一致。

## 评审采纳汇总

| 编号 | 类型 | 采纳状态 | 具体措施 |
|------|------|----------|----------|
| B1 | 阻塞 | ✅ 已修正 | NFC + Levenshtein 模糊匹配关键词，含繁简变体 |
| B2 | 阻塞 | ✅ 已修正 | 缩进阶为辅助，主策略编号+关键词；文档显式说明 |
| B3 | 阻塞 | ✅ 已修正 | `enrich_book_result` docstring 注明就地修改；后续可改纯函数 |
| B4 | 阻塞 | ✅ 已修正 | 测试计划含 OCR 噪声组 |
| B5 | 阻塞 | ✅ 已修正 | `build_toc` 无目录页返回 None |
| R1 | 建议 | ✅ 采纳 | 去掉 `TocTree.book` / `total_pages` |
| R2 | 建议 | ✅ 采纳 | 交叉验证（默认关闭，可选入参） |
| R3 | 建议 | ✅ 采纳 | 正则量词 `{1,200}` 防 ReDoS |
| R4 | 建议 | ⏳ 文档 | docstring 注明"输出仅用于元信息，不直接拼文件路径" |
| R5 | 建议 | ✅ 采纳 | 节标题扩展 "第一节"、"一、"、"（一）" |
| R6 | 建议 | ✅ 采纳 | 中文数字→阿拉伯转换 |
| R7 | 建议 | ⏳ 后续 | edition profile 留做可配置接口 |
| R8 | 建议 | ✅ 采纳 | 测试含 500+ 条目压力用例 |
| R9 | 建议 | ✅ 采纳 | 编号重复不崩，warnings 记录 |
| R10 | 建议 | ✅ 采纳 | 跨页非连续页测试 |

## 关键文件清单

新增：
- `kzocr/engine/toc.py`
- `docs/reviews/toc_plan_review_r1.md`（评审报告）

修改：
- `kzocr/engine/types.py` — 新增 `TocEntry` / `TocTree`，`BookResult.toc`
- `tests/test_toc.py`

## 测试计划（含 B4/B5/R8/R9/R10）

- 正向：3 层、5 层解析正确性
- **B4 OCR 噪声**：日录→仍发现、页码 I→1 混淆、缺行合并恢复、全角/半角混用
- **B5 负向**：无目录→None、正文含"目录"二字但无结构→不误报、仅标题行无条目不报告
- 跨页合并（连续页 + **R10 非连续页**）
- 格式变体（无缩进分隔符填充、繁体/简体混用）
- **R8 大目录压力**：500+ 条目 5 层深，<1s
- **R9 编号重复**：section_no 重复不崩，记录 warnings
