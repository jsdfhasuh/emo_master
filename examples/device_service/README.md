# 第三方持续设备服务示例

本示例注册 device.example.ticker 和 example.ticker.sample。
驱动不依赖 DeviceManager 私有基类，不需要给 DeviceManager 增加类型分支。
它维护一个后台计数线程，节点及设备页都能调用 sample；reject 参数用于验证一次明确失败后仍可继续使用。

使用 Python 3.10 和项目依赖，从仓库根目录启动，使
examples.device_service.driver 可以被导入。内置根目录与本示例目录都必须作为 plugin root
传给 Runtime；只有扫描到 JSON 但 Python 模块不可导入时，Runtime 会报告驱动不可用。

```python
from pathlib import Path
from emo_master.apps.runtime.grpc_server.service import RuntimeService

repo = Path.cwd()
runtime = RuntimeService(
    dbPath=repo / "build/ticker-demo/runtime.db",
    workspaceRoot=repo / "build/ticker-demo/jobs",
    pluginRootPaths=(
        str(repo / "src/emo_master/plugins/builtins"),
        str(repo / "examples/device_service"),
    ),
)
# 为该 Runtime 导入/写入当前 2.7 工程；退出时调用 runtime.close()。
```

如果从其他目录或冻结产品使用，先把 examples.device_service 作为可信模块安装到执行端，
或明确把仓库根加入 PYTHONPATH；plugin root 负责发现，Python 导入路径负责加载代码，
两者不能互相替代。正式产品应使用自己的可安装插件包名。

在设备管理器创建“后台计数服务”，默认按 on_project_run 策略启动，
将节点 service 槽绑定该设备，再保存并写入 Runtime。运行后输出 ticks，
设备页显示 ready、ownsResources、ticks 和后台启动日志。
设备页“业务动作 → sample”经同一准入与配置周期执行，使用真实管理来源，不伪造 Job。
关闭／重开观察窗口不会重启线程。维护等待线程退出，恢复准入与显式连接后可再次使用。

对应自动化入口：

```powershell
python -m pytest -q tests/runtime/test_tcp_device_integration.py
```

该测试使用临时工程、隔离 Runtime 和模拟服务，验证节点日志、自动失败投影、
管理动作、owner/cycle 退休以及窗口关闭重开。它不访问真实相机或 PLC。
