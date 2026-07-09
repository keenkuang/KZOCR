# 安全评审：ocr-engine-unification.v0.4-AMEND

**评审结论：** 4 项安全关注点中 3 项低风险通过，1 项（C3 DoS）需修订后通过。整体安全风险可控，无严重或高危发现。

---

- [x] **C1 L1 prompt 约束** — 通过（低）
  基线从已处理页的视觉 OCR 字符数计算，PDF 内容不注入 prompt 文本。恶意文本最多触发 L3 重 OCR（无害冗余），无法突破 prompt 约束本身。建议在 L1 注释中明确禁止将来将 PDF 元数据/文本串入 prompt。

- [ ] **C3 限流器 — 需修订（中）**
  API key 管理（仅环境变量）当前安全，但设计未提及 key 轮换或最小权限范围。主要风险在 DoS：(1) `MultiTokenRateLimiter` 若为每进程内存状态，单进程失效后配额丢失，重启冲刷不限流；(2) 无速率极限数据上限验证 — 恶意大批量请求可耗尽内存。**要求：** 声明限流器状态生命周期（建议 SQLite 持久化或共享 redis）；添加 `AdaptiveRateLimiter` 数据结构上限守卫（max_entries）。

- [x] **C2 `os.replace` 路径穿越** — 通过（低）
  `os.replace` 本身无穿路面。风险在调用侧：若 `path` 源自 PDF 文件名或用户输入，可构造 `../../` 穿越。设计文档中的用例（逐页/TOC 按节写目标文件）路径由系统依输出目录 + ID 构造，非用户可控。建议 `atomic_write` 内加 `resolve()` + 前缀校验。

- [x] **B3 egress allowlist 交互** — 通过（低）
  C3 限流器通信目标（sensenova.cn, api.deepseek.com, modelscope.cn）均在 B3 allowlist 中，无新出境面。限流器与 egress 守卫各司不同层，不冲突。建议 C3 实现时在文档中显式标注 B3 兼容，并确保限流器的健康检查/指标上报端点也尊重复用 `egress.py` 校验。
