import { useState } from 'react';
import type { CSSProperties } from 'react';
import * as theme from '../../../contracts/panel-contract-v1/theme.json';
import type { Appearance, Component, ImageView as ImageSettings, IndicatorStyle, Panel, Surface, Typography } from '../contracts/types';
import { appearanceBorder, appearancePart } from './advancedAppearance';

const tones = theme.foreground as Record<string, string>;
const softTones = theme.soft as Record<string, string>;
const palette = theme as unknown as Record<string, string>;

export const backgroundColor = (value: string) => value === 'transparent' || value.startsWith('#') ? value : palette[value];
export const textColor = (value: string) => value.startsWith('#') ? value :
    value === 'primary' ? theme.primary : value === 'neutral' ? theme.muted : tones[value] ?? theme.text;

export const themeStyle = {
    '--panel-page': theme.page,
    '--panel-surface': theme.surface,
    '--panel-subtle': theme.subtle,
    '--panel-border': theme.border,
    '--panel-text': theme.text,
    '--panel-muted': theme.muted,
    '--panel-primary': theme.primary,
    '--panel-blue-soft': softTones.blue,
    '--panel-red': tones.red,
    '--panel-red-soft': softTones.red,
    '--panel-amber': tones.amber,
    '--panel-amber-soft': softTones.amber,
    '--panel-green': tones.green,
    '--panel-green-soft': softTones.green,
} as CSSProperties;

export function pageAppearance(appearance?: Appearance): CSSProperties {
    return {
        ...themeStyle,
        maxWidth: appearance?.contentMaxWidth || undefined,
        padding: appearance?.pagePadding ?? 24,
    };
}

export function surfaceStyle(type: string, overrides?: Surface): CSSProperties {
    const card = (overrides?.kind ?? (type === 'container' ? 'card' : 'none')) === 'card';
    const background = overrides?.background ?? (card ? 'surface' : 'transparent');
    const border = overrides?.border ?? (card ? 'subtle' : 'none');
    const padding = overrides?.padding;
    const inset = card ? 16 : 0;
    return {
        backgroundColor: backgroundColor(background),
        ...appearanceBorder(overrides, border === 'subtle' ? 1 : 0, theme.border),
        borderRadius: overrides?.radius ?? (card ? 12 : 0),
        padding: `${padding?.top ?? inset}px ${padding?.right ?? inset}px ${padding?.bottom ?? inset}px ${padding?.left ?? inset}px`,
    };
}

export function roleStyle(typography: Typography | undefined, role: keyof Typography): CSSProperties {
    const defaults=theme.typography[role];
    const overrides=typography?.[role];
    const size=overrides?.size ?? defaults.size;
    const weight=overrides?.weight ?? defaults.weight;
    const color=overrides?.color ?? defaults.color;
    return {
        fontSize: size,
        fontWeight: weight as CSSProperties['fontWeight'],
        color: textColor(color),
    };
}

export function indicatorState(props: Component['props'], value: unknown, typedKey?: string): IndicatorStyle | undefined {
    const key = typedKey ?? (typeof value === 'boolean' || typeof value === 'string' || (typeof value === 'number' && Number.isInteger(value)) ? JSON.stringify(value) : undefined);
    return key !== undefined && props?.indicatorStates && Object.hasOwn(props.indicatorStates, key) ? props.indicatorStates[key] : undefined;
}

export function indicatorBackground(state?: IndicatorStyle) {
    if (state?.background === 'soft')
        return state.color.startsWith('#') ? `color-mix(in srgb, ${state.color} 12%, white)` : softTones[state.color];
    return state?.background?.startsWith('#') ? state.background : undefined;
}

const indicatorColor = (state?: IndicatorStyle) => state?.color.startsWith('#') ? state.color : tones[state?.color ?? 'neutral'];

export function indicatorSurface(state: IndicatorStyle | undefined, base: CSSProperties): CSSProperties {
    if (!state) return base;
    return { ...base, backgroundColor: indicatorBackground(state) ?? base.backgroundColor,
        ...appearanceBorder(state, Number(base.borderWidth) || 0, String(base.borderColor)), borderStyle: state.borderStyle ?? base.borderStyle };
}

function indicatorMarker(props?: Component['props']) {
    return props?.showMarker ?? (props?.indicatorView === 'badge' || props?.indicatorView === 'row');
}

