"""Plain-text execution observation, separate from connection and product results."""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from emo_master.clients.runtime.view_state import JobView


def jobStatusText(job: "JobView | None") -> str:
    if job is None:
        return '任务执行状态不可用 · 尚无已核验的任务身份'
    prefix = '任务 ' + job.jobId + ' · '
    if job.availability != 'AVAILABLE':
        return prefix + '执行状态不可用 · ' + (job.detail or job.availability)
    states = {'ACCEPTED': '已接受', 'STARTING': '启动中', 'RUNNING': '运行中',
              'STOPPING': '停止中', 'COMPLETED': '已完成', 'FAILED': '执行失败', 'ABORTED': '已中止'}
    if job.status not in states:
        return prefix + '执行状态不可用 · 未识别的状态'
    parts = [prefix + states[job.status] + ' (' + job.status + ')']
    if job.resourcesReleased is True:
        parts.append('任务资源已释放')
    elif job.resourcesReleased is False:
        parts.append('任务资源尚未全部释放')
    if job.captureEnabled is False:
        parts.append('无页面采集数据')
    parts.append('执行状态不等于产品 OK/NG')
    return ' · '.join(parts)
