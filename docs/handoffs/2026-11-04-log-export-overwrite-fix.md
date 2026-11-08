# 日志导出追加扩展名覆盖修复

- 执行与记录：dot（OpenAI 助手），2026-11-04。此修复由 dot 实施，不代表其他开发者操作。
- 基准：`main` 的 `7919f9a5b5335f55f6eeb0fb1e77109653e4f32c`；开始前核对远端及 `AGENTS.md`。
- 问题：原生保存框确认的是用户输入路径。Designer 历史导出和 Operator Runtime 导出随后追加格式扩展名时，最终路径可能已存在，原子替换会无提示覆盖它。
- 修改：仅在程序追加扩展名、且最终路径已存在时询问覆盖，展示最终路径并默认“否”。拒绝后不启动导出、不改原文件、不报告成功。显式扩展名继续由原生保存框确认，避免重复弹窗；新目标不增加确认。
- 范围：Designer 本机文件与 Runtime 历史；Operator Runtime 当前显示记录、选定文件与全部文件。Designer 当前缓存已有此保护，予以保留。共享原子写入、取消与临时文件清理逻辑未改。

## 验证

Python 3.10.22、PySide2 5.15.2.1、Linux offscreen Qt，使用仓库声明依赖。

- 新回归先验证失败：Designer 本机/Runtime 历史及 Operator 当前记录在修复前均没有最终路径确认。
- `python -m pytest tests/designer/test_log_export.py tests/designer/test_log_history_instance.py tests/ui/operator_view/test_runtime_log_view.py tests/runtime/test_operator_runtime_logs.py -q --timeout=40`：**96 passed**。包含 JSON/JSONL/TXT 接受与拒绝覆盖、新目标无额外确认，以及既有取消、原子替换失败、快照和历史实例回归。
- `python -m ruff check src tests`：通过。
- `python scripts/gen_proto.py --check`：通过。
- `python -m mypy --config-file mypy.ini src`：通过，614 个源码文件无问题。
- `git diff --check`：通过。

未执行全仓 pytest、Windows 原生保存框人工验收、冻结包或真实设备验收。此记录仅对应上述相关测试，不替代完整 CI。
