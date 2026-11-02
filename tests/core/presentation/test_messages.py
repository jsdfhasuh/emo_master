import pytest

from emo_master.core.presentation.messages import budgetMessage, sourceIssue


@pytest.mark.parametrize('reason,kind,level,phrase', [
    ('source image lane is busy; capture never waits', 'IMAGE_BUSY', 'INFO', '预览通道正忙'),
    ('image slab quarantined until actual owner retirement', 'IMAGE_QUARANTINED', 'ERROR', '尚未释放'),
    ('raw image 6220800 bytes exceeds source lane limit 4194304; no implicit resize',
     'DISPLAY_LIMIT', 'WARN', '5.93 MiB，上限 4.00 MiB'),
    ('raw images 18000000 bytes exceeds result limit 16777216; no implicit resize',
     'DISPLAY_LIMIT', 'WARN', '本次预览图片总量过大'),
    ('result values 1200000 bytes exceeds result limit 1048576',
     'DISPLAY_LIMIT', 'WARN', '本次显示数据过多'),
    ('BUDGET_EXCEEDED', 'DISPLAY_LIMIT', 'WARN', 'Runtime 未提供具体原因'),
    ('future busy error', 'DISPLAY_LIMIT', 'WARN', '具体原因见提示详情'),
])
def testBudgetLoggingUsesSpecificCauseAndSharedExplanation(reason, kind, level, phrase):
    actualKind, actualLevel, message = sourceIssue('BUDGET_EXCEEDED', reason)
    assert (actualKind, actualLevel) == (kind, level)
    assert message == budgetMessage(reason) and phrase in message


@pytest.mark.parametrize('code,level,phrase', [
    ('EXPORT_TIMEOUT', 'WARN', '导出超时'),
    ('EXPORT_FAILED', 'ERROR', '导出失败'),
    ('IPC_ERROR', 'ERROR', '通信异常'),
    ('INVALID_VALUE', 'ERROR', '数据异常'),
    ('SOURCE_MISSING', 'ERROR', '数据缺失'),
    ('NODE_FAILED', 'ERROR', '节点执行失败'),
])
def testUnavailableSeverityKeepsTimeoutDistinctFromFailure(code, level, phrase):
    kind, actualLevel, message = sourceIssue(code, 'details supplied separately')
    assert kind == code and actualLevel == level and phrase in message


@pytest.mark.parametrize('code', ['OPTIONAL_ABSENT', 'BRANCH_SKIPPED', 'EXECUTION_CANCELLED'])
def testExpectedAbsentOrCancelledResultsAreNotLogFailures(code):
    assert sourceIssue(code, 'normal business path') is None


def testUnknownCodeRetainsEvidenceWithoutGuessingKnownFailure():
    assert sourceIssue('NODE_FAILED_OTHER', 'detail') == (
        'NODE_FAILED_OTHER', 'WARN', '预览结果不可用：NODE_FAILED_OTHER · detail')
    assert sourceIssue('', '') == ('UNKNOWN', 'WARN', '预览结果不可用，原因未知')
    assert budgetMessage('') == budgetMessage('BUDGET_EXCEEDED')
