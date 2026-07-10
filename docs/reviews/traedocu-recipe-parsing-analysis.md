# traedocu 方剂结构化入库解析 — 关键设计提取报告

> 基于 `/home/keen/Documents/trae_projects/traedocu/ocr_pipeline_v2/`  
> 文件：`db_builder.py`（1283行）、`section_merger.py`（633行）、`db/schema.sql`（267行）

---

## 1. 九字段提取逻辑（纯规则，无需 LLM）

### 字段列表
`FIELD_IDENTIFIERS = ["来源", "组成", "用法", "功用", "方解", "主治", "加减", "疗效", "附记"]`

### 提取算法（`_parse_recipes` 方法，第201-307行）
1. **逐行扫描** OCR 文本
2. **方剂标题行检测**：正则 `RECIPE_NO_RE = r"^(\d+\.\d+(?:\.\d+)?)\s+(.+)"` 匹配 `"1.1 特效感冒宁"` → 创建新 recipe 容器
3. **字段首行检测**：当前行以 `fid + 全角空格` / `fid + 半角空格` / `fid + ◊` 开头 → 提取值存入 `recipe["fields"][fid]`
4. **续行处理**：非字段首行的行追加到 `current_field`（跨行拼接）

**KZOCR 可直接复用**：仅需将 `FIELD_IDENTIFIERS` 替换为中医古籍方剂的字段标签（如 KZOCR 的 `[来源, 组成, 用法, 主治]`）。

---

## 2. recipe_herb 表解析逻辑（`_upsert_herbs`，第467-580行）

### schema 关键字段
| 字段 | 用途 | 示例 |
|------|------|------|
| `herb_name` | 药名（保持原文） | "甲珠"（不改"穿山甲"） |
| `dosage` / `unit` | 剂量 + 单位 | "10" / "克" |
| `preparation` | 炮制标注 | "制"、"炒"、"先煎" |
| `dosage_group` | 剂量组号 | 共享"各X克"的药同组 |
| `dosage_min`/`max` | 数值化剂量（支持范围查询） | 6.0 / 10.0 |

### 解析算法
1. 按 `"，"` 切分为组（groups）
2. 每组有三种模式：

**模式A — "各X克"句式**（`re.search(r"各(\d+(?:\.\d+)?)(克\|ml\|...)", group)`）
- 示例：`"苏叶、薄荷、藿香、防风、荆芥各10克"`
- 提取 `"各"` 前的药名列表（按"、"分割）
- 每味药分配相同 dosage + unit + dosage_group
- 剂量组号自增，同组药关联

**模式B — 单味加剂量**（`re.search(r"(\d+(?:[~～]\d+)?)\s*(克\|ml\|...)", group)`）
- 示例：`"金银花12克，甘草3克"`
- 提取药名 + 剂量 + 单位
- 支持范围剂量：`"10~15克"` → dosage_min=10, dosage_max=15
- 无"各"字的逗号分组：仅最后一味药分配剂量，其余剂量留空（中医惯例）

**模式C — 无剂量**：纯药名列表，剂量字段为空

**炮制提取**：`re.search(r"[（(](.+?)[）)]", herb_name)` → 存入 preparation，药名中去掉括号内容

### 可复用到 KZOCR 的关键点
- `dosage_group` 字段设计优雅解决了"各X克"共享剂量问题
- `dosage_min`/`dosage_max` 支持范围查询，比纯字符串更实用
- 炮制标注的分离逻辑可直接复制

---

## 3. modification 表解析逻辑（`_upsert_modifications`，第614-737行）

### schema 核心
| 字段 | 用途 |
|------|------|
| `condition_text` | 条件："咽喉痛者"、"咳嗽痰多稠者" |
| `action_type` | `add`/`remove`/`replace`/`adjust` |
| `content` | 加减内容原文 |
| `context_ref` | 上下文继承（指向上一条件） |
| `is_ambiguous` | 解析存疑标记 |

