# 文档与代码一致性评审报告

> 评审日期：2026-07-17
> 目标：验证文档完整性、文档与代码相符程度、识别缺失或过时的文档

---

## 一、文档完整性检查

### 现有文档体系

| 文档 | 说明 | 行数 |
|------|------|------|
| `README.md` | 项目首页（功能/架构/命令/REST API） | ✅ |
| `CHANGELOG.md` | 版本发布记录 | ✅ |
| `CONTRIBUTING.md` | 贡献指南 | ✅ |
| `docs/deploy-v07.md` | 部署与配置指南 | ✅ |
| `docs/TEST_REPORT.md` | 测试报告 | ✅ |
| 计划文档 `docs/plans/*` | v0.3–v0.7 设计文档 | ✅ |
| 评审文档 `docs/reviews/*` | 多轮多角色评审报告 | ✅ |

### 发现的问题

#### 🔴 严重不一致

| # | 问题 | 位置 | 代码/标签 | 文档值 |
|---|------|------|-----------|--------|
| P1 | **版本号过时** | `pyproject.toml:3` | `v0.17`（git tag） | `0.11.0` |
| P2 | **版本标签错误** | `README.md:6` | `v0.17` | `0.14.0` |
| P3 | **项目状态表不全** | `README.md:157-164` | 缺 v0.15/v0.16/v0.17 | 只到 v0.14 |

#### 🟡 文档与代码部分不符

| # | 问题 | 位置 | 代码实际 | 文档描述 |
|---|------|------|----------|----------|
| P4 | **v0.7 部署文档未更新** | `docs/deploy-v07.md` | 当前 v0.17（含 Web/REST API/Docker/批量/质检） | 只描述 v0.7 编排层 |
| P5 | **CHANGELOG v0.14 缺失** | `CHANGELOG.md` | v0.14 已发布（git tag） | CHANGELOG 直接从 v0.13 跳到 v0.15 |
| P6 | **README 功能列表缺项** | `README.md:13-28` | 已有 `batch`/`quality`/登记功能 | 未列出 |
| P7 | **README 命令参考缺项** | `README.md:78-90` | 缺 `kzocr quality check/list`、缺 `kzocr batch`、缺登记功能 | 只列到 v0.14 命令 |

#### 🟢 文档与代码一致

| 检查项 | 状态 |
|--------|------|
| CI 配置与步骤 | ✅ 一致（test/benchmark/chaos/docker） |
| Dockerfile 构建步骤 | ✅ 与 docker-compose.yml 一致 |
| FastAPI 路由与 REST API 文档 | ✅ 自动从代码生成 |
| 测试配置 pyproject.toml | ✅ `testpaths = ["tests"]` 正确 |

---

## 二、修复建议

### 必须修复

1. **`pyproject.toml`**: `version = "0.11.0"` → `version = "0.17.0"`
2. **`README.md`**: 版本徽章 `0.14.0` → `0.17.0`；状态表补 v0.15/v0.16/v0.17
3. **`CHANGELOG.md`**: 补 v0.14 条目（从 git log 补上：JSON导出/Web可视化/批量处理/校对工作台）
4. **`README.md`**: 命令参考补 `batch`、`quality`、`review`；功能列表补全

### 建议更新

5. **`docs/deploy-v07.md`**: 补充 v0.11–v0.17 新增功能（REST API/Docker/批量处理/质检/登记）
6. **`README.md`**: 项目状态表统一用清晰表格格式，补完整版本历史

---

## 三、多角色评审意见

### 架构师视角

- v0.7 设计文档（`docs/plans/ocr-engine-unification.v0.7-DETAILED.md`）仍为最完整的架构参考，v0.8–v0.17 的功能演进基本遵循了"委派模式，旧签名保留"原则。
- 规劝：`pyproject.toml` 版本与 git tag 长期不同步会造成发布困惑。建议 CI 中增加版本号一致性检查。

### 领域专家视角

- `docs/deploy-v07.md` 仍是唯一面向部署者的文档，但其名称为"v0.7"可能让人误以为只适用于 v0.7。
- 建议重命名为 `docs/deploy.md` 并统一更新所有版本内容。

### 测试专家视角

- 测试与文档的对应关系：`docs/TEST_REPORT.md` 内容未更新到当前 479 测试的状态。
- 测试覆盖充足（479 tests），但文档描述的测试数与实际不符。

### 安全专家视角

- 文档中未提及安全最佳实践（如 Docker 非 root 运行、API key 管理等），建议在 deploy.md 中补充。

---

## 四、修复计划

| 步 | 内容 | 涉及文件 |
|----|------|----------|
| 1 | pyproject.toml 版本同步 | `pyproject.toml` |
| 2 | CHANGELOG 补 v0.14 | `CHANGELOG.md` |
| 3 | README 全面更新（版本/功能/命令/状态表） | `README.md` |
| 4 | docs/deploy-v07.md → docs/deploy.md + 内容更新 | `docs/deploy.md` |
| 5 | docs/TEST_REPORT.md 更新 | `docs/TEST_REPORT.md` |
