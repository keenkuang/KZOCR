# 运维评审：v0.7 自适应 OCR 引擎编排层详细设计

> 评审人：运维工程师
> 评审对象：`docs/plans/ocr-engine-unification.v0.7-DETAILED.md`
> 评审日期：2026-07-10

---

## 1. §8 Benchmark NDJSON 容量管理：100MB/引擎 + 截断最老 50% + 90 天窗口

### 1.1 总评：大体合理，但截断实现细节缺失，需补充

三个维度的容量策略本身是合理的，三者形成互补：

| 维度 | 作用域 | 作用时机 | 目的 |
|------|--------|---------|------|
| 90 天窗口（max_age_days） | 启动加载 | `load_benchmarks() ` | 限制启动时内存重建数据量 |
| 50000 行上限（max_load_lines） | 启动加载 | `load_benchmarks()` | 防单引擎历史过长拖慢启动 |
| 100MB / 截断 50% | 磁盘文件 | 持久化时 | 防止磁盘无限膨胀 |

### 1.2 关键问题

#### OPS-01：截断实现方式未定义（严重）

文档只说"文件超 100MB 时自动截断最老 50%"，但 NDJSON 是纯追加文件，截断最老 50% 至少面临两种实现：

- **方案 A：读全量 → 过滤 → 覆写**：需先读整个 100MB 文件到内存，解析所有行，取最新 50%，再覆写。此时磁盘原有文件 + 临时文件共存，2x 峰值磁盘。
- **方案 B：逐行跳过头指针 + 写新文件**：读取前半部分行并丢弃，后半部分写入新文件，rename 替换。同样有 2x 峰值。

**缺少的关键细节：**
- 截断检查时机：每次 persist 都检查？写满 100MB 后首次 persist 触发？周期性检查？
- 原子性保障：截断进行中程序崩溃，原始数据是否完整？
- 是否复用 `atomic.py` 的原子写入机制？如果覆写，是否通过写临时文件 + rename 实现？
- 100MB 阈值检查是检查单引擎文件还是所有引擎文件合计？

**建议：** 明确截断实现为：**写临时文件 → rename 替换**，复用现有 `atomic.py`。将阈值描述由"100MB"改为 `benchmark_max_mb`（已定义），并在伪代码中补充截断流程。

#### OPS-02：100MB 与 50000 行上限存在隐含矛盾

- 每条 NDJSON 记录约 120–200 字节。
- 50000 行 ≈ 6–10 MB，远低于 100MB。
- 因此启动时 `max_load_lines`（50000）是实际决定加载数据量的硬限制，"仅读 90 天内"和"100MB 截断"在加载场景下基本不会触发。

运行时写入侧，文件会持续增长到 100MB 才被截断（约 50–67 万条/引擎），但进程重启后只读最近 5 万条。这意味着：

- 磁盘上保留 100MB/引擎的数据
- 重启后只用到前 ~7.5MB
- 剩余 ~92.5MB 是"保存但不使用"的状态

运维角度，如果不期望保留如此多的离线数据，`benchmark_max_mb` 可下调至 10–20MB，与 50000 行加载上限对齐。

#### OPS-03：90 天窗口是加载时过滤器，并非数据保留政策

文档 §8.4 `load_benchmarks()` 中 `if event["ts"] < cutoff: continue` 意味着：90 天以外的数据在被加载时静默跳过，**但仍在磁盘上**。加上截断只关心文件大小不关心时间，一个引擎可能在 90 天内超过 100MB 而被截断，也可能在 90 天后文件未达 100MB 而永久保留。

建议明确：90 天窗口是否应配套一个**额外的主动清理策略**（如启动时或定时任务中删除 `ts < cutoff` 的行），还是完全依赖大小触发的截断。

#### OPS-04：无压缩，长期运维成本较高

7 个引擎，每个 100MB，合计 700MB 用于 benchmark。NDJSON 对文本压缩友好（gzip 通常可压缩至原大小 10–15%），建议评估是否在写入或归档时启用压缩（如 `.ndjson.gz`）。

非阻塞建议——可暂缓，保留为未来优化项。

---

