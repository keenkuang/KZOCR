# KZOCR v0.4 AMEND — round5 多角色评审汇总

> 评审对象：`docs/plans/ocr-engine-unification.v0.4-AMEND.md`（5 项新增决策 C1–C5）
> 评审角色：架构师 / 测试 / 安全 / 性能 / 软件工程 / 领域
> 日期：2026-07-10

---

## 总体裁决

**有条件通过（6/6 角色均为有条件通过）。** 无拒绝项。

| 角色 | 裁决 | 修订数 |
|------|------|--------|
| **架构师** | ✅ 有条件通过 | 4 项（High×1, Medium×3） |
| **测试** | ✅ 有条件通过 | 2 项（Medium×2） |
| **安全** | ✅ 有条件通过 | 1 项（Medium×1） |
| **性能** | ✅ 有条件通过 | 2 项（High×1, Medium×1） |
| **领域** | ✅ 有条件通过 | 2 项（Medium×2） |
| **软件工程** | ✅ 有条件通过 | 3 项（Medium×2, Low×1） |
| **合计** | ✅ **有条件通过** | **14 项修订**（High×2, Medium×11, Low×1） |

---

## 必须修订项（定稿前）

### High — 2 项

| # | 来源 | 问题 | 要求 |
|---|------|------|------|
| H1 | 性能 | **C3 6s interval + MAX_PAGES=50 + TOTAL_TIMEOUT=7200s 不自洽** | 500 页 × ~16s/页 = 8000s > 7200s。建议 C3 interval 从 6s 降到 2-3s，或提高 TOTAL_TIMEOUT 到 9600s |
| H2 | 架构 | **P2.5 (TOC 管线) 在 P3 之前，绕开 Router 降级链** | TOC 管线直接实例化 SenseNovaAdapter，产生"双路径"架构债务。推荐 P2.5 移到 P3 之后，或预留 Router 替换接口 |

### Medium — 11 项

| # | 来源 | 问题 | 要求 |
|---|------|------|------|
| M1 | 架构 | C1 L3 + C3 Layer 3 退避堆叠（潜在 6 次 API 调用） | 统一重试链上下文，总上限在 C1 层控制 |
| M2 | 架构 | C3 Layer 2 600 req/min 与 DeepSeek 500 req/5h 不匹配 | C3 支持按目标域配置不同速率 |
| M3 | 架构 | C5 TOC 管线适配器实例化跳过 egress 校验 | 必须通过 EngineRouter 或 BaseAdapter.create() 工厂 |
| M4 | 测试 | C2 缺少中断恢复集成测试用例定义 | `_run_vlm` / `section_ocr` 的中断恢复测试 |
| M5 | 测试 | C4 修复后 mock DB 测试需确认断言是否需要更新 | 检查 `test_pipeline.py` 因 SQL 变更的潜在影响 |
| M6 | 安全 | C3 限流器状态生命周期未声明 | 推荐 SQLite/Redis 持久化 + max_entries 上限守卫 |
| M7 | 性能 | C3 MAX_CONCURRENCY=1 与 C5 TOC 4 节并行冲突 | 需区分 cloud 限流与 local 并行，不互相阻塞 |
| M8 | 领域 | C4 COALESCE 一视同仁保护所有字段，阻塞了元数据迭代 | 增加 `immutable`/`mutable` 字段分类（如 `page_start` 应允许更新） |
| M9 | 领域 | C5 `_parse_toc_text` 页码正则需用 `\d+` 非 `\d{1,3}` | 防 4 位页码被截断 + 加 `page_start + page_offset ≤ page_count` 断言 |
| M10 | 软件工程 | C2 atomic_write 需对齐 fsync/目录创建/清理 | 加目录自动创建, atomic_write 确保 sync, 临时文件清理机制 |
| M11 | 软件工程 | C1 建议拆出 `leakage.py` 而非合入 `run.py` | 新建独立模块，4 层分离 |

### Low — 1 项

| # | 来源 | 问题 |
|---|------|------|
| L1 | 软件工程 | C3 `modelscope_pool.py` 令牌桶应迁移到新 `ratelimit.py` 而非复制 |

---

## 通过项

- **C2 `os.replace` 路径穿越安全** → 安全评审已确认低风险，仅建议加 `resolve()` 校验
- **C1 L1 prompt 约束安全** → 基线来自视觉统计而非 PDF 文本，无 prompt injection 面
- **C1 L4 重叠探针误报率** → 领域评审确认 TCM 领域风险可接受，建议加排除词字典
- **C4 修复方向** → 所有角色确认正确
- **C5 渐进扫描范围** → 领域评审确认 `[1,5],[1,10],[1,21]` 合理
- **C1 性能影响** → 4 层合计 <5% 退化（L1/L2/L4 <1ms, L3 +5-10%）

---

## 快速跟进（可并行执行，不限后果）

1. **C4 修复** → 已在 `kimi_agent_ocr` 提交 `d833cb4`，无需再议
2. **H1 interval 下调** → v0.4 AMEND 中 C3 base_interval 从 6s 改为 3s
3. **M9 页码正则防截断** → v0.4 AMEND 中 C5 文档更新
4. **M8 immutable/mutable 字段分类** → 更新 C4 裁决
5. **L1 modelscope_pool 迁移** → 在 C3 实现时清理
