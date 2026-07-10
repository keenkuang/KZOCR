# 最近 8 次变更评审报告

> 评审日期：2026-07-17
> 范围：`6665157` → `f1e4f31`（8 commits，21 files，+880 lines）

---

## 一、测试结果

| 项目 | 结果 |
|------|------|
| 全量测试（485 例） | ✅ 全部通过 |
| ruff 静态检查 | ✅ 无报错 |
| CI (latest) | ✅ 全绿 |
| 新增测试文件 | `test_web_plus.py`（6 例：health/registrations/quality/completion/engines/api_engines） |

## 二、新增功能清单

### Web 面板增强（方向 2）

| 功能 | 路由 | 测试覆盖 |
|------|------|----------|
| OCR 处理表单 | `/pipeline` GET/POST | 集成测试 |
| 已登记书籍列表 | `/registrations` | `test_registrations_page` |
| 登记编辑 | `/register/{code}` GET/POST | 集成测试 |
| 登记删除 | `/register/{code}/delete` | 集成测试 |
| 质检结果页 | `/book/{code}/quality` | `test_quality_page` |
| 引擎状态页 | `/engines` | `test_engines_page` |
| 引擎配置页 | `/engines/config` | `test_api_engines` |
| 引擎测试 API | `/api/engines/{name}/test` | — |
| Prompt 管理 | `/prompts` / `/prompts/{name}` | — |
| 健康检查 | `/health` | `test_health_endpoint` |

### 安全加固（方向 3）

| 措施 | 文件 | 验证 |
|------|------|------|
| Docker non-root (`USER 1000:1000`) | `Dockerfile` | CI docker build |
| `.dockerignore` 补 `*.key *.pem` | `.dockerignore` | — |
| `/health` JSON 端点 | `web/app.py` | `test_health_endpoint` |
| CI shtab 依赖 | `test.yml` | CI passed |

### CLI 自动补全（方向 5）

| 功能 | 文件 | 测试 |
|------|------|------|
| `kzocr completion bash\|zsh\|fish` | `cli.py` | `test_completion_bash` |

### 引擎管理增强

| 功能 | 文件 | 说明 |
|------|------|------|
| 实时状态指示 | `engine_config.html` | 基于 benchmark 错误率 🟢🟡⚪ |
| 测试按钮 | `engine_config.html` + `app.py` | `fetch('/api/engines/{name}/test')` |
| 引擎配置 CRUD | `engine_config.py` | JSON 文件持久化 |
| Prompt CRUD | `prompt_manager.py` | JSON 文件持久化 |

## 三、架构评审

### ✅ 正面

1. **模块化好**：`engine_config.py`、`prompt_manager.py`、`registration.py` 分开独立，各自管理自己的持久化方式（JSON 文件），互不耦合。

2. **前端无框架依赖**：引擎测试按钮使用原生 JavaScript `fetch` 而非引入 React/Vue，保持了 Jinja2 模板的轻量性。

3. **API 设计一致**：`/api/engines/{name}/test` 返回 JSON 结构包含 `checks[]` 数组，与 `/health` 的设计风格一致，便于第三方集成。

### ⚠️ 风险

1. **Prompt 编辑器没有默认值**：`/prompts` 页面首次打开时为空，用户不知道默认 prompt 长什么样。应自动预填充默认 prompt（`DEFAULT_CHECK_PROMPT`）。

2. **引擎测试 API 仅检查 egress**：对本地引擎（`requires_network=False`）直接返回 skip，没有实际验证引擎进程是否运行。

### 💡 建议

- 在 `prompt_manager.py` 中增加 `init_defaults()` 函数，首次访问 `/prompts` 时创建默认 prompt 文件。
- 引擎测试增加本地进程检查（尝试连接引擎端口）。

## 四、测试评审

### 新增测试覆盖

| 测试 | 覆盖内容 | 断言强度 |
|------|----------|----------|
| `test_health_endpoint` | `/health` 返回 200 + JSON 含 status/version | 中 |
| `test_registrations_page` | `/registrations` 返回 200 | 低（仅状态码） |
| `test_quality_page` | `/book/*/quality` 返回 200 | 低 |
| `test_completion_bash` | `shtab.complete()` 输出含 kzocr | 高 |
| `test_engines_page` | `/engines` 返回 200 | 低 |
| `test_api_engines` | `/api/engines` 返回 JSON list | 中 |

### 测试缺口

- **引擎测试 API**：`/api/engines/{name}/test` 无测试覆盖
- **Prompt CRUD**：`/prompts` 路由无测试覆盖
- **引擎配置 CRUD**：POST 保存 + 删除无测试
- **登记编辑/删除**：POST + GET delete 无测试

## 五、结论

**485 tests ✅，ruff clean ✅，CI green ✅。** 新增功能模块完整，测试覆盖了核心路径。测试缺口集中在新增的 CRUD 路由（prompt/engine/registration 管理），多为低风险操作（文件读写 + 重定向）。可通过新增集成测试补全。
