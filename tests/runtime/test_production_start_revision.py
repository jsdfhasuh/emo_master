"""Production Start binds its inputs and capture intent to one online revision."""
from copy import deepcopy
import json
from uuid import uuid4

import grpc
import pytest

from emo_master.apps.operator_runtime.controller import ProductionRuntime
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb, runtime_pb2_grpc as rpc
from emo_master.core.project.configuration import projectChanges
from tests.runtime.current_production_fixture import currentProductionProject
from tests.runtime.production_fixture import waitFor


def testConcurrentPublicationRejectsStaleProductionStartWithoutRetry(tmp_path, monkeypatch):
    path, _ = currentProductionProject(tmp_path / 'project')
    owner = ProductionRuntime(tmp_path / 'data')
    try:
        owner.load(path)
        before = owner.runtime.projectConfiguration.snapshot()
        requests = []
        original = owner.runtime.StartJob

        def publishBeforeAdmission(request, context):
            requests.append(request)
            if len(requests) == 1:
                candidate = deepcopy(before['payload'])
                candidate['production']['inputs'] = {'part': 2}
                candidate['presentation'].update(defaultPageId=None, pageOrder=[], pages={}, dataSources={}, resultScopes={})
                requestId = uuid4().hex
                with grpc.insecure_channel(owner.address) as channel:
                    reply = rpc.RuntimeServiceStub(channel).WriteProjectConfiguration(pb.ProjectConfigurationWriteRequest(
                        request_id=requestId, expected_runtime_instance_id=owner.runtime.runtimeInstanceId,
                        expected_revision=before['documentRevision'], local_payload_json=json.dumps(candidate),
                        selected_paths=[change['path'] for change in projectChanges(before['payload'], candidate)]), timeout=10)
                assert reply.ok, reply.message
                waitFor(lambda: owner.runtime.projectConfiguration.active is None)
                assert owner.runtime.projectConfiguration.status(requestId)['state'] == 'COMMITTED'
            return original(request, context)

        monkeypatch.setattr(owner.runtime, 'StartJob', publishBeforeAdmission)
        with pytest.raises(RuntimeError, match='E_CONFIG_CONFLICT'):
            owner.start()
        assert len(requests) == 1 and not owner.runtime.jobRepository.all()
        stale = requests[0]
        assert stale.expected_document_revision == before['documentRevision']
        assert json.loads(stale.inputs_json) == before['payload']['production']['inputs']
        assert stale.capture_presentation
        assert not owner.startUncertain and not owner.jobId
        outcome = owner.runtime.GetStartRequest(pb.StartRequestLookup(
            runtime_instance_id=owner.runtime.runtimeInstanceId, start_request_id=stale.start_request_id), None)
        assert outcome.status == 'REJECTED' and not outcome.ok
        # Only a new, explicit command uses the newly published snapshot.
        job = owner.start()
        assert job and len(requests) == 2
        assert requests[1].start_request_id != stale.start_request_id
        assert requests[1].expected_document_revision == before['documentRevision'] + 1
        assert json.loads(requests[1].inputs_json) == {'part': 2}
        assert not requests[1].capture_presentation
        waitFor(lambda: owner.status()['canLoad'])
        assert owner.runtime.jobRepository.get(job).status == 'COMPLETED'
    finally:
        owner.close()
