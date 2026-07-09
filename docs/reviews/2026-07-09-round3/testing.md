# KZOCR 统一 OCR 引擎架构 — 测试与质量保障评审（round3）

> 评审对象：`docs/plans/ocr-engine-unification.md`（重点第 2/3/4 章）
> 评审视角：可测试性 / 回归防护 / CI 可跑通性
> 参考代码：`tests/test_vlm.py`、`tests/test_pipeline.py`、`kzocr/engine/run.py`、`kzocr/engine/types.py`、`kzocr/config.py`、`kzocr/engine/mock.py`

---

## 结论

方案在「架构解耦」方向上正确，但**第 2/3/4 章对测试契约的定义是缺失或自相矛盾的**，直接落地会导致：(a) 现有 15 个测试全部失去断言锚点；(b) `GlyphVerifier` 与现有 `Line.glyphVerified` 字段语义冲突无法落库；(c) `probe_environment()` 在无 GPU/无 key 的 CI 下无法被确定性测试。

核心结论：**必须先冻结三套契约（适配器返回结构、路由选择纯函数签名、字形校验状态枚举与落库字段），再谈实现**。这三套契约冻结后，本架构可以在「无 GPU / 无云端 key / 无网络」条件下做到 100% 单元可测，且只需一个「假适配器」即可跑通端到端 smoke。

**有条件通过**：在补完下述「关键问题」中 3 项 High 后方可进入阶段 1 实现；否则现有测试会在重构时静默失效。

---

## 关键问题（严重度）

### [High] K1 — `GlyphVerifier` 输出与现有 `Line.glyphVerified` 字段语义冲突

- **现象**：方案 §4.3 把 `Line.glyphVerified` 定义为状态枚举 `PASS | UNKNOWN | FAIL | UNCERTAIN`；但 `kzocr/engine/types.py:36` 现有 `glyph_verified: Optional[str]` 存的是**校验后的文本**（`mock_book_result` 中 `glyph_verified="方用白术三钱…"`），`to_zai_prisma.py:41` 的 DB 列 `glyphVerified TEXT` 也存文本，`export_zai`/`cli` 均按文本消费。
- **影响**：若按方案把该字段改成状态枚举，现有 `mock.py`、导出、落库、CLI 全部要改；若不改，则状态无处落地。这是方案与既有 schema 的硬冲突，且方案未提及。
- **要求**：方案必须显式声明——
  - 新增 `Line.glyph_status: Literal["PASS","UNKNOWN","FAIL","UNCERTAIN"]`（或枚举），保留 `glyph_verified` 作为「校验后文本」（可为空）；
  - 或明确删除文本语义、迁移所有消费方。
  - 测试需同时覆盖两种字段的落库与导出。

### [High] K2 — 适配器接口缺少「置信度」与「引擎标识」通道，`engine_label` 契约未对齐

- **现象**：方案 §2.1 `OCREngineAdapter.recognize_page(img) -> str` 只返回字符串；但 §4.2 明确要「OCR 引擎自带的字级置信度（PaddleOCR 有）」参与校验，且 `BookResult.engine_label` 依赖适配器暴露可读标识。
- **现状佐证**：
  - `types.py:13` `EngineResult` 已含 `confidence`，`mock` 中 4 个引擎各有置信度 —— 说明置信度是**逐引擎**的，单 `str` 接口丢失该信息。
  - 现有 `_init_vlm_adapter`（`run.py:222,236,490`）给适配器打 `adapter.engine_label`，而 `BookResult.engine_label` 在 round2 已被 architect 评审指出「硬编码 `PaddleOCR-VL-1.6`」（`architect.md:68`）。方案 `AdapterMeta.name="paddleocr"` 是内部 id，与对外 `engine_label` 不是一回事，契约模糊。
- **影响**：测试无法断言「某引擎产出的置信度被正确传递」「engine_label 反映实际选中适配器」。
- **要求**：接口应返回结构化结果，例如 `recognize_page(img) -> AdapterPageResult(text: str, confidence: float, char_confidences: list[float] | None)`，并从 `meta` 派生对外 `engine_label`（建议 `meta.label`）。

### [High] K3 — `probe_environment()` 真实探测与 CI 确定性测试互斥

- **现象**：方案 §3 `probe_environment()` 直接采集 GPU/显存/端口/key。CI（无 GPU、无 key、无端口）下若真探测，结果不确定，路由选择无法被断言。
- **要求**：`probe_environment()` **必须返回可注入的数据对象** `ProbeResult`，且路由选择应抽出**纯函数**：
  - `probe_environment() -> ProbeResult`（副作用隔离，可 mock）
  - `select_adapters(probe, strategy, registry) -> list[AdapterMeta]`（纯函数，全参数化测试）
  - 这样 CI 用 `ProbeResult(gpu=False, keys={}, ports={})` 即可确定性验证「无 GPU → 选 PaddleOCR 本地非视觉」等分支。

