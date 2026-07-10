"""方剂解析器测试：方剂识别、九字段提取、药材解析、加减解析。"""

from __future__ import annotations

from kzocr.analysis.recipe_parser import (
    is_valid_recipe_no,
    looks_like_dose,
    parse_herbs,
    parse_modifications,
    parse_recipes,
)


# ── 方剂编号 ──

def test_valid_recipe_no():
    assert is_valid_recipe_no("1.1")
    assert is_valid_recipe_no("109.7")
    assert is_valid_recipe_no("16.7.1")
    assert not is_valid_recipe_no("1")
    assert not is_valid_recipe_no("1.a")
    assert not is_valid_recipe_no("")


def test_looks_like_dose():
    assert looks_like_dose("克 10")
    assert not looks_like_dose("特效感冒宁")
    assert looks_like_dose("ml 5")


# ── 药材解析 ──

def test_parse_herbs_single():
    herbs = parse_herbs("金银花12克，甘草3克")
    assert len(herbs) == 2
    assert herbs[0].herb_name == "金银花"
    assert herbs[0].dosage == "12"
    assert herbs[0].unit == "克"
    assert herbs[1].herb_name == "甘草"
    assert herbs[1].dosage == "3"


def test_parse_herbs_share_dose():
    """各X克句式的共享剂量。"""
    herbs = parse_herbs("苏叶、薄荷、藿香、防风、荆芥各10克")
    assert len(herbs) == 5
    for h in herbs:
        assert h.dosage == "10"
        assert h.unit == "克"
    assert herbs[0].dosage_group == herbs[1].dosage_group > 0


def test_parse_herbs_preparation():
    """炮制标注提取。"""
    herbs = parse_herbs("半夏（制）10克，生姜3片")
    assert len(herbs) >= 2
    assert herbs[0].preparation == "制"
    assert herbs[0].herb_name == "半夏"


def test_parse_herbs_no_dose():
    """无剂量药材。"""
    herbs = parse_herbs("大枣、生姜")
    assert len(herbs) >= 2
    assert herbs[0].dosage == ""
    assert herbs[1].dosage == ""


def test_parse_herbs_range_dose():
    """支持范围剂量。"""
    herbs = parse_herbs("金银花10~15克")
    assert len(herbs) == 1
    assert herbs[0].dosage_min == 10.0
    assert herbs[0].dosage_max == 15.0


# ── 加减解析 ──

def test_parse_modifications_add():
    mods = parse_modifications("咽喉痛者，加桔梗10克，僵蚕6克")
    assert len(mods) >= 1
    assert "咽喉痛者" in mods[0].condition
    assert mods[0].action == "add"
    assert len(mods[0].herbs) >= 1


def test_parse_modifications_remove():
    mods = parse_modifications("表虚者去薄荷")
    assert len(mods) >= 1
    assert "表虚者" in mods[0].condition
    assert mods[0].action == "remove"


def test_parse_modifications_multi():
    """多个加减条目用分号分割。"""
    text = "咽喉痛者，加桔梗10克；咳嗽者，加浙贝母10克"
    mods = parse_modifications(text)
    assert len(mods) >= 2
    assert mods[0].action == "add"


# ── 主解析函数 ──

def test_parse_recipes_single():
    pages = [
        "1.1 特效感冒宁\n来源　张氏医书\n组成　金银花12克，甘草3克\n主治　感冒\n",
    ]
    recipes = parse_recipes(pages)
    assert len(recipes) == 1
    assert recipes[0].recipe_no == "1.1"
    assert recipes[0].title == "特效感冒宁"
    assert "张氏医书" in recipes[0].fields.get("来源", "")
    assert len(recipes[0].herbs) == 2


def test_parse_recipes_multi():
    pages = [
        "1.1 特效感冒宁\n组成　金银花12克\n主治　感冒\n",
        "1.2 止咳散\n组成　桔梗10克\n",
    ]
    recipes = parse_recipes(pages)
    assert len(recipes) == 2
    assert recipes[0].recipe_no == "1.1"
    assert recipes[1].recipe_no == "1.2"


def test_parse_recipes_no_recipes():
    """无方剂的文本返回空列表。"""
    assert parse_recipes(["普通正文"]) == []


def test_parse_recipes_duplicate_no():
    """重复方剂编号不抛错，第二个仍然解析。"""
    pages = [
        "1.1 特效感冒宁\n组成　金银花12克\n",
        "1.1 重复方\n组成　甘草3克\n",
    ]
    recipes = parse_recipes(pages)
    assert len(recipes) == 2  # 两条都被解析


def test_parse_recipes_hash():
    """每个方剂应有 raw_hash。"""
    pages = ["1.1 测试方\n来源　测试书\n"]
    recipes = parse_recipes(pages)
    assert len(recipes) == 1
    assert len(recipes[0].raw_hash) == 64  # SHA256 hex
    assert recipes[0].parse_status == "parsed"
