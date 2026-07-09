# 安全评审 — v0.5 AMEND（D1–D4 异常处理体系改进）

> 评审对象：`/home/keen/KZOCR/docs/plans/ocr-engine-unification.v0.5-AMEND.md`
> 评审视角：安全 / 数据合规 / 隐私
> 代码基线：`kzocr/security/egress.py`（B3 出境校验）, `kzocr/engines/atomic.py`（C2 原子写入）, `kzocr/engine/run.py`（VLM 主循环现状）
> 前置评审：round4 security.md（B6 allowlist 治理 / M-c 审计机制 / M-d 跨云 consent）, round3 summary.md（I4 出境面 / M-A SSRF / M-E mock 阻断）
> 范围：仅调查与文档评审，未修改方案或代码。

---

## 结论

**有条件通过（Conditional Pass）。** v0.5 AMEND 的 D1–D4 在安全视角上总体低风险，不引入新出境面、不新增凭据/密钥路径、不改变 egress allowlist 机制。**但 D3 VLM 缓存存在一个未治理的敏感数据残留面**——缓存文件中保存了 PDF 页面明文 OCR 结果，且缺乏生命周期/清除/审计策略。

逐项评分：

| 项 | 安全风险 | 裁决 |
|----|---------|------|
| D1 异常分类 + 重试 | 极低（略优于现状） | 通过 |
| D2 VLM 重试增强 | 无新增风险 | 通过 |
| **D3 VLM 缓存** | **中（敏感数据残留）** | **有条件通过** |
| D4 层级异常检测 | 极低 | 通过 |

---

## D1 — 异常分类 + 重试策略统一

### 问题：异常消息可能泄漏内部路径信息

现状代码已在多处做 `except Exception as exc: logger.warning("...%s", exc)`，将原始异常消息引入日志。D1 的 `retry_with_policy()` 和新的异常继承体系在实践中不会**扩大**这一风险面——因为：

1. 新异常类的消息来自可靠的信号源（API 返回码、超时标志、字数统计），而非 `traceback.format_exc()` 或 `os.getcwd()` 等路径相关来源。
2. 现有代码已不做异常路径脱敏，D1 不会使情况变差。

### 例外：包装下层库异常时需谨慎

`ApiError` 和 `RateLimitedError` 在构造时可能包装底层 HTTP 库的异常消息（如 `requests.exceptions.ConnectionError`）。若底层异常消息包含 URL 路径（例如 `Failed to connect to api.deepseek.com/v1/chat`），该 URL 会进入日志。但这属于**已存在风险**（原 `except Exception: continue` 前的 `logger.warning` 同样记录），D1 不新增此面。

### 裁决

**通过，无安全漏洞。** 建议在 `retry_with_policy` 和异常构造器中统一做一次 `str(exc).split("?")[0]` 级的基本 URL 参数截断（过滤 query 参数中的潜在敏感 token），但此建议为"锦上添花"，非 blocker。

---

## D2 — VLM 主循环重试 + 失败分类增强

现状：`run.py:503-505` 裸 `except Exception: continue` 静默吞掉所有异常。
D2 将其拆为 `RateLimitedError` / `OverSizeError` / `ApiError` / `OcrSkipError` 四大类，加上日志分类 + `_record_failure` 计数。

### 安全评估

- **无新增出境面**：重试仍经既有 `vlm.recognize_pages()` 路径，不增加调用量控制外的出境次数。实际减少了因重试耗尽而悄无声息丢页的风险——原本静默跳过的页现在有分类记录。
- **日志安全性**：`logger.error("第 %d 页重试耗尽，跳过", i + 1)` + `_record_failure` 的按类型记录均不含敏感内容（页号 + 异常类型名）。相比现状直接打 `exc` __str__，D2 实际**缩小了**日志的敏感信息面。

### 裁决

**通过，无安全漏洞。** 比现有代码更安全（失败有分类有记录）。

---

## D3 — VLM 主循环断点续跑集成 【需关注】

### 核心风险

D3 新增 VLM 缓存文件 `{output_dir}/vlm_cache/{book_code}/page_{page_num:04d}.txt`，每页一个文件，内容为 OCR 识别结果的**明文文本**。若源 PDF 含患者姓名、诊断、处方剂量等敏感数据，缓存文件将保留等量敏感信息。

### 逐项分析

| 子问题 | 风险等级 | 说明 |
|--------|---------|------|
| **缓存内容敏感度** | 中 | 缓存内容 = OCR 识别出的全文。中医古籍本身敏感度低，但若系统扩展用于现代病历/处方，缓存即等同源文档。 |
| **缓存生命周期** | 中 | 方案仅提供 `KZOCR_CLEAR_CACHE=1` 环境变量手动清除，无 TTL、无自动清理策略、无完成后清除选项。缓存文件在输出目录永久存在，除非手动清理或该环境变量被设置。 |
| **缓存文件权限** | 低 | 缓存写入通过 `atomic_write(cache_path, text)`（无 `allowed_base` 参数），使用默认 umask。若系统 umask 宽松（如 `0022`），缓存文件可能对同一服务器上其他进程可读。`output_dir` 自身需已有 `0600` 保护（round4 M-f 建议），但缓存作为 `output_dir` 子目录应继承同权限。 |
| **缓存目录结构泄露** | 低 | 目录 `vlm_cache/{safe_book_code}/` 暴露了哪些书被处理过。`safe_book_code` 经 `re.sub(r"[^A-Za-z0-9_\-]", "_", _)` 消杀，无路径穿越风险。 |
| **原子性与完整性** | 低 | `is_complete()` + `atomic_write` 的组合正确：文件存在且非空 = 无需重处理。但 `is_complete` 内无内容校验——恶意篡改缓存文件内容会导致篡改数据被 pipeline 消费，不过此场景需要对本地文件系统有写权限。 |

