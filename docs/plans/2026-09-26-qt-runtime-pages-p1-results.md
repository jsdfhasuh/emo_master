# Qt 运行页面 P1 执行记录

本轮起始 HEAD：`5924c1dcbbcb7fdd36a25833b14e01ce519557fa`，工作分支
`agent/runtime-workflow-architecture-v1`，起始工作区 clean。用户明确允许 P1 开发；
P0 原始报告、FAIL、预算及原始证据不变。**P1 模型/状态层退出条件已满足，本轮止于 P1**。
完整现场页面与发布未实现，不能把本记录当成 P0、P2/P3 或硬件性能验收。

## 提交与正式功能

| 批次 | 提交 | 内容与验证 |
| --- | --- | --- |
| 1 | `3791c2f` | 计划准入、页面/组件/导航严格模型、实例输出目录、绑定类型/调用路径、显式 2.2 迁移与资源模型；27 passed |
| 2 | `7b42ae4` | ProjectEditSession、PresentationStore、WorkflowStore 字段保留；复制重绑、详情、撤销/重做、原子保存；50 passed |
| 3 | `44e1ba7` | 快照/资源解析、指纹、结果封闭/资产 DTO、Loop v2 兼容、局部工作流包兼容、2.2 发布保护和证据脚本；64 passed，Ruff/mypy 通过 |
| 4 | `0fdbcf4` | 广回归发现的旧错误消息兼容修复；81 项定向测试通过，未修改旧测试断言 |

最终受测实现 HEAD：`0fdbcf47a6231074fdb5d646b6b3ce398c81a4cf`，所有最终验证开始时 clean，
各次受测源码前后摘要完全相同。后续提交仅更新本文、主计划和验证证据。
具体代码摘要、真实命令及原始输出见 [证据索引](../evidence/p1-runtime-pages/manifest.json)。

正式模块与改动文件（均位于 src/emo_master 下，文档/测试除外）：

- `core/presentation/{models,catalog,validation,results}.py` 与包初始化：纯 Python/Pydantic。
  未运行即可列出真实插件的节点实例输出；保留可选 overlay 提示，integer/number/boolean 区分，
  Blob/Detection 已知字段投影仍是集合，不自动取第一项。直接节点、工作流输出、只读计数和平台状态分开。
- `apps/designer/state/{presentation_store,project_edit_session}.py`：多页 CRUD、稳定 ID、组件树、
  页面复制/内部导航映射、写时复制重绑、详情选择实际显示 resultKey；统一事务、撤销/重做和保存。
  活动页和结果选择不污染 dirty，不进入 project.json。
- `apps/designer/state/{workflow_store,workflow_package}.py`：原工作流保存和状态替换保留页面/资源；
  工作流局部导入导出不夹带页面，不修改目标页面。
- `core/project/{models,migration,resources,snapshots}.py`：显式升级 2.2；严格验证旧输入后迁移，
  幂等、不改输入、不改变 Loop 契约；debug/release 不可变准备记录、三类指纹、资源/站点参数声明与路径隔离。
- `core/workflow/validation.py`：仅扩展 Loop v2 的项目版本允许集合至 2.1/2.2，旧行为不变。
- `core/project/package_builder.py`：2.2 明确要求后续 P5 发布链路，避免旧打包入口绕过页面/资源验收；2.1 保持原语义。
- `tests/core/presentation/` 5 个文件（含 fixture），`scripts/p1_validate.py`；
  `docs/project-json-spec.md`、[正式 API 契约](../runtime-pages-p1-contract.md)、主计划及本报告。

没有 Qt 渲染器、拖拽工作区、Runtime 展示通道或默认入口接入；没有改变 Job 启动/设备执行行为。
原型只选择性复用了业务规则；正式模块不导入 prototypes。证据脚本仅复用 P0 的外层进程清理工具。

## 环境与实际命令

解释器 `C:\Users\jsdfhasuh\.conda\envs\emo_master\python.exe`，Python 3.10.21 / Windows x64
`Windows-10-10.0.26200-SP0`；PySide2 5.15.2.1、Pydantic 2.13.5、grpcio/grpcio-tools 1.78.0、
protobuf 6.33.6、numpy 1.26.4、opencv-python 4.10.0.84、pytest 9.1.1、Ruff 0.15.6、mypy 2.3.1。
按开发指南配置本仓 src 的 PYTHONPATH、QT_QPA_PLATFORM=offscreen、HUARAY_CAMERA_SMOKE=0；
测试使用临时项目/数据目录，没有真实相机、PLC、机器人或现场数据库操作。

以下 python 均为上述解释器；每次输出目录不同，旧结果未覆盖：

```powershell
python scripts/p1_validate.py --baseline-ref 5924c1dcbbcb7fdd36a25833b14e01ce519557fa --suite ci --output manual_test_workspace/p1/verified-baseline
python scripts/p1_validate.py --suite ci --output manual_test_workspace/p1/final-ci
python scripts/p1_validate.py --suite focused --output manual_test_workspace/p1/accepted-focused
python scripts/p1_validate.py --suite regression --output manual_test_workspace/p1/accepted-regression
```

