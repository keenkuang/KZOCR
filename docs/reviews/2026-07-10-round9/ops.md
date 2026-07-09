# OPS 评审：v0.5 AMEND rc4 — 运维视角

**评审人：** OPS (general-purpose-16)
**日期：** 2026-07-10
**提交：** `ocr-engine-unification.v0.5-AMEND.md` (rc4)
**总评：** **CONDITIONAL** — 4 PASS / 1 FAIL / 3 建议项

---

## 1. D3 缓存目录：磁盘空间、清理策略、`/tmp` 适用性

**结论：FAIL**

### 1.1 `/tmp` 默认路径在生产环境不适用

现代 Linux 发行版中 `/tmp` 的部署行为不可靠：

- **tmpfs 场景：** 许多发行版（Fedora ≥30、Arch、Ubuntu 21.04+ Server 选项）将 `/tmp` 挂载为 tmpfs（内存盘）。VLM 缓存页数以文本为主（每页约 2-8 KB），单本书几十 KB 无害，但并发多书场景下（例如批量入库 100+ 本）可能膨胀到数 MB ~ 数十 MB。问题不在于内存不够，而在于**用户不知道缓存占用了 RAM**。
- **systemd-tmpfiles 场景：** `/tmp` 可能被 `systemd-tmpfiles-clean` 定期清理（默认 `/usr/lib/tmpfiles.d/tmp.conf` 中 `q /tmp 1777 root root 10d`，即 10 天 TTL）。这与此方案的 24h TTL 不一致——系统可能在你希望缓存仍有效时就清理了文件。
- **容器场景：** 在 Docker/K8s 中，`/tmp` 是容器层（通常是 overlayfs），重启后丢失。如果用户期望缓存跨部署持久化，`/tmp` 不满足需求。

**建议：** 至少在产品文档中明确注明生产部署应设置 `KZOCR_OUTPUT_DIR=/data/kzocr/output`（或持久卷路径）。D0 文档注释应标注该限制。

### 1.2 缓存 TTL 未由代码强制执行

**严重缺陷。** 计划的 D3.3 提到"默认 TTL：24 小时"和 `KZOCR_CACHE_TTL` 环境变量，但是：

- **代码中没有 TTL 校验逻辑。** `_cache_is_valid()` 只检查 `config_hash` 匹配和 `is_complete`，没有读取文件 mtime/ctime 与当前时间比较。
- 这意味着：**24h TTL 是一个文档意图，并非实际行为。** 重配置后缓存仍会被使用，直到手册告诉用户用 `KZOCR_CLEAR_CACHE=1`。
- `KZOCR_CACHE_TTL` 环境变量在计划中提及，但未在任何函数签名或伪代码中出现。

**严重性：** Major。长时间运行的部署中，缓存文件会无限累积且永不过期（除非主动删除）。

**修复要求：** `_cache_is_valid()` 必须追加 mtime 检查：

```python
def _cache_is_valid(cache_path: Path, cfg: Config) -> bool:
    if not is_complete(cache_path):
        return False
    # TTL 检查
    if cfg.cache_ttl_seconds > 0:
        age = time.monotonic() - cache_path.stat().st_mtime
        if age > cfg.cache_ttl_seconds:
            return False
    # config_hash 检查
    ...
```

同时需在 Config 中增加 `cache_ttl_seconds: int` 字段（映射 `KZOCR_CACHE_TTL`，默认 `86400`）。

### 1.3 磁盘空间评估

VLM 缓存为纯文本，每页 ~2-8 KB。若按每本书 50 页算，100 本书约 10-40 MB。**磁盘空间不是实际风险。** 主要风险是 1.1（路径语义）和 1.2（TTL 未实现）。

---

## 2. D0 `kzocr_output_dir`：部署可配置性与影响

**结论：PASS**（但有一个部署注意事项）

### 2.1 可配置性

通过 `KZOCR_OUTPUT_DIR` 环境变量可配置——这是正确的做法。与现有 Config 模式一致。

### 2.2 部署注意事项：`from_env()` 缺失映射

当前 `config.py` 的 `from_env()` 和 `load_config()` 均未反映 `kzocr_output_dir`。计划使用 `field(default_factory=...)` 方式，但现有模式的 `from_env()` 通过显式 kwargs 构造 `Config` 实例。有两种方式配置此字段：

