"""LLM 质检管道：对 recipe_parser 解析结果做后校验。

纯规则模式（rule-only）为默认，LLM 模式可选（仅处理规则标记的疑点）。
不含药材名归一化。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from kzocr.analysis.recipe_parser import ParsedRecipe

_logger = logging.getLogger(__name__)


@dataclass
class RecipeIssue:
    """单条质检问题。"""
    field: str               # 字段名（如"组成"、"主治"）
    issue_type: str          # missing_field / dose_reason / suspicious_name / inconsistency
    severity: str            # error / warning / info
    detail: str
    suggestion: str = ""


@dataclass
class QualityResult:
    """单方剂质检结果。"""
    recipe_no: str
    status: str              # verified / corrected
    issues: list[RecipeIssue] = field(default_factory=list)
    confidence: float = 1.0


class QualityChecker:
    """质检器。默认 rule-only，可传入 llm_client 启用 LLM 辅助。"""

    def __init__(self, llm_client: Optional[Callable[[str], str]] = None) -> None:
        self.llm = llm_client

    def check(self, recipe: ParsedRecipe) -> QualityResult:
        """执行完整质检流水线。"""
        issues = self._rule_checks(recipe)
        if self.llm and issues:
            try:
                llm_issues = self._llm_check(recipe, issues)
                issues.extend(llm_issues)
            except Exception as exc:
                _logger.warning("[quality] LLM check failed: %s", exc)
        status = "corrected" if issues else "verified"
        return QualityResult(
            recipe_no=recipe.recipe_no,
            status=status,
            issues=issues,
            confidence=0.5 if issues else 1.0,
        )

    def _rule_checks(self, recipe: ParsedRecipe) -> list[RecipeIssue]:
        """纯规则检查。"""
        issues: list[RecipeIssue] = []
        # 字段完整性
        if "组成" not in recipe.fields:
            issues.append(RecipeIssue(
                field="组成", issue_type="missing_field", severity="error",
                detail="缺少组成字段",
            ))
        if "主治" not in recipe.fields:
            issues.append(RecipeIssue(
                field="主治", issue_type="missing_field", severity="warning",
                detail="缺少主治字段",
            ))
        # 剂量检查
        for h in recipe.herbs:
            if h.dosage:
                try:
                    dose = float(h.dosage)
                    if dose > 100:
                        issues.append(RecipeIssue(
                            field="组成", issue_type="dose_reason", severity="warning",
                            detail=f"{h.herb_name} 剂量 {h.dosage}{h.unit} 偏大（>{100}g）",
                        ))
                except ValueError:
                    issues.append(RecipeIssue(
                        field="组成", issue_type="dose_reason", severity="error",
                        detail=f"{h.herb_name} 剂量不合法: {h.dosage}",
                    ))
            # 单字药名可疑
            if h.herb_name and len(h.herb_name) == 1:
                issues.append(RecipeIssue(
                    field="组成", issue_type="suspicious_name", severity="warning",
                    detail=f"单字药名: {h.herb_name}",
                ))
        return issues

    def _llm_check(self, recipe: ParsedRecipe, rule_issues: list[RecipeIssue]) -> list[RecipeIssue]:
        """LLM 辅助检查：确认规则疑点、补充跨字段一致性。"""
        if not self.llm:
            return []
        prompt = _build_prompt(recipe, rule_issues)
        response = self.llm(prompt)
        return _parse_llm_response(response)


def _build_prompt(recipe: ParsedRecipe, issues: list[RecipeIssue]) -> str:
    """构建 LLM 质检 prompt。"""
    fields_str = "\n".join(f"{k}：{v}" for k, v in recipe.fields.items())
    if not fields_str:
        fields_str = "（该方剂无提取字段）"  # R3: 空字段占位
    issues_str = "\n".join(f"- [{i.severity}] {i.field}: {i.detail}" for i in issues)
    return (
        f"请审核下方中医方剂的解析结果。方剂编号：{recipe.recipe_no}，标题：{recipe.title}\n\n"
        f"字段内容：\n{fields_str}\n\n"
        f"药材列表：\n"
        + "\n".join(f"  {h.herb_name} {h.dosage}{h.unit}" for h in recipe.herbs)
        + f"\n\n规则检查发现以下疑点：\n{issues_str}\n\n"
        + "请逐条回答：1) 此疑点是否真实问题？2) 如需修正请给出建议。"
        + "如无问题请回答「全部正确」。"
    )


def _parse_llm_response(response: str) -> list[RecipeIssue]:
    """解析 LLM 返回的质检意见。"""
    if not response or "全部正确" in response:
        return []
    issues: list[RecipeIssue] = []
    for line in response.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        issues.append(RecipeIssue(
            field="llm",
            issue_type="inconsistency",
            severity="info",
            detail=line[:200],
            suggestion=line,
        ))
    return issues