### 解析算法
1. 按 `"；"` 分割为 raw_items
2. 对每个 raw_item，检测是否含多条件子句：
   - 用 `re.split(r"(?<=[者时])\s*[,，]\s*(?=[^\s，,；;]+[者时][，,]|\w[者时](加|去|易))", raw_item)` 拆分
   - 示例：`"咽喉痛者，加桔梗10克；咳嗽痰多稠者，加浙贝母10克，清稀者加半夏6克（制）"` → 拆为3条
3. 每条提取：
   - `action_type`：含"去"+"加"→replace，仅"去"→remove，"易"→replace，默认add
   - `condition`：`r"(.+?[者时])"` 提取条件字
   - `context_ref`：短条件（≤4字且以"者"结尾）继承前一条条件的 ID
4. 子表 `modification_herb`：按 `[，、]` 分割药名+剂量

### 可复用亮点
- `context_ref` 字段设计解决中医"省略继承"（如"清稀者"继承"咳嗽痰"的条件）
- `action_type` 枚举清晰覆盖加减替换场景
- `is_ambiguous` 标记为人工审核留接口

---

## 4. LLM vs 纯规则边界

### 可纯规则实现
| 模块 | 说明 | 可靠性 |
|------|------|--------|
| 九字段分割 | 按标识符前缀+续行 | >95%（OCR识别准确时） |
| 药材解析（"各X克"） | 正则 + 顿号分割 | >90% |
| 单味药剂量提取 | 正则匹配数字+单位 | >95% |
| 加减动作识别 | 去/加/易关键词 | >95% |
| 条件提取（"XX者"） | 正则捕获 | >85% |
| 剂型检测 | 关键字匹配（水煎→汤） | >90% |
| 剂量数值化 | 正则提取+浮点转换 | 100%（纯计算） |
| 层级异常检测 | 根据编号中点判断 | 100% |

### 需要 LLM
| 场景 | 原因 |
|------|------|
| **药名归一化**（同名异写） | "甲珠"→"穿山甲"、"北芪"→"黄芪"等别名映射 |
| **病证智能分类**（`symptom_type`） | 判断"感冒"是主病还是证型，规则过于简单 |
| **加减条件歧义消解** | "清稀者"继承哪个条件，仅靠词长规则不够 |
| **质检异常提醒** | 字段缺失时判断是 OCR 丢字还是方剂本身就没有该字段 |
| **跨页断裂合并** | 代码有规则但复杂场景（多页断裂）易出错 |

### 建议
- **核心入库管道纯规则即可**（traedocu 已验证可处理 2000+ 方剂）
- **LLM 仅用在后处理质检 + 药材标准名映射**
- 药材标准名可先用 `herb_master` + `herb_alias` 表做规则匹配，LLM 兜底

---

## 5. 建议 KZOCR 的最小可行方剂解析范围

### 阶段一：纯规则（两周可实现）
```
recipe 表： 九字段 + raw_text + parse_status
recipe_herb 表：药名 + 剂量 + 单位 + 炮制 + dosage_group (dosage_min/max 可选)
modification 表：条件 + 操作 + 内容 + context_ref (可选)
```
- 复用 traedocu 的 `_parse_recipes`（字段分割）+ `_upsert_herbs`（药材解析）
- 去掉 TOC/章/节层级（KZOCR 无此书级结构）
- 预计能处理 KZOCR 80%+ 的方剂

### 阶段二：扩展（后续）
- `recipe_symptom`：主治分词 + 按证查方
- `herb_master`/`herb_alias`：药材标准名映射
- LLM 质检管道

### schema 可裁减项
| traedocu 字段 | KZOCR 建议 | 原因 |
|------|------|------|
| `hierarchy_anomaly` | 不要 | KZOCR 无三级目录层级 |
| `book`/`chapter`/`section` | 剪裁 | KZOCR 结构更扁平 |
| `dosage_form` | 要（简化关键词集） | 对剂型筛选有用 |
| `parse_status`/`confidence_score` | 要 | 质控必需 |
| `raw_text`/`raw_text_hash` | 要 | 变更检测 + 审计 |
| `modification.context_ref` | 可选 | 简化版可省 |
| `modification_herb` | 可选 | 加减内容存 text 也可 |
