from copy import deepcopy

import pytest
from pydantic import ValidationError

from emo_master.core.project.communication import CommunicationSettings, projectCommunication
from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.models import ProjectDocument
from emo_master.apps.designer.state.workflow_store import WorkflowStore


def project():
    return dict(schemaVersion="2.1", project=dict(projectId="tcp-project", name="TCP", revision=1,
                createdAt="2026-10-15T00:00:00Z", updatedAt="2026-10-15T00:00:00Z"),
                entryWorkflowId="main", workflowOrder=["main"],
                workflows={"main": dict(name="Main", nodes=[], edges=[])})


def settings(port=10000, **changes):
    value = dict(enabled=True, startupPolicy="manual", tcpServer=dict(serviceId="tcp-server",
                 name="Robot", bindAddress="127.0.0.1", port=port,
                 channels=[dict(channelId="front", name="前工位", allowedIps=["127.0.0.1"])]))
    value.update(changes)
    return CommunicationSettings.model_validate(value)


def testLegacyDefaultsDisabledWithoutWritingNewSchema():
    original = project()
    for version in ("2.0", "2.1"):
        original["schemaVersion"] = version
        document = ProjectDocument.model_validate(original)
        assert not projectCommunication(document).enabled
        assert "communication" not in document.model_dump()
    assert "communication" not in migrateProjectPayload(original)


def testExplicitUpgradePreservesVariableAndCommunicationSettings():
    old = migrateProjectPayload(project(), enableGlobalVariables=True)
    from emo_master.core.project.global_variables import VariableDefinition
    old["globalVariables"] = {"flag": VariableDefinition(name="flag", type="boolean", initialValue=True).model_dump()}
    upgraded = migrateProjectPayload(old, enableCommunication=True)
    upgraded["communication"] = settings().model_dump()
    assert upgraded["schemaVersion"] == "2.5"
    assert upgraded["globalVariables"] == old["globalVariables"]
    for flag in ("enablePresentation", "enableProduction", "enableGlobalVariables", "enableCommunication"):
        assert migrateProjectPayload(deepcopy(upgraded), **{flag: True}) == upgraded
    store = WorkflowStore()
    store.loadPayload(upgraded)
    saved = store.toPayload()
    assert saved["schemaVersion"] == "2.5"
    assert saved["communication"] == upgraded["communication"]
    assert saved["globalVariables"] == upgraded["globalVariables"]


def testConfigurationRejectsUnknownFieldsAndAmbiguousRouting():
    with pytest.raises(ValidationError):
        CommunicationSettings.model_validate(dict(enabled=True, unknown=True))
    with pytest.raises(ValidationError, match="至少一个"):
        CommunicationSettings(enabled=True)
    value = settings().model_dump()
    value["tcpServer"]["channels"].append(dict(channelId="rear", name="后", allowedIps=["127.0.0.1"]))
    with pytest.raises(ValidationError, match="唯一"):
        CommunicationSettings.model_validate(value)
    value["tcpServer"]["channels"][1]["allowedIps"] = ["999.0.0.1"]
    with pytest.raises(ValidationError):
        CommunicationSettings.model_validate(value)


def testOldSchemaCannotSmuggleNewFeature():
    value = project()
    value["communication"] = settings().model_dump()
    with pytest.raises(ValidationError, match="explicit project 2.5"):
        ProjectDocument.model_validate(value)
