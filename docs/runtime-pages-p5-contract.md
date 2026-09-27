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
