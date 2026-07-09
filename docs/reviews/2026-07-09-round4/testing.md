# KZOCR 统一 OCR 引擎架构 — 测试与质量保障评审（round4 · v0.2）

> 评审对象：`docs/plans/ocr-engine-unification.md`（**v0.2**，已吸收 round3 + `summary.md` 裁决）
> 评审视角：可测试性 / 回归防护 / CI 可跑通性 / round3 问题是否**真闭合**
> 代码现状核验：`kzocr/engines/` 尚未创建（`base.py`/`router.py` 不存在）；`kzocr/engine/types.py:36` 仍只有 `glyph_verified: Optional[str]`，**无 `glyph_status`**；`tests/test_vlm.py` 仍 11 例 + `test_pipeline.py` 4 例 = 15 例，路由断言仍 `patch("kzocr.engine.run._run_vlm")`。
> 范围：仅调查与文档评审，未修改主方案或代码。

---

## 结论

**v0.2 在「契约冻结文本层」对 round3 的 K1–K7 全部给出了对应表述，但属于「文本承诺」而非「契约真闭合」**。核验代码现状后确认：

- **K2（置信度通道）、K3（probe 可注入）、K4（块级对齐）、K6（假适配器）** 在方案文本中已被正确吸收，方向正确；
- **K1（字段语义冲突）是「伪闭合」**：v0.2 把矛盾改写成"新增 `glyph_status` + 保留 `glyph_verified`，或显式迁移消费方——二者择一，定稿时冻结"，**留了 fork 且未在 `types.py` 落地 `glyph_status`**，等于把冲突推迟而非解决；
- **K5（15 测试回归）与 K7（smoke 无依赖）在主方案 §7 中被弱化**：`run_engine` 薄门面迁移只在 round3 `summary` 清单出现，v0.2 正文 §7 阶段 6 未再承诺，存在 15 测试静默失锚的回归风险；
- **v0.2 新引入一个 round3 未单独点名、但 architect 已标注为 residual 的核心测试缺口**：`AdapterPageResult → LineResult` 的转换责任未定义，导致行级置信度/`char_level_json`/裁剪图在 Router 装配时**无契约承载、无断言锚点**。

**总体裁决：有条件通过，但 v0.2 进入阶段 1 前必须先把 4 件事从"文本承诺"落到"契约定义"**：
1. `types.py` 冻结 `glyph_status` 字段（消除 K1 的 fork）；
2. 给出 `ProbeResult` 显式字段表（消除 K3 的"可注入但字段不明"）；
3. 明确 `AdapterPageResult → LineResult` 转换归属层（v0.2 新缺口）；
4. 在 v0.2 §7 显式保留 `run_engine` 薄门面 + 15 测试迁移表（补回 K5）。

---

## round3 问题闭合度（逐条）

### [High] K1 — `glyph_verified` 文本 vs `glyph_status` 枚举语义冲突 → **伪闭合（fork 未消）**

- **v0.2 做了什么**：§4.3 改为「新增 `Line.glyph_status: Literal[PASS|RARE|UNKNOWN|FAIL|UNCERTAIN]`（不占用现有文本语义列）；保留 `Line.glyph_verified` 作『校验后文本』用途（与现有 `mock.py`/导出/落库/CLI 文本消费兼容），**或显式迁移所有消费方——二者择一，方案定稿时冻结**」。
- **为何不是真闭合**：
  1. `kzocr/engine/types.py:36` 当前**只有 `glyph_verified: Optional[str]`，根本没有 `glyph_status` 字段**——契约未落地，仍是口头文字。
  2. 「或显式迁移所有消费方——二者择一」把 round3 要求的"必须显式声明其一"降级成"二选一定稿时再定"，等于**把冲突推迟到定稿**，而非在 v0.2 冻结。一旦定稿时选错（删文本语义），`mock.py`/`export_zai`/`cli`/现有 `test_pipeline.py` 全部失锚——这正是 K1 担心的场景。
  3. §4.3 提到 `auditSource` 改回语义、修正 `to_zai_prisma.py:153` 误写成 `engine_label` 的 bug，但**未在该节给出 `glyph_status` 与 `glyph_verified` 在落库/导出中的共存列定义**（DB 列是 `glyphVerified TEXT` 还是新增 `glyphStatus`？）。
