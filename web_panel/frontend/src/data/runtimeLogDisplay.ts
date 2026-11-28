import type { RuntimeLogEvent } from '../contracts/types';

const localDateTime = /^(\d{4,6})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?$/;

export function localLogTimestamp(value: string): number | undefined {
    if (!value) return undefined;
    const parts = localDateTime.exec(value);
    if (parts) {
        const [year, month, day, hour, minute, second] = parts.slice(1, 7).map(part => Number(part ?? 0));
        const millisecond = Number((parts[7] ?? '').padEnd(3, '0'));
        const date = new Date(year, month - 1, day, hour, minute, second, millisecond);
        const timestamp = date.getTime();
        if (timestamp >= 0 && date.getFullYear() === year && date.getMonth() === month - 1
                && date.getDate() === day && date.getHours() === hour && date.getMinutes() === minute
                && date.getSeconds() === second && date.getMilliseconds() === millisecond) {
            return timestamp;
        }
    }
    throw new RangeError('请输入有效的本地日期时间');
}

const timestampFormat = new Intl.DateTimeFormat(undefined, {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    second: '2-digit', fractionalSecondDigits: 3,
});

export function runtimeLogTime(value: string): string {
    const date = new Date(Number(value));
    return value && Number.isFinite(date.getTime()) ? timestampFormat.format(date) : value;
}

export function logFields(value: unknown): Record<string, unknown> {
    return value !== null && typeof value === 'object' && !Array.isArray(value)
        ? value as Record<string, unknown> : {};
}

export function logText(value: unknown): string {
    return ['string', 'number', 'boolean'].includes(typeof value) ? String(value) : '';
}

const frameworkMessages = new Map<string, [string, string][]>([
    ['loop.started', [['loop started', '循环开始']]],
    ['loop.completed', [['loop completed', '循环结束']]],
    ['job.accepted', [['job accepted', '任务已受理']]],
    ['job.process.started', [['job.process.started', '任务进程已启动']]],
    ['job.started', [['job.started', '任务开始执行']]],
    ['job.completed', [['job.completed', '任务已完成']]],
    ['job.stopping', [
        ['job stopping (graceful)', '任务正在正常停止'],
        ['job stopping (force)', '任务正在强制停止'],
        ['runtime shutting down', 'Runtime 正在关闭，任务正在停止'],
    ]],
    ['job.aborted', [
        ['job.aborted', '任务已停止'],
        ['job force aborted', '任务已强制停止'],
        ['execution cancelled', '任务执行已取消'],
        ['job aborted after process exit', '任务进程退出后停止'],
        ['graceful stop timed out; worker terminated', '正常停止超时，任务进程已终止'],
        ['runtime shutting down', 'Runtime 关闭，任务已停止'],
    ]],
    ['job.failed', [
        ['job.failed', '任务失败'],
        ['runtime restarted while job was active', '任务运行期间 Runtime 已重启'],
        ['worker heartbeat timed out', '任务进程心跳超时'],
        ['worker exited without terminal event', '任务进程退出，未报告结束结果'],
    ]],
    ['process.terminated', [
        ['worker process terminated', '任务进程已终止'],
        ['worker process terminated after heartbeat timeout', '心跳超时，任务进程已终止'],
        ['worker process terminated after graceful stop timeout', '正常停止超时，任务进程已终止'],
        ['worker process terminated during runtime shutdown', 'Runtime 关闭时终止了任务进程'],
        ['worker process terminated after event bridge failure', '事件转发失败，任务进程已终止'],
    ]],
    ['dispatch.group.started', [['persistent consumer group admitted', '派发消费者组已受理']]],
    ['dispatch.group.retired', [['all run-group processes retired', '派发任务组已退出']]],
    ['dispatch.source.ended', [['source ended; downstream group remains managed', '派发入口已结束，下游仍由 Runtime 管理']]],
    ['dispatch.accepted', [['dispatch admission receipts', '已记录派发受理回执']]],
    ['dispatch.skipped', [['dispatch skipped: queue full', '派发队列已满，跳过']]],
    ['dispatch.joined', [['dispatch target outcomes', '已记录派发目标结果']]],
    ['dispatch.invocation.completed', [['consumer invocation completed', '消费者调用已完成']]],
]);

