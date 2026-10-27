"""Stable, field-level project comparison without validating unselected draft edits."""
from copy import deepcopy
import hashlib
import json


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _tree(payload):
    result = deepcopy(payload)
    for workflow in result.get("workflows", {}).values():
        nodes = workflow.get("nodes", [])
        if isinstance(nodes, list):
            mapped = {node["nodeId"]: node for node in nodes}
            if len(mapped) != len(nodes):
                raise ValueError("duplicate node IDs cannot be compared")
            workflow["nodes"] = mapped
    return result


def _payload(tree):
    for workflow in tree.get("workflows", {}).values():
        if isinstance(workflow.get("nodes"), dict):
            workflow["nodes"] = list(workflow["nodes"].values())
    return tree


def _pointer(parts):
    return "/" + "/".join(part.replace("~", "~0").replace("/", "~1") for part in parts)


def projectChanges(online, local):
    changes = []
    missing = object()

    def visit(before, after, parts):
        if parts in {("project", "revision"), ("project", "updatedAt")}:
            return
        if isinstance(before, dict) and isinstance(after, dict):
            for key in sorted(before.keys() | after.keys()):
                visit(before.get(key, missing), after.get(key, missing), (*parts, key))
        elif before is missing or after is missing or canonical(before) != canonical(after):
            changes.append(dict(path=_pointer(parts), parts=parts,
                before=None if before is missing else deepcopy(before),
                after=None if after is missing else deepcopy(after),
                operation="remove" if after is missing else "add" if before is missing else "replace"))

    visit(_tree(online), _tree(local), ())
    return changes


def projectConfigurationChanges(online, local):
    """Compare publishable settings without treating node placement as a change."""
    changes = projectChanges(online, local)
    if online["project"]["projectId"] != local["project"]["projectId"]:
        return changes  # Replacing a project still requires its complete document.
    # WorkflowLayout stores node positions; the broader layout category also
    # contains names, which must remain visible and explicitly publishable.
    return [change for change in changes if not (
        len(change["parts"]) >= 3 and change["parts"][0] == "workflows" and change["parts"][2] == "layout")]


def selectedCandidate(online, local, selected):
    if not selected or len(selected) != len(set(selected)):
        raise ValueError("select at least one unique change")
    changes = {item["path"]: item for item in projectChanges(online, local)}
    if not set(selected) <= changes.keys():
        raise ValueError("selected change is missing or no longer differs")
    candidate = _tree(online)
    for path in selected:
        change = changes[path]
        target = candidate
        for part in change["parts"][:-1]:
            target = target[part]
        key = change["parts"][-1]
        if change["operation"] == "remove":
            del target[key]
        else:
            target[key] = deepcopy(change["after"])
    return _payload(candidate)


def requiredDeviceChanges(online, local, selected):
    """Return only missing device-definition dependencies, never whole draft edits."""
    candidate = selectedCandidate(online, local, selected)
    definitions = candidate.get("devices", {}).get("definitions", {})
    available = local.get("devices", {}).get("definitions", {})
    changes = {item["path"]: item for item in projectChanges(online, local)}
    required: dict[str, list[str]] = {}
    for workflowId, workflow in candidate.get("workflows", {}).items():
        for node in workflow.get("nodes", []):
            for binding in node.get("deviceBindings", {}).values():
                deviceId = binding.get("deviceId")
                if not isinstance(deviceId, str) or deviceId in definitions:
                    continue
                path = _pointer(("devices", "definitions", deviceId))
                change = changes.get(path)
                # Deletion conflicts and absent local definitions require an
                # explicit repair; never undo a selected deletion implicitly.
                if deviceId not in available or change is None or change["operation"] != "add":
                    raise ValueError("missing device dependency: " + str(deviceId))
                required.setdefault(path, []).append(f"{workflowId}/{node['nodeId']}")
    return required


