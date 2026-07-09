# v0.7 自适应 OCR 引擎编排层 — 安全评审报告

**评审对象**: `docs/plans/ocr-engine-unification.v0.7.md`（E1–E5）
**评审角色**: 安全工程师
**评审日期**: 2026-07-10
**涉及现有模块**: `egress.py` (B3)、`ratelimit.py` (C3)、`config.py`、`errors.py` (D1)、`types.py`、`run.py`

---

## 总体判断

v0.7 方案在架构层面合理引用了已有安全控制（B3 egress allowlist、C3 rate limiter、C2 atomic write、D1 retry），但这些控制的**集成点**在方案中未做明确约定。编排层引入了 4 类新攻击面（benchmark 数据污染、引擎配置凭证散布、调度器选择逻辑旁路、跨引擎数据流不可见），且现有 B3 出站校验在 `engine.run()` 调用链中**可能被静默绕过**。

**风险等级**: 中等（若无集成点加固，生产部署后预期出现 API key 泄露和/或非预期出站流量）。

---

## 逐项评审

### 1. B3 出站 allowlist — Tier 2 是否会绕过？

**结论：存在实际绕过风险，需明确集成点。**

- `egress.py:validate_url()` 为校验入口，但**现有云端适配器（sensenova_adapter.py）直接调用 `requests.post(self.base_url, ...)`，未经过 `validate_url()` 校验**。当前仅在配置层拦截（`config.py` 中 `allow_cloud_vision` 开关），运行时无逐次出站校验。
- v0.7 方案 `<E4>` 中 `engine.run(page)` 的调用链未指明出站校验插入点。若 `run()` 直接调用适配器的 `requests.post()`，B3 即被绕过。
- SiliconFlow VLM（Tier 2 提及）的域名 `api.siliconflow.cn` 已在 allowlist 中，覆盖 OK。但引擎 `base_url` 通过 `config: dict` 传入——若配置被篡改指向 `siliconflow.cn.evil.com`，allowlist 通配 `*.siliconflow.cn` 无法防御（因 eg 使用的是 `host == pattern` 精确匹配，不匹配）。检查 `_match_domain()` 逻辑：`*.siliconflow.cn` → host 必须以 `.siliconflow.cn` 结尾或等于 `siliconflow.cn`，因此 `evil.com` 不会被允许——但 `siliconflow.cn.evil.com` 不会被拒绝。**这是一个实际的 SSRF 前置条件。**
- 未加入 allowlist 的云端端点通过 Tier 2 路由后，不会在前置检查中被拦截。

**建议：**
- 在 `EngineRegistry` 中为每个云端引擎注册一个 `egress_validator` 回调，实际 HTTP 调用前调用。
- 或在 Orchestrator 层统一加一道 `validate_url(engine.base_url)` 检查。
- 将 `validate_url()` 嵌入 adapter 层（如 `requests.post` 的 wrapper）。

---

### 2. C3 速率限制 — 调度器模式下的适配性

**结论：可工作，但多引擎并发时需要重新限定作用域和 key 设计。**

- `AdaptiveRateLimiter` 的持久化 key 写死为 `"adaptive_default"`（`ratelimit.py:109`）。**多个不同服务共享同一个 interval 状态**——SenseNova 被限流会导致 SiliconFlow 也被降速，反之亦然。
- `MultiTokenRateLimiter` 按 `key` 参数区分（如 `"deepseek"`），结构正确。但 v0.7 方案未定义每个引擎/服务对应哪个 limiter key。
- 方案提到「多引擎并行（同一页）」但未描述并发下的锁竞争。当前 `AdaptiveRateLimiter.wait()` 和 `MultiTokenRateLimiter.acquire()` 使用 `threading.Lock`，线程安全；但若未来引入进程级并发（`multiprocessing`），内存锁失效，需切到文件锁或 Redis。
- `ExponentialBackoff` 与 `retry_with_policy` 的重试机制 (`errors.py:50–54`) 与调度器循环存在嵌套重试的风险——`E4` 的 `for engine in engines` 循环已经是重试，若 `engine.run()` 内部又有 `retry_with_policy`，重试次数叠加可能导致等待时间远超 `KZOCR_TOTAL_TIMEOUT`。

**建议：**
- 为每个目标服务（`sensenova`、`siliconflow`）创建独立的 `AdaptiveRateLimiter` 实例，持久化 key 使用服务名。
- 精确定义限流-调度交互：调度器的 `select_candidates` 应查询 limiter 的 `remaining` 属性筛选可用候选，而非在所有引擎都限流后才降级。
- 文档化嵌套重试的保护策略（如 `Budget` 应扣除退避等待时间）。
- 若未来支持进程级并发，预先设计 `RateLimitStore` 的共享机制（当前 SQLite 可，但需要跨进程连接）。

---

### 3. EngineRegistration.config 中的 API key 泄露

**风险等级：高**

