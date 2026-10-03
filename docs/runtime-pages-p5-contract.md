# P5-A 开发态测试项目交付

这是源码态测试项目机制，不是完整 P5、冻结 EXE 或现场发布批准。
沿用 Python 3.10 / PySide2；原 P0/P2/P3 性能 FAIL、资源稳态缺证及 Qt 组合崩溃保持阻塞。

## 导出

Designer 的 `EMO_PAGE_DESIGNER=1` 工作区工具栏增加“导出测试项目包”。
先保存并登记输入资源，选择项目外目录；冻结的是统一 ProjectEditSession 当前草稿。
`core.project.package_builder.buildPageTestPackage(document, projectDir, outputDir)` 是新显式 API。
旧 `buildPackage` 对 2.2 的拒绝保护保持；不递归收集项目文件。

`emo-pages-test-1` 清单单独管理交付格式，项目2.2 / presentation1.0不变。
清单含实际应用版本、受信任内置算子版本、组件版本、文件大小/SHA256和内容修订。
兼容策略暂为应用版本精确匹配，不作未经验证的跨版本兼容承诺。
包仅含规范 project.json、manifest.json 和已声明且被引用的本地图像。
资源/站点参数的开发路径替换为空占位，执行前必须通过正式准备物化；不回退开发路径。
包内不能安装或执行插件。摘要证明完整性，不是来源签名。

目前允许 ImageLoader1.1.0 / Blob1.2.0 / Count1.1.0 / ImageSaver1.0.0，
一个明确调用作用域、最多16来源、跨页共用一个图像源。设备、秘密、自定义插件、模型及
静态图片/图标资源交付暂明确拒绝。输出仅声明 output_file，启动时明确提供相对站点值。
沿用P1发布绑定校验、release快照和真实 WorkflowCompiler，导出不运行算子。
静态非空text和无绑定runtime_status按P3语义合法；空文字、未绑定业务控件继续拒绝。

预算：项目JSON1MiB、清单1MiB、单图8MiB、最多32资源/34文件、总解压64MiB、ZIP72MiB。
这些是开发包准入上限，不扩大P2图像、导出/读图期限或P3客户端预算。
有限复制后复核摘要，输出使用临时文件和原子替换；草稿后续编辑不改变已有包。

## 导入与启用

`python scripts/p5_project.py export --project DIR --output OUTSIDE_DIR`
复用Designer导出API。`import --package FILE --store EMPTY_DIR`仅导入，输出内容revision；
`activate --revision REVISION --store DIR`单独启用；`rollback --store DIR`回到上一完整版；
`status --store DIR`查询。脚本通过自身位置确定源码，支持非项目cwd和中文/空格目录。

`core.project.delivery_store.DeliveryStore`先限制ZIP大小、中央目录64KiB及条目数量，再检查所有
规范路径、Windows别名/保留名称、大小、重复项、链接/特殊文件、压缩方式和CRC/SHA256。
最多64MiB校验内容在内存中，所有条目完整性验证后才写独立暂存。拒绝额外脚本/DB等未声明文件。
暂存完成正式模型、插件版本、release绑定和编译复核，目录整体改名成为不可变内容revision。
`active.json`一个原子指针同时记录active/previous；失败不修改现用版，回退重验目标全部文件。
最多8个保留版本，超额拒绝，不自动删上一版或运行数据。没有数据库降级/迁移。

OS文件锁从Runtime准备开始持有至明确停服；持有时拒绝导入/启用/回退，包含尚未运行阶段。
这是比“仅检测中不可切换”更保守的P5-A策略。跨进程与同进程冲突都拒绝；崩溃由OS释放锁，
锁文件不通过删除来抢占。只读页面关闭不归还Runtime所有权。状态查询不隐式启用或启动。

## 独立源码入口和生命周期

```powershell
python scripts/p5_runtime.py --store C:/test/store --data C:/test/runtime --ready-file C:/test/runtime/ready.json
# 另一终端打开待运行页面，点击“明确启动本地图像检测（测试 release）”才执行：
python scripts/p5_operator_view.py --ready-file C:/test/runtime/ready.json
# 只读重连指定现有Job（ID显示在启动壳）：
python scripts/p5_operator_view.py --ready-file C:/test/runtime/ready.json --job JOB_ID
# 明确停止本测试宿主；观看端关闭不会执行此命令：
python scripts/p5_runtime.py --stop --ready-file C:/test/runtime/ready.json
```

脚本可使用绝对路径从任意cwd调用。Runtime数据目录必须在不可变项目仓库外。
就绪文件必须位于Runtime自己的数据根内；先完成store校验、P2资源准备和真实Capabilities握手才原子写出。
UI后台再核对运行实例和协议，校验包配置后才报告“页面就绪”。不以sleep推断就绪。
控制停止文件只对应握手核对过的当前实例；陈旧描述符拒绝，不按PID杀未知进程。
Ctrl+C也可停止自己启动的宿主。没有后台自动服务、自动检测或启动设备策略。

`apps.runtime.release_host.ReleaseHost`使用唯一RuntimeService和P2 PresentationService。
调用正式 `prepare(mode='release', releaseRevision=内容摘要)`，保留稳定的项目release DB/输出命名空间；
不拿debug快照改标签。P2默认typed Prepare保持原debug兼容；新测试宿主拒绝远端Prepare注入另一草稿。
UI仅通过已有typed Start明确启动已准备ID，随后完全复用DisplaySession/DisplayHub/RuntimePages。
一个宿主允许一次明确测试启动（失败/不确定的Start也不自动重试）；再次检测先明确停服重开。
这是最小测试交付限制，不是连续生产控制器。输出站点使用 `--site FIELD=相对路径`，缺失拒绝准备。

Runtime独占项目和数据目录直到显式停服，包含尚未检测阶段。端口默认由OS分配；指定端口冲突拒绝，
显式aio服务禁用reuseport，不共享同一监听端口。默认旧Runtime入口/无页面项目不切换。
退出宿主先停新请求、停止自有Job，再收尾展示、稳定快照和Runtime，最后释放目录所有权；
release数据库/正式输出不因展示清理被删除。关闭只读界面只取消自己的会话/有限租约。
页面最多两窗共享一次订阅/解码，额度与500ms期限沿用P2/P3。多页/第二窗口/--job重连不Start。

独立窗口模块在 `apps/operator_view`，不导入Designer MainWindow、页面编辑器或工具箱。
后台有界操作队列1、单一操作线程，GUI通过25ms定时器收取完成；启动中关闭会栅栏迟到回调。
图片读取/解码仍由P2固定线程负责；错误/断线/过期和真实检测身份由P3原渲染器呈现。

源码 `python -m emo_master.apps.windows_entry --runtime ...` 和 `--operator-view ...` 做惰性分派，
先freeze_support；无参数Designer及--self-test保持。这是源码分派验证，**未构建或交付EXE**。
现有已发布旧EXE不因此自动获得新参数。外部打包仓库应后续收集新增apps/delivery、operator_view、
runtime/release_host及core/project交付模块，并完成冻结spawn/资源自检；本轮未改外部构建仓库。