def requiredProjectChanges(online, local, selected, *, deviceTypes=None, operatorRegistry=None):
    """Find missing references transitively, offering exact changes for confirmation.

    Never copy edits to an existing dependency implicitly. Atomic list changes
    (ordering/bindings) remain visible in the confirmation, including their own
    dependencies. Selected removals that leave dangling references are errors.
    """
    changes = {item["path"]: item for item in projectChanges(online, local)}
    required = {}
    selected = list(selected)
    while True:
        candidate = selectedCandidate(online, local, selected + list(required))
        tree = _tree(candidate)
        found = {}

        def require(parts, owner):
            value = tree
            for part in parts:
                if not isinstance(value, dict) or part not in value:
                    break
                value = value[part]
            else:
                return
            for length in range(len(parts), 0, -1):
                path = _pointer(parts[:length])
                change = changes.get(path)
                if change and change["operation"] == "add" and path not in selected:
                    found.setdefault(path, []).append(owner)
                    return
            raise ValueError("missing project dependency: " + _pointer(parts) + " referenced by " + owner)

        def order(parts, keys):
            value = tree
            for part in parts:
                value = value.get(part, {})
            if set(value) == set(keys) and len(value) == len(keys):
                return
            path = _pointer(parts)
            if path in changes and path not in selected and path not in required:
                found.setdefault(path, []).append("ordered membership")
            else:
                raise ValueError("inconsistent dependency order: " + path)

        def workflow(wid, owner):
            if wid:
                require(("workflows", wid), owner)

        workflow(candidate.get("entryWorkflowId"), "entryWorkflowId")
        for wid in candidate.get("workflowOrder", []):
            workflow(wid, "workflowOrder")
        order(("workflowOrder",), candidate.get("workflows", {}))
        for wid, flow in candidate.get("workflows", {}).items():
            for node in flow.get("nodes", []):
                owner = wid + "/" + node["nodeId"]
                workflow(node.get("targetWorkflowId"), owner)
                for field in ("bodyWorkflowId", "conditionWorkflowId"):
                    workflow((node.get("loop") or {}).get(field), owner)
                if node.get("kind") == "dispatch":
                    for target in node.get("params", {}).get("targets", []):
                        workflow(target.get("workflowId"), owner)
                for slot, binding in node.get("deviceBindings", {}).items():
                    require(("devices", "definitions", binding["deviceId"]), owner)
        resources = candidate.get("resources") or {}
        for binding in [*resources.get("parameterBindings", []), *resources.get("siteBindings", [])]:
            target = binding["target"]
            if "deviceId" in target:
                require(("devices", "definitions", target["deviceId"]), "resource/site target")
            else:
                require(("workflows", target["workflowId"], "nodes", target["nodeId"]), "resource/site target")
            if "resourceId" in binding:
                require(("resources", "items", binding["resourceId"]), "resource binding")
        presentation = candidate.get("presentation") or {}
        def scope(key, owner):
            if key:
                require(("presentation", "resultScopes", key), owner)
        def components(values, owner):
            for component in values:
                for field in ("imageResourceId", "iconResourceId"):
                    resource = component.get("props", {}).get(field)
                    if resource:
                        require(("resources", "items", resource), owner)
                for source in component.get("bindings", {}).values():
                    require(("presentation", "dataSources", source), owner)
                for action in component.get("actions", {}).values():
                    if action.get("pageId"):
                        require(("presentation", "pages", action["pageId"]), owner)
                    scope(action.get("resultScopeId"), owner)
                components(component.get("children", []), owner)
        for pid, page in presentation.get("pages", {}).items():
            for key in page.get("resultScopeIds", []):
                scope(key, pid)
            components(page.get("components", []), pid)
        for key, source in presentation.get("dataSources", {}).items():
            scope(source.get("resultScopeId"), key)
            workflow(source.get("workflowId"), key)
            if source.get("nodeId"):
                require(("workflows", source["workflowId"], "nodes", source["nodeId"]), key)
            if source.get("variableId"):
                require(("globalVariables", source["variableId"]), key)
        for key, value in presentation.get("resultScopes", {}).items():
            workflow(value.get("entryWorkflowId"), key)
            workflow(value.get("scopeWorkflowId"), key)
        for pid in [*presentation.get("pageOrder", []), presentation.get("defaultPageId")]:
            if pid:
                require(("presentation", "pages", pid), "page navigation")
        if presentation:
            order(("presentation", "pageOrder"), presentation.get("pages", {}))
        additions = {path: owners for path, owners in found.items() if path not in required}
        if additions:
            required.update(additions)
            continue
        # References now exist. Resolve selector collections from device metadata,
        # then let the shared compiler/editor binding rule validate the candidate.
        from emo_master.core.plugin.devices import selectorCandidates, validateDeviceBinding
        from emo_master.core.project.devices import DeviceDefinition
        for wid, flow in candidate.get('workflows', {}).items():
            for node in flow.get('nodes', []):
                descriptor = (operatorRegistry or {}).get(node.get('operatorId'), {})
                manifest = getattr(descriptor, 'manifest', descriptor)
                requirements = (manifest.get('deviceRequirements', manifest) if isinstance(manifest, dict)
                                else getattr(manifest, 'deviceRequirements', {}))
                for slot, binding in node.get('deviceBindings', {}).items():
                    key = binding['deviceId']
                    definition = candidate['devices']['definitions'][key]
                    declaration = (deviceTypes or {}).get(definition['deviceTypeId'])
                    if declaration is None:
                        continue  # Runtime compilation remains authoritative without metadata.
                    for name, value in binding.get('selectors', {}).items():
                        spec = declaration.bindingSelectors.get(name)
                        if spec is None or value in {item for item, _ in selectorCandidates(declaration, definition, name)}:
                            continue
                        parts = ('devices', 'definitions', key, *spec['itemsPath'].split('.'))
                        path = _pointer(parts)
                        localDefinition = local.get('devices', {}).get('definitions', {}).get(key, {})
                        if (path in changes and path not in selected and path not in required
                                and value in {item for item, _ in selectorCandidates(declaration, localDefinition, name)}):
                            found.setdefault(path, []).append(f'{wid}/{node["nodeId"]}/{slot}: {name}={value}')
                    if slot in requirements and not found:
                        from emo_master.core.workflow.errors import locatedValidationError
                        try:
                            validateDeviceBinding(requirements[slot], binding, DeviceDefinition.model_validate(definition), declaration)
                        except ValueError as error:
                            raise locatedValidationError(error, 'E_DEVICE_BINDING',
                                projectId=candidate['project']['projectId'], workflowId=wid, nodeId=node['nodeId'],
                                prefix=f'deviceBindings.{slot}') from error
        if not found:
            return required
        required.update(found)


