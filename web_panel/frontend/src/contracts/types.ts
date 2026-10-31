/** Browser view model; official Panel Contract v1 is normalized at the boundary. */
export type Role = 'admin' | 'operator' | 'viewer';
export interface Me {
    preview?:boolean;
    username: string;
    userId?: string;
    role: Role;
    csrf: string;
    mode?: string;
    permissions?: string[];
}
export interface Layout { type?: string; direction?: string; columns?: number; spacing?: number; alignment?: string; justify?: string; equalWidth?: boolean; }
export interface Component {
    history?: {kind:string;componentId:string;sourceComponentId?:string;orderBy?:string;descending?:boolean};
    layout?: {row?:number;column?:number;rowSpan?:number;columnSpan?:number};
    responsive?: {width?:string;minWidth?:number;preferredWidth?:number;weight?:number;minHeight?:number;preferredHeight?:number;alignSelf?:string};
    actions?: {clicked?: {type:string;pageId?:string;context?:string;resultScopeId?:string}};
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
    grid?: Layout & {
        column?: number;
        row?: number;
        columnSpan?: number;
        rowSpan?: number;
        columns?: number;
        gap?: number;
    };
}
export interface Page {
    layout?: Layout;
    id: string;
    title: string;
    components: Component[];
    grid?: Component['grid'];
}
export interface Panel {
    navigation?:{mode?:'tabs'|'buttons'};
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
    runGroupId?: string;
    allowedActions?: string[];
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
    jobId?:string;
    resultKey: string;
    resultScopeId?: string;
    resultOrdinal?: string;
    sources: Record<string, Source>;
}
export interface Envelope {
    type: string;
    event?: string;
    jobId?: string;
    status?: string;
    reason?: string;
    outcome?: string;
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
    total?: number;
    hasNext?: boolean;
    stableRowIdentity?: boolean;
    historyDefinitionRevision?: string;
    columns?: {name:string;type?:string}[];
    page: number;
    pageSize: number;
}
/** Gateway target discovery (not an arbitrary RPC surface). */
export interface RuntimeTarget { permissions?: string[]; id: string; alias: string; mode: string; address?: string; }
export interface WorkflowTarget { inputsJson?:string;inputDeclarations?:Record<string,unknown>; id: string; configurationRevision?: string; name?: string; runtimeInstanceId?: string; projectId?: string; inputSchema?: Record<string, unknown>; inputs?: Record<string, unknown>; canStart?: boolean; }
