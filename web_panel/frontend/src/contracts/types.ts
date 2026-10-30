/** Local Web adapter DRAFT, NOT the unpublished Panel Contract SDK. */
export type Role = 'admin' | 'operator' | 'viewer';
export interface Me {
    username: string;
    role: Role;
    csrf: string;
    mode?: string;
    permissions?: string[];
}
export interface Component {
    id: string;
    type: string;
    label?: string;
    props?: Record<string, unknown>;
    binding?: string | {
        sourceId: string;
        scopeId?: string;
    };
    children?: Component[];
    grid?: {
        column?: number;
        row?: number;
        columnSpan?: number;
        rowSpan?: number;
    };
}
export interface Page {
    id: string;
    title: string;
    components: Component[];
}
export interface Panel {
    id: string;
    name: string;
    packageRevision: string;
    pages: Page[];
}
export interface Run {
    id: string;
    workflowId?: string;
    state: string;
    name?: string;
    runtimeInstanceId?: string;
    projectId?: string;
}
export interface ImageRef {
    url: string;
    ownerResultKey?: string;
}
export interface Source {
    state: string;
    value?: unknown;
    valueJson?: string;
    image?: ImageRef;
    reason?: string;
}
export interface Result {
    resultKey: string;
    resultScopeId?: string;
    resultOrdinal?: string;
    sources: Record<string, Source>;
}
export interface Envelope {
    type: string;
    sessionEpoch?: string;
    targetId?: string;
    pagePackageRevision?: string;
    result?: Result;
    state?: string | Record<string, unknown>;
    message?: string;
}
export interface History {
    rows: Record<string, unknown>[];
    total: number;
    page: number;
    pageSize: number;
}
