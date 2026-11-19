import type { CSSProperties, MouseEventHandler } from 'react';
import type { HostControl, PanelShell, TextStyle } from '../contracts/types';
import * as theme from '../../../contracts/panel-contract-v1/theme.json';

const textColor = (value?:string) => value === 'primary' ? theme.primary
    : value === 'neutral' ? theme.muted : (theme.foreground as Record<string,string>)[value ?? ''] ?? theme.text;

export function shellTextStyle(shell:PanelShell|undefined, role:'title'|'helper'):CSSProperties {
    const settings = shell?.headerTypography?.[role];
    return {
        fontSize: settings?.size ?? (role === 'title' ? 20 : 12),
        fontWeight: settings?.weight ?? (role === 'title' ? 'bold' : 'normal'),
        color: textColor(settings?.color ?? (role === 'title' ? 'default' : 'neutral')),
    };
}

export function shellHeaderStyle(shell?:PanelShell):CSSProperties {
    const surface = shell?.headerSurface;
    if (!surface) return {};
    const card = surface.kind === 'card';
    const background = surface.background ?? 'surface';
    const border = surface.border ?? (card ? 'subtle' : 'none');
    return {
        backgroundColor: background === 'transparent' ? 'transparent' : theme[background],
        border: border === 'subtle' ? `1px solid ${theme.border}` : 'none',
        borderRadius: surface.radius ?? (card ? 12 : 0),
        padding: `${surface.padding?.top ?? 12}px ${surface.padding?.right ?? 24}px ${surface.padding?.bottom ?? 12}px ${surface.padding?.left ?? 24}px`,
    };
}

export function hostControlStyle(control:HostControl|undefined, kind:'start'|'stop'):CSSProperties {
    const appearance = control?.appearance;
    const style = appearance?.style ?? (kind === 'start' ? 'primary' : 'secondary');
    const text:TextStyle|undefined = appearance?.text;
    return {
        '--host-control-background': style === 'primary' ? theme.primary : style === 'link' ? 'transparent' : theme.surface,
        '--host-control-color': text?.color ? textColor(text.color) : style === 'primary' ? '#ffffff' : style === 'link' ? theme.primary : theme.text,
        '--host-control-border': style === 'primary' ? theme.primary : style === 'link' ? 'transparent' : theme.border,
        '--host-control-hover-background': style === 'primary' ? theme.primary : theme.subtle,
        borderRadius: appearance?.radius ?? 8,
        padding: `${appearance?.padding?.top ?? 8}px ${appearance?.padding?.right ?? 16}px ${appearance?.padding?.bottom ?? 8}px ${appearance?.padding?.left ?? 16}px`,
        fontSize: text?.size ?? 13,
        fontWeight: text?.weight ?? 'normal',
        minWidth: appearance?.minWidth ?? 0,
        minHeight: appearance?.minHeight ?? 32,
    } as CSSProperties;
}

export function HostControlButton({control,kind,fallbackLabel,disabled,title,onClick}: {
    control?:HostControl; kind:'start'|'stop'; fallbackLabel:string; disabled:boolean;
    title:string; onClick:MouseEventHandler<HTMLButtonElement>;
}) {
    return <button className="operator-control" style={hostControlStyle(control,kind)} disabled={disabled}
        title={title} onClick={onClick}>
        {control?.appearance?.showIcon && <svg aria-hidden="true" viewBox="0 0 16 16" width="14" height="14" fill="currentColor">
            {kind === 'start' ? <path d="M4 2L14 8L4 14Z"/> : <path d="M3 3H13V13H3Z"/>}
        </svg>}
        {control?.label ?? fallbackLabel}
    </button>;
}
