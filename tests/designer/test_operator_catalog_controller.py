from emo_master.apps.designer.controllers.operator_catalog_controller import (
    OperatorCatalogController,
)


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