- **测试层如何断言两者不混**（必须落到契约）：
  - `test_glyph_status_is_enum_not_text`：`LineResult.glyph_status` 取值 ∈ {PASS,RARE,UNKNOWN,FAIL,UNCERTAIN}，永不为长文本串。
  - `test_glyph_verified_remains_text`:`glyph_verified` 存校验后文本，`glyph_status` 存枚举，二者写入同一 `Line` 但**列名/字段分离**，断言 `glyph_status != glyph_verified`。
  - `test_glyph_status_persists_on_migration`:无论定稿选哪种，都要有回归测试锁定既有 `test_pipeline.py::test_push_to_zai_writes_all_tables` 的 `Line` 计数（当前 `Line == 3`）不被破坏。

### [High] K2 — 适配器缺置信度/引擎标识通道 → **真闭合（文本层）**

- **v0.2 做了什么**：§2.1 `AdapterPageResult(text, confidence, char_confidences, crop_img, meta)`；`AdapterMeta` 含 `label`（对外 engine_label）、`supports_context`、`supports_confidence`、`min_vram_gb`、`default_enabled`、`kind: Literal[...]`。彻底解决了「单 `str` 丢失逐引擎置信度」与「`engine_label` 硬编码」两处。
- **闭合度判定**：✅ 方向正确、契约充分。CI 可在不读图下断言 `r.confidence >= 0`、`char_confidences` 与 `text` 等长（当 `supports_confidence=True`）。
- **残留（非阻塞）**：`char_confidences` 允许 `None`（VLM 无字级），测试需对 `None` 分支显式断言"退化为行级置信"。

### [High] K3 — `probe_environment()` 真实探测与 CI 确定性互斥 → **文本闭合，但 `ProbeResult` 字段未定义（半闭合）**

- **v0.2 做了什么**：§3 明确「`probe_environment()` 返回可注入的 `ProbeResult`」+「抽成纯函数 `select_adapters(probe, strategy, registry) -> list[AdapterMeta]`」，CI 用注入 `ProbeResult` 确定性验证分支（不真探测）。
- **为何只算半闭合**：
  1. **`ProbeResult` 没有显式字段表**。round3 `testing.md` 参数化为 `ProbeResult(gpu=False, keys={}, ports={}, allow_cloud=True)`，但 v0.2 只在 §3 散文里提到 `gpu/显存/CPU 核数/端口/各云端 key/allow_cloud_vision`，**未给出 dataclass 定义**。测试无法在无 schema 下写确定性 `parametrize`。
  2. **降级链归属在 `select_adapters` 还是 Router 未澄清**：§3 说"降级链收口到 Router（逐个尝试/捕获/降级）"，而 `select_adapters` 是纯函数只返回候选序。那么"单引擎失败→下一候选"的**分支逻辑在 Router 里**，需单独可测；但 v0.2 没说 Router 是否也接受注入（应接受 `ProbeResult` 与 `registry` 双注入）。若 Router 内部再调 `probe_environment()`，可测性回退。
- **建议强制项**：v0.2 §3 补 `ProbeResult` dataclass + `EngineRouter(probe, registry)` 构造注入；`select_adapters` 仅决定候选序（含 consensus 的 N≤2 校验），降级 try/except 留在 Router 且有独立单测。

### [Medium] K4 — 多引擎共识逐行对齐未定义 → **真闭合（改为块级对齐）**

- **v0.2 做了什么**：阶段 2 清单「共识行对齐定义：基于 `ParagraphResult.node_type/heading_level` 做块级键对齐，非裸逐行」；§3 默认 single（假设 4 硬约束）；consensus 仅含云端且 N≤2。
- **闭合度判定**：✅ 对齐从"裸逐行"升为"块级键对齐"，正是 round3 K4 所求，且避开了异构引擎换行点不同的不可测问题。
- **残留（接 K2 新缺口）**：块级对齐的输入是 `LineResult` 列表，而适配器只吐 `AdapterPageResult`（页级文本）。**"页级文本 → 行/块序列"的拆分函数（即 round3 建议的 `split_pages_to_lines`）在 v0.2 中归属不明**——它到底在 `_common.py`、`BaseAdapter`、还是 Router？若不在契约里，共识对齐的输入构造无锚。

