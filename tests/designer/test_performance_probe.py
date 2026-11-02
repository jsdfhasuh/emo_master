import json
import sys
import time

import pytest

from emo_master.apps.designer.services.performance_probe import (
    DesignerPerformanceProbe, category, durationStatistics, sourceIdentity, sourceLocation)


def testSharedFlowStatisticsAndLocations():
    assert durationStatistics([30, 10, 20]) == {'samples': 3, 'medianMs': 20, 'p95Ms': 20, 'maxMs': 30}
    assert durationStatistics([])['samples'] == 0
    assert sourceLocation('D:\\private\\src\\emo_master\\canvas.py') == 'src/emo_master/canvas.py'
    assert sourceLocation('D:\\private\\helper.py') == 'helper.py'
    assert category('src/emo_master/apps/designer/page_designer/responsive_preview.py') == '候选布局测量'
    identity = sourceIdentity()
    assert len(identity['head']) == 40
    assert len(identity['files']['apps/designer/services/performance_probe.py']) == 64


def testHeartbeatExportAndBoundedBuffers(tmp_path):
    now = [100.0]
    recorder = DesignerPerformanceProbe({'fixture': 'synthetic'}, clock=lambda: now[0], detailed=False)
    # A small buffer proves reports disclose the discarded earlier samples.
    from collections import deque
    recorder.beats = deque(maxlen=2)
    recorder.MAX_MARKS = 1
    recorder.start()
    try:
        with pytest.raises(ValueError, match='停止'):
            recorder.report()
        for delay in (.020, .070, .120):
            now[0] += delay
            recorder.heartbeat('page')
        recorder.mark('page')
        recorder.mark('flow')
        recorder.event('page.paint')
    finally:
        recorder.stop()
    report = recorder.report()
    assert report['heartbeat']['maxMs'] == pytest.approx(100)
    assert report['heartbeat']['medianMs'] == pytest.approx(75)
    assert report['omitted']['beats'] == 1 and report['omitted']['marks'] == 1
    assert report['marks'][0]['workspace'] == 'page'
    assert report['events'] == {'page.paint': 1}
    assert not recorder._sampler.is_alive()
    first, second = recorder.export(tmp_path), recorder.export(tmp_path)
    assert first != second
    assert json.loads((first / 'report.json').read_text(encoding='utf-8')) == report
    assert '显示器帧率' in (first / 'summary.md').read_text(encoding='utf-8')
    with pytest.raises(ValueError, match='新的探针'):
        recorder.start()


def testProfilerKeepsCallsExceptionsAndReleasesThread():
    recorder = DesignerPerformanceProbe({})

    def businessOperation():
        sum(number * number for number in range(20000))
        raise ValueError('business failure')

    def businessCaller():
        businessOperation()

    recorder.start()
    try:
        with pytest.raises(ValueError, match='business failure'):
            businessCaller()
    finally:
        recorder.stop()
    assert sys.getprofile() is None
    assert not recorder._sampler.is_alive()
    function = next(row for row in recorder.functions if row['function'] == 'businessOperation')
    assert function['calls'] == 1
    assert function['totalCpuMs'] >= function['selfCpuMs'] >= 0
    assert function['callers'][0]['function'] == 'businessCaller'
    assert 'CPU time' in recorder.report()['functionClock']


def testExistingProfilerNotOverwritten():
    def existing(frame, event, arg):
        pass
    sys.setprofile(existing)
    try:
        recorder = DesignerPerformanceProbe({})
        with pytest.raises(ValueError, match='已有函数分析器'):
            recorder.start()
        assert sys.getprofile() is existing and not recorder.running
        light = DesignerPerformanceProbe({}, detailed=False)
        light.start()
        light.stop()
        assert sys.getprofile() is existing
    finally:
        sys.setprofile(None)


def testStallSamplingHasPythonFramesWithoutLocalsAndFinalDelay():
    recorder = DesignerPerformanceProbe({}, detailed=False)
    recorder.start()
    try:
        # Synthetic GUI-thread block, allowing the sampler to take a real stack.
        time.sleep(.25)
    finally:
        recorder.stop()
    report = recorder.report()
    assert report['heartbeat']['maxMs'] >= 150
    assert report['stallStacks']
    frames = report['stallStacks'][0]['frames']
    assert any(frame[2] == 'testStallSamplingHasPythonFramesWithoutLocalsAndFinalDelay' for frame in frames)
    assert all(len(frame) == 3 and not frame[0].startswith(('C:', 'D:')) for frame in frames)
    assert not recorder._sampler.is_alive()
