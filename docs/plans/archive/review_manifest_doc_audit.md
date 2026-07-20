# review_manifest 设计文档 vs 代码一致性核验

## 背景

v0.7 的 `review_manifest` 校对反馈闭环（§5.6，提交 `7b35a2d`）已实现并合入 main。DETAILED 设计稿 §5.6 记录了该功能的设计，但实现过程中可能产生了偏离。需逐条核对文档伪代码/描述与实际代码的差异。

## 核验范围

### 文件清单

| 文件 | 说明 |
|------|------|
| `docs/plans/ocr-engine-unification.v0.7-DETAILED.md` §5.6 | 设计文档（约 40–60 行） |
| `kzocr/scheduler/review_manifest.py` | 实现：`build_review_manifest` + `feedback_apply` |
| `kzocr/cli/review.py` | CLI `kzocr review manifest / apply` |
| `tests/test_review_manifest.py` | 测试（12 用例） |

### 需核验的关键点

1. **ReviewIssue 数据结构**：文档定义的字段与代码 `ReviewIssue` dataclass 一致？
2. **build_review_manifest 签名**：`(db: BookDB) -> ReviewManifest` 一致？
3. **P0/P1/P2 优先级产生规则**：文档定义的规则（FAIL→P0, UNKNOWN→P1, RARE→P2, conf_low→info）与代码一致？
4. **feedback_apply 签名与行为**：`(manifest: ReviewManifest, db: BookDB) -> int` 一致？返回 int 语义（实际应用数）一致？
5. **CLI review manifest / apply 行为**：与文档描述一致？
6. **manifest JSON 序列化/反序列化**：文档描述的格式与 CLI `review manifest` 输出一致？

## 方法

对每一条：
1. 在 DETAILED §5.6 中找到对应声明
2. 在 `review_manifest.py` 中 grep/read 实际代码
3. 记录不一致（如果有）
4. 有分歧时参考概览稿 `v0.7.md` §5.6

## 验收标准

1. 输出不一致清单（无变化则声明一致）
2. 如有不一致：按「文档漂移同步」惯例（c4ce2b1/747f6f8 先例）修正文档
3. ruff 干净