def changeCategory(parts):
    """Stable comparison labels; Runtime remains the authority for admission."""
    if parts[0] in {"project", "workflowOrder"}:
        return "document"
    if parts[0] == "devices":
        if len(parts) > 3 and parts[3] == "defaults":
            return "device_parameters"
        if len(parts) > 3 and parts[3] in {"name", "description"}:
            return "device_metadata"
        return "device_connection"
    if parts[0] == "workflows":
        if len(parts) > 2 and parts[2] in {"layout", "name"}:
            return "layout"
        if len(parts) > 4 and parts[2] == "nodes":
            if parts[4] == "displayName":
                return "layout"
            if parts[4] in {"params", "globalVariableBindings", "deviceOverrides", "deviceValueBindings"}:
                return "node_parameters"
            if parts[4] == "deviceBindings":
                return "device_binding"
        return "workflow_structure"
    return {"resources": "resources", "presentation": "presentation"}.get(parts[0], "runtime_configuration")


def presentationExecution(payload):
    presentation = payload.get("presentation") or {}
    used, scopes = set(), set()
    def visit(components):
        for component in components:
            used.update(component.get("bindings", {}).values())
            visit(component.get("children", []))
    for page in presentation.get("pages", {}).values():
        scopes.update(page.get("resultScopeIds", []))
        visit(page.get("components", []))
    sources = {key: presentation.get("dataSources", {}).get(key) for key in sorted(used)}
    scopes.update(source["resultScopeId"] for source in sources.values() if source and source.get("resultScopeId"))
    return dict(sources=sources, scopes={key: presentation.get("resultScopes", {}).get(key) for key in sorted(scopes)})


