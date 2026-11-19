import { useLayoutEffect, useMemo, useReducer, useState } from 'react';
import type { Component, History, HistoryView as HistoryLayout, Page } from '../contracts/types';
import { api, apiBlob, ApiError, safeAssetUrl } from '../data/api';
import { ImageView, IndicatorValue, indicatorState } from './presentation';

export const historyText = (value: unknown, empty = '—') => value == null || value === '' ? empty : String(value);
export const historyAggregateText = (data: object, empty = '—') =>
    'value' in data ? historyText(data.value, empty) : '统计能力未就绪';

type Row = Record<string, unknown>;
type Key = (string | number | bigint | boolean)[];
type HistoryAction = 'history_previous' | 'history_next' | 'history_first';
export type RecordReference =
    { componentId: string; kind: 'field'; field: string } |
    { componentId: string; kind: 'meta'; meta: 'position' | 'count' };
export interface HistoryQuery { page: number; q: string; sort: string; order: 'asc' | 'desc'; filterColumn: string; }
interface ReviewSession {
    component: Component;
    query: HistoryQuery;
    selected?: Row;
    anchor?: Key;
    data?: History;
    count?: number | bigint;
    error?: string;
    busy: boolean;
    retriedImage: boolean;
    request?: AbortController;
}

const flatten = (items: Component[]): Component[] => items.flatMap(item => [item, ...flatten(item.children || [])]);
const columnsOf = (component: Component) => (Array.isArray(component.props?.columns) ? component.props.columns : [])
    .flatMap((column): { key: string; title: string }[] => {
        if (typeof column === 'string') return [{key: column, title: column}];
        if (column && typeof column === 'object' && typeof column.key === 'string')
            return [{key: column.key, title: typeof column.title === 'string' ? column.title : column.key}];
        return [];
    });

function initialQuery(component: Component): HistoryQuery {
    return {
        page: 0, q: '', sort: component.history?.orderBy || '',
        order: component.history?.descending ? 'desc' : 'asc',
        filterColumn: columnsOf(component)[0]?.key || '',
    };
}

export function normalizeHistory(data: History): History {
    return {...data, rows: data.rows.map(row => ({
        ...row, displayValues: Object.fromEntries((data.columns || []).map((column, i) => [column.name, (row.values as unknown[])[i]])),
    }))};
}

export function returnedSelection(data: History): Row | undefined {
    return data.selectedRowId ? data.rows.find(row => row.rowId === data.selectedRowId) : data.rows[0];
}

const rowValues = (row: Row | undefined) => row?.displayValues as Row | undefined;
const rowKey = (row: Row | undefined) => Array.isArray(row?.keyValues) && row?.keyValues.length ? row.keyValues as Key : undefined;
export const historyAction = (action: string | undefined): action is HistoryAction =>
    action === 'history_previous' || action === 'history_next' || action === 'history_first';

export function encodeHistoryKey(key: Key) {
    const values = key.map(value => {
        if (typeof value === 'bigint') return String(value);
        const literal = JSON.stringify(value);
        return typeof value === 'number' && !/[.eE]/.test(literal) ? literal + '.0' : literal;
    });
    return '[' + values.join(',') + ']';
}

function historyUrl(viewId: string, component: Component, query: HistoryQuery) {
    const params = new URLSearchParams({
        page: String(query.page + 1), pageSize: String(Number(component.props?.pageSize) || 10),
        q: query.q, sort: query.sort, order: query.order, filterColumn: query.filterColumn,
    });
    return {url: `/api/views/${encodeURIComponent(viewId)}/history/${encodeURIComponent(component.id)}`, params};
}