1. 在 `from_env()` 中显式映射 `KZOCR_OUTPUT_DIR` → `kzocr_output_dir`
2. 仅在 `load_config()` 中设置（与 `use_mock` 等模式一致）

**无论哪种方式，`from_env()` 或 `load_config()` 必须修改**，否则环境变量不会被读取，始终使用默认值 `/tmp/kzocr/output`。

### 2.3 生产部署影响

- 任何消费此目录的功能（D3 缓存、D4 JSON 输出、C1 L3 下次产物）都需要知道此路径。部署时需确保此目录：
  - 有足够的 inode（文本文件多目录层级 `vlm_cache/{engine_tag}/{book_code}/` 会创建大量小目录）
  - 权限正确（运行 KZOCR 的用户有写权限）
  - 属于持久卷而非容器临时存储（如适用）

### 2.4 受影响文件

| 文件 | 改动 |
|------|------|
| `kzocr/config.py:from_env()` | 新增 `kzocr_output_dir=` 行 |
| `kzocr/config.py:load_config()` | 可能额外映射（视设计决定） |
| `kzocr/engines/atomic.py` | `_check_base` 的 `allowed_base` 传参路径——已支持 |

---

## 3. D2 重试延迟：长流程影响

**结论：PASS**（有观察建议）

### 3.1 延迟分析

`BACKOFF_CONFIGS["api"]` 参数：`base_delay=1.0, max_retries=3, max_delay=300.0, jitter=0.5`

单页重试序列：
- Attempt 1: 1.0s × (1 + 0~0.5) = 1.0~1.5s
- Attempt 2: 2.0s × (1 + 0~0.5) = 2.0~3.0s
- Attempt 3: 4.0s × (1 + 0~0.5) = 4.0~6.0s
- **累计：7.0~10.5s 每页**

50 页全部 3 重试耗尽（最坏情况）：350~525s 纯等待。

有 `total_timeout=7200s` (2h) 兜底，所以不会无限运行。但请注意：

- **3 次失败 × 50 页消耗了 6%~7% 的总时间预算**，而结果全是失败。
- 如果真实引擎失败（降级路径），这个时间仍然要被消耗。

### 3.2 RateLimitedError Retry-After 风险

如果 `Retry-After` header 返回 60s，单次等待就是 60s——远超过退避预算。3 次这样的限重试 = 180s 单页。这在一个批量处理系统中是可以接受的，但**需要有上限**（`max_delay=300.0` 已做了兜底）。

### 3.3 建议：可观察的累积重试时间

计划 v0.5-rc4 已提及"累积重试时间跟踪建议"。建议实现一个简单的累积重试时间计数器，在 D2 的主循环中可观测：

```python
_total_retry_wait = 0.0
# ...
# 在 retry_with_policy 中或循环中累积 _total_retry_wait
if _total_retry_wait > MAX_RETRY_BUDGET:
    logger.warning("[VLM] 累积重试等待 %.1fs 超过预算，终止", _total_retry_wait)
    break
```

这样即使在 `total_timeout` 耗尽之前也能避免过度的重试等待。**低优先级，P3 可选。**

---

## 4. 日志与可观测性

**结论：CONDITIONAL** — 功能性日志足够，运维可观测性有缺口

### 4.1 已有日志

| 点 | 状态 |
|----|------|
| 进度报告（每 5 页） | 已有 |
| 单页识别失败 | 已有 |
| 重试耗尽错误 | D2 新增 `logger.error("[VLM] 第 %d 页重试耗尽...")` |
| 总时间预算终止 | 已有 |
| `failed_pages` 字典 | D2 新增（内存中） |

### 4.2 关键缺口

#### 缺口 1：`failed_pages` 未暴露（Conditonal 原因）

`failed_pages` 目前仅在内存中存在。计划文档提到"可通过日志输出或扩展 `BookResult` 返回"，但没有明确指定。**如果不暴露，运维人员无法获知处理结果中哪些页失败。**

**最低要求：** `_run_vlm` 结束时至少输出一行结构化日志：
```python
logger.info("[VLM] 完成 %d/%d 页，失败页: %s", len(pages_text), total_pages, failed_pages)
```

#### 缺口 2：缺少缓存命中/未命中指标