### [Medium] K5 — 现有 15 测试重构时失锚 → **弱闭合（主方案 §7 未承诺，仅 summary 清单承诺）**

- **v0.2 做了什么**：阶段 6 清单（summary §5）含「保留 `run_engine` 为 `EngineRouter` 薄门面迁移现有 15 测试」，但 **v0.1→v0.2 正文 §7 阶段 6 改写为「测试交付物（test_router/test_glyph_verifier/test_adapters_protocol + tests/engines/fakes.py + kzocr smoke --adapter fake）」，** 已不再显式提及 `run_engine` 薄门面 **。
- **影响**：当前 15 测试里 `test_vlm.py` 有 5 个路由断言直接 `patch("kzocr.engine.run._run_vlm")` 并断言 `run_engine` 调用它（`test_routes_to_vlm/...`、`test_vlm_failure_falls_back_to_mock`）。若 v0.2 实施时 `run_engine` 被 `EngineRouter` 整体替换、且未在 §7 强制保留门面，这 5 个测试会 `AttributeError` 静默失败（pytest 报 collected-but-errored 或被删）。
- **闭合度判定**：⚠️ **半闭合 / 有回退风险**。要求：v0.2 §7 阶段 1/2 必须明文保留 `run_engine` 作为 `EngineRouter` 的向后兼容门面（签名/行为兼容现有 15 测试），或给出 15 测试逐条迁移表（round3 `testing.md` 已备表，应直接纳入方案）。

### [Medium] K6 — 测试目录结构与假适配器策略 → **文本闭合，但 Fake 同构性未定义**

- **v0.2 做了什么**：§7 阶段 6 列 `tests/engines/fakes.py(FakeOCRAdapter)`。
- **闭合度判定**：✅ 方向对，但**两个测试层隐患未定义**：
  1. **Fake 是否参数化同构 10 个适配器**？若 `FakeOCRAdapter` 是单固定实现，则 `test_adapters_protocol` 只能验证"Fake 自身合规"，无法验证"每个真实适配器都符 `OCREngineAdapter` 协议"。正确做法：`make_fake_adapter(meta: AdapterMeta) -> Adapter` 工厂，按各真实 `AdapterMeta` 镜像生成，使协议测试对 10 个注册项逐一跑同一条断言。
  2. **双协议覆盖**：v0.2 §2.2 有 `OCREngineAdapter`（页级）与 `BookLevelAdapter`（书级，返回 `BookResult`）。`test_adapters_protocol` 必须**分别**验证两类协议，且 `BookLevelAdapter`（kimi `BookPipeline` shim）返回 `BookResult` 而非 `AdapterPageResult`，协议测试不可套同一断言。
  3. **重型适配器 `importorskip`**：记忆提示 kimi 真实引擎处破损重构态，`test_adapters_protocol` 对 `PaddleOCRVl16Adapter`/`SenseNovaAdapter` 等须 `pytest.importorskip`，且"构造优雅失败而非 import 崩"需进测试。

### [Low] K7 — smoke 仅覆盖 mock 链路，未覆盖 Router 装配 → **文本闭合（新增 flag），但无依赖性待验证**

- **v0.2 做了什么**：§7 阶段 6 新增 `kzocr smoke --adapter fake` 无依赖端到端。
- **闭合度判定**：✅ 方向对，但**两个验证点需在方案内显式约束**：
  1. **真无依赖**：`--adapter fake` 路径**不得 import 任何重型适配器/kimi**（`FakeOCRAdapter` 必须零外部依赖），否则 CI 无 GPU/无 key 下仍崩。v0.2 未说 fake 适配器依赖边界。
  2. **复验 round2 H6**：`cmd_smoke` 的 `except` 须捕获 `KHUBError`/`URLError`，否则无 kHUB 时仍崩溃——v0.2 未再提，且 `--adapter fake` 路径应**默认 skip 推送**或注入 no-op 推送，不触网。