export function useHistoryReview(viewId: string | undefined, scope: string, revision: string, page: Page | undefined) {
    const sessions = useMemo(() => new Map<string, ReviewSession>(), [scope]);
    const [, update] = useReducer(version => version + 1, 0);
    const all = useMemo(() => flatten(page?.components || []), [page]);
    const countSources = new Set(all.flatMap(item => {
        const ref = item.props?.recordValue as RecordReference | undefined;
        return ref?.kind === 'meta' && ref.meta === 'count' ? [ref.componentId] : [];
    }));
    const sessionKey = (id: string) => JSON.stringify([page?.id, id]);
    const current = (id: string) => sessions.get(sessionKey(id));

    function select(session: ReviewSession, row: Row | undefined) {
        session.selected = row;
        session.anchor = rowKey(row);
        session.retriedImage = false;
        update();
    }

    async function load(session: ReviewSession, query: HistoryQuery, anchor?: Key, step = 0, retry = false) {
        if (!viewId) return;
        session.request?.abort();
        const request = session.request = new AbortController();
        session.busy = true;
        session.error = undefined;
        session.selected = undefined;
        session.anchor = anchor;
        session.count = undefined;
        session.query = query;
        session.retriedImage = retry;
        update();
        const {url, params} = historyUrl(viewId, session.component, query);
        if (anchor) {
            params.set('anchorKeyJson', encodeHistoryKey(anchor));
            params.set('anchorStep', String(step));
        }
        const options = {signal: request.signal, preserveIntegerType: true};
        try {
            const data = await api<History>(url + '?' + params, options);
            if (request.signal.aborted) return;
            session.busy = false;
            if (session.component.type === 'sqlite_value') {
                session.data = data;
                update();
                return;
            }
            session.data = normalizeHistory({...data, page: Number(data.page), pageSize: Number(data.pageSize)});
            session.query = {...query, page: session.data.page};
            session.selected = returnedSelection(session.data);
            session.anchor = rowKey(session.selected);
            update();
        } catch (error) {
            if (!request.signal.aborted) {
                session.busy = false;
                session.error = String((error as Error).message);
                session.data = undefined;
                session.anchor = undefined;
                update();
            }
            return;
        }
        if (!countSources.has(session.component.id)) return;
        const countQuery = historyUrl(viewId, session.component, query);
        countQuery.params.set('aggregate', 'true');
        try {
            const result = await api<{value: number | bigint}>(countQuery.url + '?' + countQuery.params, options);
            if (!request.signal.aborted) {
                session.count = result.value;
                update();
            }
        } catch { /* The selected record remains readable if the optional count fails. */ }
    }

    useLayoutEffect(() => {
        if (!viewId) return;
        const active = all.filter(item => item.type === 'sqlite_table' || item.type === 'sqlite_value').map(component => {
            const key = sessionKey(component.id);
            const session = sessions.get(key) || {
                component, query: initialQuery(component), busy: false, retriedImage: false,
            };
            session.component = component;
            sessions.set(key, session);
            void load(session, session.query, session.anchor);
            return session;
        });
        return () => active.forEach(session => session.request?.abort());
    }, [viewId, sessions, page?.id, revision]);

    function value(reference: RecordReference) {
        const session = current(reference.componentId);
        if (reference.kind === 'field') return rowValues(session?.selected)?.[reference.field];
        if (reference.meta === 'count') return session?.count;
        const index = session?.data?.rows.indexOf(session.selected || {}) ?? -1;
        if (session && index >= 0) return session.query.page * (session.data?.pageSize || 0) + index + 1;
    }
    function canReview(id: string | undefined, action: HistoryAction) {
        const session = id ? current(id) : undefined;
        if (!session || session.busy) return false;
        if (action === 'history_first') return true;
        const index = session.data?.rows.indexOf(session.selected || {}) ?? -1;
        if (!session.anchor || index < 0 || !session.data) return false;
        return action === 'history_previous' ? index > 0 || session.query.page > 0
            : index < session.data.rows.length - 1 || Boolean(session.data.hasNext);
    }
    function run(id: string | undefined, action: HistoryAction) {
        if (!id || !canReview(id, action)) return;
        const session = current(id)!;
        if (action === 'history_first') void load(session, {...session.query, page: 0});
        else void load(session, session.query, session.anchor, action === 'history_previous' ? -1 : 1);
    }
    function recoverImage(id: string, selected: Row | undefined) {
        const session = current(id);
        if (session && !session.busy && session.anchor && selected && session.selected === selected && !session.retriedImage)
            void load(session, session.query, session.anchor, 0, true);
    }
    return {current, select, load, value, canReview, run, recoverImage};
}

