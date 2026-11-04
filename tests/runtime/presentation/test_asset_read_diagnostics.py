import ast
from collections import deque
import json
import linecache
from pathlib import Path
from queue import Queue
import threading
from types import SimpleNamespace as Fields

import grpc
import pytest

from tests.runtime.presentation import asset_read_diagnostics as evidence


def owners():
    assets = Fields(lock=threading.RLock(), readers={"asset": 1}, items={"asset": object()},
                    retained={"result"}, leases={})
    client = Fields(lock=threading.RLock(), connection="CONNECTED", generation=0,
                    cursor=88, high={"root": 88}, stats={"read_failed": 0},
                    _scheduled={(0, "result")}, pending=Queue(), records=deque(), threads=[])
    server = Fields(presentation=Fields(assets=assets), active={"asset": 1},
                    peak={"asset": 2}, errors=deque(), thread=threading.current_thread(),
                    pools={"asset": Fields(_threads=set())})
    result = Fields(identity=Fields(resultKey="result", resultOrdinal=88))
    return server, [client], result


def testFailureSnapshotReadsExistingStateWithoutCollectingOrReadingAssets(capsys, monkeypatch):
    server, clients, result = owners()
    def forbidden():
        raise AssertionError("must not collect assets")
    server.presentation.assets.stats = forbidden
    def noSourceLookup(*args, **kwargs):
        raise AssertionError("must not look up source lines")
    with monkeypatch.context() as patch:
        for name in ("getline", "getlines", "lazycache", "checkcache", "updatecache"):
            patch.setattr(linecache, name, noSourceLookup)
        evidence.emitAssetReadFailure(server, clients, 0, result)
    value = json.loads(capsys.readouterr().out.removeprefix(evidence.PREFIX))
    assert "non-atomic" in value["diagnostics"] and "not stall attribution" in value["diagnostics"]
    assert value["ordinal"] == 88 and value["assets"]["readers"] == {"asset": 1}
    assert value["server"] == {"active": {"asset": 1}, "peak": {"asset": 2}, "errors": []}
    assert value["consumers"][0]["stats"] == {"read_failed": 0}
    assert 0 < len(value["threads"][0]["stack"]) <= 8
    assert all(set(frame) == {"file", "line", "function"} for frame in value["threads"][0]["stack"])
    assert server.presentation.assets.readers == {"asset": 1}


def testBusyLockIsUnknownWithoutWaiting():
    class Busy:
        def acquire(self, *, blocking):
            assert blocking is False
            return False
        def release(self):
            raise AssertionError("not acquired")
    server, clients, result = owners()
    server.presentation.assets.lock = clients[0].lock = Busy()
    value = evidence.assetReadFailureDetails(server, clients, 0, result)
    assert value["assets"] == value["consumers"][0] == {"unknown": "lock_unavailable"}


@pytest.mark.parametrize("diagnostic", ["success", "import_failure", "sampling_failure", "output_failure"])
def testActualReadWrapperPreservesOriginalRpcErrorAndNeverRetries(monkeypatch, diagnostic):
    # Execute the actual wrapper's try block, not a copy of its error handling.
    source = Path(__file__).with_name("test_live_decisions.py")
    tree = ast.parse(source.read_text())
    read = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "read"
                and any(isinstance(child, ast.Try) for child in node.body))
    block = next(node for node in read.body if isinstance(node, ast.Try))
    code = compile(ast.Module(body=[block], type_ignores=[]), str(source), "exec")
    original = grpc.RpcError("original deadline")
    calls = []
    def fail(*args):
        calls.append(args)
        raise original
    def broken(*args):
        raise OSError("diagnostic unavailable")
    if diagnostic == "output_failure":
        monkeypatch.setattr(evidence, "emitAssetReadFailure", broken)
    elif diagnostic == "sampling_failure":
        monkeypatch.setattr(evidence, "assetReadFailureDetails", broken)
    elif diagnostic == "import_failure":
        import builtins
        originalImport = builtins.__import__
        def importModule(name, *args, **kwargs):
            if name.endswith("asset_read_diagnostics"):
                raise ImportError("diagnostic unavailable")
            return originalImport(name, *args, **kwargs)
        monkeypatch.setattr(builtins, "__import__", importModule)
    server, clients, value = owners()
    namespace = dict(grpc=grpc, _original=fail, value=value, image=object(),
                     server=server, clients=clients, _index=0)
    with pytest.raises(grpc.RpcError) as caught:
        exec(code, namespace)
    assert caught.value is original and len(calls) == 1
    # Successful reads do not import or sample any diagnostic state.
    namespace["_original"] = lambda *_: "pixels"
    exec(code, namespace)
    assert namespace["decoded"] == "pixels"
