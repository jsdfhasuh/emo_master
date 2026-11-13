"""Independent, Qt-free project sessions using the official editor stores."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
import threading
from types import SimpleNamespace
from uuid import uuid4

from emo_master.apps.designer.state.project_edit_session import ProjectEditSession
from emo_master.apps.designer.state.project_store import loadProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore, portTypes
from emo_master.apps.designer.state.flow_graph_model import FlowGraphModel
from emo_master.apps.designer.state.global_variables import editDefinitions
from emo_master.apps.designer.state.workflow_rules import loopContract, refreshWorkflowReferences
from emo_master.authoring.commands import validate_command
from emo_master.authoring.errors import AuthoringError
from emo_master.core.project.models import ProjectDocument, WorkflowNode
from emo_master.core.workflow.errors import WorkflowCompileError, locatedValidationError


@dataclass
class Draft:
    draft_id: str
    directory: Path
    session: ProjectEditSession
    revision: str = field(default_factory=lambda: uuid4().hex)
    lock: threading.RLock = field(default_factory=threading.RLock)

    operators: dict = field(default_factory=dict)
    device_types: dict = field(default_factory=dict)
    declaration_context: dict = field(default_factory=dict)

    @property
    def id(self):
        return self.draft_id


class DraftService:
    def __init__(self, data_dir: Path, catalog=None):
        self.data_dir = Path(data_dir)
        self.root = self.data_dir / "project-drafts"
        self.root.mkdir(parents=True, exist_ok=True)
        self.catalog = catalog
        self._drafts: dict[str, Draft] = {}
        self._index_lock = threading.RLock()
        for directory in sorted(self.root.iterdir()):
            project_file = directory / "project.emoproj"
            if directory.is_dir() and project_file.is_file():
                draft = Draft(directory.name, directory, ProjectEditSession(dict(loadProject(project_file))))
                self._attach(draft)
                self._drafts[directory.name] = draft

    def _attach(self, draft):
        draft.session.presentation.validator = lambda presentation: self._validate_presentation(draft, presentation)

    def _validate_presentation(self, draft, presentation):
        from emo_master.core.presentation.validation import validateBindings
        from emo_master.core.workflow.errors import ValidationIssue
        document = draft.session.document()
        manifests = self._manifests(draft)
        old = {(issue.path, issue.code, issue.message) for issue in validateBindings(document, manifests)}
        document.presentation = presentation.model_copy(deep=True)
        introduced = [issue for issue in validateBindings(document, manifests)
                      if (issue.path, issue.code, issue.message) not in old
                      and "trusted plugin manifest unavailable" not in issue.message]
        if introduced:
            raise WorkflowCompileError([ValidationIssue(issue.code, issue.message, projectId=document.project.projectId,
                                                       fieldPath=issue.path) for issue in introduced])

    def create(self, name="project", payload=None):
        if payload is None:
            store = WorkflowStore()
            store.project["name"] = name
            payload = store.toPayload()
            payload["project"] = deepcopy(store.project)
        try:
            session = ProjectEditSession(deepcopy(payload))
            session.document()
        except (ValueError, TypeError, KeyError) as error:
            located = locatedValidationError(error, 'E_PROJECT_INVALID')
            diagnostics = [asdict(issue) for issue in located.issues]
            raise AuthoringError('E_PROJECT_INVALID', diagnostics[0]['message'], 422,
                                 fieldPath=diagnostics[0]['fieldPath'], diagnostics=diagnostics) from error
        draft_id = uuid4().hex
        directory = self.root / draft_id
        directory.mkdir()
        session.save(directory / "project.emoproj")
        draft = Draft(draft_id, directory, session)
        self._attach(draft)
        with self._index_lock:
            self._drafts[draft_id] = draft
        state = self.state(draft)
        state["createdIds"] = {"draftId": draft_id, "projectId": session.workflows.project["projectId"]}
        return state

    def list(self):
        with self._index_lock:
            ids = list(self._drafts)
        return [self.read(key) for key in ids]

    def get_draft(self, draft_id):
        with self._index_lock:
            draft = self._drafts.get(draft_id)
        if draft is None:
            raise AuthoringError("DRAFT_NOT_FOUND", "Draft not found", 404, draftId=draft_id)
        return draft

    @contextmanager
    def locked(self, draft_id, expected_revision=None):
        draft = self.get_draft(draft_id)
        with draft.lock:
            if expected_revision is not None and expected_revision != draft.revision:
                raise AuthoringError("DRAFT_REVISION_CONFLICT", "Draft revision has changed; read the current state", 409,
                                     draftId=draft_id, draftRevision=draft.revision, currentDraftRevision=draft.revision)
            yield draft

    def state(self, draft):
        return {"draftId": draft.draft_id, "draftRevision": draft.revision,
                "dirty": draft.session.dirty, "canUndo": bool(draft.session._undo),
                "canRedo": bool(draft.session._redo), "project": draft.session.payload(),
                "diagnostics": self._mode_diagnostics(draft)}

    def _mode_diagnostics(self, draft):
        from emo_master.apps.designer.state.port_applicability import activeOperatorPorts
        issues = []
        for workflow_id, workflow in draft.session.workflows.workflows.items():
            model = self._model(workflow)
            for node in model.nodes.values():
                if node.kind != "operator":
                    continue
                inputs, outputs = activeOperatorPorts(node, model.edges)
                for edge in model.edges:
                    if (edge.toNode == node.nodeId and edge.toPort not in inputs) or (
                            edge.fromNode == node.nodeId and edge.fromPort not in outputs):
                        issues.append({"code": "E_PORT_NOT_APPLICABLE", "message": "Connection is not applicable to the current operator configuration",
                                       "projectId": draft.session.workflows.project["projectId"], "workflowId": workflow_id,
                                       "nodeId": node.nodeId, "fieldPath": "edges", "edge": asdict(edge)})
        return issues

    def read(self, draft_id):
        with self.locked(draft_id) as draft:
            return self.state(draft)

    def snapshot(self, draft_id, expected_revision=None):
        with self.locked(draft_id, expected_revision) as draft:
            return draft.session.document().model_dump(mode="json"), draft.revision, draft.directory

    def commit(self, draft, before_payload):
        changed = before_payload != draft.session.payload()
        if changed:
            draft.revision = uuid4().hex
        return {**self.state(draft), "changed": changed}

    def save(self, draft_id, expected_revision):
        with self.locked(draft_id, expected_revision) as draft:
            before = draft.session.payload()
            draft.session.save(draft.directory / "project.emoproj")
            ProjectDocument.model_validate(dict(loadProject(draft.directory / "project.emoproj")))
            return self.commit(draft, before)

    def undo(self, draft_id, expected_revision):
        return self._history(draft_id, expected_revision, "undo")

    def redo(self, draft_id, expected_revision):
        return self._history(draft_id, expected_revision, "redo")

    def _history(self, draft_id, expected_revision, action):
        with self.locked(draft_id, expected_revision) as draft:
            changed = getattr(draft.session, action)()
            if changed:
                draft.revision = uuid4().hex
            return {**self.state(draft), "changed": changed}

    def read_presentation(self, draft_id):
        with self.locked(draft_id) as draft:
            return {"draftId": draft_id, "draftRevision": draft.revision,
                    "presentation": draft.session.presentation.snapshot().model_dump(mode="json")}

    def adopt_declarations(self, draft_id, expected_revision, target_id):
        """Adopt an explicit target's exact declarations without editing content."""
        from emo_master.core.plugin.devices import DeviceType
        with self.locked(draft_id, expected_revision) as draft:
            document = draft.session.document()

            def fail(code, message, field_path, *, status=409, **location):
                raise AuthoringError(code, message, status, draftId=draft_id, draftRevision=draft.revision,
                    targetId=target_id, diagnostics=[{'code': code, 'message': message,
                        'projectId': document.project.projectId, 'fieldPath': field_path, **location}])

            if self.catalog is None:
                fail('CATALOG_UNAVAILABLE', 'A configured Runtime declaration is required',
                     'declarationContext.targetId', status=503)
            versions = {}
            for index, dependency in enumerate(document.dependencies.operators):
                if (not isinstance(dependency, dict) or set(dependency) != {'operatorId', 'version'}
                        or not isinstance(dependency['operatorId'], str) or not dependency['operatorId']
                        or not isinstance(dependency['version'], str) or not dependency['version']):
                    fail('E_DECLARATION_VERSION_REQUIRED', 'Declaration adoption requires a saved exact operator version',
                         f'dependencies.operators.{index}')
                operator_id, version = dependency['operatorId'], dependency['version']
                if operator_id in versions:
                    fail('E_OPERATOR_DEPENDENCY', 'Duplicate operator dependency', f'dependencies.operators.{index}')
                versions[operator_id] = version
            for workflow_id, workflow in document.workflows.items():
                for node in workflow.nodes:
                    if node.kind == 'operator' and node.operatorId not in versions:
                        fail('E_DECLARATION_VERSION_REQUIRED', 'Operator has no saved exact dependency version',
                             'operatorId', workflowId=workflow_id, nodeId=node.nodeId)

            def query(method, *args, field_path):
                try:
                    return method(target_id, *args)
                except AuthoringError as error:
                    fail(error.code, error.message, field_path, status=error.status)

            observation = query(self.catalog.list_operators, field_path='declarationContext.targetId')
            instance_id = observation.get('runtimeInstanceId')
            operators = {}
            for operator_id, version in sorted(versions.items()):
                location = 'dependencies.operators.' + operator_id
                metadata = query(self.catalog.get_operator, operator_id, field_path=location)
                if metadata.get('operatorId') != operator_id or metadata.get('version') != version:
                    fail('E_OPERATOR_DEPENDENCY', 'Target operator version does not match the saved dependency', location)
                if instance_id and metadata.get('runtimeInstanceId') and metadata['runtimeInstanceId'] != instance_id:
                    fail('E_RUNTIME_IDENTITY', 'Target Runtime changed during declaration adoption', location)
                for name in ('inputPorts', 'outputPorts', 'paramSchema', 'deviceRequirements'):
                    if not isinstance(metadata.get(name), dict):
                        fail('E_DECLARATION_INCOMPLETE', 'Target did not provide the required declaration field',
                             location + '.' + name, status=422)
                existing = draft.operators.get((operator_id, version))
                if existing is not None and any(existing.get(name) != metadata.get(name) for name in
                        ('inputPorts', 'outputPorts', 'paramSchema', 'deviceRequirements')):
                    fail('DECLARATION_CONFLICT', 'The saved operator version has conflicting declarations', location)
                operators[(operator_id, version)] = deepcopy(metadata)
            device_types = {}
            for device_id, definition in document.devices.definitions.items():
                location = f'devices.definitions.{device_id}.driverVersion'
                type_id = definition.deviceTypeId
                if type_id not in device_types:
                    metadata = query(self.catalog.get_device_type, type_id, field_path=location)
                    try:
                        checked = DeviceType.model_validate(metadata.get('declaration', metadata))
                    except ValueError:
                        fail('E_DECLARATION_INCOMPLETE', 'Target returned an invalid device declaration', location, status=422)
                    device_types[type_id] = checked
                declaration = device_types[type_id]
                if declaration.deviceTypeId != type_id or declaration.version != definition.driverVersion:
                    fail('E_DEVICE_DEPENDENCY', 'Target device version does not match the saved driver version', location)
                existing = draft.device_types.get(type_id)
                if existing is not None and existing != declaration:
                    fail('DECLARATION_CONFLICT', 'The saved device version has conflicting declarations', location)
            context = {'targetId': target_id, 'runtimeInstanceId': instance_id or None,
                       'operators': [{'operatorId': key, 'version': version} for key, version in sorted(versions.items())],
                       'deviceTypes': [{'typeId': key, 'version': value.version} for key, value in sorted(device_types.items())]}

            def stable(values):
                return {key: {name: value for name, value in metadata.items() if name != 'observedAt'}
                        for key, metadata in values.items()}
            changed = (stable(operators) != stable(draft.operators) or device_types != draft.device_types
                       or context != draft.declaration_context)
            # Publish the whole context only after every declaration matches.
            draft.operators, draft.device_types, draft.declaration_context = operators, device_types, context
            if changed:
                draft.revision = uuid4().hex
            return {**self.state(draft), 'changed': changed, 'declarationContext': deepcopy(context)}

    def _operator(self, target_id, operator_id, draft):
        if self.catalog is None:
            raise AuthoringError("CATALOG_UNAVAILABLE", "A configured Runtime declaration is required", 503)
        value = self.catalog.get_operator(target_id, operator_id)
        key = (operator_id, value.get("version", ""))
        existing = draft.operators.get(key)
        fields = ("inputPorts", "outputPorts", "paramSchema", "deviceRequirements")
        if existing is not None and any(existing.get(name) != value.get(name) for name in fields):
            raise AuthoringError("DECLARATION_CONFLICT", "The same operator/version has different declarations; use a separate draft", 409)
        draft.operators[key] = deepcopy(value)
        return value

    def _manifest(self, node, draft):
        store = draft.session.workflows
        versions = {item["operatorId"]: item.get("version", "") for item in store.dependencies.get("operators", []) if isinstance(item, dict)}
        return draft.operators.get((node.get("operatorId"), versions.get(node.get("operatorId"), "")))

    def _manifests(self, draft):
        store = draft.session.workflows
        return {node["operatorId"]: SimpleNamespace(**value)
                for workflow in store.workflows.values() for node in workflow.nodes
                if (value := self._manifest(node, draft)) is not None}

    def output_choices(self, draft_id):
        from emo_master.core.presentation.catalog import buildOutputCatalog
        with self.locked(draft_id) as draft:
            values = buildOutputCatalog(draft.session.document(), self._manifests(draft))
            outputs = []
            for entry in values:
                value = asdict(entry)
                value["fields"] = [{"path": list(path), "type": kind} for path, kind in entry.fields.items()]
                outputs.append(value)
            return {"draftId": draft_id, "draftRevision": draft.revision, "outputs": outputs}

    def edit(self, draft_id, expected_revision, command):
        validate_command(command)
        with self.locked(draft_id, expected_revision) as draft:
            before = draft.session.payload()
            try:
                if command["type"].startswith(("page.", "component.", "presentation.")):
                    result = self._page_command(draft, command)
                elif command["type"] in {"device.put", "device.delete", "node.devices"}:
                    result = self._device_command(draft, command)
                else:
                    with draft.session.transaction():
                        result = self._command(draft, command)
                        # Keep the committed content canonical now, not only
                        # after undo/restart fills the formal model defaults.
                        draft.session.workflows.loadPayload(draft.session.document().model_dump())
                state = self.commit(draft, before)
                if result is not None:
                    state["result"] = result
                    state["createdIds"] = {key: value for key, value in result.items() if key.endswith("Id")}
                return state
            except AuthoringError:
                raise
            except (ValueError, KeyError, TypeError, StopIteration) as error:
                located = locatedValidationError(error, "EDIT_INVALID", projectId=draft.session.workflows.project["projectId"],
                    workflowId=command.get("workflowId", ""), nodeId=command.get("nodeId", ""),
                    fieldPath="params" if command["type"] == "node.configure" else "")
                issues = [asdict(issue) for issue in located.issues]
                raise AuthoringError(issues[0]["code"], str(error) or "Referenced object does not exist", 422,
                                     draftId=draft_id, draftRevision=draft.revision, diagnostics=issues) from error

    def _node(self, store, workflow_id, node_id):
        return next(node for node in store.get(workflow_id).nodes if node["nodeId"] == node_id)

    def _model(self, workflow):
        model = FlowGraphModel()
        model.loadProjectGraph(workflow.graph())
        return model

    def _store_model(self, workflow, model):
        graph = model.toProjectGraph()
        workflow.nodes, workflow.edges = graph["nodes"], graph["edges"]

    def _command(self, draft, command):
        store = draft.session.workflows
        kind = command["type"]
        if kind == "project.rename":
            store.project["name"] = command["name"]
        elif kind == "workflow.create":
            return {"workflowId": store.createWorkflow(command["name"], command.get("workflowId"))}
        elif kind == "workflow.rename":
            store.renameWorkflow(command["workflowId"], command["name"])
        elif kind == "workflow.delete":
            store.deleteWorkflow(command["workflowId"])
        elif kind == "workflow.entry":
            store.setEntryWorkflow(command["workflowId"])
        elif kind == "workflow.copy":
            from emo_master.apps.designer.state.workflow_package import buildWorkflowPackage, importWorkflowPackage
            package = buildWorkflowPackage(store, command["workflowId"], forInProjectCopy=True)
            copied = importWorkflowPackage(store, package, reuseVariableIds=True,
                                           deviceMapping={key: key for key in package.devices})
            refreshWorkflowReferences(store)
            return {"workflowId": copied.rootWorkflowId}
        elif kind == "workflow.interface":
            workflow = store.get(command["workflowId"])
            workflow.inputs = deepcopy(command["inputs"])
            workflow.outputs = deepcopy(command["outputs"])
            store.ensureBoundaryNodes(command["workflowId"])
            return {"removedEdges": refreshWorkflowReferences(store, command["workflowId"])}
        elif kind == "node.create":
            metadata = self._operator(command["targetId"], command["operatorId"], draft)
            workflow = store.get(command["workflowId"])
            node_id = command.get("nodeId") or "node-" + uuid4().hex
            if any(n["nodeId"] == node_id for n in workflow.nodes):
                raise ValueError("nodeId already exists")
            node = {"nodeId": node_id, "kind": "operator", "operatorId": metadata["operatorId"],
                    "displayName": command.get("displayName", metadata.get("displayName", metadata["operatorId"])),
                    "inputPorts": portTypes(metadata["inputPorts"]), "outputPorts": portTypes(metadata["outputPorts"]),
                    "paramSchema": deepcopy(metadata["paramSchema"]), "params": deepcopy(command.get("params", {}))}
            if "params" in command:
                self._configure_node(draft, command["workflowId"], node, command["params"], [])
            workflow.nodes.append(node)
            workflow.layout.setdefault("nodePositions", {})[node_id] = deepcopy(command.get("position", {"x": 100., "y": 100.}))
            operators = store.dependencies.setdefault("operators", [])
            current = next((d for d in operators if isinstance(d, dict) and d["operatorId"] == metadata["operatorId"]), None)
            if current is not None and current.get("version") != metadata["version"]:
                raise ValueError("project already depends on a different version of this operator")
            if current is None:
                operators.append({"operatorId": metadata["operatorId"], "version": metadata["version"]})
            return {"nodeId": node_id}
        elif kind == "node.configure":
            node = self._node(store, command["workflowId"], command["nodeId"])
            if node.get("kind", "operator") != "operator":
                raise ValueError("use control.configure for control-flow nodes")
            self._configure_node(draft, command["workflowId"], node, command["params"],
                                 command.get("globalVariableBindings", node.get("globalVariableBindings", [])))
        elif kind in {"node.rename", "node.delete", "edge.connect", "edge.disconnect"}:
            workflow = store.get(command["workflowId"])
            model = self._model(workflow)
            if kind == "node.rename":
                model.renameNode(command["nodeId"], command["name"])
            elif kind == "node.delete":
                if model.isBoundaryNode(command["nodeId"]):
                    raise ValueError("workflow boundary nodes cannot be deleted")
                model.removeNode(command["nodeId"])
                workflow.layout.get("nodePositions", {}).pop(command["nodeId"], None)
                self._remove_node_bindings(store, command["workflowId"], command["nodeId"])
            else:
                fields = {key: command[key] for key in ("fromNode", "fromPort", "toNode", "toPort")}
                if kind == "edge.connect":
                    model.connectNodes(**fields)
                    # Connectivity correctness is checked independently from
                    # whether every node is already fully configured.
                    from emo_master.core.graph.validator import validateFlowGraph
                    validation = validateFlowGraph(model.toProjectGraph())
                    if not validation["ok"]:
                        raise ValueError("; ".join(validation["errors"]))
                else:
                    model.removeEdge(**fields)
            self._store_model(workflow, model)
        elif kind == "node.move":
            self._node(store, command["workflowId"], command["nodeId"])
            store.get(command["workflowId"]).layout.setdefault("nodePositions", {})[command["nodeId"]] = deepcopy(command["position"])
        elif kind == "node.copy":
            from emo_master.core.contracts.sqlite_writer import rebindParams
            workflow = store.get(command["workflowId"])
            original = self._node(store, command["workflowId"], command["nodeId"])
            if original.get("kind", "operator") != "operator":
                raise ValueError("copy a whole workflow for control nodes")
            copied = deepcopy(original)
            copied["nodeId"] = "node-" + uuid4().hex
            copied["displayName"] = str(copied.get("displayName") or copied.get("operatorId")) + " 副本"
            copied["params"] = rebindParams(copied.get("params", {}), {})
            workflow.nodes.append(copied)
            positions = workflow.layout.setdefault("nodePositions", {})
            pos = positions.get(original["nodeId"], {"x": 20., "y": 20.})
            positions[copied["nodeId"]] = {"x": pos["x"] + 40, "y": pos["y"] + 100}
            from emo_master.core.project.resources import ParameterTarget, ResourcePlan
            plan = ResourcePlan.model_validate(store.projectExtensions['resources'])
            for collection in (plan.parameterBindings, plan.siteBindings):
                additions = []
                for binding in collection:
                    target = binding.target
                    if (isinstance(target, ParameterTarget) and target.workflowId == command['workflowId']
                            and target.nodeId == original['nodeId']):
                        replacement = binding.model_copy(deep=True)
                        replacement.target = ParameterTarget(workflowId=target.workflowId,
                            nodeId=copied['nodeId'], parameterPath=list(target.parameterPath))
                        additions.append(replacement)
                collection.extend(additions)
            store.projectExtensions['resources'] = ResourcePlan.model_validate(plan.model_dump()).model_dump()
            return {"nodeId": copied["nodeId"]}
        elif kind in {"control.create", "control.configure"}:
            return self._control(draft, command)
        elif kind == "variables.configure":
            editDefinitions(store, command["definitions"])
            refreshWorkflowReferences(store)
        elif kind in {"runtime.configure", "production.configure"}:
            if kind == "runtime.configure":
                store.runtime = deepcopy(command["settings"])
            else:
                store.projectExtensions["production"] = deepcopy(command["settings"])
        elif kind.startswith(("resource.", "site.")):
            return self._resource_binding(draft, command)
        else:
            raise AuthoringError("COMMAND_UNSUPPORTED", "Command is not implemented", 400)

    def _remove_node_bindings(self, store, workflow_id, node_id):
        plan = store.projectExtensions["resources"]
        for key in ("parameterBindings", "siteBindings"):
            plan[key] = [item for item in plan[key] if (item["target"].get("workflowId"), item["target"].get("nodeId")) != (workflow_id, node_id)]

    def _configure_node(self, draft, workflow_id, node, params, bindings):
        from emo_master.apps.designer.state.node_configuration import validateConfiguration
        store = draft.session.workflows
        metadata = self._manifest(node, draft)
        schema = metadata["paramSchema"] if metadata is not None else node.get("paramSchema", {})
        ports = validateConfiguration(store.get(workflow_id), node, params, bindings, schema,
                                      draft.session.document().globalVariables,
                                      store.projectExtensions["resources"], validateParams=True)
        if ports is not None:
            node["inputPorts"], node["outputPorts"] = portTypes(ports[0]), portTypes(ports[1])
        node["params"] = deepcopy(params)
        node["globalVariableBindings"] = deepcopy(bindings)

    def _control(self, draft, command):
        from emo_master.core.workflow.dispatch_contracts import dispatchPorts
        store = draft.session.workflows
        workflow_id = command["workflowId"]
        workflow = store.get(workflow_id)
        if command["type"] == "control.create":
            node = {"nodeId": command.get("nodeId") or "node-" + uuid4().hex, "kind": command["kind"],
                    "displayName": command.get("displayName", command["kind"]), "inputPorts": {}, "outputPorts": {}}
            if any(n["nodeId"] == node["nodeId"] for n in workflow.nodes):
                raise ValueError("nodeId already exists")
            workflow.nodes.append(node)
            workflow.layout.setdefault("nodePositions", {})[node["nodeId"]] = deepcopy(command.get("position", {"x": 100., "y": 100.}))
        else:
            node = self._node(store, workflow_id, command["nodeId"])
        config = deepcopy(command["config"])
        if node["kind"] == "subflow":
            target_id = config["targetWorkflowId"]
            if target_id == workflow_id:
                raise ValueError("subflow cannot target its current workflow")
            target = store.get(target_id)
            node["targetWorkflowId"] = target_id
            node["inputPorts"], node["outputPorts"] = portTypes(target.inputs), portTypes(target.outputs)
        elif node["kind"] == "loop":
            for key in ("maxIterations", "timeoutMs"):
                if type(config.get(key)) is not int or config[key] < 0:
                    raise ValueError(key + " must be a non-negative integer")
            for key in ("bodyWorkflowId", "conditionWorkflowId"):
                if config.get(key) == workflow_id:
                    raise ValueError("loop cannot target its current workflow")
            config["contractVersion"] = 2
            contract = loopContract(store, config)
            if contract.issues:
                from emo_master.core.workflow.errors import ValidationIssue
                raise WorkflowCompileError([ValidationIssue(issue.code, issue.message, workflowId=workflow_id,
                                                            nodeId=node["nodeId"], fieldPath=issue.fieldPath) for issue in contract.issues])
            node["loop"] = deepcopy(contract.normalizedConfig)
            node["inputPorts"], node["outputPorts"] = dict(contract.inputPorts), dict(contract.outputPorts)
        elif node["kind"] in {"dispatch", "join"}:
            node["params"] = config
            node["inputPorts"] = portTypes(command.get("inputPorts", node["inputPorts"]))
            checked = WorkflowNode.model_validate(node)
            node["inputPorts"], node["outputPorts"] = dispatchPorts(draft.session.document(), workflow_id, checked)
        else:
            raise ValueError("node is not a configurable control node")
        return {"nodeId": node["nodeId"], "removedEdges": refreshWorkflowReferences(store)}

    def _device_command(self, draft, command):
        from emo_master.apps.designer.state.device_catalog import DeviceCatalogEditor
        from emo_master.core.plugin.devices import DeviceRequirement
        store = draft.session.workflows
        target_id = command.get("targetId")
        if command["type"] == "device.put":
            definition = command["definition"]
            self._device_type(draft, target_id, definition["deviceTypeId"])
        for definition in store.devices["definitions"].values():
            if target_id:
                self._device_type(draft, target_id, definition["deviceTypeId"])
        requirements = {metadata["operatorId"]: {key: DeviceRequirement.model_validate(value) for key, value in metadata.get("deviceRequirements", {}).items()}
                        for metadata in draft.operators.values()}
        editor = DeviceCatalogEditor(draft.session, draft.device_types, requirements)
        if command["type"] == "device.put":
            return {"deviceId": editor.put(command["definition"], deviceId=command.get("deviceId"))}
        if command["type"] == "device.delete":
            editor.remove(command["deviceId"])
            return None
        node = deepcopy(self._node(store, command["workflowId"], command["nodeId"]))
        metadata = self._operator(target_id, node["operatorId"], draft)
        if metadata != self._manifest(node, draft):
            raise ValueError("node device editing requires the project's declared operator version")
        editor.operatorRequirements[node["operatorId"]] = {key: DeviceRequirement.model_validate(value) for key, value in metadata.get("deviceRequirements", {}).items()}
        node["deviceBindings"] = deepcopy(command["deviceBindings"])
        for key in ("deviceOverrides", "deviceValueBindings"):
            if key in command:
                node[key] = deepcopy(command[key])
        editor.commitNode(command["workflowId"], node, allowIncomplete=True)

    def _device_type(self, draft, target_id, type_id):
        from emo_master.core.plugin.devices import DeviceType
        if self.catalog is None:
            raise AuthoringError("CATALOG_UNAVAILABLE", "A Runtime device declaration is required", 503)
        declaration = self.catalog.get_device_type(target_id, type_id)
        checked = DeviceType.model_validate(declaration.get("declaration", declaration))
        previous = draft.device_types.get(type_id)
        if previous is not None and previous != checked:
            raise AuthoringError("DECLARATION_CONFLICT", "Device declarations differ from the draft's adopted declaration", 409)
        draft.device_types[type_id] = checked
        return checked

    def _page_command(self, draft, command):
        from emo_master.core.presentation.models import Component, DataSource, Page, Presentation
        p = draft.session.presentation
        kind = command["type"]
        if kind == "page.create":
            return {"pageId": p.createPage(command["name"])}
        if kind == "page.rename":
            p.renamePage(command["pageId"], command["name"])
        elif kind == "page.copy":
            return {"pageId": p.copyPage(command["pageId"], command.get("name"))}
        elif kind == "page.delete":
            p.deletePage(command["pageId"], repairTo=command.get("repairTo"))
        elif kind == "page.default":
            p.setDefaultPage(command["pageId"])
        elif kind == "page.order":
            p.reorderPages(command["pageOrder"])
        elif kind == "page.configure":
            checked = Page.model_validate(command["page"])
            if command["pageId"] not in p.snapshot().pages:
                raise KeyError(command["pageId"])
            p._edit(lambda pages: pages.pages.__setitem__(command["pageId"], checked))
        elif kind == "component.put":
            p.putComponent(command["pageId"], Component.model_validate(command["component"]))
        elif kind == "component.delete":
            def remove(pages):
                def visit(items):
                    for index, item in enumerate(items):
                        if item.componentId == command["componentId"]:
                            del items[index]
                            return True
                        if visit(item.children):
                            return True
                    return False
                if not visit(pages.pages[command["pageId"]].components):
                    raise KeyError(command["componentId"])
            p._edit(remove)
        elif kind == "component.bind":
            source = DataSource.model_validate(command["source"]) if command["source"] is not None else None
            p.rebind(command["pageId"], command["componentId"], command["property"], source)
        elif kind == "presentation.configure":
            checked = Presentation.model_validate(command["presentation"])
            def replace(pages):
                for name in type(checked).model_fields:
                    setattr(pages, name, deepcopy(getattr(checked, name)))
            p._edit(replace)

    def _resource_binding(self, draft, command):
        from emo_master.core.project.resources import ParameterTarget, DeviceParameterTarget, ResourceBinding, SiteBinding, ResourcePlan
        from emo_master.core.project.snapshots import PARAMETER_PURPOSES
        store = draft.session.workflows
        plan = ResourcePlan.model_validate(store.projectExtensions["resources"])
        target = (DeviceParameterTarget if "deviceId" in command["target"] else ParameterTarget).model_validate(command["target"])
        if isinstance(target, ParameterTarget):
            node = self._node(store, target.workflowId, target.nodeId)
            if node.get('kind', 'operator') != 'operator':
                raise ValueError('resource target must be an operator parameter')
            schema = node.get("paramSchema", {})
            purpose = PARAMETER_PURPOSES.get((node.get("operatorId"), tuple(target.parameterPath)))
        else:
            definition = store.devices["definitions"][target.deviceId]
            declaration = draft.device_types.get(definition["deviceTypeId"])
            if declaration is None:
                raise ValueError("device declaration required to edit file bindings")
            schema = getattr(declaration, target.section + "Schema")
            purpose = None
        for part in target.parameterPath:
            schema = schema.get("properties", {}).get(part, {})
        purpose = schema.get("xResourcePurpose", purpose)
        if not purpose and schema.get("xWidget") == "file" and schema.get("xFileMode") == "open":
            purpose = plan.items[command["resourceId"]].purpose if "resourceId" in command else None
        if command["type"] == "resource.bind":
            resource = plan.items.get(command["resourceId"])
            if resource is None or resource.purpose != purpose:
                raise ValueError("resource target missing or purpose does not match declaration")
            if schema.get('xWidget') != 'file' or schema.get('xFileMode') != 'open':
                raise ValueError('resource binding must target a declared input file')
            plan.parameterBindings = [item for item in plan.parameterBindings if item.target != target]
            plan.parameterBindings.append(ResourceBinding(target=target, resourceId=command["resourceId"]))
        elif command["type"] == "resource.unbind":
            plan.parameterBindings = [item for item in plan.parameterBindings if item.target != target]
        elif command["type"] == "site.bind":
            if purpose != command["purpose"]:
                raise ValueError("site purpose does not match declaration")
            plan.siteBindings = [item for item in plan.siteBindings if item.target != target]
            plan.siteBindings.append(SiteBinding(target=target, field=command["name"], purpose=purpose, required=command.get("required", True)))
        else:
            plan.siteBindings = [item for item in plan.siteBindings if item.target != target]
        store.projectExtensions["resources"] = ResourcePlan.model_validate(plan.model_dump()).model_dump()
        if isinstance(target, ParameterTarget):
            from emo_master.apps.designer.state.node_configuration import validateParameterBindings
            validateParameterBindings(target.workflowId, node, node.get('globalVariableBindings', []),
                                      node.get('paramSchema', {}), draft.session.document().globalVariables,
                                      store.projectExtensions['resources'])

    def validate(self, draft_id, expected_revision=None):
        from emo_master.authoring.validation import localDiagnostics
        with self.locked(draft_id, expected_revision) as draft:
            document = draft.session.document()
            issues, missing = localDiagnostics(draft, document, self._manifests(draft))
            issues.extend(self._mode_diagnostics(draft))
            return {"scope": "local", "draftId": draft_id, "draftRevision": draft.revision,
                    "status": "FAILED" if issues else "INCOMPLETE" if missing else "PASSED",
                    "compileStatus": "not_run", "executionStatus": "not_run", "diagnostics": [*issues, *missing]}
