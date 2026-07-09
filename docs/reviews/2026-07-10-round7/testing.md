# Testing Review — Round 7 (v0.5-rc2 修订后评估)

> 评估目标：v0.5-rc2 修订后（4 异常类型 + `@dataclass RetryPolicy` + D2 消费 D1 + D3 engine_tag/config_hash/TTL + D4 expected_depth 参数化），测试策略与用例估计的增量变化。

---

## D1 —— 异常分类 + `retry_with_policy`

**原估计（round 6）：32–42 用例**

**修订后分析**：异常类型从多类型缩减到 4 个实体类（ApiError / RateLimitedError / OverSizeError / RetryExhaustedError）+ 1 基类（OcrError），`RetryPolicy` 从 `dict` 升级为 `@dataclass`。用例分布：

| 类别 | 用例数 | 说明 |
|------|--------|------|
| Exception 构造 + 继承 | ~9 | 每类型构造(4) + 基类构造(1) + isinstance 验证(2) + except OcrError 捕获全部(1) + RetryExhaustedError 包装 cause(1) |
| RetryPolicy @dataclass | ~4 | 默认值(1) + 自定义值(1) + 命名策略表完整性(1) + 非法 strategy 拒绝(1) |
| retry_with_policy 成功路径 | ~5 | 立即成功(1) + exponential 1→成功(1) + exponential 2→成功(1) + reocr 成功(1) + reocr with retry_kwargs(1) |
| retry_with_policy 耗尽路径 | ~3 | exponential 耗尽(1) + on_exhausted 回调(1) + "none" 策略(1) |
| error_types 过滤 | ~2 | 不在 error_types 中不重试(1) + 在 error_types 中重试(1) |
| RateLimitedError + Retry-After | ~2 | 无 header 走退避(1) + 有 header 覆盖延迟(1) |
| ExponentialBackoff | ~3 | 递增延迟(1) + max_delay 上限(1) + jitter 随机性(1) |
| **D1 小计** | **~28** | |

**结论：用例数从 32–42 降低到 ~28。** 主要节省来自异常类型减少（节约 ~6–8 用例），但 `retry_with_policy` 作为独立公共函数增加了结构化测试（抵消约 2–4 用例）。

**建议**：28 用例是合理的乐观下界；考虑到 jitter 随机性测试需要统计性断言（多次运行），建议保留 \+2 余量，**最终 D1 估计：28–32 用例**。

---

## D2 —— VLM 主循环重试 + 失败分类

**原估计（round 6）：~15 用例**

**修订后分析**：D2 现在通过 `retry_with_policy` 实现重试（不再手写循环）。重试逻辑的测试归属转移到 D1，D2 测试聚焦于 **集成/编排** 和 **contract 验证**：

| 类别 | 用例数 | 说明 |
|------|--------|------|
| 正常路径 | ~1 | 页正常 OCR 成功，retry_with_policy 不触发 |
| ApiError 重试路径 | ~2 | retry_with_policy 被以"api"策略调用(1) + 耗尽时 failed_pages 记录(1) |
| RateLimitedError 路径 | ~1 | retry_with_policy 被以正确 error_types 调用 |
| OverSizeError 重试路径 | ~3 | 触发 reocr 策略(1) + reocr 成功文本合并(1) + reocr 耗尽抛出 RetryExhaustedError(1) |
| failed_pages 记录 | ~2 | 正确记录 error type name(1) + 多页混合记录(1) |
| dead code 验证 | ~1 | errors.py 中 retry_with_policy 被实际引用（import 验证 + mock 调用计数） |
| **D2 小计** | **~10** | |

**结论：用例数从 ~15 降低到 ~10。** 重试机制本身的测试已由 D1 覆盖。D2 测试策略从"单元测试重试逻辑"转变为"集成验证 D1 被 D2 正确消费"。

**关键关注点**：
- `retry_with_policy` 被调用时传递了正确的 `RETRY_POLICIES` 键（"api" / "oversize"）和 `error_types`
- `on_exhausted` lambda 捕获的 `pn` 值正确（D2 使用 `lambda pn, exc` — 注意 `pn` 是 page number 而非 page index）
- baseline.feed() 在 reocr 成功后调用，且输入的是"已重试的文本"，不是原始超阈文本

---

## D3 —— VLM 断点续跑

**原估计（round 6）：~6 用例**

**修订后新增测试需求**：

| 新增特性 | 测试需求 |
|---------|---------|
| engine_tag 参与路径 | 不同 engine_tag 对应不同缓存目录，不会误命中 |
| config_hash 元数据校验 | 匹配 → 缓存有效(1)；不匹配 → 缓存无效(1)；缺失/损坏 → 无效(1) |
| SHA256[:16] 哈希截断 | 配置变化（engine/max_tokens/prompt）→ 旧缓存被拒绝(1) |
| TTL 生命周期 | 新鲜缓存有效(1)；过期缓存重 OCR(1) |
| KZOCR_CLEAR_CACHE=1 | 全量清理(1) |
| TOCTOU 防护 | is_complete 真但文件在读取前被删除(1) |

