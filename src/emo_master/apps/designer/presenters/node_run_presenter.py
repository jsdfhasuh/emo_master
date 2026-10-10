"""Text for a real invocation; configurations and inferred inputs are separate."""
from datetime import datetime


def describePort(value: dict) -> str:
    kind = value.get('kind')
    if kind == 'scalar':
        item = value.get('value')
        text = 'true' if item is True else 'false' if item is False else '空值' if item is None else str(item)
        return text + ('…（已截断）' if value.get('truncated') else '')
    if kind in {'image', 'array'}:
        shape = value.get('shape', [])
        return ('图像 ' if kind == 'image' else '数组 ') + ' × '.join(str(n) for n in shape) + '（仅显示尺寸摘要）'
    if kind == 'collection':
        return f"集合 · {value.get('count', 0)} 项（仅显示数量）"
    if kind == 'object':
        return f"对象 · {value.get('count', 0)} 个字段（未展开）"
    if kind == 'redacted':
        return '敏感字段已隐藏'
    if kind == 'integer':
        return f"大整数 · {value.get('bits', 0)} 位（未展开）"
    return '不可展示 · ' + str(value.get('reason', '未提供'))


def inspectionText(record: dict | None, jobId: str | None, selected: bool = True) -> str:
    if not selected:
        return '选择节点查看本次输入、输出、耗时和错误。'
    if not jobId:
        return '尚未运行此项目；明确开始运行后在这里查看。'
    if record is None:
        return '本次尚未收到此节点的执行记录。'
    status = record['status']
    label = {'RUNNING': '运行中', 'COMPLETED': '已完成', 'FAILED': '执行失败', 'SKIPPED': '已跳过'}[status]
    lines = [f'本次运行 · {label}', f"任务：{record['jobId'][:8]} · 流程：{record['workflowId']}"]
    if record['nodeRunId']:
        lines.append('节点执行：' + record['nodeRunId'][:8])
    if record['iterationPath']:
        lines.append('循环索引：' + ' / '.join(str(n) for n in record['iterationPath']))
    timestamp = record['timestampMs']
    if timestamp:
        try:
            lines.append('更新：' + datetime.fromtimestamp(timestamp / 1000).strftime('%H:%M:%S.%f')[:-3])
        except (ValueError, OSError, OverflowError):
            lines.append('更新时间不可用')
    metrics = record['metrics']['items']
    latency = next((item for item in metrics if item['port'] == 'latencyMs'), None)
    if latency:
        lines.append('算子耗时：' + describePort(latency['value']) + ' ms')
    io = record['io']
    for side, title in (('inputs', '实际输入'), ('outputs', '实际输出')):
        lines.append(title + '：')
        batch = io.get(side)
        if batch is None:
            text = ('等待完成' if status == 'RUNNING' else '无结果（执行失败或跳过）') if side == 'outputs' and status != 'COMPLETED' else '此事件未提供端口值'
            lines.append('  ' + io.get('unavailableReason', text))
        else:
            for item in batch['items']:
                lines.append(f"  {item['port']} = {describePort(item['value'])}")
            if batch['portCount'] == 0:
                lines.append('  无传入端口值（参数默认值见配置）' if side == 'inputs' else '  无输出端口值')
            if batch['omitted']:
                lines.append(f"  另有 {batch['omitted']} 个端口因摘要额度未展开")
    remaining = [item for item in metrics if item['port'] != 'latencyMs']
    if remaining:
        lines.append('运行指标：')
        lines.extend(f"  {item['port']} = {describePort(item['value'])}" for item in remaining)
    if record['diagnostic']:
        lines.append('诊断：' + record['diagnostic'])
    if record.get('sqliteReceipt'):
        lines.append(sqliteReceiptText(record['sqliteReceipt']))
    if status in {'FAILED', 'SKIPPED'}:
        lines.append(('错误：' if status == 'FAILED' else '跳过原因：') + record['code'] + ' ' + record['message'])
    if io.get('legacy'):
        lines.append('旧事件未提供输入摘要；仅展示已上报的本次输出、指标和诊断。')
    lines.append('节点完成状态表示执行状态；判定值以实际输出为准。')
    return '\n'.join(lines)


def sqliteReceiptText(receipt):
    labels = {'COMMITTED': '事务提交成功', 'SKIPPED': '明确禁用，未写入',
              'FAILED': '数据库写入失败', 'UNKNOWN': '提交结果不确定，不自动重试'}
    elapsed = '不可取得' if receipt['elapsedMs'] is None else f"{receipt['elapsedMs']:.3f} ms"
    lines = ['receipt · 数据库写入：' + receipt['status'] + ' · ' + labels[receipt['status']],
             'writeId：' + receipt['writeId'], '影响记录数：' + str(receipt['rowsAffected']),
             '记录主键：' + str(receipt['primaryKey']), '写入耗时：' + elapsed]
    if receipt['error']:
        lines.append('错误：' + receipt['error']['code'] + ' · ' + receipt['error']['message'])
    if receipt.get('cleanupError'):
        lines.append('清理异常：' + receipt['cleanupError']['code'] + ' · ' + receipt['cleanupError']['message'])
    lines.append('节点执行完成与数据库提交状态分别展示；UNKNOWN 不能等同回滚。')
    return '\n'.join(lines)
