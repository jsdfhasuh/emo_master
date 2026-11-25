import grpc

from emo_master.apps.designer.controllers.operator_catalog_controller import (
    OperatorCatalogController,
)
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.runtime.grpc_server.generated.runtime_pb2_grpc import RuntimeServiceStub
from emo_master.apps.runtime.main import createRuntimeServer
from tests.runtime.test_operator_debug_rpc import runtime as runtime


class RuntimeClientStub:
    def listOperators(self):
        return [
            type(
                "OperatorInfo",
                (),
                {
                    "operator_id": "vision.flow.if",
                    "display_name": "If",
                    "version": "1.0.0",
                    "category": "",
                    "iconKey": "default",
                    "summary": "条件分支",
                    "input_ports": {"value": "object"},
                    "output_ports": {"true": "object", "false": "object"},
                    "param_schema": {"type": "object"},
                },
            )()
        ]


def testOperatorCatalogControllerFallsBackWhenCategoryIsBlank() -> None:
    controller = OperatorCatalogController(RuntimeClientStub(), lambda level, message: None)
    catalog = controller.refreshOperators(
        lambda operatorId: "控制流" if operatorId.startswith("vision.flow.") else "其他"
    )
    assert len(catalog) == 1
    assert catalog[0]["category"] == "控制流"
    assert catalog[0]["resultSchema"] == {}


def testCatalogRetainsSameSemanticDefinitionsForEmbeddedAndGrpcRuntime(runtime):
    server, port, _ = createRuntimeServer(port=0, runtimeService=runtime)
    server.start()

    def comparison(client):
        controller = OperatorCatalogController(client, lambda *_: None)
        return next(row for row in controller.refreshOperators(lambda _: "其他")
                    if row["operatorId"] == "vision.compare.number")

    try:
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            embedded = comparison(RuntimeClient(runtime))
            remote = comparison(RuntimeClient(RuntimeServiceStub(channel)))
        assert remote["version"] == embedded["version"] == runtime.pluginScanResult.activeOperators[
            "vision.compare.number"].manifest.version
        assert remote["resultSchema"] == embedded["resultSchema"]
        assert "description" in remote["resultSchema"]["outputs"]["result"]
        assert remote["paramSchema"] == embedded["paramSchema"]
    finally:
        server.stop(0).wait()