type HistoryReview = ReturnType<typeof useHistoryReview>;

export function HistoryTable({ component, review }: {component: Component; review: HistoryReview}) {
    const session = review.current(component.id);
    const query = session?.query || initialQuery(component);
    const data = session?.data;
    const busy = !session || session.busy;
    const columns = columnsOf(component);
    const [filterColumn, setFilterColumn] = useState(query.filterColumn);
    const [keyword, setKeyword] = useState(query.q);
    const configured = component.props?.historyView as HistoryLayout | undefined;
    const list = configured?.mode === 'list';
    const empty = typeof component.props?.emptyText === 'string' ? component.props.emptyText : undefined;
    const load = (newQuery: Partial<HistoryQuery>, keepAnchor = false) => {
        if (session && !busy) void review.load(session, {...query, ...newQuery}, keepAnchor ? session.anchor : undefined);
    };
    if (component.type === 'sqlite_value') {
        if (session?.error) return <p role="alert">{session.error}</p>;
        return busy || !data ? <p role="status">正在读取历史…</p> : <strong>{historyAggregateText(data, empty)}</strong>;
    }
    const rows = data?.rows || [];
    const select = (row: Row) => { if (session && !busy) review.select(session, row); };
    const hasNext = data?.hasNext;
    return <div className="history-review" aria-busy={busy}>
        {!!columns.length && configured?.showFilter !== false && <form className="history-filter" onSubmit={event => {
            event.preventDefault(); load({filterColumn, q: keyword, page: 0});
        }}>
            <select aria-label={`${component.id} 筛选列`} value={filterColumn} disabled={busy}
                onChange={event => setFilterColumn(event.target.value)}>
                {columns.map(column => <option key={column.key} value={column.key}>{column.title}</option>)}
            </select>
            <input aria-label={`${component.id} 筛选`} placeholder="包含关键词" value={keyword} disabled={busy}
                onChange={event => setKeyword(event.target.value)}/>
            <button type="submit" disabled={busy}>筛选</button>
            <button type="button" disabled={busy} onClick={() => {setKeyword(''); load({q: '', filterColumn, page: 0});}}>清除</button>
        </form>}
        {!!columns.length && configured?.showSort !== false && <div className="history-sort">
            <select aria-label={`${component.id} 排序`} value={query.sort} disabled={busy}
                onChange={event => load({sort: event.target.value, page: 0})}>
                {!columns.some(column => column.key === query.sort) && <option value={query.sort}>默认排序</option>}
                {columns.map(column => <option key={column.key} value={column.key}>{column.title}</option>)}
            </select>
            <button disabled={busy} onClick={() => load({order: query.order === 'asc' ? 'desc' : 'asc', page: 0})}>
                {query.order === 'asc' ? '升序' : '降序'}
            </button>
        </div>}
        {session?.error && <p role="alert">{session.error}</p>}
        {busy ? <p role="status">正在读取历史…</p> : !session?.error && !rows.length && <p>{empty || '暂无记录'}</p>}
        {list ? <div className="history-list" role="list" aria-label="历史记录">
            {rows.map((row, index) => {
                const values = rowValues(row);
                const title = historyText(values?.[configured?.titleField || columns[0]?.key], empty);
                const trailing = configured?.trailingField ? values?.[configured.trailingField] : undefined;
                return <div role="listitem" key={String(row.rowId || index)}>
                    <button className="history-record" aria-pressed={session?.selected === row} disabled={busy} onClick={() => select(row)}>
                        <span><strong>{title}</strong>
                            {configured?.subtitleField && <small>{historyText(values?.[configured.subtitleField], empty)}</small>}
                        </span>
                        {configured?.trailingField && <IndicatorValue props={{...component.props, indicatorView: 'badge'}}
                            state={indicatorState(component.props, typeof trailing === 'number' ? undefined : trailing,
                                typeof trailing === 'bigint' ? String(trailing) : undefined)} emptyText={empty}/>}
                    </button>
                </div>;
            })}
        </div> : <div className="table-scroll"><table>
            <thead><tr>{columns.map(column => <th key={column.key}>{column.title}</th>)}</tr></thead>
            <tbody>{rows.map((row, index) => <tr key={String(row.rowId || index)} tabIndex={busy ? -1 : 0}
                aria-selected={session?.selected === row} onClick={() => select(row)}
                onKeyDown={event => {if (event.key === 'Enter' || event.key === ' ') {event.preventDefault(); select(row);}}}>
                {columns.map(column => <td key={column.key}>{historyText(rowValues(row)?.[column.key], empty)}</td>)}
            </tr>)}</tbody>
        </table></div>}
        {configured?.showPagination !== false && <nav className="history-paging" aria-label="历史分页">
            <button disabled={busy || query.page <= 0} onClick={() => load({page: query.page - 1})}>上一页</button>
            <button disabled={busy || !hasNext} onClick={() => load({page: query.page + 1})}>下一页</button>
            <span>第 {query.page + 1} 页</span>
            <button disabled={busy} onClick={() => load({}, true)}>刷新历史</button>
        </nav>}
    </div>;
}

