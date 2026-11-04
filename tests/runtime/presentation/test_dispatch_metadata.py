"""Display metadata preserves admitted identity independently of capture lifetime."""
import threading
from types import SimpleNamespace

import pytest

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.jobs.models import JobRecord
from emo_master.apps.runtime.jobs.repository import JobRepository
from emo_master.apps.runtime.presentation.rpc import DisplayRpc


@pytest.mark.parametrize('status', ['RUNNING', 'COMPLETED'])
@pytest.mark.parametrize('attached', [False, True])
def testUncapturedDispatchMetadataRetainsRecordedMembership(status, attached):
    repository = JobRepository()
    for job, slot in [('root', ''), ('child', 'slot')]:
        repository.create(JobRecord(job, 'project', 1, job, status=status,
            runGroupId='real-group', sourceJobId='root', targetSlotId=slot))
    repository.create(JobRecord('ordinary', 'project', 1, 'ordinary', status=status))
    runtime = SimpleNamespace(jobRepository=repository, _jobStartRequestIds={},
        jobSupervisor=SimpleNamespace(ownsJobResources=lambda job: status == 'RUNNING'))
    service = SimpleNamespace(runtime=runtime, runtimeInstanceId='instance', lock=threading.RLock(),
        jobs={'child': {'capture': True, 'plan': '{}', 'dispatch': dict(
            runGroupId='real-group', sourceJobId='root', targetSlotId='slot')}} if attached else {})
    adapter = DisplayRpc(service)
    rows = {row.job_id: row for row in adapter.ListJobs(pb.DisplayEmpty(project_id='project'), None).jobs}
    for job, slot in [('root', ''), ('child', 'slot')]:
        row = rows[job]
        assert (row.run_group_id, row.source_job_id, row.target_slot_id) == ('real-group', 'root', slot)
        assert row.capture_enabled == (attached and job == 'child')
    assert not rows['root'].capture_definition_json and not rows['root'].capture_plan_revision
    assert not rows['root'].source_ids and not rows['root'].capture_enabled
    assert (rows['ordinary'].run_group_id, rows['ordinary'].source_job_id, rows['ordinary'].target_slot_id) == ('', '', '')
    assert {row.job_id for row in adapter.ListJobs(pb.DisplayEmpty(), None).jobs} == ({'child'} if attached else set())
    assert not adapter.ListJobs(pb.DisplayEmpty(project_id='foreign'), None).jobs
