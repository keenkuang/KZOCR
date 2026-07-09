# KZOCR 统一 OCR 引擎架构方案 —— v0.3 定稿冻结（B1–B8 裁决）

> 本文件是 `ocr-engine-unification.md`(v0.2) 的**定稿裁决**：round4（8 角色）指出 v0.2 "有条件定稿"，但须先冻结 8 个 blocker（B1–B8）才得进入阶段 1。此处给出**明确裁决**（不再是"二者择一"的待定表述）。
> 裁决来源：`docs/reviews/2026-07-09-round4/summary.md` §5（B1–B8）+ 各角色 round4 初稿。

## 裁决总原则
- 凡"文档层已接住、实现层待补"的项（如 is_mock sink、资源文件），**冻结为阶段 1/3 的首个实现切片**，不再悬决。
- 凡"二选一未决"的项（如双字段），**直接拍板**，消除架构师指出的"契约未冻结 → Router 重新单体化"风险。

---

## B1 —— `glyph_status` 与 `glyph_verified` 二选一（原 v0.2 §4.3 悬决）
**裁决：两者共存，职责切死，不再"择一"。**
- `Line.glyph_verified`：**保留为"校验后文本"**（text），与现有 `mock.py`/导出/落库/CLI 的文本消费完全兼容，不迁移任何消费方。
- 新增 `Line.glyph_status: Literal[PASS|RARE|UNKNOWN|FAIL|UNCERTAIN]`：**仅作判定枚举**，不存文本。
- 现有 `to_zai_prisma.py:153` 误把 `auditSource` 写成 `book.engine_label` 的 bug，在阶段 4 修。

## B2 —— `AdapterPageResult` → `LineResult` 转换责任悬空（架构师 residual ①）
**裁决：转换责任单点归 `EngineRouter` + `_common.py`。**
- `BaseAdapter.recognize_page` 只产出 `AdapterPageResult`（含 text/confidence/char_confidences/crop_img）。
- 新增 `_common.py: adapter_to_line_result(apr, engine_name) -> LineResult`，**唯一**把 `AdapterPageResult` 折算成 `LineResult`、填 `engine_texts[engine_name]=text`、`confidence`。
- `EngineRouter` 在汇集多引擎结果时调用它；共识比对（多引擎 `engine_texts` 合并）也在 Router 内完成。**任何适配器都不得自行折算 LineResult**，否则退回 run.py 单体化。

## B3 —— 出境 allowlist 治理缺位（可被 toml 膨胀绕过 SSRF，新引入绕过面）
**裁决：allowlist 是代码级硬常量，toml 不得增删出境目标。**
- 新增 `kzocr/security/egress.py`：`ALLOWED_EGRESS_DOMAINS = {"*.sensenova.cn","api.deepseek.com","*.modelscope.cn", ...}` 写死在代码里。
- 适配器 `*.toml` **只能配 host/port/model/timeout/enable**；云端 `base_url` 若不在 allowlist → 启动即拒绝（不靠 toml 治理，杜绝改 toml 绕过）。
- `khub/client.py:_validate_url` 扩展为统一出站校验入口，复用此 allowlist + DNS 复检 + 拒 RFC1918/回环外内网。

## B4 —— `is_mock` sink 端未落地（mock 桩仍可能无守卫重演"假古籍"）
**裁决：阶段 1 第一个实现切片，立即做。**
- `to_zai_prisma.py` 的 `Book` DDL **新增 `is_mock INTEGER` 列**（迁移：`ALTER TABLE Book ADD COLUMN is_mock INTEGER DEFAULT 0`）。
- `push_book_to_zai()` 入口：`if book.is_mock: logger.error("⚠ 阻断 publish：桩/降级假数据，不得入校对台"); return {"published": False, "blocked": "is_mock"}`（**阻断 publish**，与 round2 H8 对齐）。
- `BookResult.is_mock` 已在 `types.py`；`use_mock` 全链路回退与 Router 降级候选均须透传 `is_mock=True`。

