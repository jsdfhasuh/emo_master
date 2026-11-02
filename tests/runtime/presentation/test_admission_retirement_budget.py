"""Normal Start waits only for bounded, already-terminal display ownership."""
import pytest

from emo_master.apps.runtime.jobs.models import JobRecord
from emo_master.apps.runtime.presentation import service as presentation_service


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        assert seconds >= 0
        self.now += seconds


class RetiringReader:
    def __init__(self, clock, store, retiredAt=float('inf')):
        self.clock, self.store, self.retiredAt = clock, store, retiredAt

    def is_alive(self):
        return self.clock.now < self.retiredAt

    def join(self, timeout):
        assert not self.store.lock._is_owned(), 'the reader must drain outside the result-store lock'
        self.clock.sleep(timeout)


@pytest.fixture
def admission(channel, monkeypatch):
    # Retain the real admission/release methods, repository and ResultStore.
    # Only the final reader lifetime and passage of time are controlled.
    clock = Clock()
    jobs = ('retired-a', 'retired-b')
    for jobId in jobs:
        channel.runtime.jobRepository.create(JobRecord(jobId=jobId,
            projectId='admission-budget', projectRevision=1, workflowId='main', status='COMPLETED'))
        channel.jobs[jobId] = {'scopeIds': [], 'slots': [], 'queue': None}
    channel.runtime.jobSupervisor.maxConcurrentJobs = 1
    with monkeypatch.context() as patch:
        patch.setattr(presentation_service, 'time', clock)
        try:
            yield channel, clock, jobs, patch
        finally:
            patch.undo()
            channel.pending.clear()
            channel.readers.clear()
            for jobId in jobs:
                channel.release(jobId)


@pytest.mark.parametrize('owner', ['reader', 'export', 'supervisor'])
def testAdmissionKeepsBlockedOwnershipWithinOneBudget(admission, owner):
    channel, clock, jobs, patch = admission
    if owner == 'export':
        for jobId in jobs:
            channel.pending[jobId + '-result'] = {'job': jobId, 'exports': {}}
    else:
        for jobId in jobs:
            channel.readers[jobId] = RetiringReader(clock, channel.store)
    if owner == 'supervisor':
        patch.setattr(channel.runtime.jobSupervisor, 'ownsJobResources', lambda jobId: jobId in jobs)
        patch.setattr(channel, 'waitForLogDrain',
            lambda *args, **kwargs: pytest.fail('worker-owned Jobs must not enter display-drain waiting'))
    configs, pending, readers = dict(channel.jobs), dict(channel.pending), dict(channel.readers)
    records = channel.runtime.jobRepository.all()

    with pytest.raises(ValueError, match='display Job quota exceeded'):
        channel.checkNormalAdmission(retirementTimeoutSeconds=.2)

    assert clock.now == pytest.approx(0 if owner == 'supervisor' else .2)
    assert channel.jobs == configs and all(channel.jobs[key] is configs[key] for key in jobs)
    assert channel.pending == pending and channel.readers == readers
    assert channel.runtime.jobRepository.all() == records


def testLoweredQuotaReleasesEveryRetiredCandidateNeededForAdmission(admission):
    channel, clock, jobs, _patch = admission
    # Two retained displays exceed a newly selected one-Job limit. Their IPC
    # tails finish at different times; freeing only the first is insufficient.
    channel.readers[jobs[0]] = RetiringReader(clock, channel.store, .04)
    channel.readers[jobs[1]] = RetiringReader(clock, channel.store, .10)
    records = channel.runtime.jobRepository.all()

    channel.checkNormalAdmission(retirementTimeoutSeconds=.2)

    assert .10 <= clock.now <= .2
    assert not channel.jobs and not channel.readers and not channel.pending
    assert channel.runtime.jobRepository.all() == records
