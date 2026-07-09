# v0.4 AMEND 测试评审报告

**总体结论：** 4 项设计(C1-C4)均可测试，无不可测障碍；C1 C3 需补充 mock 策略细节，C4 需补充回归验证。

- [x] **C1 4 层泄漏防御** — L1(基线中位数)/L4(探针重叠)为纯函数，可独立单元测试；L2 是配置常量，无测试价值；L3(超阈重 OCR)需 mock VLM 返回超长文本并验证重试逻辑。泄漏注入：构造第 N 页末尾混入第 N+1 页首 300 字，通过 L4 断言截断到泄漏起始位置前。

- [ ] **C2 原子写入** — `atomic_write` 可测：mock `tmp.write_text` 成功后让 `os.replace` 抛出异常，断言 `.tmp` 残留 + 目标文件未更新；`is_complete` 空文件/存在/缺少 3 态直接测。**需补充**：`atomic_write` 调用侧（`run.py`/`_run_vlm`）的中断恢复集成测试 case 未说明。

- [x] **C3 限流器** — mock 503 响应 → 断言 `interval ×2`（上限 60s）；连续 5 次 200 → `interval ×0.9`（不低于 base）。`Retry-After` header 解析 + 1s 安全边际：mock header 验证实际等待时间。`MultiTokenRateLimiter` 80% 阈值：填充配额到 480/600 后断言下一次 acquire 阻塞。

- [ ] **C4 ON CONFLICT DO UPDATE 修复** — 验证路径清晰：1) 插入完整行；2) 用部分字段 `INSERT ... ON CONFLICT ... DO UPDATE SET col_x=COALESCE(...)`；3) 查询所有字段，断言未提供的列保持原值而非 NULL。**需补充**：`test_pipeline.py` 中 mock DB 的测试是否覆盖了 `book_metadata`/`content_node` 表——如有，需更新 mock 数据匹配新模式。

- [x] **整体评估** — 预计新增约 20-26 个测试(C1: 8-10, C2: 4-5, C3: 6-8, C4: 2-3)，对现有 41 测试回归风险低(C1-C3 为新增独立模块，C4 需验证 `test_pipeline.py` 的 5 个 mock DB 测试是否因 SQL 语句变更需同步更新)。
