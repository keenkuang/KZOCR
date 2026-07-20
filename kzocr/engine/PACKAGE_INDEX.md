> 参见：[ARCHITECTURE.md](../ARCHITECTURE.md) · [adapters/](../adapters/PACKAGE_INDEX.md) · [scheduler/](../scheduler/PACKAGE_INDEX.md)

# engine/ — OCR 引擎运行与核心类型

| 文件 | 说明 |
|------|------|
| `types.py` | 核心数据结构：BookResult、PageResult、LineResult、ParagraphResult 等 |
| `run.py` | 引擎运行主入口（run_engine、编排管线、PDF 渲染、版心裁切） |
| `adapters.py` | 引擎适配器基类与协议（EngineRunner、AdapterPageResult） |
| `engine_config.py` | 引擎配置定义（EngineConfig、LibraryConfig） |
| `mock.py` | mock_book_result 工厂（测试用） |
| `toc.py` | 目录抽取 |
| `section_merger.py` | 章节合并 |
| `layout_crop.py` | 版心裁切（PP-DocLayoutV3 + cv2 降级） |
| `registration.py` | 引擎注册信息 |
| `prompt_manager.py` | LLM 提示词管理 |
