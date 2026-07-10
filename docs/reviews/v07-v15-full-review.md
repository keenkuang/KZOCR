# 多角色架构评审：v0.7 → v0.15 全量变更

> 评审日期：2026-07-17
> 评审范围：23 commits, v0.7（tag `5e74869`）至 v0.15（tag `e032cb8`）
> 上次评审：`docs/reviews/toc_plan_review_r1.md`（F1 TOC 方案）
> 参考文档：设计文档 `docs/plans/`、部署文档 `docs/deploy-v07.md`

---

## 一、架构师 (Architect) 视角

### ✅ 正面

1. **模块边界清晰演进**：从 v0.7 的 `scheduler/`（编排层）→ v0.8 `analysis/`（内容理解）→ v0.9 `web/`（展示层），三层架构（engine→analysis→web）符合关注点分离原则。无循环依赖。

2. **配置驱动引擎发现**（v0.9）：`_init_v07_registry()` 根据 Config 字段自动注册引擎，与 `probe_engines()` 配合实现声明式引擎注入。每次 API 变更只需修改 Config，无硬编码 if-else。

3. **并发引入方式恰当**（v0.8/v0.9）：`AdaptiveController` + `run_engines_concurrent` 作为可插拔模块，E4 Orchestrator 的 Tier2/Tier3 顺序循环逐步替换为并发执行。没有一次性重写整个编排循环。

4. **资源缓存层**（v0.10）：`_RESOURCE_CACHE` 在 `verifier.py` 模块级缓存 4 个 JSON 资源，进程生命周期内只加载一次。二级构造从 ~5ms 降至 ~0.1ms。

### ⚠️ 风险

1. **并发超时未完全兜底**（`concurrency.py` `run_engines_concurrent`）：

   `as_completed` + `future.result(timeout)` 在单引擎场景下超时不生效——`as_completed` 会阻塞直到该唯一 Future 完成，然后 `result()` 对已完成 Future 直接返回。需改为 **总时间闸**（`wait` + 超时参数）而非 per-engine 超时。

   ```python
   # 脆弱路径：仅 1 个 engine 时，timeout_s 无效
   for future in as_completed(future_map):
       result = future.result(timeout=timeout_s + 5)  # Future 已完成，直接返回
   ```

2. **Web 面板异常路由缺少 CSRF 保护**（`/book/{code}/anomalies/{id}/resolve`）：

   当前使用 `GET` 方法修改状态（标记决议）。符合 RESTful 风格应为 `POST`。`GET` 可能导致浏览器预取/搜索引擎爬虫意外触发修改操作。

3. **Dockerfile 未处理数据库初始化**：

   `docker-compose.yml` 将 `./db:/app/db` 挂载为 volume，但如果宿主目录不存在，Docker 自动创建为 root 所有。首次启动时 `BookDB.__init__` 可能因权限问题无法写入。

### 💡 建议

