"""Resource adapters for explicit, isolated single-node execution only."""
from pathlib import Path

from emo_master.apps.runtime.context.coordinate_snapshots import CoordinateSnapshots

from .contracts import fail
from .image_read import validateRuntimeImagePath
from emo_master.plugins.builtins.image_batch_loader.operator import ImageBatchLoaderOperator
from emo_master.plugins.builtins.image_loader.operator import ImageLoaderOperator


class DebugImageLoader(ImageLoaderOperator):
    def executeNode(self, inputs, params, runtimeContext):
        validateRuntimeImagePath(params)
        return super().executeNode(inputs, params, runtimeContext)


class DebugImageBatchLoader(ImageBatchLoaderOperator):
    def __init__(self):
        super().__init__()
        self._loader = DebugImageLoader()


def runtimePath(raw, *, directory=False, mustExist=True):
    if not isinstance(raw, str) or not raw.strip() or "\x00" in raw or not Path(raw.strip()).is_absolute():
        fail("E_PARAM_INVALID", "需要 Runtime 所在电脑上的有效绝对路径：" + str(raw))
    path = Path(raw.strip())
    if mustExist:
        try:
            valid = path.is_dir() if directory else path.is_file()
            if not valid:
                fail("E_INPUT_MISSING", "Runtime 所在电脑找不到所需文件或目录；不会自动上传 Designer 本地资源：" + str(path))
        except OSError as error:
            fail("E_INPUT_UNREADABLE", "Runtime 无法访问路径：" + str(path) + "；" + str(error))
    return path


class DebugCoordinateSnapshots(CoordinateSnapshots):
    """Freeze at the first explicit invocation, never when opening the editor."""
    def get(self, path, params):
        from emo_master.plugins.builtins._coordinate_operators import loadCoordinateContent
        key = CoordinateSnapshots._key(path, params)
        if key not in self._contents:
            runtimePath(str(path))
            self._contents[key] = loadCoordinateContent(path, params)
        return self._contents[key]


class DebugResources:
    def __init__(self, spec):
        self.spec = spec
        self.coordinates = DebugCoordinateSnapshots()
        self.sqliteParams = None
        self.sqliteTarget = None

    def prepare(self, params, values, context, cancellation):
        """Return invocation params/ports without mutating the submitted draft."""
        operatorId = self.spec["operatorId"]
        effective = dict(params)
        mapped = {}
        notes = {}
        cancellation.raise_if_cancelled()
        if operatorId == "vision.io.image_loader":
            validateRuntimeImagePath(params)
        elif operatorId == "vision.io.coordinate_reader":
            # A frozen session remains usable if its source is later removed.
            runtimePath(params.get("filePath"), mustExist=params.get("readMode") != "session")
        elif operatorId == "vision.io.image_batch_loader":
            runtimePath(params.get("folderPath"), directory=True)
        elif operatorId == "vision.inference.yolo":
            runtimePath(params.get("modelPath"))
        elif operatorId == "vision.io.image_saver":
            runtimePath(params.get("outputPath"), mustExist=False)
        elif operatorId == "communication.plc.slmp_write" and params.get("dataType", "uint16") != "float32":
            # JSON-schema number coercion produces floats, while the protocol
            # encoder deliberately accepts only integers for integer registers.
            # Convert only exact integral numbers; never truncate a write value.
            def integer(value):
                return int(value) if type(value) is float and value.is_integer() else value
            effective["values"] = [integer(value) for value in params.get("values", [])]
            values = dict(values)
            if "value" in values:
                values["value"] = integer(values["value"])
            if "values" in values:
                values["values"] = [integer(value) for value in values["values"]]
        elif operatorId == "vision.io.result_writer":
            notes = {"debugIsolation": "仅写入 Runtime 调试临时目录，关闭会话后清理。",
                     "debugWorkspace": context.workspacePath}
        elif operatorId == "vision.io.sqlite_writer":
            from emo_master.apps.runtime.business_sqlite.backend import (
                _WriterTarget, _prepareDebugDatabase, inspect, resolveTarget, validateMappings,
            )
            from emo_master.core.contracts.sqlite_writer import parseConfig, toStorage
            from emo_master.core.workflow.parameter_bindings import BoundValue
            config = parseConfig(params)
            if self.sqliteParams != params:
                path = runtimePath(config["databasePath"])
                root = Path(context.workspacePath)
                path = resolveTarget(str(path), root)
                if config.get("debugDatabasePath"):
                    runtimePath(config["debugDatabasePath"])
                def cancelled():
                    return cancellation.isCancellationRequested
                structure = inspect(path, config["table"], cancelled)["structure"]
                if structure is None:
                    fail("E_SQLITE_SCHEMA", "Runtime 数据库缺少目标表；请先明确初始化。")
                validateMappings(config, structure)
                target = _WriterTarget(None, config, path, structure)
                # Separate configurations never share a mutable database.
                from .contracts import digest
                isolated = _prepareDebugDatabase([target], root, (*self.spec.get("sqliteProtected", []), path),
                    root / "sqlite-debug" / digest(params), cancelled)
                self.sqliteTarget = str(isolated)
                self.sqliteParams = dict(params)
            effective["databasePath"] = self.sqliteTarget
            values = dict(values)
            for index, row in enumerate(config["mappings"]):
                key = f"mapping_{index + 1}"
                if row["source"]["kind"] == "node_output" and key in values:
                    mapped[index] = BoundValue(context.workflowRunId, context.nodeRunId,
                                               toStorage(values.pop(key), row["storageType"]))
            notes = {"debugIsolation": "仅向隔离测试库写入，生产库及测试模板均不写入。关闭会话后清理。",
                     "debugDatabasePath": self.sqliteTarget}
        cancellation.raise_if_cancelled()
        return effective, values, mapped, notes
