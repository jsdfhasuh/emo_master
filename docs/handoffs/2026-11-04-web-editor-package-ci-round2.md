# 页面包浏览器保存完成等待修复

执行者：dot。本次修复与验证由 dot 完成，不归于其他开发者。基线 `fc1f596`；仅改测试与证据，不改产品、CAS 校验或发布流程。

## 失败与核对

[Ubuntu job 126587661456](https://github.com/jsdfhasuh/emo_master/actions/runs/41833870548/job/126587661456) 在提交 `43c72b` 上已通过根/嵌套 grid、撤销/重做、拖放/粘贴、BMP 上传、保存重开和示例预览，最后等待 `/publish` 响应超时。原日志没有保存失败响应体，不能只凭超时认定后端发布坏了。[原始片段](../testing/2026-11-04-web-editor-package-ci-round2/ubuntu-failure.txt) 保留。

脚本在收到保存响应 headers 后立即 reload；此时前端可能尚未读完 JSON、更新草稿修订并清除旧恢复副本。发布按钮先执行一次保存；如果恢复了旧修订，这次保存会发生 CAS 冲突，不应继续发 `/publish`。

新增真实 React 组件/jsdom 回归将保存响应的 `text()` 消费暂停，确认两条路径：

- headers 已返回、页面仍未保存时 reload，恢复旧修订；发布前保存冲突，发布请求为零，保留本地草稿。这是应保留的安全行为。
- 等待页面“草稿已保存”且保存按钮恢复可用后 reload，使用新的修订，随后正常保存和发布。

这个实际组件反例复现了原脚本缺失同步的后果，不把浏览器 HTTP 响应事件当作应用操作完成。

## 最小调整

- 保存响应成功后，等待用户可见的“草稿已保存”与保存按钮恢复可用，再 reload。
- 重开后再次检查已保存且没有草稿冲突。
- 发布时另外捕获并断言其前置保存响应，失败时直接报告响应体，避免只报缺少 `/publish`。
- 保留全部原有 spacer、BMP、预览和 `PUBLISHED` 断言；增加 UI“发布完成”和操作结束断言。未添加 sleep、延长超时、跳过步骤或自动覆盖冲突。

## 本地验证

- Node：125 passed，0 failed，0 skipped；包含新增真实 React 的中断/正常两条路径。
- Python 3.10 browser harness：3 passed。
- Python 3.10 Editor 后端：11 passed。
- TypeScript/Vite、前端打包、Python 3.10 编译和 diff 空白检查通过。
- 完整 Chromium 仍需远端 CI 重跑。本地浏览器限制延续上一轮记录，未把 jsdom/夹具检查当作真实浏览器验收。