D3 缓存实现后，运维需要知道缓存的实际效用：
- 缓存命中率（跳过 OCR 的页数 / 总页数）
- 缓存清理事件

建议增加计数器输出到日志或结构化的摘要。

#### 缺口 3：缺少结构化日志格式

当前日志全部是自由文本。对于以 JSON 为中心的日志聚合系统（ELK、Loki），建议考虑用 `extra=` 参数携带结构化字段：

```python
logger.info("[VLM] 页处理完成", extra={
    "page_num": page_num, "cache_hit": True, "retry_count": 0,
})
```

**低优先级**（P3 可选），不影响本轮功能交付。

#### 缺口 4：重试中间状态不可见

当前日志只在重试耗尽时输出。中间的重试尝试（第 1 次失败 → 等待 → 第 2 次尝试）无日志。这在调试限流问题时非常有用。建议 D2 的 `retry_with_policy` 在每次重试前输出 debug 级别日志。

---

## 5. 实施顺序：运维视角

**结论：PASS**（一个注意项）

### 5.1 顺序合理性

P0 (Config) → P1 (D1+D2 异常+重试) → P2 (D3 缓存) → P3 (D4 层级检测)

从运维角度看：
- **Config first** → 正确：没有 Config 字段，任何下游都无法使用 `kzocr_output_dir`
- **D1+D2 before D3** → 正确：缓存层需要在读取缓存失败时能够优雅重试。如果倒过来，D3 的缓存读取没有 D1 的异常分层，只能 `except Exception: redo`，丢失重试鉴别的能力
- **D4 last** → 正确：P3 低优先，不影响核心流程

### 5.2 注意项：P1→P2 过渡期间无恢复能力

D1+D2 部署后到 D3 部署之前（可能跨多个发布周期），`_run_vml` 有重试但**没有断点续跑**。如果进程在 P1 后 crash：
- 已处理的页全部丢失
- 没有缓存可恢复
- `failed_pages`（内存中的字典）也丢失

这是可以接受的，只要：
1. P1→P2 的时间窗口不要太长（建议在同一发布周期内）
2. 运维团队了解此限制，在大批量处理时避免单次运行过于关键

### 5.3 同一人实施 D1+D2+D3 的要求

计划建议 D1+D2 由同一人实施，D2+D3 也由同一人。从运维角度看这是**正确的**——`_run_vlm` 在三个 D 项中被反复修改，不同人处理会造成 diff 冲突和回归风险。

**建议：** 在 P1 实施时就将 D3 的缓存钩子占位（空的 `_get_cache_path` / `_cache_is_valid` 返回 `False`），这样 P2 只需补充实现而非重构——还可以降低回归风险。

---

## 总结

| 项 | 判决 | 关键风险 |
|----|------|----------|
| D3 缓存目录 | **FAIL** | TTL 未代码实现（1.2）；`/tmp` 默认路径不适合生产（1.1） |
| D0 可配置性 | **PASS** | 注意 `from_env()`/`load_config()` 必须更新 |
| D2 重试延迟 | **PASS** | 有 `total_timeout` 兜底，建议加累积重试预算 |
| 日志可观测性 | **CONDITIONAL** | `failed_pages` 需在结束时输出；缓存命中率缺失 |
| 实施顺序 | **PASS** | P1→P2 过渡期无断点续跑，注意窗口长度 |

### 修复阻断项（FAIL → PASS 的前置条件）

1. **`_cache_is_valid()` 必须实现 TTL 检查**（基于文件 mtime + `KZOCR_CACHE_TTL` 环境变量）
2. **Config 增加 `cache_ttl_seconds` 字段**（默认 86400），或确认 TTL 通过外部调度器管理
3. **产品文档注明**：生产部署应设置 `KZOCR_OUTPUT_DIR` 为持久卷路径（非 `/tmp`）

### 建议改进（非阻断）

1. `_run_vlm` 结束时输出 `failed_pages` 摘要日志
2. 缓存命中/未命中计数器
3. 考虑 `/tmp` 的 tmpfs 行为，在文档中标注"如需缓存持久化，设置 `KZOCR_OUTPUT_DIR` 到持久卷"
4. `from_env()` 中显式添加 `kzocr_output_dir` 映射（避免 `field(default_factory)` 与 `from_env()` 不一致的 bug）
