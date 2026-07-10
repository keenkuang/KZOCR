# KZOCR v0.7 E1 实现评审 — 领域（domain / 中医古籍 OCR）

- **评审角色**：领域（中医古籍 OCR）
- **评审日期**：2026-07-10
- **评审对象**：`kzocr/scheduler/registry.py`（`1d52cae`）、`kzocr/engine/types.py`
- **范围声明**：E1 聚焦首版，仅注册中心 + 候选选择 + 统计；竖排/领域感知权重（§4.3/§4.4）、字形验证（§5）、人工校对闭环（§6.5）属 E2/E3/E4 未实现，不计入本 E1 阻塞。

---

## 1. 【阻塞 / 必须修复】

**无。** E1 仅注册中心+候选选择+统计，tier/统计骨架对中医场景可成立，真实领域权重逻辑本属 E2，无硬伤需阻塞本提交。

## 2. 【重要 / 强烈建议】

1. **record() 静默丢弃 RARE/UNCERTAIN**（`registry.py:120-126`）。设计 `GlyphStatus` 含 5 态（`types.py`），§5.2 中 RARE（TermKBMatcher，生僻药名/古字）、UNCERTAIN（D4 字符数尖峰）是中医古籍两类高频且高价值的"疑似"状态。E1 仅分支匹配 PASS/FAIL/UNKNOWN，RARE/UNCERTAIN 落入无 else 分支被静默丢弃。后果：①古籍中 rare 字（异体字、古方名）占比高，识别正确的 rare 字不计入 `glyph_pass_rate`，对古籍优化引擎的准确率被系统性低估；②E3 落地后计数器将无声丢数。建议：补 `glyph_rare_count`/`glyph_uncertain_count`，并对未知 status 抛 `ValueError` 或告警。
2. **glyph_pass_rate 分母含 UNKNOWN 但漏 RARE**（`registry.py:51-55`）。§6.5 review_manifest 优先级 P0=FAIL/P1=UNKNOWN/P2=RARE，RARE 实为"罕见但可能正确"。将 RARE 既不计入 pass 也不计入统计，雕版/竖排古籍中本应受肯定的 rare 字识别无法反哺优先级。建议 E3 前明确 RARE 在新通过率中的口径（建议计入"非失败"分母或单列 `rare_rate`）。

## 3. 【优化 / 可选】

1. **冷启动口径不一**：`glyph_pass_rate` 无数据返回 `GLYPH_PASS_RATE_DEFAULT=0.5`（`registry.py:57`），贝叶斯先验却是 `BAYESIAN_PRIOR=0.7`（`registry.py:21`）。同是冷启动通过率却两值，`prefer="accuracy"` 与贝叶斯排序不一致，建议统一常量。
2. **tier 无界**（`types.py:157`，`tier: int = 1`）。设计 §3 为 tier 0-3，建议用 `Literal[0,1,2,3]` 或注入校验，防越界导致 §4.4 提权/降权（雕版对 VLM 提权、竖排对 T1 降权）失效。
3. **glyph_fail_count 未区分 severity**（`registry.py:31-32`）。ToxinDoseDetector 的 toxic 剂量 FAIL（§5.3，安全最高优先级）与字形 FAIL 混记，未来 §6.5 安全闭环无法据 FAIL 类型排序，建议预留 `is_toxic`/`critical` 标记位，并预留 confusion_set/rare_allowlist 回写对接计数。
