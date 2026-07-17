# BookDB 数据库接口说明

> 更新时间：2026-07-17

## 概述

`kzocr/storage/db.py` 的 `BookDB` 类管理单书 SQLite 数据库。每本书（book_code）对应一个 `.db` 文件，
默认路径 `$KZOCR_DB_DIR/{book_code}.db`，默认目录为 `$PWD/db/`。

**数据库文件使用 WAL 模式 + 外键约束**（`PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;`）。

## 表结构

### page_progress — 逐页进度追踪

| 列 | 类型 | 约束 | 说明 |
|----|------|------|------|
| `id` | INTEGER | PK AUTOINCREMENT | 自增主键 |
| `page_num` | INTEGER | NOT NULL UNIQUE | 页号 |
| `char_count` | INTEGER | DEFAULT 0 | 字符数 |
| `ocr_status` | TEXT | CHECK('pending','processing','success','failed','skipped') | OCR 阶段状态 |
| `ocr_attempts` | INTEGER | DEFAULT 0 | OCR 尝试次数 |
| `ocr_elapsed_ms` | INTEGER | DEFAULT 0 | OCR 耗时(ms) |
| `ocr_error` | TEXT | DEFAULT '' | OCR 错误信息 |
| `verify_status` | TEXT | CHECK('PENDING','PASS','RARE','UNCERTAIN','FAIL','UNKNOWN','SKIPPED') | 校验状态 |
| `verify_details` | TEXT | DEFAULT '' | 校验详情 |
| `import_status` | TEXT | CHECK('pending','imported','failed','skipped') | 导入状态 |
| `import_count` | INTEGER | DEFAULT 0 | 导入计数 |
| `import_error` | TEXT | DEFAULT '' | 导入错误 |
| `engine_label` | TEXT | DEFAULT '' | 引擎标签 |
| `created_at` | TEXT | DEFAULT datetime('now') | 创建时间 |
| `updated_at` | TEXT | DEFAULT datetime('now') | 更新时间 |

### hierarchy_anomaly — 验证异常记录

| 列 | 类型 | 约束 | 说明 |
|----|------|------|------|
| `id` | INTEGER | PK AUTOINCREMENT | 自增主键 |
| `page_num` | INTEGER | NOT NULL | 页号 |
| `verdict_status` | TEXT | NOT NULL | 裁决状态（FAIL/UNKNOWN/UNCERTAIN 等） |
| `detector_chain` | TEXT | DEFAULT '' | 检测器链（逗号分隔，如 "CrossAlign,ConfusionKeyPresence"） |
| `details` | TEXT | DEFAULT '' | 结构化详情 |
| `resolution` | TEXT | CHECK('pending','confirmed','fixed','wontfix') | 决议状态 |
| `note` | TEXT | DEFAULT '' | 备注 |
| `created_at` | TEXT | DEFAULT datetime('now') | 创建时间 |
| `updated_at` | TEXT | DEFAULT datetime('now') | 更新时间 |

### cross_divergence — 跨引擎分歧

| 列 | 类型 | 约束 | 说明 |
|----|------|------|------|
| `id` | INTEGER | PK AUTOINCREMENT | 自增主键 |
| `page_no` | INTEGER | NOT NULL | 页号 |
| `div_type` | TEXT | NOT NULL | 分歧类型（replace/delete/insert） |
| `a_seg` | TEXT | DEFAULT '' | 引擎 A 侧片段 |
| `b_seg` | TEXT | DEFAULT '' | 引擎 B 侧片段 |
| `a_context` | TEXT | DEFAULT '' | 上下文（分歧处【】标出） |
| `boxes` | TEXT | DEFAULT '[]' | 字符 box 列表（JSON 字符串） |
| `priority` | TEXT | DEFAULT 'normal' | 优先级（high/normal） |
| `status` | TEXT | DEFAULT 'pending' | 状态（pending/arbitrated/accepted_a/accepted_b/both_wrong/skipped） |
| `engine_a` | TEXT | DEFAULT '' | 引擎 A 名称 |
| `engine_b` | TEXT | DEFAULT '' | 引擎 B 名称 |
| `created_at` | TEXT | DEFAULT datetime('now') | 创建时间 |

### benchmark_results — 基准测试汇总

| 列 | 类型 | 约束 | 说明 |
|----|------|------|------|
| `id` | INTEGER | PK AUTOINCREMENT | 自增主键 |
| `book_code` | TEXT | NOT NULL | 书籍编码 |
| `engine` | TEXT | DEFAULT '' | 引擎名称 |
| `total_pages` | INTEGER | DEFAULT 0 | 总页数 |
| `success_pages` | INTEGER | DEFAULT 0 | 成功页数 |
| `fail_pages` | INTEGER | DEFAULT 0 | 失败页数 |
| `error_rate` | REAL | DEFAULT 0.0 | 错误率 |
| `total_latency_ms` | INTEGER | DEFAULT 0 | 总延迟(ms) |
| `latency_p50_ms` | REAL | DEFAULT 0.0 | P50 延迟(ms) |
| `latency_p95_ms` | REAL | DEFAULT 0.0 | P95 延迟(ms) |
| `pages_per_min` | REAL | DEFAULT 0.0 | 每分钟页数 |
| `total_elapsed_s` | REAL | DEFAULT 0.0 | 总耗时(s) |
| `created_at` | TEXT | DEFAULT datetime('now') | 创建时间 |

### quality_result — 质检结果