| 检查 | 实际结果 | 分类与原始证据 |
| --- | --- | --- |
| 起始提交独立 git archive 的 scripts/ci_check.py | proto/Ruff/mypy 成功；pytest 原生访问冲突 3221225477 | 基线 FAIL；[日志](../evidence/p1-runtime-pages/verified-baseline/1.log) |
| 最新 P1 专项 `pytest -q tests/core/presentation` | **61 passed**，1.41 s | PASS；[日志](../evidence/p1-runtime-pages/accepted-focused/1.log) |
| `pytest -q tests/core tests/runtime tests/e2e -rs` | **417 passed, 1 skipped**，41.13 s | 执行成功，skip 不计为通过；[日志](../evidence/p1-runtime-pages/accepted-regression/1.log) |
| Designer 项目/WorkflowStore/局部包/边界/依赖树/codec/主窗口保存加载 7 文件 | **42 passed**，3.72 s | PASS；[日志](../evidence/p1-runtime-pages/accepted-regression/2.log) |
| 最新完整 scripts/ci_check.py | proto/Ruff/mypy 成功；**1018 passed, 2 skipped**，106.91 s | 本次 CI 命令 PASS，非全部用例均执行；[日志](../evidence/p1-runtime-pages/final-ci/1.log) |

各套测试有重叠，不把通过数相加。P1 专项全部执行，单独子进程验证 imports 没有 PySide/PyQt、
Runtime 或 builtin 算子执行模块。最新全仓 mypy 检查 238 个源码文件。
完整 CI 的两项 skip 是已有符号链接权限限制和真实 Huaray 相机测试未启用；没有新增 skip 或放宽断言。

开发过程中新增失败也保留：第一批未绑定 fixture 同时残留详情作用域引用，修正 fixture 后通过；
第三批广回归为 `415 passed, 1 failed, 1 skipped`，失败是旧 2.2 拒绝文案不匹配。
`0fdbcf4` 保留原有断言、拒绝缺字段的版本重标，并恢复 `unsupported project schemaVersion` 前缀；
补充完整 2.2 成功与不完整 2.2 拒绝测试，后续广回归通过。
早期 in-place CI 日志也保留，但执行期间工作树开始修改，**不作为固定基线证据**；
权威基线使用源码前后摘要一致的独立 git archive。

## P1 退出条件逐项核对

| 项目 | 结果 | 证据 |
| --- | --- | --- |
| 页面/组件/导航严格模型、无 Qt 导入 | PASS | 未知字段/非法值/重复 ID/重叠/循环树拒绝，独立进程导入检查 |
| 来源目录与类型/作用域 | PASS | 真实 builtin manifest 元数据、同类不同节点、插件冲突、collection 投影、重复调用/循环作用域 |
| 草稿/发布校验分离 | PASS | 未绑定草稿允许；发布拒绝未绑定；非空错误在 debug/release 均拒绝 |
| 多页管理、详情与复制重绑 | PASS | ID 重分配、自导航映射、来源去重、副本重绑隔离、引用修复单事务、实际显示 resultKey |
| 项目持久化、兼容迁移 | PASS | v1/2.0/2.1 → 显式 2.2、幂等/输入不变/Loop 保留；原子备份、失败不丢草稿；局部包边界 |
| 快照/资源/站点纯模型 | PASS | 不可变 JSON、外观不改算法指纹、重绑改采集指纹、模型换根/缺失/hash/越界、站点白名单和目标冲突 |
| 结果封闭与资产 DTO | PASS | expectedSourceIds 完整清单、导出失败不可 COMPLETE、结果接管、有限租约、游标与业务序号分离 |
| 实际 P2 资源复制/权限/执行编译与调试状态接入 | NOT_RUN | 仅冻结准备模型；不把路径隔离测试当成正式 Runtime 状态隔离验收 |
| 正式 Qt 页面/工作区、现场冻结包、目标工控机性能 | NOT_RUN | 分属后续阶段，本轮未实施 |

## 保留缺陷与限制

1. P0 双消费者读图完整率 FAIL、原 1080p/5 Hz 性能 FAIL 和 RSS/句柄长稳态缺证继续保留，
   分别阻塞 P2/P3 集成、P6 稳定性及现场发布；原预算、时限、性能目标不变。
2. 起始提交的独立 CI 重现 Qt 访问冲突，栈在 `test_operator_icon_integration.py`。
   本轮最终 CI 单次成功，不能据此宣称已有组合崩溃被修复；与 P0 中其他 Qt 崩溃位置的共同根因未证实，
   Qt 集成/稳定性与发布仍须专门解决。没有删除用例或用重试挑选成功结果；全部原始结果保留。
3. 新 API 尚未接入 MainWindow 工作区，未运行正式页面。Snapshot 校验资源后只记录路径，
   P2 必须物化稳定资产并做完整执行编译、算子语义校验、读写权限和设备许可检查；不允许直接把准备记录当执行许可。
4. 参数 schema 支持的约束子集和文件用途适配表是显式有限集合；不支持的约束/自定义文件用途拒绝，
   不猜任意路径。正式图像来源叠加、租约清理/后台进程和网络订阅仍属 P2/P3。

P0 原报告与 evidence 相对 `5924c1d` 的 diff 为空。P1 按调整后的模型/状态开发准入完成，
不是 P0 改判；本轮不进入 P2，不发布现场版本。