## 2. §7 编排主循环的 trace 机制

### 2.1 总评：框架存在，但关键实现在文档中缺失，不足以支撑运维排障

当前已定义的 trace 元素：
- `trace: list[EngineCallRecord]` — 内存 trace
- `_write_trace(config.trace_dir, ...)` — 写入磁盘
- `BookResult.engine_trace` — 返回对象中携带
- 每页一条结构化日志消息
- 最终引擎报告 + 失败率告警

### 2.2 关键问题

#### OPS-05：`EngineCallRecord` 数据类型未定义（严重）

§7.1 中直接使用 `EngineCallRecord(page=..., tier=..., engine=..., latency_ms=..., glyph_status=...)` 构造实例，但**文档中未定义此数据类**。作为运维排障的核心数据结构，其字段完整性直接决定我们能否从 trace 中复原故障场景。

**建议补充的字段：**
```python
@dataclass
class EngineCallRecord:
    page: int
    tier: int
    engine: str
    status: EngineStatus              # 调用前引擎状态
    latency_ms: int
    glyph_status: str | None
    error: str | None = None
    detector_chain: list[str] = field(default_factory=list)  # 触发了哪些 detector
    ts: float = 0.0                    # 调用时间戳（使用 time.time()）
    cache_hit: bool = False            # 是否来自 VLM 缓存
    input_page_bytes: int = 0          # 输入图像大小（排障"图像过大导致 OOM"）
    llm_token_count: int = 0           # LLM 引擎输出 token 数（排障延迟）
```

#### OPS-06：`trace_dir` 默认值为空字符串，trace 默认不落盘（严重）

```python
trace_dir: str = ""   # KZOCR_TRACE_DIR（默认 ""）
```

这意味着默认情况下 trace 只是内存中的 Python 列表——进程退出后全部丢失。如果某次批量处理中有一本书的编排出现问题，操作员事后无法复盘 trace 信息。

**建议：** trace 默认应启用并落盘，默认路径为 `$KZOCR_OUTPUT_DIR/trace/`。可以增加 `trace_max_books` 或 `trace_retention_days` 参数控制保留数量/天数，避免磁盘无限占用。

#### OPS-07：Trace 文件格式未定义

`_write_trace()` 是空函数体。关键的运维问题：
- 输出格式是什么？建议统一用 NDJSON（与 benchmark 保持一致），方便 `jq` 解析
- 单书单文件还是全书一文件？建议 `{trace_dir}/{book_code}.ndjson`
- 是否包含渲染后图像路径？故障复现常需要"当时这张图长什么样"

#### OPS-08：Trace 无保留策略

Benchmark 有 90 天 / 100MB 保护，**trace 完全没有**。如果 trace_dir 默认启用且无保留策略，在持续处理场景下可能撑满磁盘。

建议补充 `trace_retention_days` 配置项（默认 7 天），并在启动时或写入前对过期 trace 文件做清理。

#### OPS-09：Trace 缺少时序分解

运维排障时最需要的数据是"时间花在哪里了"——当前 trace 只记录总 `latency_ms`，没有分解：

- 渲染阶段耗时
- 引擎调用耗时（含网络延迟）
- 验证阶段耗时（各个 detector 分别耗时）
- 调度器排序耗时

建议 `EngineCallRecord` 增加 `breakdown: dict[str, float]` 字段，记录各子阶段的毫秒耗时。

---

## 3. §9 Config 新增字段的运维关注点

### 3.1 总评：字段定义清晰，但默认值策略和路径解析规则需补充

### 3.2 关键问题

#### OPS-10：`trace_dir` 默认 `""` 导致首次 trace 写入失败

`_write_trace()` 在执行时如果 `trace_dir` 为空不会创建任何目录，但也没有错误日志。如果某次排障中操作员手动设置了 `KZOCR_TRACE_DIR` 但目录不存在，函数是否会自动创建父目录？

建议：
1. 默认值改为 `$KZOCR_OUTPUT_DIR/trace/`
2. `_write_trace()` 中 `os.makedirs(trace_dir, exist_ok=True)`