- `EngineRegistration.config: dict` 承载「引擎专属配置（环境变量映射 + 默认值）」，**必然包含 `sensenova_api_key`、`deepseek_api_key` 等敏感凭证**。
- 以下场景会导致凭证泄露：

  | 场景 | 路径 | 风险 |
  |------|------|------|
  | benchmark 数据持久化 | `EngineStats` 被写入 `KZOCR_BENCHMARK_DIR` 时，config dict 若一同序列化 | 磁盘文件暴露 |
  | 日志 | `logger.info("%s", engine_registration)` 或 `last_error` 包含 config 内容 | 日志泄露 |
  | `kzocr benchmark` CLI | 该子命令若转储引擎配置 | CLI 输出 → 终端历史 |
  | 调度器异常 | `SchedulerError` 或 `AllEnginesFailedError` 内容包含 engine config | 异常链路泄露 |
  | 缓存 hash | `_compute_config_hash()` (`run.py:376`) 将 `sensenova_api_key` 作为 hash 输入，hash 值写磁盘 `config_hash` 文件 | 有条件可逆（短 key 可爆破） |

- `ProbeResult.keys` (`types.py`) 已以 `dict[str, str]` 明文存储 API key，并传给 `ProbeResult` → `EngineRegistration`，**全链路明文传递**。

**建议：**
- **`EngineRegistration.config` 绝不存储 API key。** API key 只通过环境变量或专用 `CredentialsVault` 获取，config 只存指针/引用名（如 `"sensenova"` → 运行时从 `os.environ` 或 SecretsManager 读取）。
- `ProbeResult.keys` 改为只存储**是否存在**（`dict[str, bool]`），不存储 key 值本身。
- `_compute_config_hash()` 移除 API key 作为 hash 输入（它本来就不该影响缓存有效性——换 key 不换 model 不应导致缓存失效）。
- 为 `EngineStats` 定义 `__repr__`/`__str__` 掩码敏感字段。

---

### 4. 编排层新增攻击面

#### 4.1 调度器选择逻辑被篡改

- `select_candidates()` 的权重公式 `glyph_pass_rate × (1/avg_latency)` 依赖**持久化的 benchmark 数据**。若 `KZOCR_BENCHMARK_DIR` 目录权限过于开放，本地攻击者可通过写入虚假 benchmark 数据令恶意引擎被优先选择。
- 当前设计无 benchmark 数据签名/校验和——无法区分合法写入与被篡改数据。
- 纯数学加权易被极端值操纵：一个引擎可通过设置极低 latency（如 `avg_latency=0.001ms`）获得不合理的排序优势。

**建议：**
- benchmark 文件写入时附带 HMAC 签名（key 由 `Config` 内部生成，不出环境）。调度器读取时验证签名。
- 权重公式增加合理值边界钳制（如 latency 下限 100ms、glyph_pass_rate 窗口最小样本数 N≥10）。
- `KZOCR_BENCHMARK_DIR` 权限默认 `700`（仅 owner 可读写）。

#### 4.2 EngineProbe 的输入注入风险

- `probe_engines()` 检查端口、文件、API key。若引擎定义从外部文件或未校验的 env 读取：
  - **port 检查**：如果 port 范围未限制，恶意的 engine config 可以触发对内网数千端口的扫描（SSRF 风格的端口扫描）。
  - **文件检查**：`os.path.exists()` 或 `Path(model_file)` 来自 config，路径穿越可探测系统文件是否存在。
  - **网络检查**：`requests.get(engine.base_url)` 在 probe 阶段——若未经过 B3 校验，会发生非预期出站 DNS 查询。

**建议：**
- probe 阶段的网络请求也必须经过 `validate_url()`。
- 文件路径校验：拒绝含 `..`、以 `/` 开头指向系统目录的路径。
- 端口范围限制：仅允许 `1024–65535`（或引擎已知端口白名单）。

---

### 5. 多引擎数据隔离

- `E4` 循环中同一个 `page` 对象依次传给每个引擎的 `engine.run(page)`。对于 Tier 2 云端引擎，页面图像**未经 `allow_cloud_vision` 判断直接发出**。这是对 v0.4 数据出境控制的违反。
- 当前 `orchestrate_book()` 的伪代码中**没有 `allow_cloud_vision` 检查**。现有 v0.6 `_run_vlm()` 虽然通过 `cfg.vlm_engine` 逻辑间接控制，但 v0.7 的调度器路径未继承这一检查。
- 引擎间结果隔离：`engine_texts` 是 `dict[str, str]`，以引擎名为 key 分开存储，结构上 OK。但不明确是否存在**跨引擎的 prompt 注入风险**——如果引擎 A 的输出被用作引擎 B 的上下文，而引擎 A 的输出被恶意构造，可以影响引擎 B 的行为。
- 并行模式下如果多个引擎写入同一缓存文件（如 D3 VLM cache），存在竞态条件和交叉污染。

**建议：**
- `E4` 的 Tier 2 循环前必须插入 `if not cfg.allow_cloud_vision: continue`。
- 文档化：`engine.run(page)` 的输入/输出协议——引擎应被视作「无状态函数」，不允许修改 page 对象或共享可变状态。
- 缓存文件按引擎名+页号命名（如 `sensenova_p1.txt`），引擎间互不覆盖。