### [Medium] K4 — 多引擎共识的「逐行对齐」未定义，无法写交叉比对测试

- **现象**：方案 §3 策略 B「逐行交叉比对（engine_texts 多源）」。但各适配器 `recognize_page` 返回**整页字符串**，不同引擎可能返回不同行数/换行策略，逐行对齐规则缺失。
- **影响**：`GlyphVerifier` §4.2 第 4 点「分歧 → UNCERTAIN」「多数一致 → PASS」依赖对齐后的行；对齐策略不定义，共识提升逻辑不可测。
- **要求**：在方案或 types 中定义「页面文本 → 行序列」的归一化对齐函数（建议复用/统一现有 `_vlm_markdown_to_pages` 与 `_markdown_to_pages` 两种拆分逻辑为一个 `split_pages_to_lines`），并作为独立可测单元。

### [Medium] K5 — 现有 15 个测试在重构 `run_engine` 三路分支时会全部失锚

- **现象**：`test_vlm.py` 11 个 + `test_pipeline.py` 4 个 = 15 个。其中路由断言（如 `test_routes_to_vlm_when_use_vlm_is_true`）直接 `patch("kzocr.engine.run._run_vlm")` 并断言 `run_engine` 调用它。若 `run_engine` 被 `EngineRouter` 替换，这些 patch 目标不存在，测试静默失败/报错。
- **要求**：保留 `run_engine` 作为 `EngineRouter` 的**薄门面**（向后兼容入口），或把路由断言迁移到 `EngineRouter.select_adapters`，并保留 `_run_vlm` 的纯逻辑测试（拆分渲染/识别/结构化三段，使其经适配器而非 `_init_vlm_adapter` 注入）。

### [Medium] K6 — 方案未给出测试目录结构与「假适配器」策略

- **现象**：方案中 10 个适配器（A/B/C 三组），多数需 GPU/网络/云端依赖。无统一假适配器与 fixtures，CI 无法在不装依赖时验证「适配器均满足协议」「注册表完整」。
- **要求**：新增 `tests/engines/fakes.py` 与 fixtures 目录，提供 `FakeOCRAdapter`（确定性、可注入输出）和内置测试图，并对重型适配器用 `pytest.importorskip` 做「缺失依赖时优雅跳过 + 注册表登记可测」。

### [Low] K7 — `smoke` 子命令仅覆盖 mock 链路，未覆盖 Router 真实装配

- **现象**：`cli.py:89 cmd_smoke` 强制 `use_mock`，断言 `book.is_mock`；它验证的是 mock→适配器→导出→推送，但**不经过 EngineRouter**，因此重构后 Router 装配错误不会被 smoke 发现。round2 `test.md:26` 已指出其 kHUB 推送路径异常类型错误（URLError 未被捕获）会使 smoke 在 kHUB 缺失时崩溃。
- **要求**：新增 `kzocr smoke --adapter fake`（确定性假适配器走完整 Router→Verifier→落库），并修复 URLError 捕获（见 round2 H6，已落地 KHUBError 但 smoke 的 `except RuntimeError` 仍窄，需复验）。

---

## 改进建议（含推荐测试骨架 / 目录结构）

### 1. 冻结三套契约（实现前必须）

```python
# kzocr/engines/adapters/base.py（建议）
from dataclasses import dataclass
from typing import Literal, Optional
from pathlib import Path

GlyphStatus = Literal["PASS", "UNKNOWN", "FAIL", "UNCERTAIN"]

@dataclass
class AdapterMeta:
    name: str                       # 内部 id，如 "paddleocr"
    label: str                      # 对外 engine_label，如 "PaddleOCR-VL-1.6"
    kind: str                       # local-nonvision | local-vision | cloud-vision
    requires_gpu: bool
    requires_network: bool
    needs_api_key: bool = False
    default_enabled: bool = True

@dataclass
class AdapterPageResult:
    text: str
    confidence: float = 0.9
    char_confidences: Optional[list[float]] = None   # 无字级置信度的 VLM 返回 None

class OCREngineAdapter(Protocol):
    meta: AdapterMeta
    def recognize_page(self, img: np.ndarray) -> AdapterPageResult: ...
```

> 注：`engine_label` 由 `meta.label` 派生，解决 round2 architect 指出的硬编码问题（K2）。

### 2. 推荐测试目录结构

