# KZOCR 贡献指南

## 欢迎贡献

感谢你对 KZOCR 的关注！KZOCR 是一个专注于中医古籍数字化的光学字符识别（OCR）引擎，致力于将传统中医典籍从图像转换为结构化文本数据。

无论你是发现了 Bug、提出了新功能建议，还是直接提交了代码，我们都非常欢迎你的参与。

## 行为准则

请遵守 [Contributor Covenant v2.1（简体中文版）](https://www.contributor-covenant.org/version/2/1/code_of_conduct/code_of_conduct.zh-CN.md)，维护一个开放、友好、包容的社区环境。

## 如何开始

1. **阅读 README** — 了解项目概览和快速上手方式。
2. **了解架构** — `docs/plans/` 目录下收录了架构设计文档，建议贡献者先通读。
3. **运行测试** — 确保你当前的环境可以正常运行测试：

   ```bash
   python -m pytest tests/
   ```

## 报告问题

### Bug 报告

标题请清晰概括问题。正文应包含：

- **现象描述** — 发生了什么？
- **环境信息** — 操作系统、Python 版本、依赖版本
- **复现步骤** — 如何稳定复现
- **预期行为** — 你认为应该发生什么？
- **实际行为** — 实际发生了什么？
- **日志/错误输出** — 相关的错误堆栈或日志

### 功能请求

标题请以"建议"或"需求"开头。正文应包含：

- **解决的问题** — 这个功能解决什么痛点？描述具体的使用场景。
- **期望方案** — 你期望的行为或接口设计。
- **可选思路** — 你认为可行的实现方向（可选）。
- **额外上下文** — 相关的代码、文档链接等。

## 提交代码

1. **Fork 仓库** — 点击 GitHub 页面右上角的 Fork 按钮。
2. **创建分支** — 从 `main` 分支创建功能或修复分支：

   ```bash
   git checkout -b feat/xxx   # 新功能
   git checkout -b fix/xxx    # Bug 修复
   ```

3. **编码** — 请遵循[代码规范](#代码规范)。
4. **本地检查** — 运行静态检查和测试：

   ```bash
   ruff check kzocr/
   python -m pytest tests/
   ```

5. **提交** — 提交信息使用 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/) 格式：

   ```
   feat: 添加 xxx 功能
   fix: 修复 xxx 问题
   docs: 更新 xxx 文档
   test: 添加 xxx 测试
   refactor: 重构 xxx 模块
   ci: 更新 CI 配置
   ```

6. **推送并创建 PR** — 推送到你的 Fork，然后通过 GitHub 创建 Pull Request。

## 代码规范

- **Python 版本** — 目标 Python 3.10+，文件开头添加 `from __future__ import annotations`
- **类型注解** — 函数参数和返回值必须包含完整类型注解
- **代码风格** — 运行 `ruff check kzocr/ tests/` 必须无报错
- **测试优先** — 新功能必须附带测试；Bug 修复应添加对应的回归测试

## 测试

- **测试框架** — 使用 pytest
- **测试位置** — 测试文件放在 `tests/` 目录，命名 `test_<module>.py`
- **外部依赖** — 对网络请求、文件系统等外部依赖使用 Mock 替代
- **运行测试** — 请在提交前确保全部测试通过：

  ```bash
  python -m pytest tests/ -v
  ```

## 分支策略

| 分支 | 用途 | 说明 |
|------|------|------|
| `main` | 发布线 | 不允许直接推送。CI 通过后方可合并 PR。 |
| `m1` | 并行开发线 | 用于 khub 模块的并行开发。 |
| 功能分支 | 日常开发 | 从 `main` 创建，命名 `feat/xxx` 或 `fix/xxx`。 |
