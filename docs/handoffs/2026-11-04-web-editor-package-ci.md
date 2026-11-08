# Web 页面包浏览器断言修复

执行者：dot。本次修复由 dot 完成，不归于其他开发者。基线为 `e9ea0ac56d12a6558dde68680fde77b58b93f8f2`，仅修改测试与记录，不改变产品或压缩安全策略。

## 已确认的失败与原因

PR #11 的提交 `06f5124378f1f75f1ea4e24751bda2902c16c880` 在 [Ubuntu job 126582111260](https://github.com/jsdfhasuh/emo_master/actions/runs/41832257151/job/126582111260) 的页面包浏览器步骤失败。草稿恢复与 Dispatch 浏览器步骤此前已通过。原始失败片段保存在 [ubuntu-failure.txt](../testing/2026-11-04-web-editor-package-ci/ubuntu-failure.txt)。

根布局切换为 grid 后，页面 `value=fixed` 的断言已经通过。失败发生于 `option.is_enabled()`：Playwright 的 enabled-state 检查会跟随包裹 option 的 label 到其关联 select，因此这里检查的是仍可用的 select，而非设置了 disabled 的 flex option。

改用自动重试的 `expect(option).to_have_js_property("disabled", not enabled)`，直接检查选项自身原生属性。原 fixed/flex 值断言、grid 必须禁用弹性、responsive 必须可选弹性，以及后续拖放/粘贴/BMP 保存发布断言全部保留。没有把错误状态当作成功，也没有为测试关闭整个下拉框。

## 回归

- 新增浏览器无关夹具检查，执行浏览器脚本中的实际 mode 函数。模拟 enabled select/disabled option，原断言失败，新断言通过；错误禁用状态和错误值仍失败。这是测试逻辑单元检查，不替代浏览器验收。
- Browser harness：2 项通过。
- Frontend Node：124 项通过，无失败或跳过。
- Editor Python 3.10：11 项通过。
- TypeScript/Vite、前端打包、Python 3.10 编译及 diff 空白检查通过。

完整浏览器重跑仍待远端 CI：本容器原 Chromium 因进程 socket 限制无法启动；本次另外用 Playwright 1.64.1 获取与 CI 一致的 Chromium headless shell v1248，下载结果不是有效 ZIP，安装失败。未将浏览器未执行记为通过。