| 类别 | 用例数 | 说明 |
|------|--------|------|
| 基本缓存命中/未命中 | ~2 | 缓存匹配跳过(1) + 无缓存走 OCR(1) |
| engine_tag 区分 | ~2 | 不同 tag 不同路径(1) + tag 含特殊字符/路径穿越被 C2 阻断(1) |
| config_hash 验证 | ~3 | 匹配有效(1) + 不匹配无效(1) + 损坏/缺失无效(1) |
| TTL | ~2 | 缓存有效期内跳过(1) + 过期重 OCR(1) |
| 清理 | ~1 | KZOCR_CLEAR_CACHE=1 删除所有缓存 |
| 中断恢复 | ~1 | 中断后重新运行，已缓存页跳过 |
| TOCTOU | ~1 | 文件在 check 和 read 之间消失 |
| **D3 小计** | **~12** | |

**结论：用例数从 ~6 大幅增加到 ~12。** engine_tag / config_hash / TTL 三项实质性地增加了验证维度。

**关键关注点**：
- TOCTOU 测试需要 mock 文件系统操作（`is_complete` 返回 True 但后续 `read_text` 抛出 OSError）
- TTL 测试需要 mock `time.time()` 或等效的时间快进机制
- config_hash 的计算范围需与方案一致：含 engine 标识、max_tokens、VLM prompt 等影响输出的参数

---

## D4 —— 层级异常检测（P3 低优先）

**原估计（round 6）：~0（此前未细化）**

**expected_depth 参数化后新增测试需求**：

| 类别 | 用例数 | 说明 |
|------|--------|------|
| 默认 depth=2 | ~2 | 3 段编号触发异常(1) + 2 段编号无异常(1) |
| 显式 depth=3 | ~2 | 3 段编号无异常(1) + 4 段编号触发异常(1) |
| 边界/空输入 | ~1 | 无编号文本 → 空结果 |
| 输出格式 | ~1 | JSON 写入路径正确 |
| **D4 小计** | **~6** | |

**结论：新增 ~6 用例。** D4 是独立工具函数，测试简单直接。注入式参数化 pytest 即可覆盖（1 个 test + 多组 parametrize）。

---

## 冲突-2 修正影响（C1 L3 移除）

C1 L3 日志标记重 OCR 被 D2 接管。现有 leakage 测试中若有覆盖 L3 日志输出断言的，需对应调整（移除或改为验证 L3 不再出现）。

**操作**：
- 搜索现有 leakage 测试中 `apply_leakage_defense` 相关的日志断言
- 若仅验证日志输出 → 移除
- 若有依赖 L3 返回值的逻辑 → 确认 D2 接管后这部分返回值不再产生，调整断言

**不需要新增额外用例**——D2 测试已覆盖重 OCR 逻辑。只需修改/删除与被移除 L3 对应的现有断言。

---

## 汇总测试用例估计

| 项 | Round 6 估计 | Round 7 估计 | Δ |
|---|:---:|:---:|:---:|
| D1 异常分类 + retry_with_policy | 32–42 | **28–32** | -4~-10 |
| D2 VLM 主循环重试 | ~15 | **~10** | -5 |
| D3 VLM 断点续跑 | ~6 | **~12** | +6 |
| D4 层级异常检测 | ~0 | **~6** | +6 |
| 冲突-2 测试调整 | ~0 | **~0** (修改现有) | — |
| **合计** | **53–63** | **56–60** | **基本持平** |

**总用例数范围：56–60。** 虽有显著结构调整，但 D1/D2 的节省被 D3/D4 的扩增抵消，总量变化不大。

---

## 测试风险标注

| 风险 | 影响项 | 建议缓解 |
|------|--------|---------|
| D1 + D2 实施人脱节 | D1 的 `retry_with_policy` 签名或行为与 D2 期望不一致 | 计划已要求同人实施；测试阶段 D2 应先用 mock 验证 contract，再切真实实现 |
| TOCTOU 测试不可靠 | D3 | 使用 `unittest.mock.patch` 模拟文件系统行为，不依赖真实文件竞争 |
| TTL 测试依赖系统时钟 | D3 | 用 `time_machine` 或 `freezegun` 冻结时间，避免 CI 环境的时钟漂移 |
| config_hash 计算范围漂移 | D3 | 显式列出参与 VLM_PARAMS_JSON 的字段清单，测试覆盖"增加字段→哈希变化" |
| jitter 随机性测试 | D1 | 统计方式：多次调用验证延迟落在 `[0.8×delay, 1.2×delay+jitter]` 范围内，不断言具体值 |