| 列 | 类型 | 约束 | 说明 |
|----|------|------|------|
| `id` | INTEGER | PK AUTOINCREMENT | 自增主键 |
| `recipe_no` | TEXT | NOT NULL UNIQUE | 配方编号（UPSERT 键） |
| `status` | TEXT | NOT NULL | 质检状态 |
| `confidence` | REAL | DEFAULT 1.0 | 置信度 |
| `issues_json` | TEXT | DEFAULT '[]' | 问题列表（JSON 字符串） |
| `created_at` | TEXT | DEFAULT datetime('now') | 创建时间 |

## BookDB 方法

### 构造 / 生命周期

```python
def __init__(self, book_code: str, db_dir: str = "") -> None
```
- 打开或创建 `{db_dir}/{book_code}.db`
- `db_dir` 默认取 `$KZOCR_DB_DIR`，再回退到 `$PWD/db/`
- 自动建表（`create_schema()`）

```python
def create_schema(self) -> None
```
- 执行 `_SCHEMA_SQL` 建所有 5 张表（幂等，CREATE TABLE IF NOT EXISTS）

```python
def close(self) -> None
```
- 关闭 SQLite 连接

```python
def vacuum(self) -> None
```
- 执行 VACUUM 回收空间

### page_progress 操作

```python
def init_page(self, page_num: int, char_count: int = 0, engine_label: str = "") -> None
```
- 插入一条进度记录（INSERT OR IGNORE，幂等）

```python
def update_ocr(self, page_num: int, *, status: str, char_count: int = 0,
               error: str = "", latency_ms: int = 0, attempts: int = 1) -> None
```
- 更新 OCR 阶段状态

```python
def update_verify(self, page_num: int, *, verdict: str, details: str = "") -> None
```
- 更新校验阶段状态

```python
def update_import(self, page_num: int, *, status: str, count: int = 0, error: str = "") -> None
```
- 更新导入阶段状态

```python
def get_page_progress(self, page_num: int) -> Optional[dict[str, Any]]
```
- 获取单页进度记录

```python
def get_all_progress(self, *, status_filter: Optional[str] = None) -> list[dict[str, Any]]
```
- 获取全部进度记录，可选按 `ocr_status` 过滤

### hierarchy_anomaly 操作

```python
def record_anomaly(self, page_num: int, verdict: GlyphVerdict,
                   detector_chain: Optional[list[str]] = None) -> None
```
- 记录一条验证异常。`detector_chain` 存储为逗号分隔字符串

```python
def get_anomalies(self, *, status_filter: str = "pending") -> list[dict[str, Any]]
```
- 读取异常记录，默认只取 `resolution='pending'` 的未处理记录

```python
def get_unresolved_anomalies(self, book_code: str = "", *, limit: int = 50) -> list[dict[str, Any]]
```
- 联表 `page_progress` 获取待处理异常（含 char_count/engine_label 上下文）

```python
def resolve_anomaly(self, anomaly_id: int, resolution: str, note: str = "") -> None
```
- 标记异常决议。`resolution` 取值：`confirmed` / `fixed` / `wontfix`

### cross_divergence 操作

```python
def write_cross_divergences(self, page_no: int, divs: list,
                             engine_a: str = "", engine_b: str = "") -> int
```
- 写入 `kzocr.scheduler.cross_align.Divergence` 列表。返回写入行数

```python
def get_cross_divergences(self, page_no: Optional[int] = None,
                           priority: Optional[str] = None) -> list[dict[str, Any]]
```
- 读取分歧，可选按 `page_no` / `priority` 过滤，按 id 升序

```python
def update_cross_divergence_status(self, page_no: int, div_type: str, a_seg: str,
                                    b_seg: str, status: str) -> int
```
- 以 `(page_no, div_type, a_seg, b_seg)` 定位并更新分歧状态。返回更新的行数
- 用于视觉仲裁后持久化裁决（accepted_a/accepted_b/both_wrong/manual 等）

### benchmark 操作

```python
def write_benchmark(self, book_code: str, engine: str, total_pages: int,
                     success_pages: int, fail_pages: int, total_latency_ms: int,
                     total_elapsed_s: float) -> None
```
- 写入单引擎的 benchmark 汇总记录。自动计算 error_rate / pages_per_min / latency_p50

### quality_result 操作

```python
def save_quality_result(self, recipe_no: str, status: str, confidence: float = 1.0,
                         issues_json: str = "[]") -> None
```
- 写入单条质检结果。`recipe_no` 为主键，使用 INSERT OR REPLACE（UPSERT）

```python
def get_quality_results(self, *, status_filter: Optional[str] = None) -> list[dict[str, Any]]
```
- 读取质检结果，可选按 `status` 过滤

## 检测器链命名约定

`record_anomaly` 的 `detector_chain` 参数使用以下链名：

| 链名 | 来源 | 说明 |
|------|------|------|
| `CrossAlign` | `orchestrator.py` | 跨引擎分歧比对（high 分歧入 M4 队列） |
| `ConsensusErrorArbitration` | `orchestrator.py` | 共识一致页抽样送 VL 仲裁 |
| `ConfusionKeyPresence` | `verifier.py` | 一级高危基准字前置筛查 |
| `ConfusionSetDetector` | `verifier.py` | 形近字黑名单命中 |
| `PhraseErrorDetector` | `verifier.py` | 词组错扫描 |
| `ToxinDoseDetector` | `verifier.py` | 剂量安全检测 |
| `LeakageDetector` | `verifier.py` | 跨页泄漏检测 |
| `CharCountSpikeDetector` | `verifier.py` | 字符数尖峰异常 |

## 分歧状态流转

```
pending → arbitrated → accepted_a  (VL 确认引擎 A 正确)
                      → accepted_b  (VL 确认引擎 B 正确)
                      → both_wrong  (VL 给出第三字，两者皆错)
                      → manual      (VL 无法判定，强制人工)
                      → skipped     (跳过)
```