#### OPS-11：`benchmark_dir` 默认值依赖 `$KZOCR_OUTPUT_DIR` 但路径展开时机不确定

文档说默认值 `$KZOCR_OUTPUT_DIR/benchmarks/`，但：
- 是在 Config 初始化时展开 `${KZOCR_OUTPUT_DIR}`？
- 还是运行时每次都从 `os.environ` 读取？
- 如果 `KZOCR_OUTPUT_DIR` 在程序启动后被修改，benchmark_dir 是否跟随？

建议明确路径解析策略：**Config 初始化时将 `${KZOCR_OUTPUT_DIR}` 展开为绝对路径**，后续不变。并在 Config 的 `__post_init__` 或工厂函数中实现。

#### OPS-12：`benchmark_retention_days` 的值仅用于启动加载，可能误导运维人员

运维人员看到"90 天保留期"的默认值，会合理期望 90 天前的数据被自动清理。但文档中的实现仅在启动加载时做过滤，**数据在磁盘上依然存在**。这可能导致两问题：
- 运维积累数月后，发现磁盘用量超出预期
- 重启后看到文件很大但加载量很少，困惑于"数据去哪了"

建议：将此字段的文档描述从"保留 90 天"改为更准确的表述——"启动时只加载最近 N 天的数据（磁盘数据需要配合截断机制清理）"。或者增加启动时的过期数据清理步骤。

#### OPS-13：缺少 VLM cache 目录配置

§7.6 使用了 `_load_vlm_cache(config, ...)` 和 `_save_vlm_cache(config, ...)`，但 Config 中没有对应的 cache 路径字段。VLM 缓存文件存储在哪里？

假设默认路径为 `$KZOCR_OUTPUT_DIR/vlm_cache/`，建议在 `SchedulerConfig` 中增加字段：

```python
vlm_cache_dir: str = ""   # KZOCR_VLM_CACHE_DIR（默认 $KZOCR_OUTPUT_DIR/vlm_cache/）
vlm_cache_max_mb: int = 500  # KZOCR_VLM_CACHE_MAX_MB
vlm_cache_ttl_days: int = 30  # KZOCR_VLM_CACHE_TTL_DAYS
```

#### OPS-14：缺少磁盘空间检查

当 `$KZOCR_OUTPUT_DIR` 所在磁盘空间不足时，benchmark 写入、trace 写入、VLM 缓存写入都可能异常。建议至少：
- 写入前检查磁盘剩余空间（`shutil.disk_usage`），不足时日志告警
- 磁盘空间低于 5% 时自动触发 benchmark/trace 的部分清理

#### OPS-15：无配置校验

配置项没有业务语义上的合法性校验，如：
- `max_pages: int = 50` — 如果设为 0 或负数，编排循环会立即 `page_num >= 0` 触发截断
- `benchmark_retention_days: int = 90` — 如果设为 0，所有数据都被过滤
- `max_tier1_engines: int = 2`（注释标注"最大 3"）— 没有代码层面的上限约束

建议在 `SchedulerConfig` 或 `Config` 的 `__post_init__` 中增加校验。

---

## 4. 运行时数据目录布局

### 4.1 当前定义的目录

| 目录 | 配置项 | 默认值 | 文件类型 | 保留策略 |
|------|--------|--------|---------|---------|
| `benchmarks/` | `benchmark_dir` | `$KZOCR_OUTPUT_DIR/benchmarks/` | NDJSON | 90 天窗口（读）+ 100MB 截断 |
| `trace/` | `trace_dir` | `""`（默认不启用） | 未定义 | 未定义 |
| `vlm_cache/` | 未定义 | 未定义 | 未定义 | 未定义 |

### 4.2 关键问题

#### OPS-16：三个目录共享 `$KZOCR_OUTPUT_DIR` 单根，无法独立管理生命周期

当前设计将 benchmarks、trace、VLM cache 全部放在 `$KZOCR_OUTPUT_DIR` 下。运维场景中这带来几个问题：

