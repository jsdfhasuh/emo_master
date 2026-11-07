"""Auxiliary verification of the test-only evidence collector; no product fault changes."""
import json
from types import SimpleNamespace
import grpc
import pytest
from emo_master.clients.runtime.display_session import DisplaySession
from tests.runtime.presentation.test_live_decisions import result
from tests.ui.page_designer.observation_diagnostics import ObservationEvidence

class ReadError(grpc.RpcError):
    def code(self):
        return grpc.StatusCode.DEADLINE_EXCEEDED
    def details(self):
        return 'first observed asset timeout'


def instrument(monkeypatch, path):
    failure=ReadError()
    calls=[]
    sentinel=object()
    def original(self, value, image, **kwargs):
        calls.append((value.identity.resultKey,image.resourceId,dict(kwargs)))
        if len(calls)==1:
            raise failure
        return sentinel
    monkeypatch.setattr(DisplaySession,'_readImage',original)
    value=result(1)
    evidence=ObservationEvidence(path,platform='auxiliary-no-GUI',jobs=[value.identity.jobId])
    evidence.install(monkeypatch)
    image=value.sources[0].image
    return evidence,object.__new__(DisplaySession),value,image,failure,calls,sentinel


def testRecoveredReadRetainsOriginalFirstExceptionAndIdentity(monkeypatch,tmp_path):
    path=tmp_path/'trace.json'
    evidence,session,value,image,failure,calls,sentinel=instrument(monkeypatch,path)
    evidence.stage='job_2_image'
    with pytest.raises(ReadError) as caught:
        session._readImage(value,image)
    assert caught.value is failure
    first=json.loads(path.read_text())
    assert first['outcome']=='in_progress'
    assert first['firstError']['error']=={'type':'ReadError','code':'DEADLINE_EXCEEDED','details':'first observed asset timeout'}
    assert first['firstError']['identity']==value.identity.model_dump(mode='json')
    assert first['firstError']['image']==image.model_dump(mode='json')
    assert first['firstError']['startedNs']<=first['firstError']['endedNs']
    assert first['firstError']['readDecodeMs']>=0
    assert session._readImage(value,image,beforeDecode=None) is sentinel
    evidence.capture('job_2_image',SimpleNamespace(session=None,observer=None))
    evidence.finish('passed','all_owners_closed')
    report=json.loads(path.read_text())
    assert report['outcome']=='passed' and report['readCount']==2 and report['errorCount']==1
    assert report['firstError']==first['firstError'] and report['reads'][1]['error'] is None
    assert not report['diagnosticErrors']
    assert calls==[(value.identity.resultKey,image.resourceId,{}),(value.identity.resultKey,image.resourceId,{'beforeDecode':None})]


def testWriterFailureDoesNotReplaceOriginalReadError(monkeypatch,tmp_path):
    blocked=tmp_path/'file'; blocked.write_text('occupied')
    evidence,session,value,image,failure,*_=instrument(monkeypatch,blocked/'trace.json')
    with pytest.raises(ReadError) as caught:
        session._readImage(value,image)
    assert caught.value is failure
    assert evidence.data['diagnosticErrors'][0]['stage']=='write'


def testDiagnosticFailureIsExplicitAndDoesNotReplaceOriginalReadError(monkeypatch,tmp_path):
    evidence,session,value,image,failure,*_=instrument(monkeypatch,tmp_path/'trace.json')
    def broken(*args):
        raise ValueError('diagnostic failure')
    monkeypatch.setattr(evidence,'_read',broken)
    with pytest.raises(ReadError) as caught:
        session._readImage(value,image)
    assert caught.value is failure
    evidence.finish('failed','job_2_image')
    report=json.loads(evidence.path.read_text())
    assert report['diagnosticErrors'][0]['stage']=='read'
    assert report['firstError'] is None


def testOutputLimitKeepsFirstErrorAndCounts(monkeypatch,tmp_path):
    evidence,session,value,image,failure,*_=instrument(monkeypatch,tmp_path/'trace.json')
    with pytest.raises(ReadError):
        session._readImage(value,image)
    first=evidence.data['firstError']
    evidence.maxBytes=4096
    evidence.data['stages']=[{'extra':'x'*20000}]
    evidence.finish('passed','all_owners_closed')
    report=json.loads(evidence.path.read_text())
    assert evidence.path.stat().st_size<=4096
    assert report['outputTruncated'] and report['stages']==[] and report['reads']==[]
    assert report['firstError']==first and report['errorCount']==1


def testOtherJobsCannotBeMistakenForTargetReadFailure(monkeypatch,tmp_path):
    evidence,session,value,image,failure,*_=instrument(monkeypatch,tmp_path/'trace.json')
    other=value.model_copy(update={'identity':value.identity.model_copy(update={'jobId':'other-job'})})
    with pytest.raises(ReadError) as caught:
        session._readImage(other,image)
    assert caught.value is failure
    evidence.finish('passed','all_owners_closed')
    report=json.loads(evidence.path.read_text())
    assert report['expectedJobs']==[value.identity.jobId]
    assert report['readCount']==0 and report['errorCount']==0 and report['firstError'] is None


def testCleanupStageDoesNotEraseOriginalFailureStage(tmp_path):
    evidence=ObservationEvidence(tmp_path/'trace.json',platform='auxiliary-no-GUI',jobs=[])
    evidence.finish('failed','all_owners_closed',failedAt='job_2_image')
    report=json.loads(evidence.path.read_text())
    assert report['outcome']=='failed'
    assert report['failedAt']=='job_2_image'
    assert report['finalStage']=='all_owners_closed'