---

### 6. SSRF / 路径穿越 / 凭证泄露汇总

#### SSRF

| 攻击面 | 描述 | 严重程度 | 现有防御 |
|--------|------|---------|---------|
| 引擎 base_url 被篡改 | config 中的 `sensenova_base_url` 若指向内网地址 | **中** | 仅在 `egress.py` 有校验，但 adapter 调用链未接入 |
| probe 网络请求 | `probe_engines()` 对每个引擎发 HTTP 请求 | **中** | 无（新代码） |
| 恶意引擎注册 | 新增引擎若指向攻击者服务器 | **中** | 依赖 B3，但集成点不明确 |
| MetaData IP 访问 | 云 metadata `169.254.169.254` 已在 B3 拒绝列表 | **低** | 已防御 |

#### 路径穿越

| 攻击面 | 描述 | 严重程度 | 现有防御 |
|--------|------|---------|---------|
| `KZOCR_BENCHMARK_DIR` | 若用户可控或符号链接攻击 | **低** | 无（新配置项） |
| Model 路径 | `paddleocr_vl16_adapter.py:26` 硬编码 `/home/keen/models/` 路径 | **低** | 硬编码，难被篡改 |
| `kimi_engine_dir` | `_run_real()` 中 `sys.path.insert(0, engine_dir)` 路径无校验 | **低** | 仅影响导入，非写入 |

#### 凭证泄露（已在上文第 3 条详述）

汇总：**EngineRegistration.config 明文存 API key 是所有泄露路径的根因。**

---

## 严重问题

| # | 问题 | 影响 | 级别 |
|---|------|------|------|
| S1 | `EngineRegistration.config` 明文包含 API key，全链路 (probe → registry → benchmark → log → error) 明文泄露 | API key 被第三方获取，产生非预期费用/数据泄漏 | **严重** |
| S2 | Tier 2 云端引擎调用链未挂载 B3 egress 校验；adapter 直接 `requests.post()` 绕过 | 允许出站到非预期域名，SSRF 可能 | **高** |
| S3 | Tier 2 循环缺少 `allow_cloud_vision` 检查 | 违规将中医方剂数据送往外部 API，违反数据出境策略 | **高** |

---

## 建议（按优先级排序）

| 优先级 | 建议 | 对应问题 |
|--------|------|----------|
| P0 | 从 `EngineRegistration.config` 中移除所有 API key。改为 key 名引用（如 `"sensenova"` → 运行时从 `os.environ` 或凭证管理读取）。 | S1 |
| P0 | 在 Orchestrator 层或 adapter wrapper 层显式注入 B3 `validate_url()` 校验，确保 `engine.run(page)` 在发送 HTTP 请求前经过出站允许检查。 | S2 |
| P0 | Tier 2 引擎调用前检查 `allow_cloud_vision`，不通过则静默跳过该 tier。 | S3 |
| P1 | 为 benchmark 持久化数据添加 HMAC 完整性校验。 | 4.1 |
| P1 | 每个服务独立 `AdaptiveRateLimiter` 实例，持久化 key 用服务名。 | 2 |
| P1 | 文档化嵌套重试的叠加时间预算：调度器循环 + `retry_with_policy` 的总时间不应超过 `KZOCR_TOTAL_TIMEOUT`。 | 2 |
| P2 | 为 `EngineStats` 添加 `__str__` 掩码（对 `last_error` 中的可疑凭证模式过滤）。 | S1 辅助 |
| P2 | `ProbeResult.keys` 由 `dict[str, str]` 改为 `dict[str, bool]`。 | S1 辅助 |
| P2 | `_compute_config_hash()` 移除 API key 入参。 | S1 辅助 |
| P2 | Probe 阶段的网络请求也接 B3 校验。 | 4.2 |
| P3 | Benchmark 权重公式增加 latency 下限（≥100ms）和最小样本量（N≥10）钳制。 | 4.1 |

---

## 总结

v0.7 方案在架构层面是合理的，引用了多个已有安全设计，但**安全控制层的集成契约未明确定义**，导致以下三类最需关注的缺口：

1. **凭证安全（严重）**：`EngineRegistration.config` 的 dict 设计使 API key 在 probe → registry → benchmark persistent → error 全链路中明文暴露，是本次评审最严重的设计缺陷。
2. **出站控制失效（高）**：云端引擎的 HTTP 调用链未显式接入 B3 校验，allowlist 形同虚设。需在 Orchestrator 或 adapter 层注入校验点。
3. **数据出境绕过（高）**：Tier 2 循环缺少 `allow_cloud_vision` 检查，可能违反 v0.4 明确的数据出境控制策略。

其余问题（benchmark 完整性、限流作用域、路径越界）可以在同轮修复中一并处理，不阻塞 v0.7 开发，但**建议 P0/P1 项在 E1–E4 代码落地前敲定设计**，以避免后续大规模重构。
