# KZOCR 统一 OCR 引擎架构方案 —— v0.4 AMEND（TOC 项目实战经验吸收）

> 本文件是 `ocr-engine-unification.v0.3-FREEZE.md` 的**增量修订**。v0.3 FREEZE (B1–B8) 维持有效，此处仅追加 C1–C5 共 5 项从另一个 TOC OCR 项目（970 页真实中医书验证）吸收的设计决策。
> 来源项目：`/home/keen/Documents/trae_projects/traedocu/docs/`

---

## C1 —— VLM 跨页内容泄漏 4 层防御系统

**来源：** TOC 项目在 970 页上验证的 VLM 双页上下文泄漏防御。发现"泄漏模式从页面 50% 位置开始，页末检测完全漏报"。

**问题：** KZOCR 当前 `_merge_cross_page_breaks()` 只做句末标点检测，无动态基线/重叠检测。VLM 双页上下文（当前页 + 下一页顶部 15%）的跨页内容泄漏（模型输出下一页内容）未被防御。

**裁决：** 升级 VLM 泄漏检测为 4 层系统（落地 `kzocr/engines/leakage.py`）：

```
L1: Prompt 约束 + 动态字符基线
  - 前 50 页字符数中位数作为 baseline
  - Prompt 动态注入："本页文字通常在 {baseline} 字左右"
  - 阈值 threshold = baseline × 1.5

L2: max_tokens=2048 物理上限（从 4096 下调）
  - 减少模型"过度输出"倾向

L3: 超阈自动重 OCR
  - 若 char_count > threshold → 用更紧的 max_tokens=baseline×1.8 重 OCR
  - 最多重试 1 次

L4: 增量探针重叠检测（合并阶段）
  - probe = normalize(norm_b[:plen]), plen 从 50 到 300, step=10
  - 若 probe 在 norm_a 中出现且起始位置 > 30% → 判泄漏
  - 截断 norm_a 到泄漏起始位置
```

**效果目标：** 零漏报 + 零误报（TOC 项目 970 页已验证）。

**测试要求：**
- 前 50 页基线计算 → 断言 baseline 中位数正确
- L1–L4 每层独立单元测试
- 泄漏注入测试：含第 N 页内容末尾故意混入第 N+1 页开头 → 断言截断

---

## C2 —— 原子写入 + 文件存在性断点续传

**来源：** TOC 项目验证 `write(tmp)→os.replace(tmp, target)` 模式，发现 `pipeline_state.json` 是过度设计。

**问题：** KZOCR 的 VLM 路径和 TOC 管线无恢复机制。中断后必须重头开始。

**裁决：**
- 新增 `kzocr/engines/atomic.py` 工具模块：
  ```python
  def atomic_write(path: Path, content: str) -> None:
      """原子写入：先写 .tmp 再 os.replace。"""
      tmp = path.parent / (path.name + ".tmp")  # 防 path.with_suffix 丢失无后缀路径
      tmp.write_text(content, encoding="utf-8")
      os.replace(tmp, path)

  def is_complete(path: Path) -> bool:
      """文件存在 + 非空 = 已完成。"""
      return path.exists() and path.stat().st_size > 0
  ```
- 不再使用 `pipeline_state.json` — 文件存在即状态
- TOC 管线每节/每页完成后原子写目标文件；重启时检查跳过已完成的
- 现有 `run.py` 中 `_run_vlm` 的 `tmp_path` 模式升级为此统一工具

**效果目标：** 任意中断恢复，零半写文件。

---

## C3 —— 自适应 3 层限流器

**来源：** TOC 项目处理 7000+ API 调用失败率 <1%。发现令牌桶允突发 → 触发 503，固定间隔更可靠。

**问题：** KZOCR 的 CloudLLMClient 和 SenseNovaAdapter 无限流。`modelscope_pool.py` 有令牌桶逻辑但未复用。

**裁决：** 新增 `kzocr/engines/ratelimit.py`：

```
Layer 1 (固定间隔 + 自适应): AdaptiveRateLimiter
  - base_interval: 3.0s （可配置）
  - 503/429: interval × 2（上限 60s）
  - 连续 5 次成功: interval × 0.9（不低于 base）

Layer 2 (多 Token): MultiTokenRateLimiter
  - 公司级配额共享: 600 req/min
  - 80% 使用触发主动等待

Layer 3 (退避重试): ExponentialBackoff
  - base_delay: 2s, max_retries: 5, max_delay: 300s
  - jitter: 0-50% 随机
  - 尊重 Retry-After header + 1s 安全边际
```