## B5 —— 领域 4 个资源文件当前库里为空（首本影印书原样复现 UNKNOWN 淹没）
**裁决：KZOCR 以"内置种子资源"随包发布，非空。**
- 随包发布 4 个**最小但非空**的 JSON（`kzocr/resources/`）：
  - `variant_map.json`（繁→简 + 异体→正体，首批收明确等价项，如 黃→黄、參→参）
  - `confusion_set.json`（中医形似混淆，首批：莪术↔我术、黄芩↔黄芪、半夏↔半下、白木↔白术…）
  - `rare_allowlist.json`（罕见但正确中医字白名单，首批：萆薢、䗪虫…）
  - `toxic_herbs.json`（毒性药材 + 用量红线，首批：附子须炮制、细辛≤3g…）
- 启动时一次性进程内镜像；`KZOCR_TERM_KB_PATH` 仅作**可选叠加**且须校验位于受控目录。阶段 3 落地，但种子文件随 v0.3 提交（非空即生效）。

## B6 —— `TOTAL_TIMEOUT=7200s` 与 `MAX_PAGES=500` 数学互斥（大书单本跑不完）
**裁决：两闸解耦为"内存闸 vs 时间闸"，并给一致默认值。**
- `MAX_PAGES`：**内存/行数硬闸**，默认降到 **50**（CPU 无 GPU 下，50 页 @~120s/页 ≈ 6000s < 7200s，双闸自洽）。
- `TOTAL_TIMEOUT=7200s`：**wall-clock 总预算闸**，到点即停后续页、已识别页归档、未识别页转 HumanGate。
- 二者独立：一个管"太多页爆内存"，一个管"太久占机器"。文档明确此关系（原 v0.2 误写成互斥）。

## B7 —— `crop_img: np.ndarray` 长期驻留击穿内存闸（500 页≈2GB）
**裁决：crop_img 仅瞬态使用，存储改"引用"不存像素。**
- `AdapterPageResult.crop_img` 仅在本页 recheck/ UX 裁剪时瞬态存在；**入 LineResult/落库前丢弃**，不随 `engine_texts` 持久化。
- HumanGate 推送原图：改传 `(page_num, bbox)` 引用，校对台**按需**用 fitz 重新渲染该 bbox，而非存 ndarray。
- 这同时消除 B2 转换时"要不要把 ndarray 折进 LineResult"的歧义（答案：不折）。

## B8 —— 默认引擎反向（无 GPU → PaddleOCR，与 round3"无 GPU 唯一可行是 VLM"结论反向）
**裁决：无 GPU 下默认 VLM/视觉优先。**
- `EngineRouter` 默认 `prefer` 顺序修正为：`["paddleocr_vl16"(本地 llama-server 或 SenseNova key 可用时) , "sensenova", "paddleocr"]`。
- 即：无 GPU 且能跑 VLM（本地服务在听 / 有云端 key 且 `allow_cloud_vision`）→ 走 VLM 整页；纯无服务无 key 才兜底本地非视觉 PaddleOCR。原 v0.2 §3 "无 GPU→PaddleOCR" 表述删除。

---

## 定稿后实施顺序（冻结即生效）
1. **阶段 1 切片①（B4）**：`to_zai_prisma.py` 加 `is_mock` 列 + publish 守卫。
2. **阶段 1 切片②（B2/B3）**：`_common.py` 沉降 + `adapter_to_line_result` + `egress.py` allowlist + `BaseAdapter` 可观测性 + 降级收口 Router。
3. **阶段 2（B8/B6/B7 部分）**：`EngineRouter` + `probe` 纯函数 + 性能预算（MAX_PAGES=50/TOTAL_TIMEOUT/并发1/重试熔断）+ VLM 优先默认。
4. **阶段 3（B1/B5/B7）**：`GlyphVerifier`（glyph_status 枚举 + 种子资源文件镜像 + RARE 态 + VisionRecheck 挂点）。
5. **阶段 4（B1/B4 收尾）**：`HumanGate` UNKNOWN 触发 + 原图 bbox 引用 + severity + auditSource 修正。
6. **阶段 5**：归档落到规范 `schema.prisma`（ContentNode/FinalDocumentRecord/FormulaComposition + 幂等 MERGE）。

> 方案 v0.2 其余未改动处（分层架构、适配器清单、6 项假设裁决）维持有效。本文件即"定稿"，进入实施不再有悬决项。