```
tests/
├── test_vlm.py                # 迁移后：仅保留 _run_vlm 纯逻辑（经适配器注入）
├── test_pipeline.py           # 保留（mock→zai→导出），不受影响
├── test_router.py             # 新增：EngineRouter + select_adapters 纯函数
├── test_glyph_verifier.py     # 新增：GlyphVerifier 逐字/共识/空库边界
├── test_adapters_protocol.py  # 新增：10 适配器协议一致性 + 注册表完整
├── engines/
│   ├── fakes.py               # FakeOCRAdapter / 确定性假适配器
│   └── fixtures/
│       ├── sample_page.png    # 固定测试图（小体积，纳入仓库）
│       └── glyph_kb_min.json  # 最小字形白名单 fixture
└── conftest.py                # 共享 fixtures：ProbeResult、registry builder
```

### 3. 适配器可测性骨架（K6 / K2）

```python
# tests/engines/fakes.py
@dataclass
class FakeOCRAdapter:
    meta: AdapterMeta
    _fixed: str = "方用白术三钱，茯苓二钱。"

    def recognize_page(self, img: np.ndarray) -> AdapterPageResult:
        # 不读图、不依赖模型：固定输出 + 固定置信度，保证 CI 确定性
        return AdapterPageResult(text=self._fixed, confidence=0.99,
                                 char_confidences=[0.99] * len(self._fixed))

# 协议一致性：遍历注册表，每个适配器都能对固定图返回结构化结果
def test_all_registered_adapters_satisfy_protocol(registry, sample_page_png):
    for name, adapter in registry.items():
        if adapter.meta.requires_gpu and not has_gpu():
            pytest.skip(f"{name} needs GPU")
        if adapter.meta.requires_network and not has_net():
            pytest.skip(f"{name} needs network")
        r = adapter.recognize_page(sample_page_png)
        assert isinstance(r, AdapterPageResult)
        assert r.text and r.confidence >= 0.0

# 重型适配器缺失依赖时，构造应优雅失败而非 import 崩
def test_heavy_adapter_import_skips_without_deps():
    pytest.importorskip("tcm_ocr")   # 见记忆：kimi 真实引擎处于破损重构态
    from kzocr.engines.adapters.paddleocr_vl16 import PaddleOCRVl16Adapter
    assert PaddleOCRVl16Adapter.meta.kind == "local-vision"
```

### 4. 路由层测试骨架（K3 / K4）

```python
# 纯函数，全参数化，无需真实探测
@pytest.mark.parametrize("probe,strategy,expected", [
    (ProbeResult(gpu=False, keys={}, ports={}), "single", ["paddleocr"]),
    (ProbeResult(gpu=True,  keys={}, ports={"18080": True}), "single", ["paddleocr_vl16"]),
    (ProbeResult(gpu=False, keys={"sensenova": "x"}, ports={}, allow_cloud=True),
        "single", ["sensenova"]),
])
def test_select_adapters(probe, strategy, expected, registry):
    chosen = select_adapters(probe, Strategy(mode=strategy, prefer=[...]), registry)
    assert [c.name for c in chosen] == expected

def test_probe_injection_not_real_detection():
    # EngineRouter 接受注入的 ProbeResult，不触发真实 GPU/端口探测
    router = EngineRouter(registry, probe=ProbeResult(gpu=False, keys={}, ports={}))
    assert router.select("single")[0].name == "paddleocr"
```

### 5. 字形校验测试骨架（K1 / K4）

```python
def test_empty_kb_marks_unknown_not_fail(gv, line_unknown_chars):
    # 知识库为空：所有 CJK 字 UNKNOWN（不误判 FAIL），仍进待确认
    status = gv.verify(line_unknown_chars)
    assert status == "UNKNOWN"

def test_known_char_pass(gv, line_known_herb):
    assert gv.verify(line_known_herb) == "PASS"

def test_consensus_lifts_disputed_to_pass(gv, line_disputed_three_engines):
    # 3/4 引擎一致且过字形 → PASS；仅 1 个分歧 → 不降 UNCERTAIN
    assert gv.verify(line_disputed_three_engines) == "PASS"

def test_all_engines_disagree_uncertain(gv, line_all_disagree):
    assert gv.verify(line_all_disagree) == "UNCERTAIN"

def test_vlm_no_char_confidence_degrades_to_line_level(gv, line_vlm_only):
    # VLM 无字级置信度 → 退化为行级，靠共识补强（不抛、不 FAIL）
    assert gv.verify(line_vlm_only) in {"PASS", "UNKNOWN", "UNCERTAIN"}
```

> 要点：GlyphVerifier 的输入应是 `LineResult`（`consensus`/`final` + `engine_texts` + `confidence`），输出写 `Line.glyph_status`（新增字段，K1），落库与导出同时携带 `glyph_status` 与 `glyph_verified`(文本)。

### 6. 端到端 smoke 增强（K7 / K5）

