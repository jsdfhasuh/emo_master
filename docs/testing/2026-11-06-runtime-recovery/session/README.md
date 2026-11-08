# 跨标签页 CSRF 会话恢复修复验证

基线：`cdf1250ccc41b78f79b25b87e6db197809875384`。修复日期：2026-11-06。
此处记录 shared repair worktree 中的 Web 定点修复；未提交或推送。

## 已修复行为

浏览器标签页共用 session cookie，但各自缓存 CSRF。旧会话过期后，B 标签页通过本机入口创建新会话，A 标签页随后带新 cookie、旧 CSRF 保存或发布。此前该请求返回普通 403，A 页面保持已登录状态，后续保存一直失败。

后端仅在真实 CSRF 比较失败处抛出专用异常，返回 HTTP 403、原有 `detail: "CSRF denied"` 和 `code: "CSRF_MISMATCH"`。权限与 Origin 检查顺序、403 状态和本机入口的访问条件保持原行为。

前端 API 将带上述代码的 403 交给现有会话恢复通道，并传递“请重新进入”的提示。App 退回入口，用户点击“进入本机模式”后采用服务端实际身份和 CSRF。恢复过程不调用 logout，不注销 B 标签页当前 cookie，不自动重发保存、发布或其他 mutation。请求代次检查阻止旧响应清除已恢复的会话。

Editor 的现有备份钩子与内存 Map 已能跨卸载/重挂保留草稿，故未改 Editor/drafts 源码。恢复继续使用实际返回的 userId、项目和 Runtime 目标作为草稿键，账号切换不会载入其他账号草稿。

## 修改文件

- `web_panel/backend/emo_web_panel/app.py`
- `web_panel/frontend/src/data/api.ts`
- `web_panel/frontend/src/main.tsx`
- `web_panel/frontend/tests/editor_draft_recovery.test.mjs`
- `web_panel/frontend/tests/ux-recovery.test.mjs`
- `web_panel/tests/test_local_auth.py`

## 红灯与绿灯

| 检查 | 修改前 | 修改后 | 收据 |
| --- | --- | --- | --- |
| 实际 App/Editor + 隔离 API VM、共享合成 cookie 边界 | 7 个恢复场景失败；3 个普通 403 反例通过 | 10 通过 | `frontend-red.txt`、`frontend-focused-green.txt` |
| API 类型错误、请求代次、无重发 | 1 失败：未通知恢复 | 1 通过 | `api-red.txt`、`api-green.txt` |
| 实际 FastAPI/AuthStore/TestClient HTTP 验证 | 1 失败：403 缺少错误码；普通拒绝通过 | 2 通过 | `backend-behavior-red.txt`、`backend-focused-green.txt` |
| 前端完整现有测试 | 未重复跑基线全套 | 153 通过 | `frontend-suite-green.txt` |
| 后端 auth/local_auth/app/editor/editor_concurrency/editor_target_isolation/registry/preview | 未重复跑基线全套 | 72 通过，另 134 子测试通过 | `backend-suite-green.txt` |
| TypeScript 与 Vite 构建 | 未重复跑基线 | 通过 | `frontend-build.txt` |

前端四个组合覆盖正常 sessionStorage / 存储被阻止、保存 / 发布入口。每项均断言在用户重新进入前不会自动创建会话、不会调用 logout、不会成功写入或发布；重新进入保持当前 cookie，草稿恢复后必须再次显式点击保存，才能写入。另有实际返回账号、项目、目标变更的隔离测试，以及普通权限/Origin/无代码 CSRF 文本的 403 不触发恢复测试。

HTTP 测试使用应用真实 AuthStore 签发的不同 cookie/CSRF 和真实请求头。先持久化起始草稿，过期旧 session，再通过本机入口取得新 cookie；携带旧 CSRF 或缺少 CSRF 的 PUT 继续返回 403，草稿不变，响应不修改 cookie。重新进入采用当前 session，显式 PUT 成功，Runtime control 和 output 写入保持零。

## 可复现命令

从仓库根目录执行；Python 为 `/workspace/scratch/4e0e3d24442d/review-f308/venv/bin/python`（3.10.22），Node 为 24.22.1。

```bash
node --test --test-name-pattern='cross-tab|ordinary 403' web_panel/frontend/tests/editor_draft_recovery.test.mjs
node --test --test-name-pattern='typed CSRF' web_panel/frontend/tests/ux-recovery.test.mjs
PYTHONPATH=web_panel/backend:src /workspace/scratch/4e0e3d24442d/review-f308/venv/bin/python -m pytest -q web_panel/tests/test_local_auth.py -k 'shared_cookie_csrf or origin_and_permission'
PYTHONPATH=web_panel/backend:src /workspace/scratch/4e0e3d24442d/review-f308/venv/bin/python -m pytest -q web_panel/tests/test_auth.py web_panel/tests/test_local_auth.py web_panel/tests/test_app.py web_panel/tests/test_editor.py web_panel/tests/test_editor_concurrency.py web_panel/tests/test_editor_target_isolation.py web_panel/tests/test_registry.py web_panel/tests/test_preview.py
npm --prefix web_panel/frontend test
npm --prefix web_panel/frontend run build
```

## 中途失败与验证边界

首次后端收集因重建的 Python 环境缺 FastAPI 失败，原始结果保存在 `backend-environment-failure.txt`。恢复 pyproject 声明的 Web 依赖，并安装仓库内 `emo_panel_protocol-1.5.0` wheel 后才运行行为红灯；依赖安装结果见 `web-dependencies.txt`。

首轮后端绿灯尝试暴露夹具问题：空编辑器未持久化默认草稿，每次 context 读取都会生成新页面 ID，使“草稿未变化”的比较失败。该次和重复运行的失败完整保存在 `backend-fixture-failure-1.txt`、`backend-fixture-failure-2.txt`。夹具改为先合法保存起始草稿，再做过期/恢复序列；保留完整草稿与 revision 不变断言。没有放宽断言、增加超时或跳过失败。

`.txt` 收据是原 `.log` 的原字节副本，便于纳入版本控制；原日志仍在同目录。`verification.json` 包含收据和源码摘要。

以上为源码组件、HTTP 和构建验证；未运行自然长时间 Runtime/Web 回放、人工浏览器验收或真实设备验收，不据此声称这些层次通过。