- 迁移 `modelscope_pool.py` 中的令牌桶逻辑到此统一模块
- CloudLLMClient.generate() / SenseNovaAdapter 集成 AdaptiveRateLimiter
- TOC 管线 DeepSeek 后处理集成 MultiTokenRateLimiter

---

## C4 —— `INSERT OR REPLACE` 陷阱修复

**来源：** TOC 项目 bug：`INSERT OR REPLACE` = `DELETE + INSERT`，未提供的列被 NULL 覆盖。

**问题：** v1.1 `book_pipeline.py:880,936` 对 `book_metadata` 和 `content_node` 使用 `INSERT OR REPLACE`。如果后续只更新部分字段，丢失的数据不可恢复。

**裁决：** 立即修复（不等待评审）：
- `book_metadata` 写：`INSERT OR REPLACE` → `INSERT INTO ... ON CONFLICT(book_id) DO UPDATE SET ...` 逐字段保护
  - **mutable 字段**（`title`, `author`, `pub_year`, `page_start`, `page_end`, `total_pages`, `ocr_version`, `confidence`）：直接 `SET col=EXCLUDED.col`
  - **immutable 字段**（`created_at`, `book_id`）：`SET col=COALESCE(EXCLUDED.col, col)` 保护已有值不被 NULL 覆盖
- `content_node` 写：同上，按 `node_id` 冲突处理
- v1.0 frozen 同步修复

---

## C5 —— TOC 驱动管线补充设计

**来源：** TOC 项目验证了"TOC 是权威边界，覆盖 OCR 推断边界"的核心思路。

**问题：** `docs/plans/toc-driven-pipeline-design.md` 已有设计但未集成到主计划，也未吸收 TOC 项目的页码偏移处理经验。

**裁决：** 将 TOC 管线集成到 v0.3 §7 阶段路线：

- **TOC 渐进扫描策略：** `scan_ranges = [[1,5], [1,10], [1,21]]` 而非固定前 N 页
- **页码偏移处理：** `start_pg = book_start + page_offset`（TOC 项目 OCR-BUG-006 教训）
- **TOC 为权威边界：** 章节起止页由 TOC 决定，覆盖任何 OCR 推断
- 集成到 v0.3 §7 阶段路线作为**阶段 2.5**（介于阶段 2 和阶段 3 之间）

---

## 实施顺序更新（基于 v0.3 + C1–C5）

| 优先级 | 项 | 文件/模块 | 说明 |
|--------|-----|-----------|------|
| **P0** | C4 修复 | `book_pipeline.py` | INSERT OR REPLACE 陷阱，立即修 |
| **P1** | Stage 1 (B4) | `to_zai_prisma.py` | is_mock sink 守卫（v0.3） |
| **P1** | C2 原子写入 | `kzocr/engines/atomic.py` | 基础工具，后续所有阶段依赖 |
| **P2** | C1 泄漏防御 | `kzocr/engines/leakage.py` | VLM 4 层防御，升级 run.py |
| **P2** | C3 限流器 | `kzocr/engines/ratelimit.py` | 统一限流，CloudLLM + SenseNova 集成 |
| **P2.5** | C5 TOC 管线 | `pipeline/toc_*.py` | TOC 分析 + 并行 OCR 管线 |
| **P3** | Stage 2 (B2/B3/B8) | Router + egress | v0.3 阶段 |
| **P4** | Stage 3 (B1/B5) | GlyphVerifier + 资源 | v0.3 阶段 |

---

## v0.4 评审要求（6 角色）

| 角色 | 评审重点 |
|------|---------|
| **架构师** | C1 与现有 VLM 路径兼容性；C5 TOC 管线与 Router 边界 |
| **测试** | C1 泄漏测试策略；C2 断点续传可测性；C4 修复验证方案 |
| **安全** | C3 限流器键管理；L1 prompt injection 防护 |
| **性能** | C1 4 层防御每层开销；C3 限流器吞吐影响；C2 atomic_write 批量性能 |
| **领域** | C5 TOC 对中医书籍的适用性；C4 数据库字段保护策略 |
| **软件工程** | C2 实现模式；C3 降级链集成；代码质量与向后兼容 |
