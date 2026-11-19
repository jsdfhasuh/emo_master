import { useState } from 'react';
import type { CSSProperties } from 'react';
import * as theme from '../../../contracts/panel-contract-v1/theme.json';
import type { Appearance, Component, ImageView as ImageSettings, IndicatorStyle, Panel, Surface, Typography } from '../contracts/types';

const tones = theme.foreground as Record<string, string>;
const softTones = theme.soft as Record<string, string>;

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
        backgroundColor: background === 'transparent' ? 'transparent' : theme[background],
        border: border === 'subtle' ? `1px solid ${theme.border}` : 'none',
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
        color: color === 'primary' ? theme.primary : color === 'neutral' ? theme.muted : tones[color] ?? theme.text,
    };
}

export function indicatorState(props: Component['props'], value: unknown, typedKey?: string): IndicatorStyle | undefined {
    const key = typedKey ?? (typeof value === 'boolean' || typeof value === 'string' || (typeof value === 'number' && Number.isInteger(value)) ? JSON.stringify(value) : undefined);
    return key !== undefined && props?.indicatorStates && Object.hasOwn(props.indicatorStates, key) ? props.indicatorStates[key] : undefined;
}

export function indicatorBackground(state?: IndicatorStyle) {
    return state?.background === 'soft' ? softTones[state.color] : undefined;
}

export function IndicatorValue({ props, state, emptyText = '—' }: {
    props?: Component['props']; state?: IndicatorStyle; emptyText?: string;
}) {
    const badge = props?.indicatorView === 'badge';
    const marker = props?.showMarker ?? (props?.indicatorView === 'badge' || props?.indicatorView === 'row');
    return <span className={`indicator panel-indicator ${badge ? 'panel-indicator-badge' : ''}`}
        style={{ ...roleStyle(props?.typography, 'value'), color: tones[state?.color ?? 'neutral'], backgroundColor: badge ? indicatorBackground(state) : undefined }}>
        {marker && state && <span className="panel-indicator-marker" aria-hidden="true"/>}
        {state?.text ?? emptyText}
    </span>;
}

export function ImageView({ url, label, settings, fixedHeight, placeholder, onError }: {
    url?: string; label: string; settings?: ImageSettings; fixedHeight?: number; placeholder: string; onError?: () => void;
}) {
    const [ratio, setRatio] = useState<number>();
    const [failed, setFailed] = useState(false);
    const align = { start: '0%', center: '50%', end: '100%' };
    const position = `${align[settings?.horizontalAlign ?? 'center']} ${align[settings?.verticalAlign ?? 'center']}`;
    const fixed = settings?.sizing === 'fixed';
    const background = settings?.background ?? 'subtle';
    return <div className="panel-image-view" style={{
        backgroundColor: background === 'transparent' ? 'transparent' : theme[background],
        ...(fixed ? { height: fixedHeight || 180 } : { aspectRatio: settings?.aspectRatio || ratio || 4 / 3 }),
    }}>
        {url && !failed ? <img className="result-image" src={url} alt={label} style={{ objectPosition: position }}
            onLoad={event => {
                const { naturalWidth, naturalHeight } = event.currentTarget;
                if (naturalWidth && naturalHeight) setRatio(naturalWidth / naturalHeight);
            }}
            onError={() => { setFailed(true); onError?.(); }}/>
            : <p role="status">{failed ? '图片读取失败或无权访问' : placeholder}</p>}
    </div>;
}

export function hasPageNavigation(components: Component[]): boolean {
    return components.some(component => component.type === 'page_navigation' || hasPageNavigation(component.children ?? []));
}

export function PageNavigation({ panel, pageId, onPage }: { panel: Panel; pageId: string; onPage: (page: string) => void }) {
    const tabs = panel.navigation?.mode === 'tabs';
    return <nav className={`page-nav ${tabs ? 'page-tabs' : ''}`} role={tabs ? 'tablist' : undefined} aria-label="页面导航">
        {panel.pages.map(page => <button key={page.id} role={tabs ? 'tab' : undefined}
            aria-selected={tabs ? page.id === pageId : undefined} aria-current={page.id === pageId ? 'page' : undefined}
            onClick={() => onPage(page.id)}>{page.title}</button>)}
    </nav>;
}
