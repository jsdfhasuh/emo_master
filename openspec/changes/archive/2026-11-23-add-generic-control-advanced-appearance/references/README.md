# 图稿与原图来源

本目录为方案 v0.6 的参考材料，共 14 页。`advanced_appearance_v0_6.drawio` 保留可编辑的文字、形状、标注和效果原型，历史底图作为锁定图像保留；不是把整页 PNG 放进 draw.io 充当可编辑文件。

当前计划已审查修订为 v0.7。本轮保留这些布局与风格参考，不重画图册；实际实施的参数和结果判定以 [目标效果配置](../examples/target-effects.md) 与 [验收要求](../acceptance.md) 为准。

打开 `index.html` 可离线阅读全部 PNG；通过 diagrams.net 打开 `.drawio` 可修改各页原型。

## 本次同步更新

- 第 01 页：完整范围明确为 13 种可见组件＋2 个页头按钮。
- 第 04 页：实施顺序与验收按全范围交付，三个代表对象仅可作为最先贯通的一步。
- 第 14 页：新增 15 个对象逐项覆盖图，列出适用字段和专用接入点。
- 所有图稿标为 v0.6；Designer 与 Web 的原有布局及入口示意保持此前已经核对的关系。

前面效果页集中展示方向判定、导航与启停按钮，用于说明具体交互和效果；不能把这些示例对象理解为整个控件范围。

## 历史底图

| 本地原图 | 仓库路径 | 证据背景 |
| --- | --- | --- |
| `originals/designer_20261118_original.png` | `docs/testing/2026-11-18-generic-page-composition/designer-page.png` | 2026-11-18 的 Windows Qt 自动化验收截图 |
| `originals/qt_runtime_20261119_original.png` | `docs/testing/2026-11-19-page-configuration/qt-production-1920x1080.png` | 历史 Linux offscreen `QWidget.grab()`；软件 Runtime 与受控样例 |
| `originals/web_editor_20261104_original.png` | `docs/testing/2026-11-03-independent-web-editor/review-round3/final-editor-page-editor.png` | React／Edge，HTTP／WS mock；实际验收日期为 11-04，目录名是 11-03 |
| `originals/web_runtime_20261119_original.png` | `docs/testing/2026-11-19-page-configuration/web-production-1920x1080.png` | 历史 Windows Edge 与软件 Runtime |

完整固定提交链接、原图大小和 SHA-256 见 [sources.json](sources.json)。本轮代码核对基线为 `6cbff0dcbea671663cf9ab773c6ce5c81690da35`；历史截图的采集时间与本次计划日期分别记录。

## 如何理解“改后效果”

新增面板、代码、按钮和状态卡效果是覆盖在历史底图上的独立矢量原型，说明拟改位置与目标外观。本次未启动新功能的 Designer 或 Runtime，也没有获得本轮实装截图。实施时以真实渲染生成替代证据，保留历史原图来源。
