/** Local Web adapter DRAFT, NOT the unpublished Panel Contract SDK. */
export type Role = 'admin' | 'operator' | 'viewer';
export interface Me {
    username: string;
    userId?: string;
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
        sourceId?: string;
        field?: string;
        scopeId?: string;
    };
    children?: Component[];
    grid?: {
        column?: number;
        row?: number;
        columnSpan?: number;
        rowSpan?: number;
        columns?: number;
        gap?: number;
    };
}
export interface Page {
    id: string;
    title: string;
    components: Component[];
    grid?: Component['grid'];
}
export interface Panel {
    title?: string;
    id: string;
    name: string;
    packageRevision: string;
    pages: Page[];
    defaultPageId?: string;
    grid?: Component['grid'];
}
export interface Run {
    id: string;
    workflowId?: string;
    configurationRevision?: string;
    state: string;
    name?: string;
    runtimeInstanceId?: string;
    projectId?: string;
}
export interface ImageRef {
    url: string;
    ownerResultKey?: string;
    byteSize?: string | number;
    sha256?: string;
    mimeType?: string;
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
    viewGeneration?: string;
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
/** Web adapter draft target discovery; official protocol SDK remains pending. */
export interface RuntimeTarget { permissions?: string[]; id: string; alias: string; mode: string; address?: string; }
export interface WorkflowTarget { id: string; configurationRevision?: string; name?: string; runtimeInstanceId?: string; projectId?: string; inputSchema?: Record<string, unknown>; inputs?: Record<string, unknown>; canStart?: boolean; }