---

## v0.2 新引入问题（测试视角）

### [High] N1 — `AdapterPageResult → LineResult` 转换责任未定义（architect residual，最危险新缺口）

- **现象**：v0.2 §2.1 适配器返回 `AdapterPageResult(text, confidence, char_confidences, crop_img, meta)`，是**页级**结构；而层间唯一契约 `LineResult`（`types.py:31`）是**行级**，含 `consensus/engine_texts/confidence/char_level_json/final`。中间"页文本→行/块序列、并把 `confidence`/`char_confidences` 映射到 `LineResult.char_level_json`"的**转换函数归属层完全没写**——不在 `base.py`、不在 `router.py`、不在 `_common.py`。
- **测试影响**：
  - 若转换落在 Router，则 `test_router` 必须断言 `BookResult.pages[].paragraphs[].lines[]` 的 `final`/`confidence`/`char_level_json` 与适配器输出一致；
  - 若转换散落各适配器，则 10 个适配器各写一套，协议测试无法统一断言行级保真；
  - **`char_confidences`（字级）需序列化进 `LineResult.char_level_json`**（现有字段，`types.py:45`），否则 PaddleOCR 的字级置信度在 Router 装配时**静默丢失**——这正是 round2/round3 反复警告的"信息在层间蒸发"。
- **要求**：v0.2 §2.1 或 §3 明确一个单一归属（建议 `kzocr/engines/_common.py: adapter_result_to_line_result`），并作为独立可测单元，配 3 类测试：① 普通文本→多行；② `char_confidences` 长度与 `text` 不一致时截断/补默认；③ `crop_img` 经转换后可在 `Line` 上取到（接 N3）。

### [Medium] N2 — `ProbeResult` 契约字段未显式定义（接 K3）

- 见 K3 半闭合说明。v0.2 必须给出 `ProbeResult` dataclass 字段表（至少 `gpu: bool`、`vram_gb: float`、`cpu_cores: int`、`ports: dict[str,bool]`、`keys: dict[str,str]`、`allow_cloud_vision: bool`），否则 `select_adapters` 的 `parametrize` 测试无法编写。

### [Medium] N3 — `crop_img` 在 `LineResult`/`BookResult` 上无落点（字形 recheck/HumanGate 无契约载体）

- **现象**：v0.2 §2.1 `AdapterPageResult.crop_img` 有回填，§4.2 第 7 点 + §5 说 FAIL/UNKNOWN 行"回看裁剪图并随行推送"。但 `LineResult`/`BookResult`（`types.py`）**没有承载裁剪图的字段**。
- **测试影响**：`test_glyph_verifier` 无法断言"FAIL 行的裁剪图流入 HumanGate 推送体"；`VisionRecheckAdapter.recheck(line, crop_img)` 的 `crop_img` 无处取。
- **要求**：v0.2 §4.3/§5 补 `LineResult.crop_img: np.ndarray | None = None`（或 `crop_img_path`+`bbox`），并在 N1 的转换函数中从 `AdapterPageResult.crop_img` 映射过来。

### [Medium] N4 — `RARE` 态与白名单/混淆集的**优先级**未定义（字形校验边界不清）

- **现象**：v0.2 §4.2 顺序为 归一化 → 白名单 PASS → 中医候选表 RARE → 混淆集 FAIL → 其余 UNKNOWN。但存在边界：
  1. 某字**同时在白名单与混淆集**时谁优先？（如误把正字列入混淆集）应白名单优先，否则会把正确字判 FAIL。
  2. **白名单为空/不全**时：按 §4.2 第 5 点"其余未知→UNKNOWN"，即空库 ≠ FAIL，正确；但 RARE 候选表若也空，则全部 UNKNOWN 送检——需测试"空库推 UNKNOWN 而非 FAIL"的硬约束。
  3. `normalize` 失败（繁体未收录映射）时不应直接 UNKNOWN 淹没——需边界测试。
- **要求**：v0.2 §4.2 用编号显式声明优先级链（白名单 > 混淆集 > RARE 候选 > UNKNOWN），并给三组 fixture 测试。