def executionPayload(payload):
    tree = _tree(payload)
    tree.pop("project", None)
    tree["projectId"] = payload.get("project", {}).get("projectId")
    tree.pop("workflowOrder", None)
    tree["presentation"] = presentationExecution(payload)
    for definition in tree.get("devices", {}).get("definitions", {}).values():
        definition.pop("name", None)
        definition.pop("description", None)
    resources = tree.get("resources")
    if resources:
        bound = {binding["resourceId"] for binding in resources.get("parameterBindings", [])}
        resources["items"] = {key: value for key, value in resources.get("items", {}).items()
                              if value.get("purpose") not in {"page_image", "icon"} or key in bound}
    for workflow in tree.get("workflows", {}).values():
        workflow.pop("layout", None)
        workflow.pop("name", None)
        for node in workflow.get("nodes", {}).values():
            node.pop("displayName", None)
            for field in ("deviceOverrides", "deviceValueBindings"):
                if field in node:
                    node[field] = {slot: values for slot, values in node[field].items() if values}
                    if not node[field]:
                        node.pop(field)
    return tree


def executionDigest(payload):
    return digest(executionPayload(payload))


def _copyCyclePath(target, source, parts):
    key, *rest = parts
    if rest:
        if key not in source and key not in target:
            return
        child = target.setdefault(key, {})
        _copyCyclePath(child, source.get(key, {}), rest)
        if not child:
            target.pop(key, None)
    elif key in source:
        target[key] = deepcopy(source[key])
    else:
        target.pop(key, None)


def applyDeviceCycleParameters(materialized, online, deviceTypes):
    """Use the same capability projection for admission and worker installation."""
    definitions = materialized.get("devices", {}).get("definitions", {})
    for key, target in definitions.items():
        source = online.get("devices", {}).get("definitions", {}).get(key)
        declaration = deviceTypes.get(target["deviceTypeId"])
        if source is None or declaration is None or source["deviceTypeId"] != target["deviceTypeId"]:
            continue
        for path in declaration.nextCyclePaths:
            _copyCyclePath(target.setdefault("defaults", {}), source.get("defaults", {}), path.split("."))
    for workflowId, workflow in materialized.get("workflows", {}).items():
        sources = {node["nodeId"]: node for node in online.get("workflows", {}).get(workflowId, {}).get("nodes", [])}
        for node in workflow.get("nodes", []):
            source = sources.get(node["nodeId"])
            if source is None:
                continue
            for slot, binding in node.get("deviceBindings", {}).items():
                definition = definitions.get(binding["deviceId"])
                if definition is None or source.get("deviceBindings", {}).get(slot) != binding:
                    continue
                declaration = deviceTypes.get(definition["deviceTypeId"])
                if declaration is None:
                    continue
                for field in ("deviceOverrides", "deviceValueBindings"):
                    target = node.setdefault(field, {}).setdefault(slot, {})
                    values = source.get(field, {}).get(slot, {})
                    for path in declaration.nextCyclePaths:
                        if field == "deviceOverrides" and path in declaration.overridePaths:
                            _copyCyclePath(target, values, path.split("."))
                        elif field == "deviceValueBindings" and path in declaration.dynamicPaths:
                            _copyCyclePath(target, values, [path])
                    if not target:
                        node[field].pop(slot, None)


def applyCycleParameters(materialized, online, registry):
    """Install declared values without copying online paths over prepared files."""
    for workflowId, workflow in materialized["workflows"].items():
        if workflowId not in online["workflows"]:
            continue
        source = {node["nodeId"]: node for node in online["workflows"][workflowId]["nodes"]}
        for node in workflow["nodes"]:
            if node["nodeId"] not in source:
                continue
            descriptor = registry.get(node.get("operatorId"))
            if descriptor is None:
                continue
            for path in descriptor.manifest.nextCycleParameterPaths:
                target = node.setdefault("params", {})
                value = source[node["nodeId"]].get("params", {})
                parts = path.split(".")
                for part in parts[:-1]:
                    target = target.setdefault(part, {})
                    value = value.get(part, {})
                if parts[-1] in value:
                    target[parts[-1]] = deepcopy(value[parts[-1]])
                else:
                    target.pop(parts[-1], None)