- 保留 `kzocr smoke`（mock 全链路，向后兼容现有 15 测试中的 pipeline 类）。
- 新增 `kzocr smoke --adapter fake`：用 `FakeOCRAdapter` 经 `EngineRouter` → `GlyphVerifier` → 落库 → 导出，全程无 GPU/网络，验证统一架构装配正确。
- 复用 `tests/engines/fixtures/sample_page.png` + `FakeOCRAdapter`，断言 `BookResult.engine_label == meta.label`、`pages` 行数、导出 Markdown 含预期文本。
- 复验 round2 H6：`cmd_smoke` 的 `except` 需能捕获 `KHUBError`/`URLError`，否则无 kHUB 时仍崩溃。

### 7. 回归防护（K5）

- 保留 `run_engine` 为 `EngineRouter` 薄门面，使其签名/行为与现有 15 测试兼容；路由断言改为对 `EngineRouter.select_adapters` 的参数化测试。
- 迁移表（15 测试去处）：

| 现有测试 | 处置 |
|---|---|
| test_routes_to_vlm / to_real / mock_precedence / vlm_failure_* (5) | 改写为 `EngineRouter.select` + 降级到 mock 的断言（门面保留则可不动） |
| test_vlm_renders_pdf_pages_to_markdown / multi_line / empty (3) | 拆为：渲染 `_pdf_page_to_numpy`、识别（注入 FakeAdapter）、结构化 `_vlm_markdown_to_pages` 三段单测 |
| test_vlm_markdown_to_pages_* (2) | 保留，并入统一 `split_pages_to_lines` 单测 |
| test_run_real_regression_unaffected (1) | 改为断言 Router 在 `prefer=["kimi"]` 时选中真实适配器（importorskip kimi） |
| test_mock_engine_* / push_to_zai / export_* (4, pipeline) | 完全保留，不受架构迁移影响 |

---

## 对第 8 章假设项立场

1. **假设 1（字形校验机制：字典+置信度+共识，暂不加独立再识别视觉模型）**
   **支持作为默认**，但要求：GlyphVerifier 必须预留 `VisionRecheckAdapter` 挂点（对 FAIL/UNKNOWN 行回看裁剪图），方案应在 §4 注明其为「可选扩展」而非彻底排除；同时**必须先解决 K1 字段冲突**，否则该机制无落库载体。

2. **假设 2（最小小节定义：TOC 三级 vs 更小）**
   与测试正交，但建议测试以「最小小节切割函数」为单位单测（边界：无标题、跨页、方剂字段行），避免归档层回归。立场：中立，倾向「段落/方证」更细粒度以利检索。

3. **假设 3（方剂库归属：zai Formula 表 vs khub 独立库）**
   测试视角：**优先 zai `Formula` 表**（现有 schema 已覆盖，`test_pipeline` 已验证），khub 同步作为可选后置。跨库双写会增加 smoke 失败面，反对在阶段 5 强耦合 khub（尤其记忆提示 khub/真实引擎均处不稳定态）。

4. **假设 4（consensus 模式成本：无 GPU 默认仅 single，consensus 可选）**
   **强烈支持，且直接利好测试**：默认 single 使 CI 在无 GPU 下确定、快速、可断言；consensus 作为显式开关，其「逐行对齐 + 多源比对」逻辑用注入 fixture 单测（见 K4）。

5. **假设 5（适配器配置：集中 config.py vs 每适配器 *.toml）**
   **支持每适配器独立 `*.toml`**，并建议在 `tests/engines/fakes.py` 配套 `fake.toml`，使假适配器配置与真实适配器同构，测试隔离更干净，避免 `Config` 膨胀。

6. **假设 6（字形知识库来源：复用 kimi `term_kb` vs KZOCR 内置精简白名单）**
   **支持 KZOCR 内置精简字形白名单 + 可选注入 `term_kb`**。理由：记忆「kimi 真实引擎处于破损重构状态」表明强依赖 kimi 仓库风险高；测试必须**不 import kimi** 即可跑通（见 K6 `importorskip`），故知识库须可独立注入。架构上 `GlyphVerifier(kb: GlyphKB)` 接收接口而非具体来源。

---

## 落地优先级（给阶段 1–3 的建议顺序）

1. 冻结 K1/K2 字段与接口契约（阻塞一切实现与测试）。
2. 建 `base.py` + `registry.py` + `FakeOCRAdapter` + `ProbeResult`/`select_adapters` 纯函数（阶段 1–2）。
3. 写 `test_router.py` / `test_adapters_protocol.py` / `test_glyph_verifier.py`（先于适配器实现，TDD）。
4. `run_engine` 改为门面，迁移现有 15 测试（阶段 2 收尾）。
5. 增强 `smoke --adapter fake` 做无依赖端到端（阶段 3 验证）。
