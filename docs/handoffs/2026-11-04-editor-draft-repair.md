# Web 编辑草稿保护与版本冲突修复

执行与记录：dot（OpenAI AI 助手），2026-11-04。基线为远端 `main` 的
`7919f9a5b5335f55f6eeb0fb1e77109653e4f32c`。本记录只说明源码修复与实际验证，
不代表推送、合并、部署或操作用户电脑。修复在独立云端工作区完成。

## 编辑内容与逻辑

- 同一 configured Runtime 目标、同一工程的 Runtime 实例更换，只清理实时预览、
  任务选择、结果和发布状态；保留编辑源、选中项、撤销/重做与剪贴板。
- 按登录用户 ID（旧接口无 ID 时用用户名）、configured target ID、project ID
  隔离未保存恢复副本。副本保留原始草稿 CAS 修订，不按短期 Runtime 实例分开。
  普通工程/目标切换、401 卸载编辑器、明确退出和同标签页重载均可恢复对应源，
  不自动保存、发布或启动检测。不同帐号不会自动载入前一帐号的草稿。
- 恢复副本在本标签页 sessionStorage 中；不包含凭据、运行结果或图片字节。
  成功保存后清理已保存副本。浏览器存储不可用时提示限制，仅保留当前页面的内存副本。
  关闭标签页、清理浏览器数据或换电脑后的恢复不在此保障内；正式保存仍以服务器为准。
- 草稿 CAS 冲突保留本地源并读取服务器当前源，界面提供双方完整源比较。
  用户可明确采用服务器版本（可撤销），或确认用本地源覆盖所显示版本。
  后者仍使用所显示的 revision 做 CAS；比较后服务器再次变化会重新提示冲突，
  不绕开服务端并发保护、不盲目更新本地 base revision。
- 保存请求在途期间继续输入时，只将已提交的快照标为已保存，新输入继续为未保存。
- 对应的旧浏览器实例变更测试改为验证“同工程保留草稿”，项目切换仍验证历史隔离。

代码备注由 dot 留在 Runtime 上下文拆分和恢复键定义处；本记录补充编辑逻辑与边界。

## 实际验证

执行环境：Linux 云端工作区，Node 24.22.1，Python 3.12。

- `npm --prefix web_panel/frontend test`：118 项通过，0 失败，0 跳过。
  包括 9 项实际 React App/Editor + jsdom 事件回归与 4 项恢复存储单元回归。
  jsdom 27.2.0 已锁定为开发依赖，回归纳入默认 `npm test`，不是临时可选脚本。
- React/jsdom 覆盖：Runtime 重启保源/清任务、401 与明确退出、跨用户隔离、
  项目切换恢复、CAS 二次变化拒绝覆盖、采用服务器后撤销、取消退出、
  请求在途继续输入、模块重载恢复、同 projectId 不同目标隔离、存储不可用提示。
  部分场景在同一测试内组合验证，因此场景数不等于测试数。
- `npm --prefix web_panel/frontend run build`：TypeScript 与 Vite 通过。
- `python web_panel/scripts/package_frontend.py`：前端静态资源同步通过；构建输出不提交。
- 两个相关 Python 浏览器脚本语法编译通过，`git diff --check` 通过。

首轮新增 React/jsdom 回归为 5/6，通过测试发现“采用服务器”时延迟的 state updater
读取了已经替换的 docRef，导致撤销不能取回本地源。已改为先冻结本地快照再更新历史，
后续增加测试并全套重跑；未弱化该断言。

## 未执行层级

新增 `web_panel/frontend/tests/editor_draft_recovery_browser.py` 含 5 个真实浏览器场景，
可在构建后运行：

```sh
python web_panel/frontend/tests/editor_draft_recovery_browser.py --browser /path/to/chromium --report /tmp/editor-draft-browser/report.json
```

本环境普通启动和授权提升后的 Chromium 启动均因底层 Unix socket `EPERM`
（`process_singleton_posix.cc: socket() failed: Operation not permitted`）失败，
未进入页面，不能把这 5 项或更新后的历史浏览器测试记为通过。
React/jsdom 验证实际组件事件和状态，不替代真实浏览器布局、原生 Qt、实际 Runtime、
wheel 安装、LAN 或现场设备验收。本次未改后端 CAS 接口，也未重跑整仓 Python/Qt 测试。