### [Medium] N5 — consensus 仅 `N≤2` 且「块级对齐」输入构造无测试锚（接 K4）

- 见 K4 残留：块级对齐依赖 `ParagraphResult.node_type/heading_level`，而该结构由 N1 的 `AdapterPageResult→LineResult` 转换产出。若转换只产出扁平行（`node_type="text"` 全默认），块级对齐退化为逐行，共识逻辑仍不可测。需测试"带 heading 的页 → 对齐键含 `heading_level`"。

### [Low] N6 — `glyph_status` 全枚举在 HumanGate 触发矩阵未列全

- v0.2 §5 触发条件列 `glyph_status ∈ {FAIL, UNKNOWN, UNCERTAIN}` + 整页全失败 + `--require-human`/全程 mock。但 §4.2 枚举新增了 `RARE`（不进人工队）与 `PASS`。需测试矩阵确认：`RARE` 与 `PASS` **绝不**触发 HumanGate，`UNCERTAIN` 必触发——防止"RARE 被误当 UNKNOWN 送检"或"UNCERTAIN 漏放"。

---

## 改进建议（含推荐测试骨架 / 目录）

### A. 落地四件"文本→契约"的强制项（进入阶段 1 前）

1. **`types.py` 冻结字段**（消 K1 fork）：
   ```python
   # kzocr/engine/types.py::LineResult 新增
   from typing import Literal
   GlyphStatus = Literal["PASS","RARE","UNKNOWN","FAIL","UNCERTAIN"]
   # ...
   glyph_status: Optional[GlyphStatus] = None   # 新增，枚举，不占用 glyph_verified 文本列
   # glyph_verified: Optional[str] 保留作校验后文本，二者共存
   crop_img: Optional["np.ndarray"] = None       # 新增（消 N3）
   ```
   删除 v0.2 §4.3 的"或显式迁移所有消费方"fork，改为"冻结双字段共存"。

2. **`ProbeResult` 显式 dataclass**（消 N2/K3）：
   ```python
   @dataclass
   class ProbeResult:
       gpu: bool = False
       vram_gb: float = 0.0
       cpu_cores: int = 1
       ports: dict[str, bool] = field(default_factory=dict)   # {"18080": True}
       keys: dict[str, str] = field(default_factory=dict)     # {"sensenova": "x"}
       allow_cloud_vision: bool = False
   ```

3. **`AdapterPageResult → LineResult` 单一转换归属**（消 N1，放 `kzocr/engines/_common.py`）：
   ```python
   def adapter_page_to_line_result(
       r: AdapterPageResult, meta: AdapterMeta, page_idx: int, para_seq: int
   ) -> LineResult:
       """页级结构化结果 → 行级 LineResult（含 char_confidences→char_level_json、crop_img 透传）。"""
   ```

4. **v0.2 §7 明文保留 `run_engine` 薄门面 + 15 测试迁移表**（消 K5 回退），直接采纳 round3 `testing.md` 的迁移表。

### B. 推荐测试目录结构（纳入 v0.2 §7）

```
tests/
├── test_vlm.py                  # 保留（_run_vlm 纯逻辑，经 FakeAdapter 注入）
├── test_pipeline.py             # 完全保留（mock→zai→导出，4 例不受影响）
├── test_router.py               # 新增：EngineRouter + select_adapters 纯函数 + 降级链
├── test_glyph_verifier.py       # 新增：normalize/RARE/混淆集/空库/优先级链
├── test_adapters_protocol.py    # 新增：10 适配器协议一致性 + 注册表完整 + 双协议
├── engines/
│   ├── fakes.py                 # make_fake_adapter(meta) 工厂 + sample 图
│   └── fixtures/
│       ├── sample_page.png      # 固定小体积测试图（纳入仓库）
│       └── glyph_kb_min.json    # 最小白名单/混淆集 fixture
└── conftest.py                  # ProbeResult / registry builder / KB builder fixtures
```

### C. 关键测试骨架

