# Web 编辑器包生成与网格空白块维修记录

- 执行者：dot。本次代码修改、回归测试与备注由 dot 完成，不归于其他开发者。
- 基线：`7919f9a5b5335f55f6eeb0fb1e77109653e4f32c`，仓库 `jsdfhasuh/emo_master` 的实际 `main`。
- 范围：只修合法高压缩率图片的自生成包被拒，以及 grid 允许选择弹性 spacer 的编辑/发布不一致。使用云端独立 checkout、临时数据和显式 mock Runtime，没有操作用户电脑或真实设备。

## 修改及逻辑

1. 原生成器对所有条目使用 DEFLATE。1024×1024 纯白 BMP（3,145,782 字节）上传合法，但超过导入端 100 倍压缩率上限，预览返回 422。现在先沿用原配方生成包，仅将超过现有限制的条目改用 ZIP_STORED 再生成；依然调用原 `PackageStore.install`，不增加可信绕过入口、不提高外部压缩率/展开大小/单文件限制。
2. 普通包保留原 ZIP 字节与 packageId。不能把所有条目一律改为 STORED：既有存储禁止同一 project/packageRevision 对应不同 ZIP 摘要，那样会破坏旧页面的继续预览与发布。新增测试重建旧 DEFLATE 配方并比较逐字节内容，先安装旧包再安装新包确认身份一致。
3. grid 中 spacer 占用固定单元格，不是响应式伸展。空白模式下拉在 grid 禁用弹性选项并说明原因；切换父布局、拖入、粘贴和复制等既有网格整理路径将直接子 spacer 从 flex 改为 fixed。嵌套响应式容器内 spacer 保持弹性，直到它自己的父布局变为 grid。既有编辑历史保存修改前文档，转换随原操作一起撤销/重做，不引入新状态机制。

## 验证与边界

- 新增 BMP HTTP 回归在修改前实际失败：预览 422 / `Unsafe compression ratio`。修改后覆盖上传、保存、示例预览、发布、导出、独立存储重导入、字节一致、草稿重开和不启动任务；外部高压缩 ZIP 仍拒绝。只高压缩 BMP 条目使用 STORED，manifest 保持 DEFLATE。
- Python **3.10.22**、供应的 **SDK 1.5.0**、独立 Web 依赖：`PYTHONPATH=web_panel/backend python -m unittest discover -s web_panel/tests -v`，**186 total / 179 passed / 7 skipped / 0 failures**。7 项为 3 个 Windows 专项和 4 个显式完整 Runtime opt-in，不记为通过。
- `npm --prefix web_panel/frontend test`：**106 passed / 0 failed / 0 skipped**。新增网格回归在修改前实际得到 flex 而非 fixed；修改后检查根/嵌套转换、响应式保留和复制源不被改写。
- `npm --prefix web_panel/frontend run build`：TypeScript 与 Vite 通过；`python web_panel/scripts/package_frontend.py` 通过。生成静态资源仍依仓库规则忽略，不提交构建产物。
- Python 3.10 编译检查、`git diff --check` 通过。
- 新增 `web_panel/frontend/tests/editor_package_browser.py` 保留正常 UI 的根/嵌套布局、撤销/重做、拖放/粘贴、BMP 上传、保存重开、预览和发布流程。**浏览器未完成执行**：当前容器 Chromium 在创建进程 socket 时 `Operation not permitted`，普通运行和审批后运行均在打开页面前终止；这不是 UI 通过证据。脚本已通过 Python 3.10 编译检查，需在能正常启动 Chromium 的环境运行。
- 本次未执行真实 Runtime、原生 Qt、Windows、安装 wheel 完整验收或现场设备验收。未推送、未合并、未部署。

## 证据

本次成功与失败日志保留于 [维修证据目录](../testing/2026-11-04-web-editor-package-repair/)。首次全量尝试因依赖未齐和稀疏 checkout 缺少 palette/proto 文件中断；补齐依赖与对应受版本控制源码后，以上最终命令重跑通过。保留的前端修改前日志同时包含 spacer 的预期反例以及当时缺少 palette 的环境错误，不将环境错误归为产品回归。