const sqliteReceiptMessages = new Map([
    ['QUEUED', 'SQLite 写入已排队'],
    ['COMMITTED', 'SQLite 写入已入库'],
    ['SKIPPED', '已跳过 SQLite 写入'],
    ['CANCELLED', 'SQLite 写入已取消'],
    ['FAILED', 'SQLite 写入失败'],
]);

const contextualMessages = new Map<string, [string, string, 'node' | 'workflow' | 'target']>([
    ['node.started', ['node started', '开始执行节点', 'node']],
    ['node.completed', ['node completed', '节点执行完成', 'node']],
    ['node.skipped', ['node skipped', '跳过节点', 'node']],
    ['workflow.started', ['workflow started', '工作流开始执行', 'workflow']],
    ['workflow.completed', ['workflow completed', '工作流执行完成', 'workflow']],
    ['subflow.started', ['subflow started', '开始调用子工作流', 'target']],
    ['subflow.completed', ['subflow completed', '子工作流调用完成', 'target']],
]);

export function runtimeLogMessage(event: RuntimeLogEvent): string {
    const {eventType, message, workflowId, nodeId} = event;
    const payload = logFields(event.payload);
    if (eventType === 'sqlite.write.started') {
        return message === 'SQLite write started' || (message === 'UNKNOWN' && logFields(payload.receipt).status === 'UNKNOWN')
            ? '开始写入' : message;
    }
    if (eventType === 'sqlite.write.finished' && message === logFields(payload.receipt).status) {
        return sqliteReceiptMessages.get(message) ?? message;
    }
    if (eventType === 'loop.iteration.started' || eventType === 'loop.iteration.completed') {
        const iterationPath: unknown[] = Array.isArray(event.iterationPath) ? event.iterationPath : [];
        const iteration = Number.isInteger(payload.iteration) ? payload.iteration : iterationPath.at(-1);
        if (typeof iteration === 'number' && Number.isInteger(iteration) && iteration >= 0
                && message === `loop iteration ${iteration}`) {
            if (eventType === 'loop.iteration.started') return '该轮开始';
            return payload.bodyExecuted === false ? '退出条件检查结束' : '该轮结束';
        }
    }
    const contextual = contextualMessages.get(eventType);
    if (contextual) {
        const [defaultPrefix, title, scope] = contextual;
        const prefix = `${defaultPrefix}: `;
        let id = scope === 'node' ? nodeId : scope === 'workflow' ? workflowId
            : logText(payload.targetWorkflowId ?? payload.targetWorkflow);
        if (scope === 'target' && !id && message.startsWith(prefix)) {
            const candidate = message.slice(prefix.length);
            if (/^[\p{L}\p{N}_-]+$/u.test(candidate)) id = candidate;
        }
        if (id && message === prefix + id) {
            return scope === 'target' ? `${title} · ${logText(payload.targetWorkflowName) || id}` : title;
        }
    }
    if (eventType === 'artifact.created') {
        const artifact = logFields(payload.artifact);
        const path = logText(artifact.path ?? payload.path);
        const prefix = 'artifact created: ';
        if (message.startsWith(prefix)) {
            const source = message.slice(prefix.length);
            if (source && (source === path || artifact.artifactId)) return `已生成产物 · ${source}`;
        }
    }
    if (eventType === 'node.log' && event.code === 'W_OPERATOR_LOG_DROPPED'
            && message === 'operator logs were dropped by runtime limits') {
        return '算子日志因 Runtime 限额被丢弃';
    }
    return frameworkMessages.get(eventType)?.find(([text]) => text === message)?.[1] ?? message;
}
