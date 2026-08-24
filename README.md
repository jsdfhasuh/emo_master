# emo_master

emo_master 是一个参考 VisionMaster 思路实现的机器视觉流程设计与运行平台，当前版本为 `0.2.0`，包含：

> 项目仍处于实验性阶段，适合学习、验证和二次开发，尚未面向生产环境。它是独立实现，与 VisionMaster 及其厂商不存在隶属或官方关联。

- Designer（PySide2 / Qt5）
- Runtime（gRPC 服务）
- 插件算子框架（含 Empty 与 Canny 示例）
- 项目目录格式、打包与回滚基础能力
- `project.json v2` 多工作流、入口工作流、Subflow 与结构化 Loop
- 异步 Job、SQLite 事件重放、spawn worker 隔离和实时 follow 事件流

## Conda 环境准备

项目建议使用 Python 3.10。

### 1) 创建并激活环境

```bash
conda create -n emo_master python=3.10 -y
conda activate emo_master
python -m pip install --upgrade pip
```

### 2) 安装依赖

如果你使用单文件依赖：

```bash
pip install -r requirements.txt
```

如果你拆分了开发依赖（可选）：

```bash
pip install -r requirements-dev.txt
```

## 初始化与校验

```bash
python scripts/gen_proto.py
python scripts/gen_proto.py --check
python scripts/ci_check.py
```

## 常用开发命令

```bash
python scripts/dev.py proto
python scripts/dev.py test
python scripts/dev.py run-runtime
python scripts/dev.py run-designer
```

## 运行与图片测试

1) 启动 Designer（当前内嵌 Runtime）

```bash
python scripts/dev.py run-designer
```

如果要连接外部 Runtime（默认地址 127.0.0.1:50051）：

```bash
set EMO_RUNTIME_TARGET=127.0.0.1:50051
python scripts/dev.py run-designer
```

Runtime 默认将 SQLite 数据库保存在
`~/.emo_master/runtime/emo_master.db`，Job workspace 保存在同一目录下的
`jobs/`。可通过以下环境变量覆盖：

- `EMO_RUNTIME_DB_PATH`：指定完整 SQLite 文件路径，优先级最高。
- `EMO_RUNTIME_DATA_DIR`：指定运行时数据目录，数据库文件名固定为 `emo_master.db`。

两个路径都支持 `~` 展开。显式传入的 `RuntimeService(dbPath=...)` 优先于环境变量。

2) 在 Designer 中点击 `Import Image` 选择本地图片（png/jpg/jpeg/bmp/tif）

3) 点击 `Start` 触发最小运行流程（内置 Canny）

4) 在原图同目录查看输出：`<原文件名>.edges.png`

5) 运行日志通过 Designer 中的 `Open Logs` 按钮打开日志窗口查看。

## 测试

```bash
pytest -q
```

Runtime 每个 Job 使用独立的 `multiprocessing.spawn` 子进程；Designer 通过
`RuntimeWorker` 在后台接收事件。项目保存会自动迁移 v1 到 v2，读取旧文件不会覆盖原文件，保存 v2 会生成 `.bak` 备份并原子替换。
