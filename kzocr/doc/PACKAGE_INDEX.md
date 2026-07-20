> 参见：[ARCHITECTURE.md](../ARCHITECTURE.md) · [storage/](../storage/PACKAGE_INDEX.md) · [adapter/](../adapter/PACKAGE_INDEX.md)（旧位置）

# doc/ — 文档导出模块

| 文件 | 说明 |
|------|------|
| `zai.py` | zai 校对台数据库写入（push_book_to_zai + 模式库写入 + PG 注册） |
| `export.py` | 统一导出：export_markdown（BookResult→MD）、export_book_markdown（zai SQLite→MD）、export_json |
| `proofread.py` | 校对包导入（import_proofread_package + PG 归档） |
| `freeze.py` | custom.db 冻结（freeze_custom_db） |
