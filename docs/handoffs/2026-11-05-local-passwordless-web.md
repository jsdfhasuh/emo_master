# 本机免密管理员交接

由 dot 实施并推送。基于 PR #11 `8fc3433973a865823e1b596dadf3dc8bc7f04fd1`，继续原分支 `codex/postmerge-usability-fixes-20261104`，不合并或部署。

## 使用与逻辑

计划见 [本机免密实施计划](../plans/2026-11-05-local-passwordless-web.md)，启动说明见 [部署文档](../../web_panel/docs/DEPLOYMENT.md#本机免密管理员模式)。

- `serve --local-no-auth` 默认关闭。开启后首次不需要创建账号，使用独立稳定的本机管理员身份；权限依用户明确选择，涵盖全部已登记 Runtime，包括远端目标。
- 普通密码登录继续保留。本机模式复用 session/CSRF/TTL/注销和既有业务权限，旧本机会话不跨服务实例复活。GET 不创建会话；进入端点要求精确同源 POST 和显式请求头。
- HTTP 与 WS 统一限制真实 loopback、精确 Host/Origin，拒绝转发及重复关键头。CLI 不信任代理头；不可反代/隧道暴露，本机用户/进程仍可取得管理员权限。
- 首次自动进入；同标签页退出/到期后，即使刷新也需明确点击进入。浏览器存储不可用时，保守改为显式进入，不阻断正常使用。草稿和待确认请求保留，不自动保存、发布、查询或重发。
- 两页持续提示模式、权限和停用方法；退出不关闭入口，停用要停止服务或移除启动选项后重启。

## 已执行验证

- Python 3.10 完整独立 Web 后端：215 项，208 通过、7 项按既有 owning Runtime 条件跳过。其中新增本机模式 17 项，包括首次零账号、配置、身份/会话隔离、CSRF、过期、退出、HTTP/WS边界、草稿与CLI代理设置。
- 前端最终 131 项全部通过，TypeScript/Vite 构建通过。新增 jsdom 验证双路由首次进入、StrictMode、退出/过期及刷新、原草稿/pending恢复而不重放、普通账号警示、存储不可用与旧health兼容。
- 独立只读安全复核未发现阻断问题；实际 CLI/Uvicorn + HTTP/原始 socket/WS 29 项断言通过，涵盖合法管理员、WS、注销，以及 Host/Origin/转发头/重复头/CSRF 拒绝。真实非回环网络 peer 未运行，通过 TestClient 合成 peer 覆盖。
- wheel 构建与源码树外安装快测通过：静态 UI、首次零账号本机管理员、编辑器 context 和 Host 拒绝有效，不导入 Qt/主应用。
- `git diff --check` 通过。

## 未执行与后续验收

新增 `web_panel/frontend/tests/local_no_auth_e2e.py` 使用真实 Chromium + HTTP/WS + MockRuntime，覆盖首次、保存/刷新、过期/退出/重入、草稿不重放和实时 WS，已加入 Linux/Windows Web CI。

本云工作区启动 Chromium 被底层 IPC socket 权限阻止；允许的提升执行重试仍被同一限制阻止。因此本地真实浏览器用例**未执行，不记通过**，以该提交 CI 结果为准。自动测试不等于现场部署；没有访问用户电脑、真实相机/PLC、现场工程或凭据。此次功能验收不代替原有密码登录的人工安全登录验收。

测试开发期间一次完整后端扫描碰到尚未写完的 WS 用例，其错误为 TestClient 使用默认 testserver URL，已改成显式固定端口 WS URL；最终完整重跑为上述 215 项结果。构建依赖安装及浏览器环境限制均未通过修改产品认证或削弱断言规避。
