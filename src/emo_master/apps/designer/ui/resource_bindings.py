"""Project-local resource and site targets, driven by installed metadata."""
from copy import deepcopy
import hashlib
from pathlib import Path
from uuid import uuid4

from PySide2.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from emo_master.apps.designer.ui.icon_map import icon
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.snapshots import PARAMETER_PURPOSES


def bindingTargets(document, deviceTypes, operators):
    result = []
    def visit(value, schema, target, path=(), operatorId=None):
        purpose = schema.get("xResourcePurpose") or PARAMETER_PURPOSES.get((operatorId, path))
        if purpose and schema.get("type") == "string":
            result.append((dict(target, parameterPath=list(path)), purpose, value if isinstance(value, str) else ""))
        if schema.get("type") == "object":
            value = value if isinstance(value, dict) else {}
            for key, child in schema.get("properties", {}).items():
                visit(value.get(key), child, target, (*path, key), operatorId)
    for key, definition in document.devices.definitions.items():
        declaration = deviceTypes.get(definition.deviceTypeId)
        if declaration is not None:
            visit(definition.connection, declaration.connectionSchema, dict(deviceId=key, section="connection"))
            visit(definition.defaults, declaration.defaultsSchema, dict(deviceId=key, section="defaults"))
    for wid, flow in document.workflows.items():
        for node in flow.nodes:
            metadata = operators.get(node.operatorId, {})
            schema = metadata.get("paramSchema", {})
            visit(node.params, schema, dict(workflowId=wid, nodeId=node.nodeId), operatorId=node.operatorId)
    return result