- **存储差异化**：benchmark 需要低延迟（影响启动速度），trace 可放廉价存储，VLM cache 需大容量且低延迟——单一 `$KZOCR_OUTPUT_DIR` 无法做分层存储映射
- **风险传导**：VLM cache 膨胀可能撑爆磁盘，间接导致 benchmark 写入失败
- **备份策略冲突**：benchmark 需要定期备份以复原调度历史；VLM cache 无需备份；trace 可能按合规要求需要归档——三者共用父目录使 rsync/排除规则更复杂

**建议：** 不改变接口（兼容现有单一 OUTPUT_DIR 的简单部署），但文档中明确说明每个目录可独立通过环境变量覆盖路径，并给出典型生产布局示例：

```
# 简单部署（默认）
$KZOCR_OUTPUT_DIR/
├── benchmarks/
├── trace/
└── vlm_cache/

# 生产部署（推荐）
KZOCR_OUTPUT_DIR=/data/kzocr/output
KZOCR_BENCHMARK_DIR=/data/kzocr/benchmarks      # SSD
KZOCR_TRACE_DIR=/data/kzocr/logs/trace           # HDD
KZOCR_VLM_CACHE_DIR=/data/kzocr/cache/vlm        # SSD
```

#### OPS-17：Benchmark 文件的权限 700 与多进程/容器共享场景不兼容

§3.4 规定 benchmark NDJSON 权限为 700（仅 owner 可读写）。但在下列部署场景中存在问题：

- **Docker 容器**：如果 benchmark 目录通过 volume 挂载，主机上 `kzocr` 用户 UID 与容器内 UID 可能不一致，导致 cross-user 无法读取
- **Cron 备份脚本**：以 root 运行的备份脚本需要读这些文件——700 权限使 root 以外的操作受阻
- **多实例共享**：如果未来多个 KZOCR 实例共享同一个 benchmark 目录（比如通过 NFS），权限 700 使其他实例无法写入

建议：将基准权限改为 750（owner 读写、group 读），或保持 700 但记录到文档中作为已知约束。

#### OPS-18：无目录初始化逻辑

启动时，`benchmark_dir`、`trace_dir`、`vlm_cache_dir` 如果不存在，是自动创建还是报错退出？当前文档没有覆盖。

建议统一在 `Config.__post_init__` 或 `orchestrate_book()` 初始化阶段执行 `os.makedirs(dir, exist_ok=True)`。

---

## 5. 总结与优先级建议

### 必须修复（阻塞发布）

| 编号 | 严重度 | 问题 |
|------|--------|------|
| OPS-05 | 严重 | `EngineCallRecord` 未定义，trace 数据结构缺失 |
| OPS-06 | 严重 | `trace_dir` 默认空，trace 不落盘 |
| OPS-01 | 严重 | 100MB 截断实现细节缺失（原子性、峰值磁盘） |
| OPS-07 | 严重 | Trace 输出格式未定义 |
| OPS-13 | 严重 | VLM cache 路径无配置，目录未定义 |

### 建议修复（影响运维体验）

| 编号 | 严重度 | 问题 |
|------|--------|------|
| OPS-08 | 中 | Trace 无保留策略 |
| OPS-10 | 中 | `trace_dir` 默认为空导致首次 trace 写入故障 |
| OPS-11 | 中 | `benchmark_dir` 路径展开时机不明确 |
| OPS-12 | 中 | `benchmark_retention_days` 含义与运维预期不一致（仅加载过滤，非清理） |
| OPS-17 | 中 | 权限 700 与容器/备份场景不兼容 |
| OPS-18 | 中 | 目录无自动初始化 |

### 建议关注（非阻塞，建议记录）

| 编号 | 严重度 | 问题 |
|------|--------|------|
| OPS-02 | 低 | 100MB 与 50000 行上限对齐问题 |
| OPS-03 | 低 | 90 天窗口不配套清理策略 |
| OPS-04 | 低 | 无压缩，7 引擎合计可达 700MB |
| OPS-09 | 低 | Trace 缺少时序分解 |
| OPS-14 | 低 | 无磁盘空间检查 |
| OPS-15 | 低 | 配置项无业务语义校验 |
| OPS-16 | 低 | 单 OUTPUT_DIR 无法分层存储 |

---

*评审结束。*