- **修复 `concurrency.py`**：为 `run_engines_concurrent` 添加全局超时保卫（timeout 从入口处开始倒计时，不论多少个引擎）。
- **将 resolve 路由改为 POST**：`/api/books/{code}/anomalies/{id}/resolve` 已是 POST，但 HTML 页面路由仍是 GET。对齐后统一为 POST`。
- Docker volume 预创建目录 + 权限提示。

---

## 二、安全专家 (Security) 视角

### ✅ 正面

1. **Egress 校验前置**（v0.9）：Tier2/Tier3 引擎的 `validate_url` 检查从 per-engine 循环提前到并发执行前的预过滤，防止 DNS 复检和 RFC1918 绕过。

2. **无明文凭证存储**：`EngineConfig` 仅存环境变量名引用（`api_key_env`），不存 API key 明文值。`EngineRegistration.__repr__` 掩码敏感字段。

3. **FTS5 搜索无 SQL 注入**：跨书搜索使用参数化查询（`?` 占位符）。

### ⚠️ 风险

1. **Docker 以 root 运行**：`Dockerfile` 未创建非 root 用户，容器内进程以 root 运行。若 `kzocr` 的 `kzocr/web/app.py` 或其他模块存在漏洞，攻击者可能从容器逃逸。

2. **`.dockerignore` 缺 `.env`**：`.dockerignore` 未包含 `.env` 文件。构建镜像时 `.env` 会被复制到镜像中，若其中包含 API Key 等凭证会泄露。

### 💡 建议

- Dockerfile 末尾添加 `USER 1000:1000` 运行非 root 用户。
- `.dockerignore` 添加 `.env`、`*.key` 等敏感文件模式。
- CI 构建镜像前增加 `docker build --secret` 或 ARG 验证步骤。

---

## 三、领域专家 (Domain) 视角

### ✅ 正面

1. **九字段分割规则准确**（v0.8 `recipe_parser.py`）：traedocu 验证过的 `FIELD_IDENTIFIERS` 正则、`"各X克"` 共享剂量逻辑、`dosage_group` 设计，已完整迁移到 KZOCR。

2. **三级编号校验**（v0.8 `section_merger._validate_numbers`）：traedocu OCR-BUG-001 的 33% 事故根源已通过编号连续性校验机制覆盖。当前版本检测到跳号/节号偏差时会记录 anomalies，未来可按章节聚合导出时提示人工核验。

3. **LLM 质检管道设计合理**（v0.13）：`QualityChecker` 以 rule-only 为默认（零外部依赖），LLM 仅用于规则标记可疑后的确认环节。prompt 设计将方剂字段内容 + 规则疑点一起发给 LLM，LLM 只需回答「是否真实问题」而非从头扫描全文。

### ⚠️ 风险

1. **`QualityChecker._llm_check` 的 prompt 未处理空字段**：

   ```python
   fields_str = "\n".join(f"{k}：{v}" for k, v in recipe.fields.items())
   ```

   若方剂完全没有字段（空 `fields` dict），`fields_str` 为空字符串，LLM prompt 中「字段内容」为空。某些 LLM 可能因此误判或输出无关内容。应加「无字段」占位。

2. **Web 搜索的药材名反向查询未处理别名**：

   当前 `/search?q=...` 直接在 `title` 和 `herb_name` 中做子串匹配。但中医药材常见别名（如"北芪"→"黄芪"、"甲珠"→"穿山甲"），当前搜索不会匹配别名。搜索 `"北芪"` 不会命中 `herb_name="黄芪"` 的方剂。

### 💡 建议

- prompt 增加空字段兜底文本 `"（该方剂无此字段）"`。
- 搜索可扩展为 `herb_master` + `herb_alias` 表的模糊匹配（FTS5 方向 B，当前版本可先建简单别名表）。

---

## 四、测试专家 (Test) 视角

### ✅ 正面

1. **历史最高覆盖**：从 v0.7 评审时的 396 tests → 当前 468 tests (+18%)。覆盖编排/方剂/章节/并发/Web/API/质量/Docker 等模块。

2. **性能基准门禁**（v0.10 `tests/benchmarks/`）：5 个微基准测试 + 2 个全书端到端基准（100 页 < 30s, 10 页 < 5s）。CI 中每次提交都通过。

3. **混沌注入**（v0.8 `tests/test_chaos.py`）：Tier2 异常降级到 Tier3、全引擎失败→HumanGate、RateLimitedError 捕获验证。编排系统的容错路径有回归保护。

### ⚠️ 风险

1. **无批量处理的集成测试**（`cmd_batch`）：

   `kzocr batch <pdf_dir>` 没有对应的测试用例。`test_cli.py` 中有 pipeline/smoke/export 的模拟测试，但 batch 命令未被覆盖。此命令涉及文件系统扫描和顺序调用 `run_engine`，若出错可能影响整个批次而不被测试发现。

2. **Web 面板 4 个新路由测试不足**：

   `test_web_enhanced.py` 只有状态码断言（200），没有验证页面内容（如 dashboard 上 benchmark 数据是否正确渲染、recipe_detail 的 herbs 表格行数）。当前 6 例测试仅验证路由可达。

3. **Dockerfile 无 CI 构建测试**：

   `test.yml` 未包含 `docker build` 步骤。Dockerfile 中的依赖（apt-get packages、PyMuPDF 系统库）可能在 CI 环境中出错却不被测试发现。

### 💡 建议

- 新增 `test_batch.py`（至少 1 例）：创建含 2 个 mock PDF 的临时目录，调用 `cmd_batch`，验证返回码和输出。
- 增强 `test_web_enhanced.py`：对 `dashboard` 测试添加 benchmark 数据断言；对 `recipe_detail` 测试添加药材行数断言。
- CI 增加 `docker build .` 作为 lint 步骤的一部分（非矩阵，单次运行）。

---

## 五、汇总

### 🚫 阻塞项

| # | 角色 | 问题 | 涉及文件 | 建议修复 |
|---|------|------|----------|----------|
| B1 | 架构 | `run_engines_concurrent` 单引擎时超时不生效 | `concurrency.py` | 添加全局超时保卫 |
| B2 | 安全 | resolve 使用 GET 而非 POST | `web/app.py` | HTML 路由改为 POST |
| B3 | 测试 | `cmd_batch` 无测试覆盖 | `tests/test_batch.py` | 新增 1+ 用例 |
| B4 | 测试 | Docker CI 无构建验证 | `.github/workflows/test.yml` | 添加 `docker build` 步骤 |

### 💡 建议项

| # | 角色 | 建议 | 优先级 |
|---|------|------|--------|
| R1 | 安全 | Dockerfile 非 root 用户运行 | 中 |
| R2 | 安全 | `.dockerignore` 添加 `.env` | 高 |
| R3 | 领域 | LLM prompt 空字段占位 | 低 |
| R4 | 领域 | Web 搜索别名扩展 | 中 |
| R5 | 测试 | Web 新增路由增强断言 | 中 |