export function IndicatorTitle({ props, state, title, owner }: {
    props?: Component['props']; state?: IndicatorStyle; title: string; owner?: string;
}) {
    const tone = indicatorColor(state);
    return <span className="panel-field-label" {...appearancePart(owner, 'title', {...roleStyle(props?.typography, 'title'),
        ...(props?.titleFollowsState ? {color: tone} : {})}, { display: 'inline-flex', alignItems: 'center', gap: 8 })}>
        {props?.markerPosition === 'title' && indicatorMarker(props) && state &&
            <span className="panel-indicator-marker" {...appearancePart(owner, 'marker', {color: tone})} aria-hidden="true"/>}
        <span>{title}</span>
    </span>;
}

export function IndicatorValue({ props, state, emptyText = '—', hasTitle = false, owner }: {
    props?: Component['props']; state?: IndicatorStyle; emptyText?: string; hasTitle?: boolean; owner?: string;
}) {
    const badge = props?.indicatorView === 'badge';
    const marker = indicatorMarker(props) && !(hasTitle && props?.markerPosition === 'title');
    const tone = indicatorColor(state);
    const normal = {...roleStyle(props?.typography, 'value'), color: tone};
    const base = badge && state ? indicatorSurface(state, {...normal, borderWidth: 0, borderColor: theme.border, borderStyle: 'solid'}) : normal;
    return <span className={`indicator panel-indicator ${badge ? 'panel-indicator-badge' : ''}`}
        {...appearancePart(owner, 'value', base)}>
        {marker && state && <span className="panel-indicator-marker" {...appearancePart(owner, 'marker', {color: tone})} aria-hidden="true"/>}
        {state?.text ?? emptyText}
    </span>;
}

export function ImageView({ url, label, settings, fixedHeight, placeholder, onError, owner, typography }: {
    url?: string; label: string; settings?: ImageSettings; fixedHeight?: number; placeholder: string; onError?: () => void;
    owner?: string; typography?: Typography;
}) {
    const [ratio, setRatio] = useState<number>();
    const [failed, setFailed] = useState(false);
    const align = { start: '0%', center: '50%', end: '100%' };
    const position = `${align[settings?.horizontalAlign ?? 'center']} ${align[settings?.verticalAlign ?? 'center']}`;
    const fixed = settings?.sizing === 'fixed';
    const background = settings?.background ?? 'subtle';
    return <div className="panel-image-view" {...appearancePart(owner, 'viewport', { backgroundColor: backgroundColor(background) },
        fixed ? { height: fixedHeight || 180 } : { aspectRatio: settings?.aspectRatio || ratio || 4 / 3 })}>
        {url && !failed ? <img className="result-image" src={url} alt={label} style={{ objectPosition: position }}
            onLoad={event => {
                const { naturalWidth, naturalHeight } = event.currentTarget;
                if (naturalWidth && naturalHeight) setRatio(naturalWidth / naturalHeight);
            }}
            onError={() => { setFailed(true); onError?.(); }}/>
            : <p role="status" {...appearancePart(owner, 'helper', typography?.helper && roleStyle(typography, 'helper'))}>{failed ? '图片读取失败或无权访问' : placeholder}</p>}
    </div>;
}

export function hasPageNavigation(components: Component[]): boolean {
    return components.some(component => component.type === 'page_navigation' || hasPageNavigation(component.children ?? []));
}

export function PageNavigation({ panel, pageId, onPage, owner, typography }: {
    panel: Panel; pageId: string; onPage: (page: string) => void; owner?:string; typography?:Typography;
}) {
    const tabs = panel.navigation?.mode === 'tabs';
    return <nav className={`page-nav ${tabs ? 'page-tabs' : ''}`} role={tabs ? 'tablist' : undefined} aria-label="页面导航">
        {panel.pages.map(page => <button key={page.id} role={tabs ? 'tab' : undefined}
            {...appearancePart(owner, tabs ? 'tab' : 'action', typography?.value && roleStyle(typography, 'value'))}
            aria-selected={tabs ? page.id === pageId : undefined} aria-current={page.id === pageId ? 'page' : undefined}
            onClick={() => onPage(page.id)}>{page.title}</button>)}
    </nav>;
}
