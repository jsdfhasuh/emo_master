# While 布尔条件：实现与验证记录

## 范围

- 新建 While 默认选择循环体的布尔状态端口，不再要求独立条件工作流。
- `conditionMode=boolean` / `conditionPort=hasNext` 由共享契约、编译器、校验器和 Runtime 支持。
- 每轮执行前严格检查当前布尔值；循环体输出更新下一轮状态，初始 false 不执行循环体。
- 保留旧 v1/v2 条件工作流逻辑；旧画布和关系树明确显示 `continue:boolean`。
- 显式切换模式保留现有端口键、连线、节点位置和原工作流；不自动改写用户保存的项目。
- 条件端口失效时保留原选择并阻止应用；支持导出/导入工作流包及工作流 ID 冲突重映射。
- 新增独立的双工作流示例 `examples/image_batch_while_boolean`，不改写原 `examples/image_batch_while`。

## 验证结果

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 核心契约、Runtime、子工作流和 Runtime v2 e2e 回归 | PASS，116 项 | `runtime-core-final.txt` |
| Designer 全量回归 | 842 passed / 2 failed，不是全量 PASS | `designer-final.txt` |
| Ruff，修改涉及的源码、测试和原生验证脚本 | PASS | `ruff-final.txt` |
| Mypy，13 个涉及的源码文件 | PASS；原有未标注函数提示仍保留 | `mypy-final.txt` |
| Windows 原生 UI，100% 缩放 | PASS，新旧模式各 4 个窗口/宽度组合 | `native-100-complete/report.json` |
| Windows 原生 UI，150% 缩放 | PASS，新旧模式各 4 个窗口/宽度组合 | `native-150-complete/report.json` |

原生验证使用实际生产控件、Microsoft YaHei UI 字体和独立 Qt 进程；验证参数窗口
460/640 像素宽、关系树 240/308 像素宽以及画布布尔文字、行高、边界和渲染。
两个完整原生报告均为 `runtimeCalls=0`。带有 `complete/report.json` 的目录才是
已通过证据；较早未带完整报告的目录是验证脚本调试中断产生的临时输出，不计入 PASS。

图片用例实际执行批量导入和 Blur，覆盖 1/3 张图片、新 Job 重置、最后一张不丢失、
布尔模式零次调用条件工作流，以及旧条件工作流模式的对照执行。布尔模式另外覆盖
初始 false、严格类型、错误端口编译失败、取消、超时和迭代上限。

## Designer 剩余两项失败

均位于未修改的 `tests/designer/test_minimal_project_metadata.py`，使用原示例：

1. `testEditorUsesLateCatalogWithoutMutatingSavedGraph` 假定保存的 loader `paramSchema` 为空；
   当前原示例已有 schema，因此断言不成立。
2. `testNormalizationPreservesSavedContractsAndOwnsFallbackSchema` 使用 `nodes[1]` 假定它是 loader；
   当前原示例的该位置是 Blur，因而不存在 `folderPath`。

原示例当前的 body 节点顺序为 `load, blur, input, output`；loader 和 Blur 均已保存 schema。
本次没有改动该示例，也没有放宽这两项检查。首次全量运行中另外两项旧显示文案断言，
已按本次明确的布尔文案更新，并在最终全量运行通过。

## 验收边界

源码和隔离 UI 验证，不代表便携包、发布、部署、相机/PLC 硬件或现场验收。
没有提交、推送、发布或重启用户当前 Designer/Runtime。重新启动双方后才加载新源码。