class ResourceBindingsDialog(QDialog):
    def __init__(self, coordinator, deviceTypes, operators, parent=None):
        super().__init__(parent)
        self.c, self.types, self.operators = coordinator, deviceTypes, operators
        self.session = coordinator.session
        self.baseline = self.session.payload()
        self.payload = migrateProjectPayload(self.baseline, enableDevices=True)
        self.imports = {}
        self.setWindowTitle("工程资源 / 现场绑定")
        self.resize(1000, 620)
        layout = QVBoxLayout(self)
        tools = QHBoxLayout()
        self.purpose = QComboBox()
        for title, key in (("输入图片", "input_image"), ("模型", "model"), ("页面图片", "page_image"), ("图标", "icon")):
            self.purpose.addItem(title, key)
        tools.addWidget(self.purpose)
        self.importButton = QPushButton("导入资源")
        self.importButton.setIcon(icon("file-input"))
        self.importButton.clicked.connect(self.importResource)
        tools.addWidget(self.importButton)
        remove = QPushButton()
        remove.setIcon(icon("trash-2"))
        remove.setToolTip("移除资源登记（保留磁盘文件）")
        remove.setAccessibleName("移除资源登记")
        remove.clicked.connect(self.removeResource)
        tools.addWidget(remove)
        tools.addStretch()
        layout.addLayout(tools)
        self.inventory = QTableWidget(0, 3)
        self.inventory.setHorizontalHeaderLabels(["资源 ID", "用途", "相对路径 / SHA-256"])
        self.inventory.setEditTriggers(QTableWidget.NoEditTriggers)
        self.inventory.setSelectionBehavior(QTableWidget.SelectRows)
        self.inventory.setSelectionMode(QTableWidget.SingleSelection)
        self.inventory.setColumnWidth(0, 240)
        self.inventory.setColumnWidth(1, 110)
        self.inventory.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.inventory)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["参数目标", "用途", "来源", "资源 / 现场字段", "现场值", "必填"])
        for column, width in enumerate((230, 120, 120, 180, 200, 55)):
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确定")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.rows = []
        self.refresh()

    def refresh(self):
        document = ProjectDocument.model_validate(self.payload)
        resources = self.payload["resources"]
        self.inventory.setRowCount(len(resources["items"]))
        for row, (key, item) in enumerate(resources["items"].items()):
            for col, text in enumerate((key, item["purpose"], item["path"] + "\n" + item["sha256"])):
                cell = QTableWidgetItem(text)
                cell.setToolTip(text)
                self.inventory.setItem(row, col, cell)
        self.inventory.resizeRowsToContents()
        targets = bindingTargets(document, self.types, self.operators)
        self.table.setRowCount(len(targets))
        self.rows = []
        for row, (target, purpose, value) in enumerate(targets):
            label = "/".join(str(v) for k, v in target.items() if k != "parameterPath") + "/" + "/".join(target["parameterPath"])
            for col, text in enumerate((label, purpose)):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                from PySide2.QtCore import Qt
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row, col, item)
            mode, resource = QComboBox(), QComboBox()
            field, literal, required = QLineEdit(), QLineEdit(value), QCheckBox()
            mode.addItem("未绑定", "none")
            isResource = purpose in {"input_image", "model"}
            mode.addItem("工程资源" if isResource else "现场字段", "resource" if isResource else "site")
            for key, item in resources["items"].items():
                if item["purpose"] == purpose:
                    resource.addItem(key, key)
            existing = next((b for b in resources["parameterBindings"] if b["target"] == target), None)
            site = next((b for b in resources["siteBindings"] if b["target"] == target), None)
            if existing:
                mode.setCurrentIndex(1)
                resource.setCurrentIndex(resource.findData(existing["resourceId"]))
            if site:
                mode.setCurrentIndex(1)
                field.setText(site["field"])
                required.setChecked(site["required"])
            self.table.setCellWidget(row, 2, mode)
            self.table.setCellWidget(row, 3, resource if isResource else field)
            self.table.setCellWidget(row, 4, literal)
            self.table.setCellWidget(row, 5, required)
            literal.setEnabled(not isResource)
            required.setEnabled(not isResource)
            self.rows.append((target, purpose, mode, resource, field, literal, required))

    def removeResource(self):
        row = self.inventory.currentRow()
        if row < 0:
            return
        key = self.inventory.item(row, 0).text()
        try:
            candidate = self.candidate()
            if any(binding["resourceId"] == key for binding in candidate["resources"]["parameterBindings"]):
                raise ValueError("资源仍被引用，请先解除绑定")
            del candidate["resources"]["items"][key]
            self.payload = candidate
            self.imports.pop(key, None)
            self.refresh()
        except Exception as error:
            self.error.setText(str(error))

    def candidate(self):
        result = deepcopy(self.payload)
        plan = result["resources"]
        for target, purpose, mode, resource, field, literal, required in self.rows:
            plan["parameterBindings"] = [b for b in plan["parameterBindings"] if b["target"] != target]
            plan["siteBindings"] = [b for b in plan["siteBindings"] if b["target"] != target]
            if mode.currentData() == "resource":
                if resource.currentData() is None:
                    raise ValueError("请选择已登记的匹配资源")
                plan["parameterBindings"].append(dict(target=target, resourceId=resource.currentData()))
            elif mode.currentData() == "site":
                if not field.text().strip() or (required.isChecked() and not literal.text().strip()):
                    raise ValueError("现场字段及必填值不能为空")
                if purpose == "secret_ref":
                    import re
                    if literal.text() and not re.fullmatch(r"secret://[A-Za-z0-9_./-]+@[A-Za-z0-9_.-]+", literal.text()):
                        raise ValueError("凭证只能使用带版本的 secret:// 引用，不能保存明文")
                plan["siteBindings"].append(dict(target=target, field=field.text().strip(), purpose=purpose, required=required.isChecked()))
                if "deviceId" in target:
                    value = result["devices"]["definitions"][target["deviceId"]][target["section"]]
                else:
                    value = next(n["params"] for n in result["workflows"][target["workflowId"]]["nodes"] if n["nodeId"] == target["nodeId"])
                for part in target["parameterPath"][:-1]:
                    value = value.setdefault(part, {})
                value[target["parameterPath"][-1]] = literal.text()
        return ProjectDocument.model_validate(result).model_dump(mode="json")

    def importResource(self):
        filename, _filter = QFileDialog.getOpenFileName(self, "选择工程资源")
        if not filename:
            return
        try:
            self.payload = self.candidate()
            path = Path(filename)
            if not path.is_file() or path.stat().st_size > 512 * 1024 * 1024:
                raise ValueError("资源必须是小于 512 MiB 的文件")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            key = "resource-" + uuid4().hex
            item = dict(path="assets/" + key + path.suffix.lower(), sha256=digest.hexdigest(),
                        size=path.stat().st_size, purpose=self.purpose.currentData())
            self.payload["resources"]["items"][key] = item
            self.imports[key] = path
            self.refresh()
        except Exception as error:
            self.error.setText(str(error))

    def accept(self):
        written = []
        try:
            if self.c.session is not self.session or self.session.payload() != self.baseline:
                raise ValueError("工程草稿已改变，请重新打开资源绑定")
            candidate = self.candidate()
            if self.imports and self.c.directory is None:
                raise ValueError("请先保存工程，再导入资源")
            root = Path(self.c.directory).resolve() if self.imports else None
            for key, source in self.imports.items():
                item = candidate["resources"]["items"][key]
                target = (root / item["path"]).resolve()
                if not target.is_relative_to(root):
                    raise ValueError("资源路径越界")
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                with source.open("rb") as incoming, target.open("xb") as outgoing:
                    written.append(target)
                    for chunk in iter(lambda: incoming.read(1024 * 1024), b""):
                        outgoing.write(chunk)
                        digest.update(chunk)
                if digest.hexdigest() != item["sha256"] or target.stat().st_size != item["size"]:
                    raise ValueError("导入期间资源发生变化")
            with self.session.transaction():
                self.session._restore(candidate)
            self.c.window.workflowController._renderActive()
            self.c.chrome.update()
        except Exception as error:
            for target in written:
                if target.resolve().is_relative_to(root):
                    target.unlink(missing_ok=True)
            self.error.setText(str(error))
            return
        super().accept()