export function HistoryImage({ component, viewId, review, label }: {
    component: Component; viewId: string; review: HistoryReview; label: string;
}) {
    const props = component.props;
    const target = typeof props?.sourceComponentId === 'string' ? props.sourceComponentId : undefined;
    const session = target ? review.current(target) : undefined;
    const selected = session?.selected;
    const sourceId = component.history?.componentId;
    const ref = sourceId ? (selected?.imageRefs as
        Record<string, {status: string; imageRef?: string; message?: string}> | undefined)?.[sourceId] : undefined;
    let path: string | undefined;
    if (ref?.status === 'AVAILABLE' && ref.imageRef)
        path = `/api/views/${encodeURIComponent(viewId)}/history-images/${encodeURIComponent(component.id)}?imageRef=${encodeURIComponent(ref.imageRef)}`;
    const identity = JSON.stringify([selected?.rowId, path]);
    return <HistoryImageContents key={identity} component={component} label={label} path={safeAssetUrl(path)}
        empty={session?.busy ? '正在读取记录…' : ref?.message || (selected ? '所选记录的图片不可用' : '尚未选择记录')}
        recover={() => {if (target) review.recoverImage(target, selected);}}/>;
}

function HistoryImageContents({component, label, path, empty, recover}: {
    component: Component; label: string; path?: string; empty: string; recover: () => void;
}) {
    const [image, setImage] = useState<string>();
    const [error, setError] = useState('');
    useLayoutEffect(() => {
        if (!path) return;
        const request = new AbortController();
        let objectUrl: string | undefined;
        apiBlob(path, {signal: request.signal}).then(blob => {
            if (!request.signal.aborted) {
                objectUrl = URL.createObjectURL(blob);
                setImage(objectUrl);
            }
        }).catch((problem: ApiError) => {
            if (!request.signal.aborted) {
                setError(problem.message);
                if (problem.code === 'E_HISTORY_IMAGE_EXPIRED') recover();
            }
        });
        return () => {request.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl);};
    }, [path]);
    return <ImageView url={image} label={label} settings={component.props?.imageView}
        fixedHeight={component.responsive?.preferredHeight || component.responsive?.minHeight}
        placeholder={error || (path ? '正在加载图片…' : empty)}/>;
}
