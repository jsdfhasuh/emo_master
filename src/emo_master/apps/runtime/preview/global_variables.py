"""Resolve a preview once without synchronizing or mutating real Runtime state."""
from emo_master.apps.runtime.context.global_variables import ProjectGlobalVariables, ReadOnlyVariables
from emo_master.core.project.global_variables import (
    VariableError, validateBindings, resolveParams, validateEffectiveParams, WRITE_VARIABLE,
)


def previewParameters(store, document, workflowId, nodeId, params, schema, jobId="", *, withSnapshot=False):
    node = next(node for node in document.workflows[workflowId].nodes if node.nodeId == nodeId)
    if node.operatorId in {WRITE_VARIABLE, "vision.state.counter"}:
        raise VariableError("E_VARIABLE_PREVIEW_READ_ONLY", "preview cannot write, reset or increment variables")
    occupied = [b.target.parameterPath for b in (*document.resources.parameterBindings, *document.resources.siteBindings)
                if b.target.workflowId == workflowId and b.target.nodeId == nodeId] if document.resources else []
    validateBindings(node.globalVariableBindings, document.globalVariables, schema, occupied=occupied)
    keys = list(document.globalVariables)
    accessor = ProjectGlobalVariables(store, document.project.projectId, document.globalVariables, jobId)
    values = {}
    # One database read transaction freezes all referenced values together.
    with store._connect() as connection:
        connection.execute("BEGIN")
        for key in keys:
            definition = accessor._definition(key)
            try:
                values[key] = accessor._read(connection, key).value
            except VariableError as error:
                if error.code not in {"E_VARIABLE_JOB_REQUIRED", "E_VARIABLE_NOT_INITIALIZED"}:
                    raise
                values[key] = definition.initialValue
    snapshot = ReadOnlyVariables(values)
    resolved = resolveParams(params, node.globalVariableBindings, snapshot.readMany(keys))
    resolved = validateEffectiveParams(resolved, node.globalVariableBindings, schema) if node.globalVariableBindings else resolved
    return (resolved, snapshot) if withSnapshot else resolved
