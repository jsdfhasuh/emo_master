/** Browser view model; official Panel Contract v1 is normalized at the boundary. */
export type Role = 'admin' | 'operator' | 'viewer';
export interface Me {
    authMode?: 'local' | 'password' | 'preview';
    localNoAuth?: boolean;
    preview?:boolean;
    username: string;
    userId?: string;
    role: Role;
    csrf: string;
    mode?: string;
    permissions?: string[];
}
export interface Layout { type?: string; direction?: string; columns?: number; spacing?: number; alignment?: string; justify?: string; equalWidth?: boolean; }
export interface Appearance { theme?: 'light'; contentMaxWidth?: number; pagePadding?: number; }
export interface PanelShell {
    title?: string; subtitle?: string; logoResourceId?: string; diagnosticsInitiallyExpanded?: boolean;
    showConnection?: boolean; showJobState?: boolean; showControls?: boolean;
    logoWidth?: number; logoHeight?: number; headerSurface?: Surface; headerTypography?: Typography;
    startControl?: HostControl; stopControl?: HostControl;
}
export type HexColor = `#${string}`;
export interface BorderAppearance {
    borderColor?: HexColor; borderWidth?: number; borderStyle?: 'solid' | 'dashed' | 'dotted';
}
export interface ButtonStateAppearance extends BorderAppearance {
    background?: HexColor | 'transparent'; color?: HexColor;
}
export type ButtonStates = Partial<Record<'default' | 'hover' | 'pressed' | 'disabled', ButtonStateAppearance>>;
export interface StyleBranch { enabled: boolean; code: string; }
export interface CustomStyle { qtQss?: StyleBranch; webCss?: StyleBranch; }
export interface ButtonAppearance {
    iconPosition?: 'left' | 'right'; iconSize?: number; iconGap?: number; buttonStates?: ButtonStates;
}
export interface ControlAppearance extends ButtonAppearance, BorderAppearance {
    style?: 'primary' | 'secondary' | 'link'; text?: TextStyle; radius?: number;
    padding?: Surface['padding']; minWidth?: number; minHeight?: number; showIcon?: boolean;
    background?: HexColor | 'transparent'; customStyle?: CustomStyle;
}
export interface HostControl { label?: string; appearance?: ControlAppearance; }
export interface Surface extends BorderAppearance {
    kind?: 'none' | 'card'; background?: 'transparent' | 'surface' | 'subtle' | HexColor;
    border?: 'none' | 'subtle'; radius?: number;
    padding?: Partial<Record<'top' | 'right' | 'bottom' | 'left', number>>;
}
export interface TextStyle { size?: number; weight?: 'normal' | 'bold'; color?: string; }
export type Typography = Partial<Record<'title' | 'value' | 'helper', TextStyle>>;
export interface ImageView {
    sizing?: 'fixed' | 'aspect'; aspectRatio?: number | null; background?: 'subtle' | 'dark' | 'transparent' | HexColor;
    horizontalAlign?: 'start' | 'center' | 'end'; verticalAlign?: 'start' | 'center' | 'end';
}
export interface IndicatorStyle extends BorderAppearance { text: string; color: string; background?: 'none' | 'soft' | HexColor; }
export interface HistoryView {
    mode?: 'table' | 'list'; titleField?: string; subtitleField?: string; trailingField?: string;
    showFilter?: boolean; showSort?: boolean; showPagination?: boolean;
}
export interface Component {
    history?: {kind:string;componentId:string;sourceComponentId?:string;orderBy?:string;descending?:boolean};
    layout?: {row?:number;column?:number;rowSpan?:number;columnSpan?:number};
    responsive?: {width?:string;minWidth?:number;preferredWidth?:number;weight?:number;minHeight?:number;preferredHeight?:number;alignSelf?:string;canvasOffsetY?:number};
    actions?: {clicked?: {type:string;pageId?:string;context?:string;resultScopeId?:string;componentId?:string}};
    id: string;
    type: string;
    label?: string;
    props?: Record<string, unknown> & ButtonAppearance & {
        spacerMode?: 'fixed' | 'flex'; surface?: Surface; typography?: Typography; imageView?: ImageView;
        collapse?: {initiallyCollapsed?: boolean}; indicatorStates?: Record<string, IndicatorStyle>;
        customStyle?: CustomStyle;
    };
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
    canvasTop?: number;
    layout?: Layout;
    id: string;
    title: string;
    components: Component[];
    grid?: Component['grid'];
}
export interface Panel {
    runtimeId?: string;
    projectId?: string;
    runtimeInstanceId?: string;
    navigation?:{mode?:'tabs'|'buttons'};
    appearance?: Appearance;
    shell?: PanelShell;
    logoUrl?: string;
    historyDefinitionRevision?: string;
    title?: string;
    id: string;
    name: string;
    packageRevision: string;
    pages: Page[];
    defaultPageId?: string;
    grid?: Component['grid'];
}
export interface Run {
    status?:string;active?:boolean;ownsResources?:boolean;displayResourcesReleased?:boolean;
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
    operation?: 'start' | 'stop';
    latestStartedOrdinals?:Record<string,string>;
    expiredScopeOrdinals?:Record<string,string>;
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
    hasNext?: boolean;
    stableRowIdentity?: boolean;
    historyDefinitionRevision?: string;
    selectedRowId?: string;
    columns?: {name:string;type?:string}[];
    page: number;
    pageSize: number;
}
/** Gateway target discovery (not an arbitrary RPC surface). */
export interface RuntimeTarget { permissions?: string[]; id: string; alias: string; mode: string; address?: string; }
export interface WorkflowTarget { inputsJson?:string;inputDeclarations?:Record<string,unknown>; id: string; configurationRevision?: string; name?: string; runtimeInstanceId?: string; projectId?: string; inputSchema?: Record<string, unknown>; inputs?: Record<string, unknown>; canStart?: boolean; startBlockReason?: string; }


export interface RuntimeLogEvent {
    eventId: string; sequence: string; timestampMs: string;
    jobId: string; projectId: string; workflowId: string; nodeId: string;
    workflowRunId?: string; parentWorkflowRunId?: string; nodeRunId?: string; iterationPath?: unknown;
    originApp: string; component: string; runtimeInstanceId: string;
    eventType: string; level: string; code: string; message: string; payload: unknown;
}
export interface RuntimeLogPage {
    ok: boolean; code: string; message: string; runtimeInstanceId: string;
    upperEventId: string; nextEventId: string; hasMore: boolean; events: RuntimeLogEvent[];
}
export interface RuntimeLogQuery {
    runtimeInstanceId?: string; projectId?: string; jobId?: string; deviceId?: string;
    component?: string; levels?: string; workflowId?: string; nodeId?: string;
    startTimestampMs?: string; endTimestampMs?: string;
    afterEventId?: string; upperEventId?: string; limit?: string;
}
