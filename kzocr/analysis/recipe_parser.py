"""方剂结构化解析器：从 OCR 文本中提取方剂9字段 + 药材解析 + 加减解析。

纯规则实现（无需 LLM），参考 traedocu db_builder.py 的解析逻辑。

字段标识符：
  来源 / 组成 / 用法 / 功用 / 方解 / 主治 / 疗效 / 加减 / 附记
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

_logger = logging.getLogger(__name__)

# ── 常量 ──

FIELD_IDENTIFIERS = ["来源", "组成", "用法", "功用", "方解", "主治", "疗效", "加减", "附记"]

# 方剂编号模式: "1.1 特效感冒宁" / "1.1.1 百冬止血汤"
RECIPE_NO_RE = re.compile(r"^(\d{1,3}\.\d{1,3}(?:\.\d{1,3})?)\s+(.+)")

# "各X克"句式: "苏叶、薄荷、藿香、防风、荆芥各10克"
DOSE_SHARE_RE = re.compile(r"各(\d+(?:\.\d+)?)\s*(克|ml|毫升|g|g|毫克|两|钱|分)")

# 单味剂量: "金银花12克"
DOSE_SINGLE_RE = re.compile(r"(\d+(?:[~～]\d+)?)\s*(克|ml|毫升|g|mg|毫克|两|钱|分)")

# 剂量单位集合
DOSE_UNITS = {"克", "ml", "毫升", "g", "mg", "毫克", "两", "钱", "分"}

# 炮制标注: 括号内的炮制信息
PREPARATION_RE = re.compile(r"[（(](.+?)[）)]")


@dataclass
class HerbItem:
    """单味药材解析结果。"""
    herb_name: str
    dosage: str = ""
    unit: str = ""
    preparation: str = ""
    dosage_group: int = 0
    dosage_min: Optional[float] = None
    dosage_max: Optional[float] = None


@dataclass
class ModificationItem:
    """加减条目。"""
    condition: str = ""
    action: str = "add"  # add / remove / replace / adjust
    content: str = ""
    herbs: list[HerbItem] = field(default_factory=list)


@dataclass
class ParsedRecipe:
    """单方剂完整解析结果。"""
    recipe_no: str
    title: str
    fields: dict[str, str] = field(default_factory=dict)  # 来源→"xxx", 组成→"xxx"...
    herbs: list[HerbItem] = field(default_factory=list)
    modifications: list[ModificationItem] = field(default_factory=list)
    start_page: int = 0
    raw_text: str = ""
    raw_hash: str = ""
    parse_status: str = "pending"  # pending / parsed / verified / corrected


def strip_meta(content: str) -> str:
    """去掉 OCR 文件的元数据头。"""
    if content.startswith("<!-- meta:"):
        meta_end = content.find("-->")
        if meta_end > 0:
            return content[meta_end + 3:].strip()
    return content


def looks_like_dose(title_text: str) -> bool:
    """判断标题是否像剂量而非方名。"""
    first_word = title_text.lstrip().split()[0] if title_text.lstrip() else ""
    return first_word in DOSE_UNITS or first_word.rstrip("，。、；") in DOSE_UNITS


def is_valid_recipe_no(no: str) -> bool:
    """验证方剂编号是否合法。"""
    parts = no.split(".")
    if len(parts) < 2 or len(parts) > 3:
        return False
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return False
    return all(n > 0 for n in nums)


# ── 药材解析 ──

def parse_herbs(composition_text: str) -> list[HerbItem]:
    """从"组成"字段解析药材列表。

    支持三种模式：
    A. "各X克" 句式 — 共享剂量
    B. 单味加剂量 — "金银花12克"
    C. 无剂量 — 纯药名列表
    """
    if not composition_text:
        return []

    herbs: list[HerbItem] = []
    groups = [g.strip() for g in re.split(r"[，；;]", composition_text) if g.strip()]
    dose_group_id = 0

    # 炮制标注: 填充到每个 herb_item 的 preparation 字段
    def _extract_prep(name: str) -> tuple[str, str]:
        m = PREPARATION_RE.search(name)
        if m:
            return PREPARATION_RE.sub("", name).strip(), m.group(1)
        return name, ""

    for group in groups:
        share_m = DOSE_SHARE_RE.search(group)
        if share_m:
            # 模式A: "各X克"
            share_dose = share_m.group(1)
            share_unit = share_m.group(2)
            dose_group_id += 1
            # "各" 前面的药名列表（用顿号/空格分割）
            before_share = group[:share_m.start()].strip()
            names = re.split(r"[、，,\s]+", before_share)
            for n in names:
                if not n or not re.search(r"[\u4e00-\u9fff]", n):
                    continue
                name_clean, prep = _extract_prep(n)
                herbs.append(HerbItem(
                    herb_name=name_clean,
                    dosage=share_dose,
                    unit=share_unit,
                    preparation=prep,
                    dosage_group=dose_group_id,
                    dosage_min=float(share_dose) if share_dose else None,
                    dosage_max=float(share_dose) if share_dose else None,
                ))
        else:
            # 模式B/C: 单味加剂量 或 纯药名
            items = re.split(r"[、，,/\s]+", group) if "、" in group or "，" in group else [group]
            for item in items:
                item = item.strip()
                if not item or not re.search(r"[\u4e00-\u9fff]", item):
                    continue
                dose_m = DOSE_SINGLE_RE.search(item)
                if dose_m:
                    # 模式B: 有剂量
                    dose_str = dose_m.group(1)
                    unit = dose_m.group(2)
                    name_part = item[:dose_m.start()].strip().rstrip("，,")
                    name_clean, prep = _extract_prep(name_part)
                    dosage_min: Optional[float] = None
                    dosage_max: Optional[float] = None
                    if "~" in dose_str or "～" in dose_str:
                        parts = re.split(r"[~～]", dose_str)
                        try:
                            dosage_min = float(parts[0])
                            dosage_max = float(parts[1]) if len(parts) > 1 else dosage_min
                        except ValueError:
                            pass
                    else:
                        try:
                            dosage_min = dosage_max = float(dose_str)
                        except ValueError:
                            pass
                    herbs.append(HerbItem(
                        herb_name=name_clean,
                        dosage=dose_str,
                        unit=unit,
                        preparation=prep,
                        dosage_min=dosage_min,
                        dosage_max=dosage_max,
                    ))
                else:
                    # 模式C: 无剂量
                    name_clean, prep = _extract_prep(item)
                    herbs.append(HerbItem(herb_name=name_clean, preparation=prep))

    return herbs


# ── 加减解析 ──

def parse_modifications(modification_text: str) -> list[ModificationItem]:
    """从"加减"字段解析加减条目。"""
    if not modification_text:
        return []
    items: list[ModificationItem] = []
    raw_items = [x.strip() for x in re.split(r"[；;]", modification_text) if x.strip()]
    prev_condition = ""
    for raw in raw_items:
        # 检测动作
        action = "add"
        if re.search(r"去.*加|易|去.*代|改", raw):
            action = "replace"
        elif raw.startswith("去") or re.match(r".*[者时]去", raw):
            action = "remove"
        elif "易" in raw:
            action = "replace"
        # 提取条件
        cond_m = re.search(r"(.+?[者时])", raw)
        condition = cond_m.group(1).strip() if cond_m else ""
        # 条件继承：短条件（≤4字且以"者"结尾）继承上一条
        if len(condition) <= 4 and condition.endswith("者"):
            condition = f"{prev_condition},{condition}" if prev_condition else condition
        if condition:
            prev_condition = condition
        # 提取药材
        herbs = parse_herbs(raw)
        items.append(ModificationItem(
            condition=condition,
            action=action,
            content=raw,
            herbs=herbs,
        ))
    return items


# ── 方剂解析主函数 ──

def parse_recipes(pages_text: list[str]) -> list[ParsedRecipe]:
    """从逐页文本中解析所有方剂。

    Args:
        pages_text: 全书逐页文本列表（0-indexed）。

    Returns:
        ParsedRecipe 列表。
    """
    recipes: list[ParsedRecipe] = []
    buf_recipe: Optional[ParsedRecipe] = None
    buf_field: Optional[str] = None
    seen_nos: set[str] = set()

    for page_num, text in enumerate(pages_text):
        if not text:
            continue
        content = strip_meta(text)
        for line in content.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue

            # 检测方剂标题
            recipe_match = RECIPE_NO_RE.match(stripped)
            if recipe_match and is_valid_recipe_no(recipe_match.group(1)):
                title_text = recipe_match.group(2).strip()
                if not re.search(r"[\u4e00-\u9fff]", title_text):
                    continue
                if looks_like_dose(title_text):
                    continue

                recipe_no = recipe_match.group(1)
                # flush 前一个方剂
                if buf_recipe is not None:
                    buf_recipe.raw_text = _build_raw_text(buf_recipe)
                    buf_recipe.raw_hash = hashlib.sha256(buf_recipe.raw_text.encode()).hexdigest()
                    buf_recipe.herbs = parse_herbs(buf_recipe.fields.get("组成", ""))
                    buf_recipe.modifications = parse_modifications(buf_recipe.fields.get("加减", ""))
                    buf_recipe.parse_status = "parsed"
                    recipes.append(buf_recipe)

                if recipe_no in seen_nos:
                    _logger.warning("[recipe] duplicate recipe_no=%s at page %d", recipe_no, page_num)
                seen_nos.add(recipe_no)

                buf_recipe = ParsedRecipe(
                    recipe_no=recipe_no,
                    title=title_text,
                    start_page=page_num,
                )
                buf_field = None
                continue

            # 检测字段标识符
            if buf_recipe is not None:
                field_found = False
                for fid in FIELD_IDENTIFIERS:
                    if stripped.startswith(f"{fid}") and len(stripped) > len(fid):
                        val = stripped[len(fid):].strip().lstrip("　 ")
                        buf_recipe.fields[fid] = val
                        buf_field = fid
                        field_found = True
                        break
                if not field_found and buf_field and buf_recipe.fields.get(buf_field):
                    buf_recipe.fields[buf_field] += stripped

    # flush 最后一个方剂
    if buf_recipe is not None:
        buf_recipe.raw_text = _build_raw_text(buf_recipe)
        buf_recipe.raw_hash = hashlib.sha256(buf_recipe.raw_text.encode()).hexdigest()
        buf_recipe.herbs = parse_herbs(buf_recipe.fields.get("组成", ""))
        buf_recipe.modifications = parse_modifications(buf_recipe.fields.get("加减", ""))
        buf_recipe.parse_status = "parsed"
        recipes.append(buf_recipe)

    return recipes


def _build_raw_text(recipe: ParsedRecipe) -> str:
    """从 ParsedRecipe 重建原始文本（用于 hash 计算）。"""
    lines = [f"{recipe.recipe_no} {recipe.title}"]
    for fid in FIELD_IDENTIFIERS:
        if fid in recipe.fields:
            lines.append(f"{fid}　{recipe.fields[fid]}")
    return "\n".join(lines)