**C1 · 协议一致性（K6 / N6）——Fake 必须参数化同构真实 `AdapterMeta`**
```python
# tests/engines/fakes.py
def make_fake_adapter(meta: AdapterMeta, text="方用白术三钱。") -> "OCREngineAdapter":
    class _Fake:
        meta = meta
        def recognize_page(self, img):
            cc = [0.99]*len(text) if meta.supports_confidence else None
            return AdapterPageResult(text=text, confidence=0.99,
                                     char_confidences=cc, crop_img=None, meta=meta)
    return _Fake()

# tests/test_adapters_protocol.py
def test_all_registered_adapters_satisfy_protocol(registry):
    for name, meta in registry.metas.items():
        if meta.requires_gpu and not has_gpu():
            pytest.skip(name)
        if meta.requires_network and not has_net():
            pytest.skip(name)
        adp = registry.build(name)            # 真实构造（重型 importorskip 内）
        r = adp.recognize_page(SAMPLE_PNG)
        assert isinstance(r, AdapterPageResult)
        assert r.text and 0.0 <= r.confidence <= 1.0

def test_fake_is_isomorphic_to_real(registry):
    # 同一组断言同时跑真实与 Fake，证明 Fake 同构
    for name, meta in registry.metas.items():
        real = registry.build(name) if available(name) else make_fake_adapter(meta)
        assert isinstance(real.recognize_page(SAMPLE_PNG), AdapterPageResult)
```

**C2 · 路由纯函数 + 降级链（K3 / N2）**
```python
@pytest.mark.parametrize("probe,mode,expected", [
    (ProbeResult(gpu=False, keys={}, ports={}), "single", ["paddleocr"]),
    (ProbeResult(gpu=True,  keys={}, ports={"18080": True}), "single", ["paddleocr_vl16"]),
    (ProbeResult(gpu=False, keys={"sensenova":"x"}, ports={}, allow_cloud_vision=True),
        "single", ["sensenova"]),
])
def test_select_adapters(probe, mode, expected, registry):
    assert [m.name for m in select_adapters(probe, Strategy(mode=mode), registry)] == expected

def test_consensus_rejected_without_gpu_local_only():
    probe = ProbeResult(gpu=False, keys={}, ports={})  # 全本地 CPU
    with pytest.raises(RuntimeError):
        select_adapters(probe, Strategy(mode="consensus"), registry)

def test_router_degrade_chain():
    # 注入_probe + 注入首候选抛错 → 验证降级到下一候选（逻辑在 Router，不在 select_adapters）
    router = EngineRouter(registry, probe=ProbeResult(gpu=False, keys={}, ports={}))
    router.adapters["paddleocr"].recognize_page = lambda img: (_ for _ in ()).throw(RuntimeError)
    res = router.run(SAMPLE_PDF)         # 降级到 mock 或下一候选
    assert res.is_mock or res.engine_label != "paddleocr"
```

**C3 · 字形校验边界（K1 / N4 / N6）**
```python
def test_empty_kb_marks_unknown_not_fail(gv):
    assert gv.verify("莪术") == "UNKNOWN"          # 空库：UNKNOWN 而非 FAIL

def test_whitelist_beats_confusion(gv):
    # 若某正字被误列入混淆集，白名单优先 → PASS
    assert gv.verify("黄芪") == "PASS"

def test_confusion_set_fails(gv):
    assert gv.verify("我术") == "FAIL"             # 黄芩↔黄芪 混淆

def test_rare_candidate_not_human(gv):
    assert gv.verify("䗪虫") == "RARE"             # 罕见中医字，不进人工队

def test_humangate_matrix(gv):
    for st in ("FAIL","UNKNOWN","UNCERTAIN"):
        assert triggers_human_gate(st) is True
    for st in ("PASS","RARE"):
        assert triggers_human_gate(st) is False
```

**C4 · 转换保真（N1 / N3）—— 行级信息不蒸发**
```python
def test_adapter_result_maps_to_line_with_char_conf():
    r = AdapterPageResult(text="白术三钱", confidence=0.9,
                          char_confidences=[0.9,0.8,0.95,0.7])
    line = adapter_page_to_line_result(r, META, 0, 1)
    assert line.final == "白术三钱"
    assert line.confidence == 0.9
    assert json.loads(line.char_level_json)["conf"] == [0.9,0.8,0.95,0.7]

def test_crop_img_passthrough_to_line(registry):
    adp = make_fake_adapter(META, crop=IMG)
    line = adapter_page_to_line_result(adp.recognize_page(IMG), META, 0, 1)
    assert line.crop_img is not None               # N3：裁剪图经转换存活
```

