import type { Run } from '../contracts/types';

/** A repeated workflow name is not a run identity. Keep the full ID in the tooltip. */
export function runLabel(run:Run):string {
    return `${run.name || '运行'} · ${run.id.slice(0,8)} · ${run.status || run.state}`;
}

export function runTooltip(run?:Run):string|undefined {
    return run ? `${run.name || '运行'} · ${run.id} · ${run.status || run.state}` : undefined;
}
