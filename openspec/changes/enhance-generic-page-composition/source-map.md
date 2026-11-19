# V1.3 源码落点

固定实现基线：[ce8f84ca](https://github.com/jsdfhasuh/emo_master/tree/ce8f84caf4d0fd779e579b23ff82fa4e80de978d)。本表按职责给出实际文件，行号以执行时 HEAD 为准。先复用后续修复，不回退到最初计划的 31afa5ec。

## Qt 文字与状态

| 路径 | 本轮职责 |
| --- | --- |
| src/emo_master/ui/presentation/appearance.py | WrappedText.setText/sizeHint/heightForWidth、ValueView 对齐/状态浅底、主题类型错误 |
| src/emo_master/ui/presentation/renderer.py | 现有容器/值控件挂接与动态业务布局 |
| src/emo_master/ui/presentation/responsive_layout.py | 复用现有尺寸求解；只在必要时修正局部失效传播 |
| src/emo_panel_protocol/theme.py | 唯一主题及前景/浅底类型，不另建主题引擎 |
| tests/ui/presentation/test_components.py、test_responsive_native_layout.py | 优先扩展能够复现短文本到长文本的现有测试；按实际文件与入口选择 |

## 正式模型与 Web 创作

| 路径 | 本轮职责 |
| --- | --- |
| src/emo_panel_protocol/models.py | Props.historySource 的唯一正式定义；Component.checkShape 与 Presentation.checkStructure 区分拥有来源和引用已有来源 |
| src/emo_panel_protocol/published.py | 复用现有 PublishedComponent.history，明确来源ID与页内表格ID |
| web_panel/frontend/src/editor/model.ts | compatible/bindChoice 整数支持；copyComponents 保留来源ID、重映射页内ID |
| web_panel/frontend/src/editor/Editor.tsx | addHistory 保留新UI ID，写正式引用；目录点击/拖放/属性统一 |
| web_panel/frontend/src/editor/PresentationFields.tsx | 从控件的historySource寻找原目录列；允许来源多个显示实例及正确同页表格选择 |
| web_panel/backend/emo_web_panel/editor.py | validate_source、build_package、history_sources 的正式来源引用及再编辑闭环 |
| tests/core/presentation/、web_panel/frontend/tests/、web_panel/tests/ | 复用已有模型、复制、编辑和发布夹具；不新建独立测试平台 |

## Web 发布、查询与显示

| 路径 | 本轮职责 |
| --- | --- |
| web_panel/backend/emo_web_panel/packages.py | 去除UI ID与来源ID必须相等的错误约束，保留类型/互斥/同页关系/当前包完整性 |
| web_panel/backend/emo_web_panel/app.py | 按UI实例找到允许访问的已发布组件，再取history.componentId传给原历史RPC；固定图片相同 |
| web_panel/frontend/src/renderer/HistoryView.tsx | 选中状态用页内表格ID；imageRefs用实际Runtime图片来源ID查找 |
| web_panel/frontend/src/main.tsx、style.css | 紧凑页头与运行设置布局，保留原目标/权限/确认/命令行为 |
| web_panel/frontend/src/renderer/presentation.tsx、PanelRenderer.tsx | row背景整行应用，badge/text表现和neutral清色 |

现有 src/emo_master/apps/runtime/presentation/history.py 与 core/presentation/sqlite_reader.py 继续使用原正式来源ID、anchor、只读预算和opaque imageRefs。默认不改 RPC 或数据库查询。

## 当前生成物与 CI

| 路径 | 本轮职责 |
| --- | --- |
| packages/emo_panel_protocol/pyproject.toml、src/emo_panel_protocol/__init__.py | owning model 实际变化时推进当前 SDK 正式版本 |
| scripts/build_panel_contract.py | 正式生成 Schema、TS、wheel 与manifest |
| web_panel/contracts/panel-contract-v1/ | 唯一当前契约目录 |
| web_panel/backend/emo_web_panel/contract_data/、web_panel/pyproject.toml、.github/workflows/web-panel-ci.yml | 当前内嵌契约、依赖与CI安装版本一致 |
| web_panel/frontend/tests/asset_retirement_browser.py | 根据真实页面诊断/结果身份入口修同步，保留原停止/图片归属断言 |
| web_panel/frontend/tests/product_runtime_e2e.py | 根据真实登录会话和目标就绪修同步，不绕过鉴权 |
| mypy.ini、现有CI脚本 | 保留有效检查；局部修类型，不通过关闭检查消除红灯 |

## 继续使用的样例与证据

examples/generic_page_composition/inspection.emoproj、inspection.emoweb、build_example.py 和 check_archive.py 继续承担同一双页示例。docs/testing/2026-11-18-generic-page-composition/ 保存原实现验证记录。新报告注明最终SHA、当前实际执行和引用旧证据；原目标图继续使用本change的references/。

旧基线源码地图保存在 [V1.2 source-map](https://github.com/jsdfhasuh/emo_master/blob/ce8f84caf4d0fd779e579b23ff82fa4e80de978d/openspec/changes/enhance-generic-page-composition/source-map.md)，不作为当前待改清单。