**C5 · 无依赖端到端（K7）**
```python
def test_smoke_fake_no_deps():
    # 不 import kimi / 不触网 / 无 GPU
    out = run_cli(["kzocr","smoke","--adapter","fake"])
    assert out.book.is_mock is False               # 注意：fake≠mock，应是真实装配
    assert out.book.engine_label == "FakeOCR"
    assert len(out.book.pages) >= 1
```

---

## 对测试相关假设裁决再确认

1. **假设 1（字形校验默认不加独立再识别视觉模型）** — **再确认支持**。但补强：① `VisionRecheckAdapter` 挂点必须落到 `LineResult.crop_img`（N3），否则 recheck 无图可看；② K1 字段冲突未解前，该机制无落库载体，必须先冻结 `glyph_status`。

2. **假设 2（最小小节可配置 + 经 contentNodeId 挂载）** — **中立，测试无关但建议单测切割函数**。以"最小小节切割函数"为单位单测（边界：无标题/跨页/方剂字段行），避免归档层回归。

3. **假设 3（方剂主链只写 zai，khub 异步可选）** — **再确认强烈支持（测试视角）**。跨库双写会放大 smoke 失败面；记忆提示 khub/真实引擎均处不稳定态，`test_pipeline.py` 现有 4 例只验证 zai，khub 同步必须 `importorskip` 且失败不阻塞主链。

4. **假设 4（默认 single，consensus 仅 opt-in 且 N≤2）** — **再确认强烈支持，直接利好 CI 确定性**。但要求 `select_adapters` 对"无 GPU 全本地 consensus"显式抛错（`test_consensus_rejected_without_gpu`），且 single 模式下"以 UNKNOWN/低置信补触发"需在 Router/Verifier 层有可测断言（呼应 I6，防系统性一致错误漏放）。

5. **假设 5（集中 schema + 每适配器 toml 仅覆盖层 + 密钥不进 toml）** — **再确认支持集中 schema**。测试层要求：配置加载期 schema 校验须有单测（缺字段/类型错即失败）；假适配器配套的 `fake.toml` 与真实同构，使测试隔离干净；密钥绝不进 toml（CI 扫描 `.toml` 含 `api_key`/`secret` 即失败）。

6. **假设 6（KZOCR 内置精简白名单为事实源，kimi term_kb 仅可选增强）** — **再确认支持**。测试必须**不 `import kimi` 即可跑通**；`GlyphVerifier(kb: GlyphKB)` 接收接口而非具体来源，`KZOCR_TERM_KB_PATH` 叠加须校验受控目录（防路径穿越）；空 KB fixture 验证"全 UNKNOWN 不 FAIL"（N4）。

---

## 一句话给用户的闭合度评分

| round3 项 | 闭合度 | 备注 |
|---|---|---|
| K1 字段冲突 | ⚠️ 伪闭合 | fork 未消，`types.py` 无 `glyph_status` |
| K2 置信度通道 | ✅ 真闭合 | 文本层充分 |
| K3 probe 可注入 | 🟡 半闭合 | 缺 `ProbeResult` 字段表 |
| K4 共识对齐 | ✅ 真闭合 | 升为块级对齐 |
| K5 15 测试回归 | ⚠️ 弱闭合 | §7 未承诺 `run_engine` 门面 |
| K6 假适配器 | 🟡 半闭合 | Fake 同构性/双协议未定义 |
| K7 smoke 无依赖 | 🟡 半闭合 | 无依赖性/URLError 复验未约束 |
| N1 转换责任 | 🔴 新缺口 | 最危险，行级信息蒸发风险 |
| N3 crop_img 落点 | 🔴 新缺口 | recheck/HumanGate 无载体 |
| N4 优先级链 | 🟡 新缺口 | 空库/混淆集优先未定义 |