### 建议

1. **增加缓存生命周期策略**（建议写入方案而非仅环境变量）：
   - 默认缓存保留，但新增 `KZOCR_CACHE_TTL_HOURS` 环境变量（默认 `0`=永久），设值后缓存文件超过 TTL 视为过期并重新 OCR。
   - 可选：在 `BookResult` 归档完成后自动清除 `vlm_cache` 子目录，通过配置开关 `cache_clean_on_complete: bool` 控制。

2. **`atomic_write` 传入 `allowed_base`**：当前 D3 伪码调用 `atomic_write(cache_path, text)` 未传 `allowed_base`。虽然 `safe_book_code` 已消杀防穿越，但传 `output_dir` 作为 `allowed_base` 是零成本的深度防御（`atomic.py` 已有该参数）。

3. **权限继承确认**：方案应明确声明 `vlm_cache/` 目录及文件的权限继承 `output_dir` 的 `0600` 设定（参照 round4 M-f）。

### 裁决

**有条件通过（须采纳建议 1–3）。** D3 缓存在当前场景（中医古籍 OCR，非 PII/PHI）风险可接受，但机制上留下了"敏感数据残留"面。若 KZOCR 后续扩展到现代病历/处方 OCR，该面升级为严重；建议在方案提交时即采纳上述三条建议，一次性闭环。

---

## D4 — 层级异常检测

### 数据敏感性评估

输出文件 `{output_dir}/hierarchy_anomalies.json` 内容：
```json
[
  {"recipe_no": "16.7.1", "depth": 3, "source_page": 42, "resolution": "pending"}
]
```

仅含方剂编号、深度、页码和解决状态。**不含鉴别患者信息、不含处方剂量、不含全文文本。** 前述 D3 的风险在此不适用。

### 裁决

**通过，无安全漏洞。** 无敏感数据残留风险。记录供完整性审计之用。

---

## 其他安全关注点

### [O1] `is_complete` 的 TOCTOU 窗口

D3 模式是：
```python
if is_complete(cache_path):          # 检查
    text = cache_path.read_text(...)  # 使用（不同时刻）
```

两操作间有极小的时间窗口。本地文件系统上 TOCTOU 风险极低，但不可忽视在共享文件系统/NFS 场景的可能。建议将读-检查合并：
```python
try:
    text = cache_path.read_text(encoding="utf-8")
except (FileNotFoundError, OSError):
    # 文件不存在或不可读 -> 正常执行 OCR
    pass
else:
    if text:  # 非空
        ...
```

或明确声明：本系统的 `output_dir` 仅单进程单线程独占访问，NFS/共享文件系统不在设计目标内（这是合理的排他声明）。

### [O2] 无内容校验的缓存命中

当前 `is_complete` 只检查 "文件存在且 `st_size > 0`"，不验证内容的有效性（UTF-8 有效、属于预期文本格式等）。若缓存因磁盘错误写入乱码内容，重跑时不会重新 OCR。

建议：`is_complete` 可考虑在缓存写入时存储一个简单校验和（`sha256[:8]` 到文件名后缀），读时校验。但此建议为"锦上添花"（非 blocker），磁盘错误概率远低于重跑一份 500 页 PDF 的成本。

### [O3] D1 异常体系与 v0.4 C3 限流器持久化的交互

v0.4 AMEND 的 C3 限流器使用持久化机制（参考 `kzocr_engines_security_review_v04.md`：C3 限流器 DoS 需持久化）。D1 的 `retry_with_policy` 复用 `ratelimit.py` 的 `ExponentialBackoff`，当前两者之间没有接口耦合冲突。但注意：若 `ExponentialBackoff` 是进程内状态（默认），长时间运行后重试计时器可能漂移；若需持久化状态，需在 D1 集成时确认 C3 限流状态与 D1 重试退避状态不冲突。此项属**架构评审**范围，安全视角仅记录供参考。

---

## 与 v0.3/v0.4 安全成果的交互

| v0.4 安全项 | 与 v0.5 D1–D4 的关系 | 影响 |
|-------------|----------------------|------|
| B6 — allowlist 冻结代码常量 | 无交互 | — |
| M-c — 出境审计日志 | D3 缓存写入不涉及出境（纯本地落盘），不需审计 | — |
| M-e — `is_mock` 透传阻断 | D2 重试耗尽后 `_record_failure` 为 `OcrSkipError`，不影响 mock 状态 | — |
| M-f — 归档落盘 `0600` | D3 缓存落盘应统一为 `0600`（见 D3 建议 3） | 建议 |
| C2 — `atomic_write` 路径穿越防护 | D3 调 `atomic_write` 未传 `allowed_base`（见 D3 建议 2） | 建议 |

---

## 一句话裁决

v0.5 AMEND 的 D1/D2/D4 在安全视角干净——D1 不劣于现状、D2 明显优于现状、D4 无害。**唯一需关注的 D3 VLM 缓存存在敏感数据残留面**：缓存文件含 PDF 全文明文、无 TTL/生命周期策略、缺 `allowed_base` 防护。建议在方案中补入缓存 TTL、完成后清除选项和 `allowed_base` 参数后，即可安全进入实现。
