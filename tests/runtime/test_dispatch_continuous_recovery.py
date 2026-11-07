"""Keep reading real consumer images before and after an explicit group restart."""
import hashlib
import json
import time

import cv2
import numpy as np

from examples.dispatch_queue_demo import demoProject
from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.core.project.models import ProjectDocument
from tests.runtime.runtime_test_utils import jobFailureDetails, waitForTerminal
from tests.runtime.test_device_production_outcomes import waitFor
from tests.runtime.test_dispatch_queue_integration import runtime as runtime


def testContinuousConsumerImagesSurviveStopAndRestartInSameRuntime(runtime, tmp_path):
    service, client, display = runtime
    payload = demoProject(tmp_path).model_dump(mode="json")
    payload["devices"]["definitions"]["camera"]["defaults"]["exposureUs"] = 1_000_000
    document = ProjectDocument.model_validate(payload)
    assert document.schemaVersion == "2.9" and document.presentation.schemaVersion == "1.2"
    assert document.runtime.maxConcurrentJobs == 2
    path = tmp_path / "continuous-dispatch.emoproj"
    saveProject(path, document.model_dump(mode="json"))
    assert loadProjectDocument(path) == document
    loaded = client.loadProject(str(path))
    assert loaded.ok, loaded.message
    validated = client.validateProject(service.loadedProjectId)
    assert validated.ok, validated.errors
    instance = service.runtimeInstanceId
    driver = service.deviceManager.drivers["camera"]
    roots, consumers, observed_keys = set(), set(), set()

    def admission():
        rows = client.runtimeService.ListWorkflows(
            pb.ListWorkflowsRequest(project_id=service.loadedProjectId), timeout=5).workflows
        return next(row for row in rows if row.workflow_id == "main")

    for _ in range(2):
        assert admission().can_start
        started = client.startJob(service.loadedProjectId, "main", capturePresentation=True)
        assert started.ok, started.message
        assert started.job_id not in roots
        roots.add(started.job_id)
        group = service.deviceManager.dispatchGroups[started.job_id]
        consumer = group.slots["detection"].jobId
        assert consumer not in consumers
        consumers.add(consumer)
        blocked = admission()
        assert not blocked.can_start and blocked.start_block_reason == "E_MAX_CONCURRENT_JOBS"
        cycle_ordinals = []
        deadline = time.monotonic() + 25
        while len(cycle_ordinals) < 6 and time.monotonic() < deadline:
            for job in (started.job_id, consumer):
                assert not service.jobRepository.get(job).isTerminal, jobFailureDetails(service, job)
            snapshot = display.Snapshot(pb.DisplayRequest(
                runtime_instance_id=instance, job_id=consumer), timeout=5)
            assert snapshot.runtime_instance_id == instance and snapshot.job_id == consumer
            for result in snapshot.results:
                key = result.identity.result_key
                if result.status != "COMPLETE" or key in observed_keys:
                    continue
                assert result.identity.job_id == consumer
                assert result.identity.runtime_instance_id == instance
                sources = {source.source_id: source for source in result.sources}
                assert set(sources) == {"count", "image"}
                assert all(source.state == "AVAILABLE" for source in sources.values())
                assert json.loads(sources["count"].value_json) == 1
                image = sources["image"].image
                assert image.owner_result_key == key
                asset = display.ReadAsset(pb.DisplayAssetRequest(
                    runtime_instance_id=instance, job_id=consumer,
                    resource_id=image.resource_id), timeout=5)
                assert hashlib.sha256(asset.content).hexdigest() == image.sha256 == asset.sha256
                decoded = cv2.imdecode(np.frombuffer(asset.content, np.uint8), cv2.IMREAD_UNCHANGED)
                assert decoded.shape == (240, 320, 3)
                observed_keys.add(key)
                cycle_ordinals.append(result.identity.result_ordinal)
            time.sleep(.1)
        assert len(cycle_ordinals) >= 6, jobFailureDetails(service, consumer)
        assert cycle_ordinals == sorted(set(cycle_ordinals))
        assert service.jobRepository.get(started.job_id).status == "RUNNING"
        assert service.jobRepository.get(consumer).status == "RUNNING"
        stopped = client.stopJob(started.job_id, mode="graceful")
        assert stopped.ok, stopped.message
        terminal = waitForTerminal(service, started.job_id, timeoutSeconds=15)
        assert (terminal.status, terminal.error_code) == ("ABORTED", "E_CANCELLED")
        waitFor(lambda: not service.jobSupervisor._ownedJobs()
                and not service.deviceManager.workers
                and not service.deviceManager.dispatchGroups, timeout=15)
        terminal_sequence = service.eventStore.terminalSequence(started.job_id)
        assert terminal_sequence > 0
        retired_events = list(client.runtimeService.StreamJobEvents(pb.StreamJobEventsRequest(
            job_id=started.job_id, after_sequence=terminal_sequence, follow=False), timeout=5))
        assert [event.event_type for event in retired_events] == [
            "dispatch.source.ended", "dispatch.group.retired"]
        assert all((event.job_id, event.project_id, event.workflow_id)
                   == (started.job_id, document.project.projectId, "main") for event in retired_events)
        assert [event.sequence for event in retired_events] == [terminal_sequence + 1, terminal_sequence + 2]
        waitFor(lambda: admission().can_start, timeout=5)
        assert service.runtimeInstanceId == instance
        assert service.deviceManager.drivers["camera"] is driver
        assert not service.deviceManager.frames.frames and not service.deviceManager.frames.reservations
        assert not service.projectConfiguration.cycles and not service.projectConfiguration.childCycles
        assert not service.deviceManager.executors["camera"].quarantined
        assert all(record.status != "FAILED" for record in service.jobRepository.all())

    assert len(roots) == len(consumers) == 2
    assert len(observed_keys) >= 12
