from __future__ import annotations


class OperatorCatalogController:
    def __init__(self, runtimeClient, appendLog) -> None:
        self.runtimeClient = runtimeClient
        self.appendLog = appendLog
        self.operatorCatalog: list[dict[str, object]] = []

    def refreshOperators(self, classifyOperator) -> list[dict[str, object]]:
        operators = self.runtimeClient.listOperators()
        self.operatorCatalog = []
        for operatorInfo in operators:
            displayName = getattr(operatorInfo, "display_name", None)
            if displayName is None:
                displayName = getattr(operatorInfo, "displayName", None)
            operatorId = getattr(operatorInfo, "operator_id", "unknown")
            if operatorId == "unknown":
                operatorId = getattr(operatorInfo, "operatorId", "unknown")
            version = getattr(operatorInfo, "version", "")
            inputPorts = getattr(operatorInfo, "input_ports", None)
            if inputPorts is None:
                inputPorts = getattr(operatorInfo, "inputPorts", None)
            outputPorts = getattr(operatorInfo, "output_ports", None)
            if outputPorts is None:
                outputPorts = getattr(operatorInfo, "outputPorts", None)
            inputPortSpecs = getattr(operatorInfo, "input_port_specs", None)
            if inputPortSpecs is None:
                inputPortSpecs = getattr(operatorInfo, "inputPortSpecs", None)
            outputPortSpecs = getattr(operatorInfo, "output_port_specs", None)
            if outputPortSpecs is None:
                outputPortSpecs = getattr(operatorInfo, "outputPortSpecs", None)
            paramSchema = getattr(operatorInfo, "param_schema", None)
            if paramSchema is None:
                paramSchema = getattr(operatorInfo, "paramSchema", None)
            if displayName:
                rawCategory = str(
                    getattr(operatorInfo, "category", classifyOperator(operatorId))
                ).strip()
                payload = {
                    "operatorId": operatorId,
                    "displayName": displayName,
                    "version": version,
                    "category": rawCategory
                    if rawCategory != ""
                    else classifyOperator(operatorId),
                    "iconKey": str(getattr(operatorInfo, "iconKey", "default")),
                    "summary": str(getattr(operatorInfo, "summary", "")),
                    "inputPorts": inputPorts if isinstance(inputPorts, dict) else {},
                    "outputPorts": outputPorts if isinstance(outputPorts, dict) else {},
                    "inputPortSpecs": (
                        inputPortSpecs if isinstance(inputPortSpecs, dict) else {}
                    ),
                    "outputPortSpecs": (
                        outputPortSpecs if isinstance(outputPortSpecs, dict) else {}
                    ),
                    "paramSchema": paramSchema if isinstance(paramSchema, dict) else {},
                    "editorSpec": (
                        dict(getattr(operatorInfo, "editorSpec", {}))
                        if isinstance(getattr(operatorInfo, "editorSpec", {}), dict)
                        else {}
                    ),
                    "editorIssues": list(
                        getattr(operatorInfo, "editorIssues", ())
                    ),
                }
                self.operatorCatalog.append(payload)
        self.appendLog("INFO", f"算子加载完成：{len(operators)}")
        return list(self.operatorCatalog)

    def getOperatorsByCategory(self, category: str) -> list[dict[str, object]]:
        if category == "全部":
            return list(self.operatorCatalog)
        return [
            payload
            for payload in self.operatorCatalog
            if str(payload.get("category", "")) == category
        ]

    def getCatalog(self) -> list[dict[str, object]]:
        return list(self.operatorCatalog)
