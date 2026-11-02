"""Shared explanations for unavailable display results and operational logs."""
import re


_BUSY = 'source image lane is busy; capture never waits'
_QUARANTINED = 'image slab quarantined until actual owner retirement'


def budgetMessage(reason: str) -> str:
    """Explain existing wire reasons without guessing unknown failure causes."""
    if reason == 'BUDGET_EXCEEDED':
        reason = ''
    if reason == _BUSY:
        return '预览通道正忙，本帧未显示\n请等待后续画面更新'
    if reason == _QUARANTINED:
        return '预览通道异常后尚未释放，本帧未显示\n若持续出现，请根据提示详情排查'
    size = re.fullmatch(r'(raw image|raw images|result values) (\d+) bytes exceeds '
                        r'(source lane|image|result) limit (\d+)(?:; no implicit resize)?', reason)
    if size:
        kind, actual, _limitKind, limit = size.groups()
        title, advice = {
            'raw image': ('预览图片过大', '请缩小用于显示的预览图'),
            'raw images': ('本次预览图片总量过大', '请减少或缩小本次显示的图片'),
            'result values': ('本次显示数据过多', '请减少用于显示的数据'),
        }[kind]
        return f'{title}：{int(actual) / 1048576:.2f} MiB，上限 {int(limit) / 1048576:.2f} MiB\n{advice}'
    return '显示资源受限，本次结果未显示' + ('\n具体原因见提示详情' if reason else '\nRuntime 未提供具体原因')


def sourceIssue(code: str, reason: str) -> tuple[str, str, str] | None:
    """Return a stable category, Runtime log level and explanation for a failure."""
    if code in {'OPTIONAL_ABSENT', 'BRANCH_SKIPPED', 'EXECUTION_CANCELLED'}:
        return None
    if code == 'BUDGET_EXCEEDED':
        kind, level = ('IMAGE_BUSY', 'INFO') if reason == _BUSY else (
            ('IMAGE_QUARANTINED', 'ERROR') if reason == _QUARANTINED else ('DISPLAY_LIMIT', 'WARN'))
        return kind, level, budgetMessage(reason)
    messages = {
        'EXPORT_TIMEOUT': ('WARN', '预览图导出超时，本次图像未显示'),
        'EXPORT_FAILED': ('ERROR', '预览图导出失败，本次图像未显示'),
        'IPC_ERROR': ('ERROR', '预览结果通信异常，本次结果未显示'),
        'INVALID_VALUE': ('ERROR', '预览结果数据异常，本次结果未显示'),
        'SOURCE_MISSING': ('ERROR', '预览所需数据缺失，本次结果未显示'),
        'NODE_FAILED': ('ERROR', '预览数据来源节点执行失败，本次结果未显示'),
    }
    if code in messages:
        level, message = messages[code]
        return code, level, message
    detail = ' · '.join(value for value in (code, reason if reason != code else '') if value)
    return code or 'UNKNOWN', 'WARN', '预览结果不可用' + ('：' + detail if detail else '，原因未知')
